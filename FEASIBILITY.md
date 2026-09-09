# Neuroglancer 按 xyz 范围取图 / 取数：可行性报告

日期：2026-09-07
范围：H01 数据集（`gs://h01-release/data/20210601/4nm_raw` 图像 + `c3` 分割），统一测试范围 x 246000-246700, y 201300-201900, z 2048-2052（dimension 单位 = 8 nm 体素坐标；约 5.6 µm × 4.8 µm × 5 片）。
产物目录：`/Users/mac/PycharmProjects/Neuroglancer/demos/`（`backend/`、`frontend/` 未改动）。

---

## 1. 一句话结论

**方案2（cloud-volume 直接按范围取数）做主力数据管道；方案1（脚本化截图）只作为"复现 Neuroglancer 画面"的展示图与抽检工具；"LLM agent 操控浏览器截图"不建议作为生产手段。**

依据：同一范围下方案2 端到端 6.4-8.7 s 拿到 uint8 EM 与 uint64 分割原始体素（175 个 ID 可直接回查），方案1 每片 14.5-15.5 s、5 片 74 s，只得到 alpha 混合后的 RGB PNG，分割 ID 不可逆推；两者坐标系经交叉比对完全一致（EM 皮尔逊 0.961-0.964，最优平移 (0,0)）。三位评审给方案1 的分数为 3 / 4 / 3.5，方案2 为 9 / 8 / 8.5。

---

## 2. 两个需求的重新表述

用户原话：

- 方案1："AI agent 模拟窗口截图，要根据左上角的 xyz 输入范围来实现截图"
- 方案2："直接根据 xyz 输入范围进行代码爬虫"

重新表述为可验证的目标：

| | 方案1：按范围截图 | 方案2：按范围取数 |
|---|---|---|
| 输入 | 老板的 Neuroglancer URL（含 182 个已选 segment）+ x/y/z 范围（顶栏坐标单位，8nm/8nm/33nm） | 同一范围 + 图像 mip |
| 输出 | 每个 z 一张 PNG，画面 = Neuroglancer 在该范围内渲染的 EM + 分割叠加，且像素能回溯到体素坐标 | 每层一个 numpy 数组（EM uint8、分割 uint64）+ 统计（ID 列表、体素数、老板 segment 命中）+ 预览 PNG |
| "AI agent"含义 | 需求原文说 AI agent 模拟操作窗口；实际有两种形态：确定性脚本（Playwright/官方 screenshot 工具）与 LLM 操控浏览器，见第 6 节 | 无 agent，纯代码 |

两个需求的共同点是"坐标范围 → 产物"，差别在产物是渲染图还是原始数据。

---

## 3. 两个 demo：是什么、怎么跑、产出什么

### 3.1 方案1：`demos/demo1_screenshot/`

文件：
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo1_screenshot/screenshot_range.js`（主脚本，Playwright + CDP 截图）
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo1_screenshot/png.js`（零依赖 PNG 解码/编码/裁剪，因 node_modules 无 PNG 库且禁止安装）
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo1_screenshot/boss_url.txt`（老板 URL 原文）
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo1_screenshot/validate_mapping.py`（用 cloud-volume 反向校验像素→坐标公式）
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo1_screenshot/README.md`

原理：解码老板状态 JSON，只保留 4nm EM 层与 c3 分割层（原样带 182 个 segment），去掉侧栏/mesh/十字线/比例尺，layout 设为 `xy`（单面板占满 1400×754），position = 范围中心、z+0.5，crossSectionScale = max(Δx/1400, Δy/754)；每个 z 新开 page 再 goto（只改 `#` 不会重载），固定等 12 s + 轮询 `window.viewer.isReady()` + settle 1.5 s，用 CDP `Page.captureScreenshot` 截图（`page.screenshot` 在 WebGL 页会超时），裁掉 46 px 顶栏并按公式裁出范围矩形，写 meta.json。

