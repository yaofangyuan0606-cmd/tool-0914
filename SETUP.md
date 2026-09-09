# h01 工具安装指南（给同事）

H01 连接组数据工具：取数 / 本地重绘 / 批量截图 / xyz 范围探测。

---

## 一、前置条件

| 依赖 | 版本 | 是否必需 |
|---|---|---|
| Python | >= 3.9 | 必需 |
| Node.js | >= 18 | **只有 `shot` / `limits` 需要**（它们要开浏览器截图） |
| 网络 | 能访问 Google Cloud Storage | 必需（H01 是公开数据，CC BY 4.0，**不需要任何凭证**） |

> 如果你只用 `info` / `render` / `fetch`，可以完全不装 Node。

---

## 二、安装

```bash
# 1. 进入项目（clone 下来的目录，默认叫 tool-0914）
cd tool-0914

# 2. 建虚拟环境（推荐，避免污染系统 Python）
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. 装 Python 依赖 + 工具本体
pip install -r requirements.txt
pip install -e .

# 4. 装 Node 依赖（只有要用截图功能才需要）
npm install
npx playwright install chromium
```

装完验证：
```bash
h01 --help
```

---

## 三、五个子命令

```bash
# 查看 npy 信息（纯本地）
h01 info --npy data/block1

# 本地重绘：npy → PNG（不碰网络、不开浏览器，几十 ms/片）
h01 render --npy data/block1 --kind em,overlay,boundary --out data/block1/figs

# 取数：xyz 范围 → npy + 本地重绘（走网络，这是完整流水线）
h01 fetch --x 247552-247808 --y 193664-193920 --z 2001-2002 --align --out data/block1

# 批量截图（开浏览器，首片 ~13s，后续 ~2s）
h01 shot --x 247296-247808 --y 193408-193920 --z 2001-2003 --seg --out figs

# 探测单次截图能覆盖的 xyz 范围上下限
h01 limits --center 247552,193664,2001 --json limits.json
```

---

## 四、常见报错

**`找不到 node`**
```bash
export H01_NODE_BIN=/path/to/node          # 指向 node 可执行文件
export H01_NODE_BIN=/path/to/node/bin      # 或它所在的 bin 目录
```
也可以只装 Node 让它出现在 PATH 里。

**`找不到 playwright`**
```bash
npm install
npx playwright install chromium
# 或指向已有安装：
export PLAYWRIGHT_PATH=/path/to/node_modules/playwright
```

**`ModuleNotFoundError: cloud_volume` / `numpy` / `PIL`**
```bash
pip install -r requirements.txt
```

**`h01: command not found`**
命令装在虚拟环境里，先 `source .venv/bin/activate`；
想全局可用就建软链接：
```bash
sudo ln -sf "$(pwd)/.venv/bin/h01" /usr/local/bin/h01
```

---

## 五、使用注意事项

1. **坐标单位**：8 nm 体素单位（mip1），跟 Neuroglancer 视口坐标 1:1，直接从 URL 的 `position [x,y,z]` 取即可。
   数据集边界：x < 515892，y < 356400，z < 5293。

2. **`--align` 会把范围放大**到 chunk 网格（128×128×32）。
   例如请求 `x[300000,300256]` 实际会读 `x[299904,300288]`。
   **实际范围看产物里的 `run.json` → `s2.read`**。要精确范围就去掉 `--align`（代价：读放大变大、变慢）。

3. **内存**：seg 是 uint64 = 8 字节/体素。384³ 的 seg 约 37 MB，
   但 1024³ 就是 **8.6 GB**。范围别一次开太大。

4. **带宽是瓶颈**：出口带宽约 4 MB/s，**并发无效**（实测 2/4 并发都卡在 ~6 片/分钟），别盲目加并发。

5. **产物别放 `/tmp`**，重启会被清空。输出到项目目录（如 `data/...`）。

6. **仓库里不带示例数据**（`test_data/` 等测试产物已排除），所以没有现成的 `npy`。
   顺序是：先 `h01 fetch` 拉一块，再拿它的输出目录做 `info` / `render`。

6. **流水线默认不截图**：`fetch` 是「取数 → 本地重绘」，不开浏览器。
   截图（`shot`）只在抽检 / 出汇报图时才用。

---

## Quick start (EN)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
npm install && npx playwright install chromium     # only for shot / limits
h01 --help
```

Public dataset, no credentials needed. Coordinates are 8 nm voxel units (mip1), 1:1 with Neuroglancer viewer coords.
