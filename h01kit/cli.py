#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""h01 命令行工具入口。"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINES = ROOT / "pipelines"
DEMO1 = ROOT / "demo1_screenshot"

# mip1（8nm/8nm/33nm）体素单位下的数据集尺寸
DATASET = {"x": 515892, "y": 356400, "z": 5293}

sys.path.insert(0, str(PIPELINES))


def _find_node():
    """定位 node 可执行文件。

    优先级：H01_NODE_BIN 环境变量 → 常见安装位置 → PATH 里的 node。
    不再写死某台机器的绝对路径，方便把工具交给同事。
    """
    cand = os.environ.get("H01_NODE_BIN")
    if cand:
        p = Path(cand).expanduser()
        if p.is_dir():
            p = p / "node"
        if p.exists():
            return str(p)
    for legacy in ("/Users/mac/.workbuddy/binaries/node/versions/22.22.2/bin/node",
                   "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if os.path.exists(legacy):
            return legacy
    return shutil.which("node")


NODE = _find_node()


def _node_env():
    env = dict(os.environ)
    if NODE:
        env["PATH"] = os.path.dirname(NODE) + os.pathsep + env.get("PATH", "")
    return env


def _need_node():
    if not NODE:
        raise SystemExit(
            "找不到 node：请先安装 Node.js（>=18），或设置环境变量\n"
            "    export H01_NODE_BIN=/path/to/node        # node 可执行文件\n"
            "    export H01_NODE_BIN=/path/to/node/bin   # 或它所在的 bin 目录")


def _run(cmd, cwd, env=None):
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------- info / render（委托给 pipelines 的重绘实现）----------------
def cmd_info(a):
    import render_tool
    return render_tool.cmd_info(a)


def cmd_render(a):
    import render_tool
    return render_tool.cmd_render(a)


# ---------------- shot：批量截图 ----------------
def cmd_shot(a):
    _need_node()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cmd = [NODE, str(PIPELINES / "shot_batch.js"),
           "--x", a.x, "--y", a.y, "--z", a.z,
           "--wait", str(a.wait), "--wait-next", str(a.wait_next),
           "--settle", str(a.settle), "--width", str(a.width),
           "--height", str(a.height), "--out", str(out)]
    if a.seg:
        cmd += ["--seg", "--segments", a.segments]
    if a.url:
        cmd += ["--url", a.url]
    print(f"[shot] x={a.x} y={a.y} z={a.z} seg={a.seg}")
    rc, log = _run(cmd, PIPELINES, env=_node_env())
    print(log.strip())
    return rc


# ---------------- fetch：取数 ----------------
def cmd_fetch(a):
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(PIPELINES / "run.py"),
           "--scenario", a.scenario, "--x", a.x, "--y", a.y, "--z", a.z,
           "--out", str(out)]
    if a.align:
        cmd += ["--align"]
    print(f"[fetch] scenario={a.scenario} x={a.x} y={a.y} z={a.z} align={a.align}")
    rc, log = _run(cmd, PIPELINES)
    print(log.strip())
    return rc