运行命令：

```bash
export PATH="/Users/mac/.workbuddy/binaries/node/versions/22.22.2/bin:$PATH" \
&& cd /Users/mac/PycharmProjects/Neuroglancer/demos/demo1_screenshot \
&& node screenshot_range.js --x 246000-246700 --y 201300-201900 --z 2048-2052 --out out/em \
&& node screenshot_range.js --x 246000-246700 --y 201300-201900 --z 2048-2052 --seg --out out/seg \
&& node screenshot_range.js --x 246000-246700 --y 201300-201900 --z 2048-2052 --seg --segments all --out out/seg_all
```

产出（每组 5 片）：`out/{em,seg,seg_all}/z_2048..2052.png`（范围裁剪 880×754）、`full_z_XXXX.png`（整幅 1400×800）、`meta.json`。

实测数字（验证者在干净 shell 重跑）：

| 指标 | 数值 |
|---|---|
| crossSectionScale | 0.7957559681697612 维度单位/px（= 6.366 nm/px） |
| 范围裁剪尺寸 | 880×754 px（879.67 px 四舍五入；754 px × 6.366 = 4800 nm 精确） |
| 每片耗时 | 14.5-15.5 s（em 组 15478/14657/14584/14566/14573 ms） |
| 5 片总耗时 | em 74.2 s、seg 74.4 s、seg_all 78.7 s，三组合计 231 s |
| 裁剪区非黑比例 | em/seg 0.975-0.977，seg_all 0.984-0.986 |
| seg_all 带色像素比例 | 0.759-0.788 |
| 像素→坐标公式 | `x = xA + (px+0.5)*0.79576`，同理 y；z 直接对应 |
| 公式校验（validate_mapping.py 色度残差） | 不平移 1.92，平移 1 px 5.6，2 px 9.3，4 px 22.3，用错 z 片 22.5 → 5 片 ALL OK，x/y/z 准到 1 px 内 |
| 老板 URL segment 数 | 182（任务描述写 190，实际解码为 182） |
| 老板 segment 出现在范围内的数量 | 0 → `out/seg` 与 `out/em` 逐字节相同（cmp 一致），需 `--segments all` 才能看到叠加 |
| 极限测试 | 128×128×2 跨 chunk 边界 1.358 nm/px 31 s；64×64×1 0.679 nm/px 16 s（4 nm 数据上采样）；2000×2000×2 21.22 nm/px 53 s（23-28 s/片） |

验证阶段修复：参数校验（非数字、负 margin、零宽范围）、数据集边界检查（`--z 6000` 由 60 s 黑图重试变为 1 s 报错 exit 1）、meta.json 路径改绝对路径。

### 3.2 方案2：`demos/demo2_fetch/`

文件：
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo2_fetch/fetch_range.py`（独立脚本，不 import backend；仅依赖 numpy/PIL/cloud-volume，WorkBuddy python 已有）
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo2_fetch/README.md`

原理：用 `CloudVolume(url, mip, use_https=True, fill_missing=True)` 打开 4nm_raw（默认 mip1 = 8 nm，与范围单位一致）与 c3 mip0；chunk 大小、voxel_offset、volume_size 全部从 info 读取；对半开区间做 chunk 算术（触及 chunk 数、读放大），`--align` 时向外吸附到 chunk 边界；切片下载得 [x,y,z] 数组；保存 npy、统计 ID、与老板 URL 的 segment 列表求交、按 z 写 EM PNG 与 overlay PNG。

运行命令：

```bash
cd /Users/mac/PycharmProjects/Neuroglancer/demos/demo2_fetch \
&& PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python \
&& $PY fetch_range.py --x 246000-246700 --y 201300-201900 --z 2048-2052 --out out/plain \
&& $PY fetch_range.py --x 246000-246700 --y 201300-201900 --z 2048-2052 --out out/aligned --align
```

