#!/usr/bin/env python
"""S2 体素层 —— 按 xyz 范围直接从数据源取体素，绕过界面。

独立实现（不 import demo2_fetch），但用的是同一套 precomputed 读法。
关键开关：--align 把范围吸附到 128×128×32 的 chunk 网格，读放大从 10.5× 降到 1.0，
而下载的 chunk 数完全不变 —— 同样的流量多拿 10 倍数据。

用法:
    python s2_fetch.py --x 246000-246700 --y 201300-201900 --z 2050-2051 --out out/roi
    python s2_fetch.py ... --align            # 吸附到 chunk 网格
    python s2_fetch.py ... --seg-only         # 只要分割（几乎不占带宽）
    python s2_fetch.py ... --em-only          # 只要电镜
"""
import argparse
import json
import math
import os
import sys

import numpy as np

from common import (BYTES_PER_VOXEL, EM_URL, SEG_URL, align_outward, chunk_span,
                    count_wire, human, open_cv, timer)


def _squeeze(a):
    """cloud-volume 返回 (x, y, z, channels)；单通道时去掉最后一维。"""
    a = np.asarray(a)
    if a.ndim == 4 and a.shape[-1] == 1:
        a = a[..., 0]
    return a


def fetch(x0, x1, y0, y1, z0, z1, want_em=True, want_seg=True,
          align=False, mip=1, parallel=8, fill_missing=False, save=True, out=None):
    if not (want_em or want_seg):
        raise SystemExit("--em-only 和 --seg-only 不能同时关掉两边")

    rx0, rx1 = (align_outward(x0, x1, 128) if align else (x0, x1))
    ry0, ry1 = (align_outward(y0, y1, 128) if align else (y0, y1))
    rz0, rz1 = (align_outward(z0, z1, 32) if align else (z0, z1))

    res = {"requested": {"x": [x0, x1], "y": [y0, y1], "z": [z0, z1]},
           "read": {"x": [rx0, rx1], "y": [ry0, ry1], "z": [rz0, rz1]},
           "align": align, "mip": mip, "layers": {}}
    if out:
        os.makedirs(out, exist_ok=True)

    with count_wire() as wire:
        if want_em:
            with timer() as t:
                cv = open_cv(EM_URL, mip=mip, parallel=parallel, fill_missing=fill_missing)
                # 输入是 8nm 单位；mip0 = 4nm(×2)，mip1 = 8nm(×1)
                scale = 2 ** (1 - mip)
                if scale != int(scale):
                    raise SystemExit(f"--mip {mip} 与 8nm 输入分辨率不是整数比，只支持 0 / 1")
                scale = int(scale)
                arr = _squeeze(cv[rx0 * scale:rx1 * scale,
                                  ry0 * scale:ry1 * scale, rz0:rz1])
                em = arr[::scale, ::scale, :]               # 降回 8nm 网格
            res["layers"]["em"] = {
                "dtype": "uint8", "shape_xyz": list(em.shape),
                "decoded_bytes": int(em.nbytes), "seconds": round(t["s"], 3),
            }
            if save and out:
                np.save(os.path.join(out, "em.npy"), em)
            del arr
        else:
            em = None

        if want_seg:
            with timer() as t:
                cv = open_cv(SEG_URL, mip=0, parallel=parallel, fill_missing=fill_missing)
                seg = _squeeze(cv[rx0:rx1, ry0:ry1, rz0:rz1])
            ids, cnts = np.unique(seg, return_counts=True)
            res["layers"]["seg"] = {
                "dtype": "uint64", "shape_xyz": list(seg.shape),
                "decoded_bytes": int(seg.nbytes), "seconds": round(t["s"], 3),
                "unique_ids": int(ids.size),
                "nonzero_fraction": float((seg != 0).mean()),
                "top_ids": [[int(i), int(c)] for i, c in
                            sorted(zip(ids, cnts), key=lambda p: -p[1])[:5]],
            }
            if save and out:
                np.save(os.path.join(out, "seg.npy"), seg)
                json.dump(res["layers"]["seg"]["top_ids"],
                          open(os.path.join(out, "seg_ids.json"), "w"), indent=1)
        res["wire_bytes"] = wire["bytes"]
        res["requests"] = wire["requests"]

    req_vox = (x1 - x0) * (y1 - y0) * (z1 - z0)
    read_vox = (rx1 - rx0) * (ry1 - ry0) * (rz1 - rz0)
    res["requested_voxels"] = int(req_vox)
    res["read_voxels"] = int(read_vox)
    cx = len(range(*chunk_span(rx0, rx1, 128)))
    cy = len(range(*chunk_span(ry0, ry1, 128)))
    cz = len(range(*chunk_span(rz0, rz1, 32)))
    res["chunks_touched"] = cx * cy * cz
    # 服务端的最小传输单位是整个 chunk，所以真正下行的体素数由 chunk 数决定，
    # 与请求框是否对齐无关。读放大 = 传输的体素 / 最终留下来的体素：未对齐时边缘
    # 体素被解码后丢弃，对齐后同样的传输一个不浪费。
    xfer_vox = res["chunks_touched"] * 128 * 128 * 32
    res["transferred_voxels"] = int(xfer_vox)
    res["amplification"] = round(xfer_vox / max(read_vox, 1), 3)
    res["transfer_over_requested"] = round(xfer_vox / max(req_vox, 1), 3)
    res["align_expansion"] = round(read_vox / max(req_vox, 1), 3)
    total = sum(v.get("seconds", 0) for v in res["layers"].values())
    res["fetch_seconds"] = round(total, 3)
    return res