# ---------------- limits：探测单次截图可覆盖的 xyz 范围上下限 ----------------
def cmd_limits(a):
    """以 center 为中心，扫不同的正方形边长，找出截图可用范围的 min / max。"""
    _need_node()
    cx, cy, cz = a.center
    sizes = a.sizes
    tmp = Path(a.tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    # 中心到数据集各边界的距离决定该中心能撑的最大正方形
    half_cap = min(cx, DATASET["x"] - cx, cy, DATASET["y"] - cy)
    print(f"中心 ({cx}, {cy}, {cz})  该中心可容纳的最大半边长 = {half_cap}")
    print(f"视口 {a.width}x{a.height}，可用面板 {a.width}x{a.height - 46}")
    print()
    print(f"{'边长':>8} {'nm/px':>10} {'isReady':>8} {'nonBlack':>9} {'耗时s':>7} {'判定':>8}")

    rows = []
    for s in sizes:
        if s // 2 > half_cap:
            rows.append({"size": s, "skipped": "超出数据集边界"})
            print(f"{s:>8} {'-':>10} {'-':>8} {'-':>9} {'-':>7} {'跳过':>8}")
            continue
        x0, x1 = cx - s // 2, cx - s // 2 + s
        y0, y1 = cy - s // 2, cy - s // 2 + s
        out = tmp / f"size_{s}"
        cmd = [NODE, str(PIPELINES / "shot_batch.js"),
               "--x", f"{x0}-{x1}", "--y", f"{y0}-{y1}", "--z", f"{cz}-{cz}",
               "--wait", str(a.wait), "--settle", str(a.settle),
               "--width", str(a.width), "--height", str(a.height),
               "--out", str(out)]
        if a.seg:
            cmd += ["--seg", "--segments", a.segments]
        t0 = time.time()
        rc, log = _run(cmd, PIPELINES, env=_node_env())
        dt = time.time() - t0
        meta_p = out / "meta.json"
        if rc != 0 or not meta_p.exists():
            rows.append({"size": s, "ok": False, "error": log[-200:], "seconds": round(dt, 1)})
            print(f"{s:>8} {'-':>10} {'-':>8} {'-':>9} {dt:>7.1f} {'失败':>8}")
            continue
        meta = json.loads(meta_p.read_text())
        sl = (meta.get("slices") or [{}])[0]
        ld = sl.get("load", {})
        nmpx = meta.get("nm_per_pixel", {}).get("x", 0)
        ok = bool(ld.get("ok"))
        nb = ld.get("range_nonBlackFraction", 0)
        rows.append({"size": s, "nm_per_px": round(nmpx, 4), "is_ready": ld.get("viewer_isReady"),
                     "nonblack": round(nb, 4), "ok": ok, "seconds": round(dt, 1)})
        print(f"{s:>8} {nmpx:>10.3f} {str(ld.get('viewer_isReady')):>8} {nb:>9.3f} "
              f"{dt:>7.1f} {'通过' if ok else '不达标':>8}")

    good = [r for r in rows if r.get("ok")]
    result = {"center": a.center, "dataset": DATASET,
              "viewport": [a.width, a.height], "rows": rows}
    if good:
        result["min_size"] = min(r["size"] for r in good)
        result["max_size"] = max(r["size"] for r in good)
        result["min_nm_per_px"] = min(r["nm_per_px"] for r in good)
        result["max_nm_per_px"] = max(r["nm_per_px"] for r in good)
        print(f"\n可用范围：边长 {result['min_size']} ~ {result['max_size']} 体素单位")
        print(f"对应分辨率：{result['min_nm_per_px']:.4f} ~ {result['max_nm_per_px']:.1f} nm/px")
        print(f"（体素本身是 8 nm，nm/px < 8 即亚体素放大，不再有真实信息）")
    if a.json:
        Path(a.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果 → {a.json}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="h01", description="H01 连接组数据工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="查看 npy 信息")
    p.add_argument("--npy", required=True)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("render", help="本地重绘 npy → PNG（不碰网络）")
    p.add_argument("--npy", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--kind", default="em,overlay")
    p.add_argument("--z", default=None, help="切片范围如 16-19，默认全部")
    p.add_argument("--scale", type=int, default=1)
    p.add_argument("--alpha", type=float, default=0.4)
    p.add_argument("--jobs", type=int, default=1)
    p.add_argument("--prefix", default="z")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("shot", help="批量截图（复用 page，首片慢后续快）")
    p.add_argument("--x", required=True)
    p.add_argument("--y", required=True)
    p.add_argument("--z", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--url", default=None)
    p.add_argument("--seg", action="store_true")
    p.add_argument("--segments", default="all")
    p.add_argument("--wait", type=int, default=1000)
    p.add_argument("--wait-next", type=int, default=300)
    p.add_argument("--settle", type=int, default=500)
    p.add_argument("--width", type=int, default=1400,
                   help="视口宽；输出像素 ≈ 高度-46，这是分辨率瓶颈")
    p.add_argument("--height", type=int, default=800)
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("fetch", help="取数：xyz 范围 → npy")
    p.add_argument("--x", required=True)
    p.add_argument("--y", required=True)
    p.add_argument("--z", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--scenario", default="seg-train")
    p.add_argument("--align", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("limits", help="探测单次截图可覆盖的 xyz 范围上下限")
    p.add_argument("--center", default="247552,193664,2001",
                   help="中心坐标 x,y,z，默认测试位置")
    p.add_argument("--sizes", default="1,4,16,64,256,1024,4096,16384,65536,262144",
                   help="要扫的正方形边长列表")
    p.add_argument("--width", type=int, default=1400)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--wait", type=int, default=1000)
    p.add_argument("--settle", type=int, default=500)
    p.add_argument("--seg", action="store_true")
    p.add_argument("--segments", default="all")
    p.add_argument("--tmp", default="/tmp/h01_limits")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_limits)

    a = ap.parse_args(argv)
    if a.cmd == "limits":
        a.center = [int(v) for v in a.center.split(",")]
        a.sizes = [int(v) for v in str(a.sizes).split(",")]
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
