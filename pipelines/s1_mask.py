#!/usr/bin/env python
"""S1 质控层 —— 爬之前先看 masking，确认这块区域成像是否合格。

masking 层是 64 nm 的 uint64 分割，标记哪些区域成像有缺陷（褶皱、裂纹、缺失）。
在花几小时爬体素之前，用这一步几秒钟就能把坏区域剔掉。

用法:
    python s1_mask.py --x 246000-246700 --y 201300-201900 --z 2050-2051
"""
import argparse
import json
import sys

import numpy as np

from common import MASK_URL, align_outward, count_wire, human, open_cv, timer

MASK_CHUNK = 64       # masking mip0 chunk = [64, 64, 64]
DIM_RES_NM = (8, 8, 33)   # 调用方 x/y/z 的单位，即 EM 的 mip1 分辨率


def _divisors(mask_resolution_nm):
    """每轴各自的换算倍数 = mask 分辨率 / 输入单位分辨率。

    masking 是 [64, 64, 66] nm，输入是 [8, 8, 33] nm，所以 x/y 要 ÷8 而 z 只要 ÷2。
    三轴统一用 8 会让 z 读到体积里完全不同的位置（且不会报错）。
    """
    div = []
    for res, unit in zip(mask_resolution_nm, DIM_RES_NM):
        d = int(round(res / unit))
        if d < 1:
            raise SystemExit(f"masking 分辨率 {mask_resolution_nm} 比输入单位 {DIM_RES_NM} 还细，无法换算")
        div.append(d)
    return div


def check(x0, x1, y0, y1, z0, z1, parallel=8):
    """x/y/z 为 8 nm 维度单位（半开区间）。返回掩码统计。"""
    with timer() as t, count_wire() as w:
        cv = open_cv(MASK_URL, mip=0, parallel=parallel)
        dx, dy, dz = _divisors([int(v) for v in cv.resolution])
        mx0, mx1 = x0 // dx, -(-x1 // dx)
        my0, my1 = y0 // dy, -(-y1 // dy)
        mz0, mz1 = z0 // dz, -(-z1 // dz)
        arr = cv[mx0:mx1, my0:my1, mz0:mz1]
    arr = np.asarray(arr).ravel()

    vals, cnts = np.unique(arr, return_counts=True)
    order = np.argsort(-cnts)
    top = [(int(vals[i]), int(cnts[i])) for i in order[:6]]
    return {
        "requested_dim_units": {"x": [x0, x1], "y": [y0, y1], "z": [z0, z1]},
        "mask_resolution_nm": [int(v) for v in cv.resolution],
        "divisors": [dx, dy, dz],
        "mask_units_read": {"x": [mx0, mx1], "y": [my0, my1], "z": [mz0, mz1]},
        "mask_voxels": int(arr.size),
        "wire_bytes": w["bytes"],
        "requests": w["requests"],
        "seconds": round(t["s"], 3),
        "distinct_values": int(vals.size),
        "top_values": top,
        "dominant_fraction": float(cnts[order[0]] / arr.size),
    }


def main():
    ap = argparse.ArgumentParser(description="S1：区域质控")
    ap.add_argument("--x", required=True)
    ap.add_argument("--y", required=True)
    ap.add_argument("--z", required=True)
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--out", help="写出 JSON")
    a = ap.parse_args()

    rng = lambda s: [int(v) for v in s.replace(":", "-").split("-")]
    x0, x1 = rng(a.x); y0, y1 = rng(a.y); z0, z1 = rng(a.z)

    r = check(x0, x1, y0, y1, z0, z1, a.parallel)
    print(f"范围 x{x0}-{x1} y{y0}-{y1} z{z0}-{z1}（8nm 维度单位）")
    print(f"掩码体素 {r['mask_voxels']:,}   线上 {human(r['wire_bytes'])}   "
          f"{r['requests']} 次请求   {r['seconds']} s")
    print(f"取值种类 {r['distinct_values']}   主导值占比 {r['dominant_fraction']*100:.2f}%")
    print("取值分布（值, 体素数）:")
    for v, c in r["top_values"]:
        print(f"    {v:>6}  {c:>12,}  ({c/r['mask_voxels']*100:5.2f}%)")

    if r["distinct_values"] == 1:
        print("\n→ 整块区域掩码取值单一，要么全合格要么全排除，需要对照已知好区域确认语义")
    else:
        print(f"\n→ 区域内部有 {r['distinct_values']} 种取值，"
              f"非主导值占 {(1-r['dominant_fraction'])*100:.2f}%，建议再细看")

    if a.out:
        with open(a.out, "w") as f:
            json.dump(r, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