产出：`out/plain/{em.npy, seg.npy, stats.json, seg_ids.json, em_z_XXXX.png ×5, overlay_z_XXXX.png ×5}`；`out/aligned/` 同结构但 z 膨胀到 32 片（64 张 PNG）。

实测数字：

| 指标 | plain | aligned |
|---|---|---|
| 实际数组形状 (x,y,z) | EM 700×600×5 uint8，SEG 700×600×5 uint64 | 896×768×32（读范围 [245888,246784)×[201216,201984)×[2048,2080)） |
| 耗时 | open_info 1.8 s + fetch_em 2.8 s + fetch_seg 1.1 s，总 6.4 s（干净 shell 重跑 7.5 s，另一次 8.7 s） | 15.2-17.0 s |
| 触及 chunk / 读放大 | 42 个（7×6×1）/ 10.49×（z 只要 5 片但 chunk 深 32） | 42 个 / 1.0 |
| 解码字节 | EM 2.10 MB，SEG 16.80 MB | EM 22.0 MB，SEG 176.2 MB |
| 线上估算 | EM ≈ 6.6 MB，SEG ≈ 3.3 MB（经验系数，非实测流量） | 同 |
| EM 均值/标准差 | 127.577 / 63.549 | 127.532 / 64.674 |
| 唯一 ID | 175（174 非零），背景 21.82%，top ID 5436902566 占 407888 体素；URL position 处 ID 5799532727 | 450 |
| 老板 segment 命中 | 0 / 182 | 0 / 182 |
| 磁盘 | 24 MB | 252 MB |

一致性校验（验证者）：plain 是 aligned 在偏移 (112,84,0) 处的精确子集；跨 chunk 边界 200×200×2、64×64×1 与 aligned 子块逐体素相等；`em_z_2050.png` 与 `em.npy[:,:,2].T` 逐像素相等。大范围 2000×2000×2：21.5 s，272 chunk，读放大 17.8×，峰值 RSS 593 MB，1477 个 ID，老板 segment 命中 1 个（5115869424）—— 证明命中逻辑本身正确。`--mip 0` 冒烟测试：EM 200×200 vs SEG 100×100，5.2 s；mip0 2×2 降采样与 mip1 相关 0.976。

验证阶段修复：空区间/反向/越界（x 600000、z 6000、z 5290-5300）由 cloud-volume 回溯改为一行中文错误 exit 1；EM 全黑时打 `[warn]`；stats 增加 `read_physical_size_um`。

---

## 4. 同一范围的对比结果

脚本 `/Users/mac/PycharmProjects/Neuroglancer/demos/compare_demos.py`，对比图 `/Users/mac/PycharmProjects/Neuroglancer/demos/comparison.png`（1900×2310），指标 `/Users/mac/PycharmProjects/Neuroglancer/demos/comparison_metrics.json`。方法：把方案1 的 880×754 裁剪图（6.37 nm/px）重采样到方案2 的 700×600 体素网格，逐片比较。

| z | EM 皮尔逊 | 归一化 MAD | 最优平移 (px) | 翻转/转置后相关 | 分割灰度相关 | 边界 IoU（随机平移 30 px 基线） | 边界相关 |
|---|---|---|---|---|---|---|---|
| 2048 | 0.9621 | 0.182 | (0,0) | -0.024 / 0.029 / 0.011 | 0.887 | 0.605 (0.161) | 0.719 |
| 2049 | 0.9609 | 0.185 | (0,0) | -0.004 / 0.009 / 0.008 | 0.882 | 0.605 (0.169) | 0.716 |
| 2050 | 0.9616 | 0.179 | (0,0) | 0.006 / 0.017 / 0.025 | 0.883 | 0.607 (0.174) | 0.717 |
| 2051 | 0.9620 | 0.178 | (0,0) | 0.009 / 0.011 / 0.041 | 0.887 | 0.609 (0.171) | 0.716 |
| 2052 | 0.9641 | 0.174 | (0,0) | 0.020 / 0.011 / 0.017 | 0.896 | 0.605 (0.170) | 0.711 |

