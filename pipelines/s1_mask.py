#!/usr/bin/env python
"""S1 质控层 —— 爬之前先看 masking，判断这块区域值不值得进训练集。

【masking 到底是什么】
masking 是 64nm 的 uint64 **组织类型分割图**，不是「缺陷掩码」。
官方 segment_properties 给出的语义（见 common.MASK_LABELS）：
    1 neuropil 神经毡 / 3 nucleus 细胞核 / 4 blood vessel 血管
    5 myelin 髓鞘 / 7 fissure 裂隙
其中只有 **fissure(7)** 是成像缺陷（褶皱、裂缝、组织撕裂）；
**neuropil(1)** 才是神经元突起交织区 —— 突触几乎全在这里，是我们唯一想要的。

所以判定一块 ROI 好不好，只看两个数：
    - neuropil 占比够不够高（有没有足够的神经元结构可学）
    - fissure 占比是不是接近 0（有没有成像缺陷）

在花几小时爬体素之前，用这一步几秒钟就能把坏区域剔掉。

用法:
    python s1_mask.py --x 246000-246700 --y 201300-201900 --z 2050-2051
"""
import argparse
import json
import sys

import numpy as np

from common import (MASK_LABELS, MASK_URL, align_outward, count_wire, human,
                    open_cv, timer)

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


def check(x0, x1, y0, y1, z0, z1, parallel=8, min_neuropil=0.5, max_fissure=0.02):
    """x/y/z 为 8 nm 维度单位（半开区间）。返回组织类型构成 + 是否合格。

    min_neuropil : neuropil 占比下限，低于此值说明这块没多少神经元结构可学
    max_fissure  : fissure 占比上限，高于此值说明有成像缺陷
    这两个阈值是保守初值，等用已知好 / 坏区域标定过之后再调。
    """
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

    total = int(arr.size)
    composition = {}
    for v, c in zip(vals.tolist(), cnts.tolist()):
        name = MASK_LABELS.get(int(v), f"unknown_{int(v)}")
        composition[name] = round(int(c) / total, 6)

    neuropil = composition.get("neuropil", 0.0)
    fissure = composition.get("fissure", 0.0)
    reasons = []
    if neuropil < min_neuropil:
        reasons.append(f"neuropil 占比 {neuropil:.2%} 低于下限 {min_neuropil:.0%}")
    if fissure > max_fissure:
        reasons.append(f"fissure 占比 {fissure:.2%} 高于上限 {max_fissure:.0%}")
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
        "composition": composition,
        "verdict": {
            "neuropil_fraction": round(neuropil, 4),
            "fissure_fraction": round(fissure, 4),
            "ok": not reasons,
            "reason": "；".join(reasons) or "neuropil 充足且无明显裂隙",
        },
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
    print()
    print("组织类型构成（官方语义）：")
    for name, frac in sorted(r["composition"].items(), key=lambda kv: -kv[1]):
        print(f"    {name:<14} {frac * 100:6.2f}%")
    v = r["verdict"]
    print()
    print(f"判定：{'合格' if v['ok'] else '不合格'} —— {v['reason']}")
    if not v["ok"]:
        print("  → 建议换一块 ROI；neuropil 是唯一含大量突触的区域，"
              "fissure 是成像缺陷。")

    if a.out:
        with open(a.out, "w") as f:
            json.dump(r, f, indent=1)


if __name__ == "__main__":
    sys.exit(main())
