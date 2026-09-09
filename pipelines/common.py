"""共享工具：路径常量、线上字节统计、计时、chunk 算术。

只新增，不修改 demo1_screenshot / demo2_fetch 里已有的任何代码。
"""
import math
import os
import re
import time
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO1_DIR = os.path.join(ROOT, "demo1_screenshot")
DEMO2_DIR = os.path.join(ROOT, "demo2_fetch")
NODE22_BIN = "/Users/mac/.workbuddy/binaries/node/versions/22.22.2/bin"

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