结论：
- 两方案取到的是同一块组织、同一坐标系：EM 相关平均 0.962，最优平移全为 (0,0)，任何翻转/转置都把相关打到 ≈0，说明方案1 的 position→像素换算（8 nm 单位、y 向下、裁剪偏移 x=260 y=46）与方案2 的 cloud-volume mip1 [x,y,z] 索引完全一致。
- 剩余约 4% 的不相关来自截图的非整数缩放重采样（0.7958 体素/px → LANCZOS 到 700×600）、Neuroglancer 的 shader/插值与 8 bit 量化；方案1 平均灰度比方案2 低约 0.6（126.57-127.49 vs 127.15-128.12）。
- 分割只能比边界不能比颜色（两者配色哈希不同）：边界 IoU 0.605-0.609 远高于基线 0.17，轮廓位置一致。
- 对比图第三列用的是 `out/seg_all`（`segments=all`），因为 `out/seg` 与 `out/em` 逐字节相同（老板 182 个 segment 在范围内为 0 个）。

---

## 5. 可行性对比表

打分为 1-10，取三位评审的中位/综合；依据只引用证据包数字。

| 维度 | 方案1：脚本截图 | 方案2：cloud-volume 取数 | 依据 |
|---|---|---|---|
| 保真度 | 3 | 9 | 方案1 输出 R=G=B 的 8 bit PNG，EM 归一化 MAD 0.17-0.19，uint64 ID 不可逆，seg_all 中 EM 标准差从 63.5 降到 50-52（alpha 混色）；方案2 `em_z_2050.png` 与 `em.npy` 逐像素相等，175 个 ID 可直接查表；两者共有的底层损失是 4nm_raw 三级 encoding 均为 jpeg（数据集固有） |
| 速度 | 3 | 9 | 方案1 每片 14.5-15.5 s、5 片 74 s，EM/分割要分开跑（三组 231 s）；方案2 EM+SEG 一次取回 6.4-8.7 s（≈1.1-1.7 s/片），快 10-13 倍 |
| 成本 | 6 | 9 | 两者 API 成本均为 0；方案1 每片重开 page 无缓存，视口 1400×754 比范围面积大约 1.6 倍且三组各拉一遍；方案2 42 chunk、读放大 10.49×（align 后 1.0），线上估算 EM 6.6 MB + SEG 3.3 MB；LLM agent 形态另计（第 6 节，$0.06-0.30/张） |
| 鲁棒性 | 4 | 8 | 方案1 依赖 Node 22 + 借用 pos_v2 的 Playwright + Chromium/SwiftShader + appspot 站点，TOP_BAR_PX=46、面板矩形、`window.viewer` 均为实测硬编码，加载判定是"固定 12 s + 非黑 ≥0.05"启发式；方案2 仅依赖 precomputed info 与 GCS 匿名 https，9 项破坏性测试全部一行错误 exit 1 |
| 规模（1 万 ROI × 5 片） | 3.5 | 8.5 | 方案1 5 万次页面加载 × 15 s ≈ 208 h 串行，4-6 个浏览器 35-52 h，每个 Chromium 几百 MB；方案2 ≈ 8.5 s/ROI ≈ 24 h 串行，4-8 进程 3-6 h，带宽约 100 GB 量级，可加 `cache=` 目录与 `parallel` |
| 合规 | 5 | 9 | 方案2 只匿名读 storage.googleapis.com/h01-release（curl 无凭证 200，NEARLINE，无 requester-pays），数据 CC BY 4.0，程序化读取是官方页推荐方式；方案1 额外把 5 万次加载压在公共演示站 h01-dot-neuroglancer-demo.appspot.com 上，且无版本锁定 |

