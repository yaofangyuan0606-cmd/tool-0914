#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重绘工具 —— 把 S3 本地重绘做成可复用的命令行工具。

不碰网络、不开浏览器：输入是磁盘上的 em.npy / seg.npy，输出是 PNG。
取数（S2）只需做一次，之后想渲染几种、几次，都走这个工具（0.15 s/次）。

子命令:
    info     查看 npy 的形状 / 类型 / 统计，不出图
    render   把 npy 渲染成 PNG

示例:
    ./render_tool.py info --npy test_data/pipelines
    ./render_tool.py render --npy test_data/pipelines --kind em,overlay,boundary
    ./render_tool.py render --npy test_data/pipelines --z 16-19 --scale 2 --jobs 4
    ./render_tool.py render --npy test_data/demo2_fetch --alpha 0.6 --out /tmp/figs
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from s3_render import load as load_npy, slice_xy, render_em, render_overlay, render_boundary  # noqa: E402

KINDS = ("em", "overlay", "boundary")
RENDERERS = {"em": render_em, "overlay": render_overlay, "boundary": render_boundary}

ROOT = Path(__file__).resolve().parent.parent


def _resolve_dir(p):
    """解析 npy 目录：先按 cwd 相对路径找，找不到再退回项目根目录。

    这样在任意目录敲 `h01 info --npy test_data/demo2_fetch` 都能命中，
    不必先 cd 到项目根目录。
    """
    cand = Path(p).expanduser()
    if (cand / "em.npy").exists() or (cand / "seg.npy").exists():
        return cand
    alt = ROOT / p
    if (alt / "em.npy").exists() or (alt / "seg.npy").exists():
        return alt
    return cand


def parse_z(spec, n):
    """把 '16-19' / '5' / '' 解析成切片下标列表（半开区间）。"""
    if not spec:
        return list(range(n))
    spec = spec.strip()
    if "-" in spec:
        a, b = spec.split("-", 1)
        lo, hi = int(a), int(b)
    else:
        lo, hi = int(spec), int(spec) + 1
    lo = max(lo, 0)
    hi = min(hi, n)
    return list(range(lo, hi))


_G = {}


def _init_worker(npy_dir):
    _G["em"], _G["seg"] = load_npy(npy_dir)


def _work(task):
    zi, out_dir, kinds, scale, alpha, prefix = task
    g, lab = slice_xy(_G["em"], _G["seg"], zi)
    written = []
    for kind in kinds:
        fn = os.path.join(out_dir, f"{kind}_{prefix}{zi:04d}.png")
        if kind == "em":
            img = render_em(g, scale)
        elif kind == "overlay":
            img = render_overlay(g, lab, scale, alpha)
        else:
            img = render_boundary(g, lab, scale)
        img.save(fn)
        written.append(fn)
    return written


def cmd_info(a):
    a.npy = _resolve_dir(a.npy)
    em, seg = load_npy(a.npy)
    if em is None and seg is None:
        raise SystemExit(f"{a.npy} 里既没有 em.npy 也没有 seg.npy\n"
                         f"提示：相对路径会先按当前目录解析，再按项目根目录 {ROOT} 解析")
    n = (em if em is not None else seg).shape[2]
    print(f"目录: {a.npy}")
    print(f"切片数: {n}")
    if em is not None:
        print(f"em : shape={em.shape} dtype={em.dtype} "
              f"min={em.min()} max={em.max()} mean={em.mean():.2f} std={em.std():.2f}")
    if seg is not None:
        nz = int((seg != 0).sum())
        ids = np.unique(seg)
        print(f"seg: shape={seg.shape} dtype={seg.dtype} "
              f"unique_ids={len(ids)} 非背景体素占比={nz/seg.size:.3f}")
        top = np.unique(seg, return_counts=True)
        if len(top[0]) > 1:
            order = np.argsort(-top[1])[:5]
            print("     top5:", [(int(top[0][i]), int(top[1][i])) for i in order])
    print(f"磁盘占用: {_sizeof(a.npy)}")
    return 0


def _sizeof(d):
    total = 0
    for f in Path(d).glob("*.npy"):
        total += f.stat().st_size
    return f"{total/1e6:.1f} MB"


def cmd_render(a):
    a.npy = _resolve_dir(a.npy)
    em, seg = load_npy(a.npy)
    if em is None and seg is None:
        raise SystemExit(f"{a.npy} 里既没有 em.npy 也没有 seg.npy\n"
                         f"提示：相对路径会先按当前目录解析，再按项目根目录 {ROOT} 解析")
    n = (em if em is not None else seg).shape[2]
    zs = parse_z(a.z, n)
    kinds = tuple(k.strip() for k in a.kind.split(",") if k.strip())
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        raise SystemExit(f"未知 kind: {bad}（可选 {KINDS}）")
    if "overlay" in kinds and seg is None:
        print("警告：没有 seg.npy，overlay 将退化为纯 EM", file=sys.stderr)

    out_dir = a.out or os.path.join(a.npy, "figs")
    os.makedirs(out_dir, exist_ok=True)

    tasks = [(zi, out_dir, kinds, a.scale, a.alpha, a.prefix) for zi in zs]
    t0 = time.time()
    files = []
    if a.jobs and a.jobs > 1 and len(tasks) > 1:
        import concurrent.futures as cf
        with cf.ProcessPoolExecutor(max_workers=a.jobs,
                                    initializer=_init_worker,
                                    initargs=(a.npy,)) as ex:
            for res in ex.map(_work, tasks):
                files.extend(res)
    else:
        _init_worker(a.npy)
        for t in tasks:
            files.extend(_work(t))
    dt = time.time() - t0

    print(f"{len(zs)} 片 × {len(kinds)} 种 = {len(files)} 张")
    print(f"耗时 {dt:.2f} s   每片 {dt/max(len(files),1)*1000:.0f} ms   jobs={a.jobs or 1}")
    print(f"输出 → {out_dir}")
    print(f"（对比：浏览器截图约 15000 ms/片，且每片都要走网络）")
    return 0


def main():
    ap = argparse.ArgumentParser(
        prog="render_tool",
        description="本地重绘工具：npy → PNG，不碰网络、不开浏览器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("info", help="查看 npy 信息")
    p1.add_argument("--npy", required=True, help="含 em.npy / seg.npy 的目录")
    p1.set_defaults(func=cmd_info)

    p2 = sub.add_parser("render", help="渲染成 PNG")
    p2.add_argument("--npy", required=True, help="含 em.npy / seg.npy 的目录")
    p2.add_argument("--out", default=None, help="输出目录，默认 <npy>/figs")
    p2.add_argument("--kind", default="em,overlay",
                    help=f"渲染种类，逗号分隔，可选 {KINDS}")
    p2.add_argument("--z", default=None, help="切片范围，如 16-19（半开）；默认全部")
    p2.add_argument("--scale", type=int, default=1, help="整数倍放大，最近邻")
    p2.add_argument("--alpha", type=float, default=0.4, help="overlay 不透明度")
    p2.add_argument("--jobs", type=int, default=1, help="并行进程数")
    p2.add_argument("--prefix", default="z", help="文件名前缀，默认 z")
    p2.set_defaults(func=cmd_render)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
