#!/usr/bin/env python
"""S3 派生层 —— 从 npy 本地渲染，不开浏览器。

实测 25.6 ms/片（256²→1024² PNG 含落盘），比方案 1 的 15 s/片快 585 倍。
几何与截图一致（区域 IoU 0.970），但配色用本地 splitmix64，与 Neuroglancer
的 colorSeed 不同 —— 颜色只能给人看，不能当特征。

用法:
    python s3_render.py --npy out/roi --out out/roi/figs
    python s3_render.py --npy out/roi --out figs --kind em,overlay,boundary --scale 4
"""
import argparse
import json
import os
import sys
import time

import numpy as np
from PIL import Image

from common import splitmix64


def load(npy_dir):
    em_p, seg_p = os.path.join(npy_dir, "em.npy"), os.path.join(npy_dir, "seg.npy")
    em = np.load(em_p) if os.path.exists(em_p) else None
    seg = np.load(seg_p) if os.path.exists(seg_p) else None
    if em is None and seg is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        raise SystemExit(f"{npy_dir} 里既没有 em.npy 也没有 seg.npy\n"
                         f"提示：相对路径先按当前目录解析、再按项目根目录解析（{root}）；"
                         f"也可直接用绝对路径")
    return em, seg


def slice_xy(em, seg, zi):
    """取出第 zi 片，返回 (灰度 [H,W] float64, 标签 [H,W] uint64 或 None)。"""
    g = em[:, :, zi].T.astype(np.float64) if em is not None else None
    lab = seg[:, :, zi].T if seg is not None else None
    if g is None and lab is not None:
        g = np.zeros(lab.shape, dtype=np.float64)
    return g, lab


def render_em(g, scale):
    img = Image.fromarray(g.clip(0, 255).astype(np.uint8), mode="L").convert("RGB")
    return _up(img, scale)


def render_overlay(g, lab, scale, alpha=0.4):
    base = np.stack([g] * 3, axis=-1).clip(0, 255)
    out = base.copy()
    if lab is not None:
        m = lab != 0
        if m.any():
            r, gg, b = splitmix64(lab[m])
            col = np.stack([r, gg, b], axis=-1).astype(np.float64)
            out[m] = base[m] * (1 - alpha) + col * alpha
    img = Image.fromarray(out.clip(0, 255).astype(np.uint8))
    return _up(img, scale)


def render_boundary(g, lab, scale):
    """只画分割边界，用来核对轮廓位置（抽检时看这个，不看颜色）。"""
    if lab is None:
        return render_em(g, scale)
    m = lab != 0
    edge = np.zeros(m.shape, dtype=bool)
    edge[:-1, :] |= m[:-1, :] ^ m[1:, :]
    edge[1:, :] |= m[1:, :] ^ m[:-1, :]
    edge[:, :-1] |= m[:, :-1] ^ m[:, 1:]
    edge[:, 1:] |= m[:, 1:] ^ m[:, :-1]
    out = np.stack([g] * 3, axis=-1).clip(0, 255)
    out[edge] = [255, 60, 20]
    return _up(Image.fromarray(out.astype(np.uint8)), scale)


def _up(img, scale):
    if scale and scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    return img


def _xyz_tag(npy_dir):
    """从 run.json 取实际读取的 xyz 范围，拼成文件名片段；没有就标 xyzNA。"""
    rp = os.path.join(npy_dir, "run.json")
    if os.path.exists(rp):
        try:
            s2 = json.load(open(rp)).get("s2", {})
            r = s2.get("read") or s2.get("requested")
            if r and r.get("x") and r.get("y") and r.get("z"):
                return (f"x{r['x'][0]}-{r['x'][1]}_y{r['y'][0]}-{r['y'][1]}"
                        f"_z{r['z'][0]}-{r['z'][1]}")
        except Exception:
            pass
    return "xyzNA"


def run(npy_dir, out_dir, kinds=("em", "overlay"), scale=1, alpha=0.4):
    os.makedirs(out_dir, exist_ok=True)
    em, seg = load(npy_dir)
    nz = (em if em is not None else seg).shape[2]
    ts = time.strftime("%Y%m%d_%H%M%S")        # 时间戳前缀，便于区分批次
    xyzt = _xyz_tag(npy_dir)                    # xyz 范围前缀（来自 run.json）
    t0 = time.time()
    written = []
    for zi in range(nz):
        g, lab = slice_xy(em, seg, zi)
        tag = str(zi).zfill(4)
        for kind in kinds:
            fn = os.path.join(out_dir, f"{ts}_{xyzt}_{kind}_z{tag}.png")
            if kind == "em":
                img = render_em(g, scale)
            elif kind == "overlay":
                img = render_overlay(g, lab, scale, alpha)
            elif kind == "boundary":
                img = render_boundary(g, lab, scale)
            else:
                raise SystemExit(f"未知 kind: {kind}")
            img.save(fn)
            written.append(fn)
    dt = time.time() - t0
    return {"slices": nz, "kinds": list(kinds), "files": written,
            "total_seconds": round(dt, 3),
            "ms_per_slice": round(dt / max(nz * len(kinds), 1) * 1000, 2)}


def main():
    ap = argparse.ArgumentParser(description="S3：本地渲染（不开浏览器）")
    ap.add_argument("--npy", required=True, help="含 em.npy / seg.npy 的目录")
    ap.add_argument("--out", required=True)
    ap.add_argument("--kind", default="em,overlay")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--alpha", type=float, default=0.4)
    a = ap.parse_args()

    r = run(a.npy, a.out, tuple(k.strip() for k in a.kind.split(",")), a.scale, a.alpha)
    print(f"{r['slices']} 片 × {len(r['kinds'])} 种 = {len(r['files'])} 张")
    print(f"总耗时 {r['total_seconds']} s   每片 {r['ms_per_slice']} ms   → {a.out}")
    print("（对比：方案 1 浏览器截图 15 000 ms/片）")


if __name__ == "__main__":
    sys.exit(main())