评审分歧：
- 分数：保真度视角给方案1 3 / 方案2 9；工程视角 4 / 8；规模视角 3.5 / 8.5。方向一致，差异在方案1 的"可并行"评价：规模评审认为"原理上可并行（官方工具 -j 6）"，工程评审认为并入 Flask 进程需拉起 Node/Chromium 子进程、无法复用 cache.py，整合难度高。
- 确定性：保真度评审指出方案1 在同站点同版本下是确定性的（并发重跑后字节数完全一致）；工程与规模评审强调加载判定启发式、慢网络下可能出现"静默的局部灰块"，两者不矛盾——网络正常时可复现，网络异常时无告警。
- 文件大小口径：交叉比对报告统计范围裁剪 5 片 4.99 MB、整幅 8.00 MB、分割 5.94 MB；工程评审统计 `out/em` 目录 12 MB、`seg_all` 15 MB（含 full 图）。

---

## 6. 方案1 的两种形态

### 6.1 确定性脚本截图（本 demo；官方 `neuroglancer.tool.screenshot`）

本 demo（Playwright + CDP）已跑通，见第 3.1 节。官方工具（调研第 1 条）的差异：
- 依赖 `neuroglancer[webdriver]`（selenium ≥4，Selenium Manager 自动下 chromedriver），托管的是 Python 包自带的本地客户端而非 appspot 页面；本机 WorkBuddy python 没有 neuroglancer/selenium，按环境规则需在 `demos/.venv` 另建 venv。
- 加载判定不是固定等待：前端 `python_integration/screenshots.ts` 只有 `viewer.isReady()` 为真且处理完 pending 事件后才 `draw()` + `toDataURL()`，未 ready 时每秒回传 chunk 统计（visibleChunksDownloading 等），60 s 无统计则重载浏览器。比本 demo 的"12 s + 非黑比例"可靠。
- 支持 `--width/--height`、`--tile-width/--tile-height`（超过 4096 自动分块拼接）、`--resolution-scale-factor`（等价 crossSectionScale 除以因子，会触发更细 mip）、`--layout xy`、`--hide-axis-lines`、`-j` 并发浏览器。
- mip 规则（`src/sliceview/base.ts`）：选择 voxel 尺寸 ≤ 屏幕像素物理尺寸 × 1.1 × renderScaleTarget 的最粗一级。老板 URL crossSectionScale 1.5075 → 12.06 nm/px，只会加载 8 nm 级；要看到 4 nm 级需 crossSectionScale ≤ ~0.45 或 `--resolution-scale-factor ≥ 4`。本 demo 测试范围 0.7958 → 6.37 nm/px，Neuroglancer 用 4 nm 级插值。

适用场景：需要"和老板在浏览器里看到的一模一样"的图（colorSeed 配色、selectedAlpha 混合、只高亮 182 个已选 segment）、需要 mesh/annotation/xy-3d 投影等 cloud-volume 拿不到的视觉元素、前端渲染回归 QA、方案2 批量取数后的随机抽检（交叉比对已证明坐标一致）。
成本估算：≈15-25 s/张，几乎全是 chunk 下载；API 成本 0；失败率约 1-3%（可用非黑 + downloading==0 自动重试），同 URL → 同像素。

### 6.2 LLM agent 操控浏览器（Anthropic computer use 类）

机制（调研第 4 条，官方文档）：每个动作一次 API 往返，截图作为 tool_result 回传；1400×800 截图 = ⌈1400/28⌉×⌈800/28⌉ = 1450 视觉 token；建议分辨率 1280×800，长循环要裁剪历史截图。

