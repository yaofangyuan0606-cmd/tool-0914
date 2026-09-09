# demo2_fetch —— 按 xyz 范围直接取数（爬虫方案 / 方案 2）

不经过 Neuroglancer 浏览器，直接用 `cloud-volume` 从 H01 公开桶按坐标范围拉图像和分割，落成 numpy + PNG + 统计 JSON。

## 用法

```bash
cd /Users/mac/PycharmProjects/Neuroglancer/demos/demo2_fetch
PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python   # 已装 cloud-volume 12.14 / numpy / PIL，无需再装东西

# 统一测试范围（8nm 体素坐标；z 是切片号，含两端）
$PY fetch_range.py --x 246000-246700 --y 201300-201900 --z 2048-2052 --out out/plain
$PY fetch_range.py --x 246000-246700 --y 201300-201900 --z 2048-2052 --out out/aligned --align

# 图像层用 mip0 (4nm)：范围仍按 8nm 单位传，脚本自动把 x/y ×2
$PY fetch_range.py --x 246000-246100 --y 201300-201400 --z 2050-2050 --out out/mip0 --mip 0
```

| 参数 | 含义 |
|---|---|
| `--x A-B` `--y A-B` | 半开区间 `[A, B)`，单位 = Neuroglancer 顶栏 dimensions 单位（8 nm） |
| `--z A-B` | 闭区间（切片号），内部转成 `[A, B+1)` |
| `--out DIR` | 输出目录，默认 `./out` |
| `--mip N` | 图像层 mip，默认 1（8 nm，与范围单位一致）。`--mip 0` 取 4 nm 原图，x/y 范围 ×2。分割层永远 c3 mip0（8 nm） |
| `--align` | 把范围向外吸附到 chunk 网格（超集），并在 stats 里报告读放大 |

## 产物

| 文件 | 内容 |
|---|---|
| `em.npy` | uint8，索引顺序 `[x, y, z]`（cloud-volume 约定） |
| `seg.npy` | uint64，`[x, y, z]`，每个体素是神经元 ID，0 = 背景/未标注 |
| `em_z_XXXX.png` | 每个 z 一张灰度 EM（图片行 = y，列 = x） |
| `overlay_z_XXXX.png` | 分割哈希配色 40% 透明叠在 EM 上，`seg==0` 不上色 |
| `seg_ids.json` | 唯一 ID + 体素数，按体素数降序 |
| `stats.json` | 请求/实际范围、形状、字节、每层耗时、EM 均值/标准差、唯一 ID 数、老板 URL 里的 segment 命中、读放大 |

`--mip 0` 时 EM 是 4 nm、分割是 8 nm，overlay 会把分割最近邻放大 2 倍再叠；`em.npy` 和 `seg.npy` 形状不同（x/y 差 2 倍）。

## 原理

### Precomputed 格式
H01 以 Neuroglancer **precomputed** 格式放在公开 GCS 桶 `gs://h01-release`（匿名可读，`use_https=True` 走 `https://storage.googleapis.com/...`）。一个数据集 = 一个 `info` JSON + 一堆按坐标命名的 chunk 文件。`info` 描述 dtype、每个 mip 的分辨率、chunk 大小、体积尺寸、编码。cloud-volume 只读 `info` 就能算出哪些 chunk 覆盖你的范围，然后并发下载、解码、裁剪、拼成 numpy 数组。

| 层 | 路径 | dtype | 编码 | mip0 | mip1 | chunk |
|---|---|---|---|---|---|---|
| 图像 | `4nm_raw` | uint8 | jpeg | 4×4×33 nm | 8×8×33 nm | mip0 **128×128×16**，mip1 起 **128×128×32** |
| 分割 | `c3` | uint64 | compressed_segmentation | 8×8×33 nm | — | 128×128×32 |

> 注意：任务描述里写"图像 chunk 128×128×16"只对 mip0 成立；本 demo 默认用的 mip1 实测是 128×128×32（脚本从 `info` 读，不硬编码）。

### mip（金字塔层级）
每升一级 mip，x/y 分辨率减半（z 不变，33 nm 是物理切片厚度）。mip1 的 8 nm 恰好等于 Neuroglancer 顶栏 dimensions 的单位，也等于分割层 mip0 的分辨率，所以默认 `--mip 1`：三者坐标一一对应，不用换算。

### chunk 与读放大
对象存储没有"半个 chunk"：要一个体素，就得下载并解码整个 128×128×32 的块。所以：

