"""共享工具：路径常量、数据集边界、线上字节统计、计时、chunk 算术、node 定位。

设计约定：
- DATASET 是 xyz 上下限的唯一真相来源（mip1 / 8nm 体素单位）。README、cli、校验
  全部从这里取，杜绝在多处硬编码同一组魔法数字导致漂移。
- node 定位只走「环境变量 / 常见安装位置 / PATH」，不再写死某台机器的绝对路径，
  这样工具交给同事也能跑。
"""
import math
import os
import re
import shutil
import time
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# mip1（8nm/8nm/33nm）体素单位下的数据集尺寸 —— xyz 上下限的唯一真相来源。
# 下限恒为 0（H01 公开数据集从原点开始）；上限见此。改这里，README 与校验自动跟着变。
DATASET = {"x": 515892, "y": 356400, "z": 5293}


def find_node():
    """定位 node 可执行文件，优先级：H01_NODE_BIN 环境变量 → 常见安装位置 → PATH。

    不再写死某台机器的绝对路径，方便把工具交给同事。
    """
    cand = os.environ.get("H01_NODE_BIN")
    if cand:
        p = os.path.join(cand, "node") if os.path.isdir(cand) else cand
        if os.path.exists(p):
            return p
    for legacy in ("/Users/mac/.workbuddy/binaries/node/versions/22.22.2/bin/node",
                   "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if os.path.exists(legacy):
            return legacy
    return shutil.which("node")

EM_URL = "precomputed://gs://h01-release/data/20210601/4nm_raw"
SEG_URL = "precomputed://gs://h01-release/data/20210601/c3"
MASK_URL = "precomputed://gs://h01-release/data/20210601/masking"
PROPS_URL = ("https://storage.googleapis.com/h01-release/data/20210601"
             "/c3/segment_properties/info")

BYTES_PER_VOXEL = {"uint8": 1, "uint16": 2, "uint32": 4, "uint64": 8}


@contextmanager
def count_wire():
    """拦截 requests 适配器，统计真实的线上传输字节数（含 HTTP 头之外的 body）。"""
    import requests
    state = {"bytes": 0, "requests": 0}
    orig = requests.adapters.HTTPAdapter.send

    def send(self, request, **kw):
        resp = orig(self, request, **kw)
        n = 0
        cr = resp.headers.get("Content-Range")
        if cr:
            m = re.match(r"bytes (\d+)-(\d+)/(\d+)", cr)
            if m:
                n = int(m.group(2)) - int(m.group(1)) + 1
        if not n:
            try:
                n = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                n = 0
        if not n:
            try:
                n = len(resp.content)
            except Exception:
                n = 0
        state["bytes"] += n
        state["requests"] += 1
        return resp

    requests.adapters.HTTPAdapter.send = send
    try:
        yield state
    finally:
        requests.adapters.HTTPAdapter.send = orig


@contextmanager
def timer():
    t = {"s": 0.0}
    t0 = time.time()
    try:
        yield t
    finally:
        t["s"] = time.time() - t0


def align_outward(lo, hi, chunk):
    """把半开区间 [lo, hi) 向外吸附到 chunk 网格边界。"""
    return int(lo // chunk * chunk), int(math.ceil(hi / chunk) * chunk)


def chunk_span(lo, hi, chunk):
    """[lo, hi) 覆盖的 chunk 起止下标（半开）。"""
    return int(lo // chunk), int(math.ceil(hi / chunk))


def human(nbytes):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024 or unit == "TB":
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024.0
    return f"{nbytes:.1f} TB"


def check_range(x0, x1, y0, y1, z0, z1):
    """校验 xyz 是否在数据集范围内（半开区间，单位 mip1 体素）。

    越界会给出清晰报错，而不是把错误甩给 cloud-volume 的 chunk 层。
    坐标以 DATASET 常量为准，因此上限随数据集动态变化、不会和 README 脱节。
    """
    for name, lo, hi, top in (("x", x0, x1, DATASET["x"]),
                              ("y", y0, y1, DATASET["y"]),
                              ("z", z0, z1, DATASET["z"])):
        if lo < 0 or hi > top:
            raise ValueError(
                f"{name} 范围 [{lo}, {hi}) 超出数据集边界 [0, {top})。"
                f"（上限来自 pipelines/common.py 的 DATASET 常量；"
                f"可用 `python -c \"from pipelines.common import DATASET; print(DATASET)\"` 自查）")
        if hi <= lo:
            raise ValueError(f"{name} 范围非法：终点 {hi} 必须 > 起点 {lo}")


def open_cv(url, mip, parallel=8, fill_missing=False, progress=False):
    from cloudvolume import CloudVolume
    return CloudVolume(url, mip=mip, use_https=True, fill_missing=fill_missing,
                       parallel=parallel, progress=progress)


def splitmix64(ids):
    """与 Neuroglancer 无关的本地配色哈希（注意：与 colorSeed 不一致，仅供可视化）。"""
    import numpy as np
    x = np.asarray(ids, dtype=np.uint64)
    x = (x + np.uint64(0x9E3779B97F4A7C15)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    z = x.copy()
    z = ((z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    z = ((z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)) & np.uint64(0xFFFFFFFFFFFFFFFF)
    z = z ^ (z >> np.uint64(31))
    r = (z & np.uint64(255)).astype(np.uint8)
    g = ((z >> np.uint64(8)) & np.uint64(255)).astype(np.uint8)
    b = ((z >> np.uint64(16)) & np.uint64(255)).astype(np.uint8)
    return r, g, b