估算：
- 时延：最小流程"导航 → 等待 → 截图 → 确认加载 → 保存"约 5-8 个动作，每次往返 3-10 s，加上必须等的 12-18 s 加载 → 单张 ≈ 45-120 s，比脚本慢 3-5 倍，且每个并发 agent 需独立上下文，不易并行。
- token：每步重发历史（系统提示 + 工具定义 ≈ 3k），6 步累计输入 ≈ 50k、输出 ≈ 1.5k。
- 成本：Opus 5（$5/$25 per MTok）≈ $0.30/张（prompt caching 后约 $0.10-0.15）；Sonnet 5（$2/$10）≈ $0.12/张；Haiku 4.5（$1/$5）≈ $0.06/张。5 片 z 约 $0.3-1.5；1 万 ROI × 5 片按 Sonnet 5 约 $6,000，按 Opus 5 约 $15,000。
- 失败率：每步 1-5%，6 步复合后任务级 5-20%，且不可复现（等待时机、面板尺寸、鼠标 hover 会改变分割 saturation 混色）。

只有在这些情况才值得用：状态无法用 URL/JSON 表达、只能靠 UI 操作的一次性探索（例如在页面里找某个未知 segment、读右侧面板文字）；需要"判断"而非"截图"——让脚本出图、模型只看一张图做 QA（一次调用 ≈ 1.5k token ≈ $0.01，比开浏览器便宜约 30 倍）；让 LLM 生成一次确定性脚本，之后批量走脚本。对本需求（固定范围、已知 segment 列表）LLM agent 没有优势。

---

## 7. 风险与限制

方案1（截图）：
1. 数据不可用于计算：JPEG 源 → 三线性插值（`src/sliceview/volume/frontend.ts` getInterpolatedDataValue）→ shader invlerp 归一化 → 8 bit 帧缓冲 → 分割 alpha 0.5 混色；uint64 ID 不可逆（调研第 2 条；交叉比对 MAD 0.17-0.19）。
2. 加载判定启发式：`viewer.isReady()` 很早返回 true，实际靠固定 12 s + 非黑 ≥0.05；慢网络下可能截到局部灰块仍通过（方案1 验证记录 issues 第 5 条、known_limits 第 3 条）。1 万 ROI 规模下 1-3% 静默坏图难以事后发现（规模评审）。
3. 站点耦合：TOP_BAR_PX=46、面板矩形 [0,46,1400,754]、`window.viewer` 暴露均为实测值（`screenshot_range.js` 第 21 行注释；known_limits 第 6 条），appspot 改版即失效，无版本锁定；页面自身打两条 404 资源错误需人工判断无害。
4. 依赖链：`require('/Users/mac/Desktop/github/pos_v2/frontend/node_modules/playwright')` 借用另一项目的 node_modules + Chromium + SwiftShader WebGL2（工程评审）。
5. 分辨率被 crossSectionScale 绑死：nm/px = max(Δx/1400, Δy/754)×8，测试范围 6.37 nm/px，2000×2000 范围 21.2 nm/px，64×64 范围 0.68 nm/px（4 nm 数据上采样，不会更细）（known_limits 第 1 条；验证 break_runs）。
6. `--margin 0` 时面板角落坐标轴图标/右上按钮可能进入裁剪区（known_limits 第 2 条）。
7. 无缓存、无断点续传：每片新开 page，z 2048-2052 同在一个 128×128×32 chunk 里却被下载 5 次，三组运行同一 chunk 约拉 15 次；中断只能整组重跑（规模评审，grep 无 existsSync）。
8. `png.js` 只支持 8 bit RGB/RGBA 非交错 PNG，输出丢 alpha（known_limits 第 8 条）。

