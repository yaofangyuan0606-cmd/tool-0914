# h01kit — H01 脑连接组数据工具

按 xyz 范围从 H01 公开数据集（人脑皮层，Google Cloud Storage，CC BY 4.0）**取数据 / 出图**的命令行工具。

核心思路：**取一次，渲染多次**。数据只从网络拉一次存成 `npy`，之后想出几种图、出几次，都在本地秒级完成——不用反复开浏览器截图。

---

## 一、安装（同事看这里）

```bash
cd <项目目录>

# 1. Python 环境
python3 -m venv .venv
source .venv/bin/activate

# 2. Python 依赖 + 工具本体
pip install -r requirements.txt
pip install -e .

# 3. Node 依赖（只有要用截图功能才需要）
npm install
npx playwright install chromium
```

验证装好了：
```bash
h01 --help
```

> 只用 `info` / `render` / `fetch` 的话，Node 那步可以跳过。
> 详细安装与排错见 `SETUP.md`。

---

## 二、我要「截取」数据 —— 先选对命令

| 你想干什么 | 用哪个命令 | 要不要网络 | 典型耗时 |
|---|---|---|---|
| **取数据训练模型**（要 npy） | `h01 fetch` | 要 | 一块 32 切片约 14 秒 |
| **已有 npy，想出图** | `h01 render` | 不要 | **40~85 ms / 片** |
| **要「和网页里看到一样」的图** | `h01 shot` | 要 | 首片 ~13 秒，后续 ~2 秒 |
| 看看 npy 里有什么 | `h01 info` | 不要 | 瞬间 |
| 想知道一次能截多大范围 | `h01 limits` | 要 | 每个尺寸约 12 秒 |

**一句话判断**：
- **要喂模型的数据 → `fetch`**（产出 `em.npy` + `seg.npy`，这才是模型能吃的）
- **要给人看的图 → `render`**（本地重绘，最快）或 `shot`（像素级还原网页）
- `shot` 只在抽检、出汇报图时用，别用来批量生产数据

---

## 三、五个命令

### 1. `h01 fetch` —— 取数据（主力命令）

```bash
h01 fetch --x 247552-247808 --y 193664-193920 --z 2001-2002 --align --out data/block1
```

内部流程：`S1 掩膜质检 → S2 取体素 → S3 本地渲染`

| 参数 | 说明 |
|---|---|
| `--x / --y / --z` | 范围，格式 `起点-终点`（**含首不含尾**） |
| `--align` | 范围吸附到 chunk 网格，强烈建议加（会放大范围，见注意事项） |
| `--scenario` | 业务场景，见下表 |
| `--out` | 输出目录 |

**`--scenario` 选哪个**：

| 场景 | 取什么 | 用途 |
|---|---|---|
| `seg-train`（默认） | EM + SEG + 出图 | 训练分割模型 |
| `ssl` | **仅 EM** | 自监督预训练 |
| `morph` | **仅 SEG** + 出图 | mesh / 形态重建 |
| `figure` | 取体素 + 截图 | 汇报配图 |
| `qa` | 仅截图 | 前端回归 QA |
| `celltype` | 属性表筛选（**不下载体素**） | 按标签找 ROI |

产物：`em.npy`、`seg.npy`、`figs/`（em + overlay 图）、`run.json`（每步耗时与字节）。

> `figs/` 每张图命名为 `<时间戳>_x起-止_y起-止_z起-止_<kind>_z<片号>.png`，例如
> `20260909_131908_x247552-247680_y193664-193920_z1984-2016_em_z0000.png`——时间戳和 xyz 范围都写在文件名里，
> 方便区分批次、回溯来源。xyz 取自同目录 `run.json`；对非 `fetch` 产出的 npy 会显示 `xyzNA`。

### 2. `h01 render` —— 本地重绘（不碰网络）

```bash
h01 render --npy data/block1 --kind em,overlay,boundary --out data/block1/figs_all
```

| 参数 | 说明 |
|---|---|
| `--npy` | 含 `em.npy` / `seg.npy` 的目录 |
| `--kind` | `em`（电镜灰度）/ `overlay`（叠加分割色）/ `boundary`（只画边界），逗号分隔可多选 |
| `--z` | 只渲染某几片，如 `16-19`，默认全部 |
| `--jobs N` | 并行进程数。**约 16 片以上才划算**，少了反而变慢 |

### 3. `h01 shot` —— 浏览器截图

```bash
h01 shot --x 247296-247808 --y 193408-193920 --z 2001-2003 --seg --out figs
```

| 参数 | 说明 |
|---|---|
| `--seg` | 带分割叠加（默认只有电镜） |
| `--segments` | `all` / `boss`（老板 URL 里的那些）/ 指定 ID |
| `--width --height` | 视口尺寸。**输出像素 = 高 − 46**，这是分辨率瓶颈 |

产物：`<时间戳>_x起-止_y起-止_z起-止_z<片号>.png`（裁剪后）、同前缀加 `full_` 的整幅图、`meta.json`（URL、坐标、nm/px、耗时）。

### 4. `h01 info` —— 查看 npy

```bash
h01 info --npy data/block1
```

输出形状、dtype、灰度统计、分割 ID 数量与占比。

> 顺序是：**先 `fetch` 拉一块数据，再拿它的输出目录做 `info` / `render`**。
> 仓库里不带示例数据（`test_data/` 等测试产物已排除），所以没有现成的 `npy` 可看。

### 5. `h01 limits` —— 探测范围上下限

```bash
h01 limits --center 247552,193664,2001 --json limits.json
```

回答「一次截图最多/最少能覆盖多大范围」。实测：边长 **754** 体素单位 = 8.0 nm/px，即 **1 像素 = 1 体素**，这是有意义的下限；再小只是插值放大。