```
实际传输体素 = 覆盖到的 chunk 数 × 128×128×32
读放大        = 实际传输体素 / 请求体素
```

统一测试范围 700×600×5 = 2.1M 体素，覆盖 7×6×1 = 42 个 chunk = 22.0M 体素，**读放大 10.49×**。大头来自 z：chunk 深 32 片而只要 5 片，单这一项就是 6.4×。

`--align` 把范围向外吸附到 chunk 边界（x 245888-246784，y 201216-201984，z 2048-2080 → 896×768×32）。**传输的 chunk 和不对齐时一模一样（都是 42 个）**，只是不再把解码出来的边缘丢掉——同样的网络成本拿到 10.49 倍的数据。代价是内存和磁盘：分割 uint64 从 16.8 MB 变成 176 MB。

### 坐标换算
Neuroglancer URL 里的 `position` 单位是 `dimensions`（这里 8 nm / 8 nm / 33 nm），**不是 nm**：

```
layer_voxel[i] = position[i] × dim_nm[i] / layer_resolution_nm[i]
mip1 图像 / c3 分割:  ×8/8 = ×1        （直接用）
mip0 图像:            ×8/4 = ×2 (x/y)   z 不变
```

老板 URL 的 `position = [246351, 201619, 2050.5]` 正落在统一测试范围中央。

## 和项目 backend 的关系

`backend/main.py` 的 `POST /api/ingest` 收一条 Neuroglancer URL，`core/scraper.py` 从 URL 的 `position` 出发裁一个以它为中心的 ROI，读切片 + 子体 + mesh，落库到 SQLite 并写成本地 Precomputed 供 Neuroglancer 回环打开。`core/chunks.py` 做的就是本 demo 里 `chunk_cost` / `align_outward` 那套 chunk 算术（本 demo 独立重写，不 import backend）。

区别：

| | backend `/api/ingest` | 本 demo `fetch_range.py` |
|---|---|---|
| 输入 | Neuroglancer URL（中心点 + 半径 option） | 显式 xyz 范围 |
| 输出 | SQLite 记录 + tiles + 本地 Precomputed + mesh | npy + png + json，无状态 |
| 缓存 / 并发 / 进度 | 有（storage / cache / parallel） | 无 |
| 用途 | 服务化、可回放 | 验证"按范围取数"这条路本身 |

方案 2 的核心结论：**取数不需要浏览器**，只要 URL 里的 `source` + `position` + `dimensions`，就能算出 chunk 并直接下载；这正是 backend ingest 走的路径，demo 只是把它抽成最小可运行的一份。

## 已知限制

- 没有缓存：每次运行都重新下载；同一范围跑两次流量翻倍。
- 单线程读（cloud-volume 内部会并发下 chunk，但没有跨层/跨块的额外并行）。
- 字节数是**解码后**的体素字节；线上 JPEG / compressed_segmentation 实际传输更小，`wire_estimate` 只是经验系数估算。
- `--align` 后分割 uint64 数组会很大（本例 176 MB），范围再大要注意内存。
- `--mip` 只影响图像层；分割层固定 c3 mip0。
- 老板 URL 里实际是 **182** 个 segment（不是 190）；这 182 个 `#spiny-stellate` 神经元都不经过统一测试范围（命中 0），不是 bug——位置中心的那颗神经元 ID 5799532727 本来就不在选中列表里。
- z 对齐到 32 会把 5 片变成 32 片 PNG，`out/aligned` 有 64 张图。
- 不处理跨 voxel_offset 非零或 chunk 不整除的数据集（H01 offset 全 0，已够用）。
- 范围校验：x/y 空区间（A==B）、反向（A>B）、负数、超出 `info` 里 `volume_size`（z 切片号有效值 0-5292）都会在下载前报错退出，不会静默返回零数据。
- `fill_missing=True`：若某个 chunk 在桶里真的缺失，cloud-volume 会静默填 0。EM 全黑时脚本会在 stderr 打 `[warn]`，`stats.json` 里 `em.all_black` 也会标 true（实测 z=5292 最后一片在测试 xy 处本身就是全 0，chunk 存在，不是缺失）。
- `--align` 时 `stats.json` 的 `physical_size_um` 仍是请求范围；实际读取范围的物理尺寸看 `read_physical_size_um`。
- 老板 URL 的 segment 命中逻辑在大范围下验证过：x 245000-247000 y 200500-202500 z 2050-2051 命中 1 个（ID 5115869424，83703 体素）。