方案2（取数）：
1. `fill_missing=True`（`fetch_range.py` 第 185-186 行）把桶里真正缺失的 chunk 静默填 0，只有 EM 全黑（max==0）才 warn，部分 chunk 404 不会被发现；生产应改 `fill_missing=False` 或逐 chunk 校验（方案2 验证 issues 第 1 条，三位评审均提及）。
2. 无缓存、无跨层并行：同范围跑两次流量翻倍（known_limits 第 1 条）；读放大 10.49×（plain）到 17.8×（2000×2000×2），薄切片需求与 32 深 chunk 不匹配，约 90% 下载字节被丢弃（规模评审）。
3. 内存/磁盘随范围膨胀：`--align` 后 seg uint64 176 MB 内存 + 252 MB 磁盘、64 张 PNG；2000×2000×2 峰值 RSS 593 MB（验证 large_2000x2000x2）。
4. `--mip` 只支持 0/1（mip ≥2 分辨率非整数比直接报错），分割固定 c3 mip0，mip0 时 EM/SEG 形状差 2 倍；假定 voxel_offset=0（known_limits 第 5、8 条）。
5. overlay PNG 用自定义 splitmix64 配色，与 Neuroglancer colorSeed 不一致，只能看轮廓（known_limits 第 7 条；边界 IoU 0.61）。
6. 线上字节是经验系数（EM 0.3 B/voxel、SEG 0.15 B/voxel）估算而非实测流量（known_limits 第 3 条）。

两方案共有：
1. 4nm_raw 的 info 里 4 nm/8 nm/16 nm 三级 encoding 全为 jpeg，precomputed 规范明言有损（调研第 2 条来源 `storage.googleapis.com/h01-release/data/20210601/4nm_raw/info`）；取到的 uint8 是 JPEG 解码值。
2. 老板 URL 的 182 个 `#spiny-stellate` segment 在统一测试范围内一个都不出现（两 demo 独立用 cloud-volume 查证），本范围无法验证"只高亮已选 segment"的效果为真；2000×2000×2 大范围命中 1 个（5115869424）。
3. 任务描述与实际不符两处：segment 数 190 → 实际 182；图像 chunk 128×128×16 只对 mip0 成立，demo 默认用的 mip1 chunk 是 128×128×32（方案2 problems 第 2 条）。
4. 匿名读 GCS 依赖桶保持公开；`~/.cloudvolume/secrets` 不存在也能跑，cloud-files 自带 7 次指数退避（调研第 3 条）。

---

## 8. 建议的落地路线

### 近期（1-2 周）

1. 把 `demo2_fetch/fetch_range.py` 的能力并入 backend：
   - 现有 `POST /api/ingest`（`backend/main.py` 第 89 行）接收 `url + options`，`scraper.scrape_job` 用 `layer_center_voxel` + `slice_half_xy/slice_half_z` 围绕 URL position 取盒子。增加显式范围选项（如 `bounds_x/bounds_y/bounds_z`，单位与 URL dimensions 一致），有范围时跳过中心+半宽逻辑，直接走 `_read_box`。
   - `backend/core/chunks.py` 的 `grid_range/analyze/align` 与 demo2 的 `grid_range/chunk_cost/align_outward` 同构，`backend/core/cache.py` 的 `roi_key` 内容寻址缓存可直接复用，demo 里"无缓存"的短板由此补齐。
   - 打开 CloudVolume 时改 `fill_missing=False`，捕获缺 chunk 异常并在 job 进度里记录；或保留 `fill_missing=True` 但对每个 chunk 用 `exists` 校验。
   - 把 demo2 的 `seg_ids.json` 统计（ID 体素数、老板 segment 命中）作为 job 的输出之一。
2. 把 `demo1_screenshot/screenshot_range.js` 保留为独立 CLI 工具（不并入 Flask 进程），用途限定为：出"与 Neuroglancer 一致"的汇报图、前端回归对照、对方案2 产物抽检。建议默认 `--margin 40` 避开角标。
3. 补一份 `demos/README`，记录 Node 22 PATH、Playwright 借用路径、WorkBuddy python 路径，以及"任务描述 190 → 实际 182、mip1 chunk 128×128×32"两条勘误。

### 中期（1-2 月）

