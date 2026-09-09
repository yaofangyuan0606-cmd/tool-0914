# 方案1 demo：按 xyz 范围从 Neuroglancer 截图

给定 8 nm 体素坐标范围（x、y、z），用无头 Chromium 打开 H01 公开 Neuroglancer 站点，
把整个范围正好塞进切片面板，逐片截图并裁出范围对应的像素矩形。零第三方依赖
（Playwright 直接从 `pos_v2/frontend/node_modules` 引用，PNG 读写是自带的 `png.js`）。

## 文件

| 文件 | 作用 |
|---|---|
| `screenshot_range.js` | 主脚本 |
| `png.js` | 零依赖 PNG 解码/编码/裁剪/统计（只支持 8-bit RGB/RGBA，Chromium 截图就是这种） |
| `boss_url.txt` | 老板给的完整 Neuroglancer URL，脚本从这里解码图层和 182 个已选 segment |
| `validate_mapping.py` | 用 cloud-volume 校验像素→坐标公式（可选） |
| `out/em/`、`out/seg/`、`out/seg_all/` | 统一测试范围的实际输出 |

## 用法

```bash
export PATH="/Users/mac/.workbuddy/binaries/node/versions/22.22.2/bin:$PATH"   # 必须用 Node 22
cd /Users/mac/PycharmProjects/Neuroglancer/demos/demo1_screenshot

# 只有 EM
node screenshot_range.js --x 246000-246700 --y 201300-201900 --z 2048-2052 --out out/em
# EM + 分割覆盖（老板 URL 里那 182 个已选 segment）
node screenshot_range.js --x 246000-246700 --y 201300-201900 --z 2048-2052 --seg --out out/seg
# EM + 全部分割（清空已选列表，Neuroglancer 就会画所有 segment）
node screenshot_range.js --x 246000-246700 --y 201300-201900 --z 2048-2052 --seg --segments all --out out/seg_all
# 只画指定 ID
node screenshot_range.js ... --seg --segments 5436902566,5799532727
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--x A-B --y A-B --z A-B` | 必填 | 范围，单位是状态 JSON 的 dimensions 单位（x/y 每单位 8 nm，z 每单位一片 33 nm）。z 含两端，每个整数一片 |
| `--seg` | 关 | 显示 c3 分割层；不带时分割层 `visible:false` |
| `--segments boss\|all\|ID,ID` | `boss` | 已选 segment 列表来源：老板 URL 的列表 / 清空（显示全部）/ 自定义 |
| `--out DIR` | `out/em` 或 `out/seg` | 输出目录 |
| `--width --height` | 1400 800 | 视口大小；切片面板 = 视口去掉 46 px 顶栏 |
| `--wait` | 12000 | 每片固定等待 ms |
| `--max-wait` | 60000 | 每片最长等待 ms（含重试） |
| `--settle` | 1500 | `viewer.isReady()` 为真后再等的 ms |
| `--margin` | 0 | 范围与面板边缘之间留的像素（>0 时范围不再"正好"贴边，见已知限制） |
| `--min-nonblack` | 0.05 | 裁剪区非黑像素比例低于此值视为没加载完，再等 3 s 重试 |
| `--url` | boss_url.txt | 换一条 Neuroglancer 状态 URL |

输出：每片 `full_z_XXXX.png`（整屏 1400×800，含顶栏）、`z_XXXX.png`（只含范围）、`meta.json`。

## 原理

1. **状态构造**：解码老板 URL 的 `#!` JSON，只保留 `4nm EM` 图像层和 `c3 segmentation`
   层（连同它的 `segments` 列表），其它层全部丢掉；删掉分割层的 `panels`（侧栏会挤占面板宽度）、
   关掉 mesh subsource；`layout:"xy"`、`showSlices:false`、`helpPanel.visible:false`、
   `selectedLayer.visible:false`，另外关掉十字线 `showAxisLines`、比例尺 `showScaleBar`、
   `showDefaultAnnotations`，免得混进裁剪结果。
2. **视野计算**：面板矩形是 `[0, 46, 1400, 754]`（用 `.neuroglancer-panel` 的 `getBoundingClientRect` 实测）。
   `position = [(xA+xB)/2, (yA+yB)/2, z+0.5]`（z+0.5 是第 z 片体素中心，顶栏会显示 z）。
   `crossSectionScale = max((xB-xA)/面板宽, (yB-yA)/面板高)`，单位是"维度单位/屏幕像素"，
   所以 `nm/px = crossSectionScale × 8`。
3. **每片一个新 page**：只改 `#` 不会重载页面，所以每个 z 都 `context.newPage()` 再 `goto`。
4. **等待加载**：固定等 `--wait` ms → 轮询 `window.viewer.isReady()`（H01 站点把 viewer 挂在全局上）
   → 再等 `--settle` ms → CDP `Page.captureScreenshot` 截图 → 裁出范围矩形并统计非黑比例，
   低于阈值就再等 3 s 重截，直到 `--max-wait`。`page.screenshot()` 在 WebGL 页面上会超时，所以用 CDP。
5. **裁剪**：范围在整屏截图中的像素框
   `x0 = 700 + (xA - cx)/scale`，`y0 = 46 + 377 + (yA - cy)/scale`，`w = (xB-xA)/scale`，`h = (yB-yA)/scale`，
   四舍五入后裁出来存 `z_XXXX.png`。

## 像素 → 坐标换算