def main():
    ap = argparse.ArgumentParser(description="S2：按范围取体素")
    ap.add_argument("--x", required=True)
    ap.add_argument("--y", required=True)
    ap.add_argument("--z", required=True, help="半开区间 A-B")
    ap.add_argument("--out")
    ap.add_argument("--align", action="store_true")
    ap.add_argument("--em-only", action="store_true")
    ap.add_argument("--seg-only", action="store_true")
    ap.add_argument("--mip", type=int, default=1)
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--fill-missing", action="store_true",
                    help="缺 chunk 静默填 0（默认关闭，缺块直接报错）")
    a = ap.parse_args()

    rng = lambda s: [int(v) for v in s.replace(":", "-").split("-")]
    x0, x1 = rng(a.x); y0, y1 = rng(a.y); z0, z1 = rng(a.z)

    r = fetch(x0, x1, y0, y1, z0, z1,
              want_em=not a.seg_only, want_seg=not a.em_only,
              align=a.align, mip=a.mip, parallel=a.parallel,
              fill_missing=a.fill_missing, save=bool(a.out), out=a.out)

    nx, ny, nz = (r["read"]["x"][1] - r["read"]["x"][0],
                  r["read"]["y"][1] - r["read"]["y"][0],
                  r["read"]["z"][1] - r["read"]["z"][0])
    print(f"请求 {r['requested']}  读取 {r['read']['x']} {r['read']['y']} {r['read']['z']}")
    print(f"尺寸 {nx}×{ny}×{nz}   触及 chunk {r['chunks_touched']}   "
          f"传输体素 {r['transferred_voxels']:,}   读放大 {r['amplification']}×"
          f"（丢弃 {(1 - 1 / max(r['amplification'], 1e-9)) * 100:.1f}%）")
    if not a.align and r["amplification"] > 1.5:
        print(f"    提示：加 --align 可把读放大降到 1.0×，线上字节与 chunk 数完全不变，"
              f"但能多拿 {r['amplification']:.1f} 倍体素")
    for name, v in r["layers"].items():
        print(f"  {name:3} {v['dtype']:7} {tuple(v['shape_xyz'])}  "
              f"{human(v['decoded_bytes']):>10}  {v['seconds']} s")
        if name == "seg":
            print(f"      唯一 ID {v['unique_ids']}  非零占比 {v['nonzero_fraction']*100:.1f}%")
    print(f"线上 {human(r['wire_bytes'])} / {r['requests']} 次请求   取数 {r['fetch_seconds']} s")

    if a.out:
        json.dump(r, open(os.path.join(a.out, "stats.json"), "w"), indent=1)
        print(f"已写出 {a.out}")


if __name__ == "__main__":
    sys.exit(main())