1. 批量取数：按 ROI 列表调度，`parallel` 4-8 进程 + `cache='目录'`，按 chunk 组织请求把相邻 ROI/多 z 的读放大摊平（aligned 模式证明 42 个 chunk 可换来 896×768×32 全体素）；限制单 ROI 尺寸或关闭 PNG 输出控制内存（2000×2000×2 已 593 MB RSS）。先抓一次真实网络计数替换经验系数。
2. 抽检回路：批量跑完后随机抽 ROI 用方案1 截图，与 backend 重绘的 overlay 跑 `compare_demos.py` 同款指标（EM 皮尔逊 > 0.95、最优平移 (0,0)、边界 IoU 远高于基线），作为回归测试。
3. 若需要官方渲染语义的高分辨率图，在 `demos/.venv` 装 `neuroglancer[webdriver]`，用官方 screenshot 工具的 statistics 驱动等待与 tile 拼接替换 Playwright 固定等待。
4. ImageryClient（CAVEconnectome，基于 cloud-volume）提供 `image_and_segmentation_cutout` + `composite_overlay`，可作为"确定性重画 Neuroglancer 视图"的现成参考，评估是否替代自写 overlay。
5. LLM 仅用于两处：一次性生成/修改确定性脚本；对脚本产出的图做单张视觉 QA（≈$0.01/张）。不用 LLM 操控浏览器做批量截图。

---

## 9. 附录：评审三视角原始打分

| 视角 | 方案1 | 方案2 | 推荐语（摘） |
|---|---|---|---|
| 数据保真度（原始灰度/uint64 ID、mip 控制、有损环节、像素回溯、重跑一致） | 3 | 9 | 凡是要灰度值、uint64 ID、可控 mip 或可精确复现的数据一律方案2（建议 `fill_missing=False` + cache）；方案1 只作复现 Neuroglancer 视觉效果的展示/QA |
| 工程成本与鲁棒性（依赖链、UI 改版敏感度、失败模式、无 GPU 可运行、与 backend/core 整合） | 4 | 8 | 生产取数与后续计算选方案2（与 backend/core 同构，整合后补 fill_missing=False、缓存与分块）；方案1 仅保留为展示截图与渲染 QA 辅助工具 |
| 规模、速度与合规（吞吐、并行、带宽/读放大、匿名访问与许可、1 万 ROI 成本、缓存续传） | 3.5 | 8.5 | 1 万 ROI 采用方案2 匿名读 GCS（CC BY 4.0，≈8.5 s/ROI，可加 cache 与并行）；只在需要与 Neuroglancer 画面逐像素一致的汇报图或抽检时用方案1 |

各视角认为方案1 胜出的场景（合并去重）：
- 产物就是"老板在 Neuroglancer 里看到的画面"（colorSeed 配色、selectedAlpha 混合、invlerp 窗宽窗位、182 个已选 segment 高亮），用于汇报/论文配图/UI 对照。
- 需要渲染层才有的元素：xy-3d 布局的 mesh 投影、annotation/skeleton 层、十字线/比例尺、多层叠加顺序、需要前端鉴权的 graphene 源。
- 前端渲染本身是被测对象（回归 QA），截图是"真值"而非数据源。
- 少量 ROI（几到几十个）一次性出图、本机只有浏览器工具链没有 Python/cloud-volume。
- 作为方案2 批量产物的抽检工具（坐标已证明一致，IoU 0.61 vs 基线 0.17）。
- 需要"所见即所得"证据链：meta.json 保存每片完整 URL，任何人点开即可复看。

评审引用的关键文件：
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo1_screenshot/out/em/meta.json`（timings_ms.total 15105/14620/14609/14623/14977，total_ms 74197）
- `/Users/mac/PycharmProjects/Neuroglancer/demos/demo2_fetch/out/plain/stats.json`（chunks_touched 42，amplification 10.48576）
- `/Users/mac/PycharmProjects/Neuroglancer/demos/comparison_metrics.json`
- `/Users/mac/PycharmProjects/Neuroglancer/backend/core/{scraper.py,cache.py,chunks.py,coordinates.py}`、`/Users/mac/PycharmProjects/Neuroglancer/backend/main.py`