对 `z_XXXX.png` 里的像素 `(px, py)`（左上角为 0，像素中心）：

```
x = xA + (px + 0.5) × crossSectionScale        # 单位：8 nm 体素
y = yA + (py + 0.5) × crossSectionScale
z = 文件名里的 z
x_nm = x × 8,  y_nm = y × 8,  z_nm = z × 33
```

反过来，坐标 → 像素：`px = (x - xA) / crossSectionScale`。`crossSectionScale`、`xA`、`yA` 都写在 `meta.json` 的 `pixel_to_coord` 里。
对整屏 `full_z_XXXX.png`，先减去 `range_box_px_in_full_rounded` 的 `x`、`y` 再套上式。

**校验**：`validate_mapping.py` 用 cloud-volume 拉同一范围的 c3 标签、按上式重采样，比较每个标签区域内
截图叠加色是否一致。统一测试范围上：不平移时残差 ≈1.9，平移 1 px 就升到 ≈5–6，2 px ≈9–10，用相邻 z 片的标签
也明显变大，说明公式在 x、y、z 三个方向都对到 1 px 以内。

## 统一测试范围的实际结果

x 246000–246700，y 201300–201900，z 2048–2052（5 片）：

- `crossSectionScale = 0.79576`，即 **6.366 nm/px**（y 方向 600 单位撑满 754 px 面板高度）
- 范围像素框 `{x:260, y:46, w:880, h:754}`，`z_XXXX.png` 尺寸 **880×754**，`full_z_XXXX.png` 1400×800
- 每片约 14.5–15.5 s（goto ≈0.5 s + 固定等 12 s + isReady + settle 1.5 s + 截图编码），一次 5 片总计 ≈74 s
- 裁剪区非黑比例 0.975–0.986，全部通过
- `out/seg`（老板的 182 个 segment）和 `out/em` **逐像素完全相同**：用 cloud-volume 查过，这 182 个 ID 在测试范围
  的 5 片里一个都不出现（范围里出现的是 5436902566、5799532727、40984862306 等），所以没有覆盖是正确结果，
  不是加载失败。`out/seg_all`（`--segments all`）里 ≈78% 像素带颜色，证明覆盖渲染链路是通的。

## 已知限制

- **分辨率有上限**：截图分辨率由 `crossSectionScale` 决定，范围越大每像素 nm 越多；范围小于面板像素数时
  `crossSectionScale < 1`，Neuroglancer 会取 mip0（4 nm）数据上采样，但不会比 4 nm 更细。
  想要"每体素一像素"就把范围取成 ≤1400×754 单位或改大视口。
- **`--margin 0` 时范围贴面板边**，面板自带的左上角 xyz 小坐标轴图标和右上角两个面板按钮可能进入裁剪区
  （测试范围 x 方向只占 260–1140 px，没碰到）。要保险就 `--margin 40`，代价是范围不再贴边，
  `crossSectionScale` 按缩小后的面板算，公式和 meta 会跟着变，仍然自洽。
- **加载判定是启发式的**：`viewer.isReady()` 在网页上很早就返回 true，主要靠固定等待 + 非黑检测。
  网络慢时可能截到部分 chunk 还没到的画面（表现为局部灰块）；加大 `--wait` 即可。没有逐 chunk 的进度 API。
- **分割颜色不是 ID**：叠加色是 `colorSeed` 哈希出来的，不能从颜色反推 segment ID；要 ID 得走 cloud-volume。
- **Neuroglancer 的插值/混合**：截图是 EM 与分割 alpha 混合后的画面，不是原始灰度值；做定量分析请直接用
  backend 的 cloud-volume 取数，本方案只适合"所见即所得"的可视化导出。
- **依赖公开站点**：`h01-dot-neuroglancer-demo.appspot.com` 改版（顶栏高度、`window.viewer` 是否暴露）会影响
  `TOP_BAR_PX=46` 和 readiness 轮询；换了视口尺寸也要确认顶栏仍是 46 px。
- 每片串行、每片一个新 page，5 片 ≈75 s；要更快可以并行开多个 page，但公开站点的带宽是瓶颈。
- `png.js` 只支持 8-bit RGB/RGBA 非交错 PNG，输出统一为 RGB。

## 验证记录（2026-09-07 复跑）

- 从干净 shell 复跑三条命令：全部 exit 0，耗时 74.2 s / 74.4 s / 78.7 s（合计 231 s）；产物尺寸、非黑比例、meta 数字与上文一致；`out/em` 与 `out/seg` 逐像素相同（cloud-volume 复核：182 个 boss ID 在范围内一个不出现，范围内 78.2% 体素有标签，与 seg_all ≈78% 着色像素一致）；`validate_mapping.py` 复跑 5 片 ALL OK。
- 补的输入校验：x/y 零宽、`--width abc`、`--margin` 负数或过大 → 立即报错退出 1；反向范围（A>B）自动交换并打警告；启动前读图像层 `info` 做数据集边界检查，范围完全在数据集外（如 `--z 6000-6000`）立即报错，部分超出打警告（拿不到 info 只警告不阻断）。
- `--out` 现在统一转绝对路径写进 meta.files；`validate_mapping.py` 按文件名在给定目录里找图，输出目录搬走也能校验。
- 破坏测试：跨 chunk 边界（245952-246080 / 201280-201408 / z 2047-2048）、64×64×1、2000×2000×2 都正常出图（分别 754×754 像素，nonBlack 0.96-0.99）；2000×2000 时每片约 23-28 s。