---

## 四、坐标从哪来

从 Neuroglancer 网页 URL 里拿。地址栏类似：

```
...#!{"position":[247489.015625,193590.765625,2001.5],"crossSectionScale":66.56,...}
```

- `position` 的三个数就是 **x, y, z**，单位与工具一致（8 nm 体素），**直接拿来用**
- 想以某个点为中心截一块边长 `S` 的正方形：`x = cx - S/2` 到 `cx + S/2`，y 同理

### 坐标的范围（xyz 上下限）

xyz 上下限**只有一个真相来源**：`pipelines/common.py` 里的 `DATASET` 常量（mip1 / 8nm 体素单位）。`h01kit/cli.py` 和所有校验都从它导入，README 不再单独写死数字，所以改一处、处处同步，不会脱节。

| 轴 | 下限 | 上限（来自 DATASET） | 说明 |
|---|---|---|---|
| x | 0 | 515892 | mip1 = 8 nm/体素 |
| y | 0 | 356400 | mip1 = 8 nm/体素 |
| z | 0 | 5293 | mip1 = 33 nm/体素 |

- 单位：CLI 的 xyz 就是 mip1（8nm/8nm/33nm）体素坐标，**与 Neuroglancer `position` 1:1 对应**，直接拿来用。
- **`fetch` 会严格校验**：越界（含负数、终点 ≤ 起点）直接报错并指出是哪根轴、上限多少；不再把错误甩给底层的 chunk 读取。
- **`shot`（截图）不校验**：Neuroglancer 可以渲染越界区域（只是空白/边缘），所以截图要你自己保证落在范围内。
- **`--align` 会向外吸附**：靠近数据集边缘时，吸附后的范围可能超出上限导致报错——贴边取数时要么留一点余量，要么去掉 `--align`。
- 自查上限：`python -c "from pipelines.common import DATASET; print(DATASET)"`，或直接 `grep -n DATASET h01kit/cli.py`。

---

## 五、常见场景

**A. 我要训练分割模型，要一块数据**
```bash
h01 fetch --scenario seg-train --x 247552-247808 --y 193664-193920 --z 2001-2032 --align --out data/block1
```

**B. 我要自监督预训练，只要电镜图**
```bash
h01 fetch --scenario ssl --x 247552-247808 --y 193664-193920 --z 2001-2100 --align --out data/ssl1
```

**C. 我要给汇报做几张图**
```bash
h01 shot --x 247296-247808 --y 193408-193920 --z 2001-2003 --seg --out figs --width 2800 --height 1600
```

**D. 我之前拉过数据，现在想换种画法**
```bash
h01 render --npy data/block1 --kind boundary --out data/block1/figs_boundary
```

**E. 我想先看看哪些区域值得取**（按标签筛，不下载体素，秒级）

> 这一步 `h01 fetch` 暂未封装（`--tag` / `--top` 没透传），直接用底层脚本：
```bash
python pipelines/run.py --scenario celltype --tag spiny-stellate --top 50 --out data/rois
```

---

## 六、注意事项（重要）

1. **`--align` 会放大范围**。请求 `x[300000,300256]` 实际会读 `x[299904,300288]`。
   **实际范围看产物里的 `run.json` → `s2.read`**。要精确范围就去掉 `--align`（代价：读放大变大、变慢）。

2. **内存**：`seg.npy` 是 uint64 = **8 字节/体素**。384³ 约 37 MB，但 1024³ 就是 **8.6 GB**。
   范围别一次开太大。

3. **带宽是瓶颈**：出口带宽约 4 MB/s，**并发无效**（实测 2/4 并发都卡在 ~6 片/分钟），别盲目加并发。

4. **产物别放 `/tmp`**，重启会被清空。输出到项目目录。

5. **流水线默认不截图**：`fetch` 是「取数 → 本地重绘」，不开浏览器。截图只在抽检/出图时用。

---

## 七、常见问题

**`找不到 node`**
```bash
export H01_NODE_BIN=/path/to/node        # 指向 node 可执行文件，或它所在的 bin 目录
```

**`找不到 playwright`**
```bash
npm install && npx playwright install chromium
# 或：export PLAYWRIGHT_PATH=/path/to/node_modules/playwright
```

**`ModuleNotFoundError: cloud_volume` / `numpy` / `PIL`**
```bash
pip install -r requirements.txt
```

**`h01: command not found`**
先 `source .venv/bin/activate`；想全局可用：
```bash
sudo ln -sf "$(pwd)/.venv/bin/h01" /usr/local/bin/h01
```

---

## 八、项目结构

| 目录 | 是什么 |
|---|---|
| `h01kit/` | 命令行工具本体（`h01` 命令） |
| `pipelines/` | 流水线实现：`s0` 属性表 → `s1` 质检 → `s2` 取数 → `s3` 重绘 → `s4` 截图 |
| `SETUP.md` | 详细安装文档 |
| `FEASIBILITY.md` | 完整可行性报告与四种方法的实测对比 |

> `demo1_screenshot/`、`demo2_fetch/` 是最早的两个验证脚本（方案 1 浏览器截图、方案 2 cloud-volume 直取），**已被 `h01 shot` / `h01 fetch` 取代，已从仓库移除、仅本地保留**，日常使用直接用 `h01` 即可。

---

## 九、延伸阅读

- **`SETUP.md`** —— 完整安装步骤与排错
- **`FEASIBILITY.md`** —— 为什么这么设计、四种方法的实测对比（最快与最慢差约 33000 倍）

> 早期原型 `demo1_screenshot/`、`demo2_fetch/` 的原理 README 未随仓库发布，仅本地可见；其能力均已并入 `h01 shot` / `h01 fetch`。
