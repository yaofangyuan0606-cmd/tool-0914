#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图瓶颈基准 —— 定位延迟下限（min）。

扫 wait / settle 两个等待参数，找出仍能保证 isReady=true 且 nonBlack 达标的
最小等待，从而把「固定等待」从写死的常数变成可配置的实测值。

用法:
    python bench_shot.py                    # 扫 wait/settle，找延迟下限
    python bench_shot.py --concurrency 2 4  # 测并发吞吐上限
    python bench_shot.py --both
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO1 = ROOT / "demo1_screenshot"
NODE_BIN = "/Users/mac/.workbuddy/binaries/node/versions/22.22.2/bin"
URL_FILE = ROOT / "test_url2" / "new_url.txt"

# 与 test_data 保持一致的测试范围（z=2001 单切片）
X, Y, Z = "247296-247808", "193408-193920", "2001-2001"
BASELINE_NONBLACK = 0.978  # test_data 里 z=2001 的实测值，作为合格线


def _node_env():
    env = dict(os.environ)
    env["PATH"] = NODE_BIN + os.pathsep + env.get("PATH", "")
    return env


def run_one(wait, settle, out, z=Z):
    """跑一次单切片截图，返回耗时与质量指标。"""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    url = URL_FILE.read_text().strip()
    cmd = ["node", "screenshot_range.js",
           "--x", X, "--y", Y, "--z", z,
           "--wait", str(wait), "--settle", str(settle),
           "--seg", "--segments", "all",
           "--url", url, "--out", str(out)]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(DEMO1), env=_node_env(),
                          capture_output=True, text=True)
    wall = time.time() - t0
    if proc.returncode != 0:
        return {"wait": wait, "settle": settle, "ok": False,
                "error": (proc.stderr or proc.stdout or "").strip()[-300:],
                "wall_s": round(wall, 2)}
    meta_p = out / "meta.json"
    if not meta_p.exists():
        return {"wait": wait, "settle": settle, "ok": False,
                "error": "no meta.json", "wall_s": round(wall, 2)}
    meta = json.loads(meta_p.read_text())
    sl = (meta.get("slices") or [{}])[0]
    tm = sl.get("timings_ms", {})
    ld = sl.get("load", {})
    total = tm.get("total", 0)
    return {
        "wait": wait, "settle": settle, "ok": bool(ld.get("ok")),
        "is_ready": bool(ld.get("viewer_isReady")),
        "nonblack": round(ld.get("range_nonBlackFraction", 0), 4),
        "total_ms": total,
        "goto_ms": tm.get("goto", 0),
        "ready_poll_ms": tm.get("ready_poll", 0),
        "wall_s": round(wall, 2),
        # 除去写死的等待之后，真正「有用」的耗时
        "work_ms": max(total - wait - settle, 0),
        "png": str((out / f"z_{z.split('-')[0]}.png")) if (out / f"z_{z.split('-')[0]}.png").exists() else None,
    }


def sweep(tmp):
    """扫 wait/settle，找延迟下限。"""
    combos = [(500, 300), (1000, 300), (2000, 500),
              (4000, 1000), (8000, 1500), (12000, 1500)]
    rows = []
    print("=== wait / settle 扫描（单切片 z=2001，含 EM + c3 分割）===")
    print(f"{'wait':>6} {'settle':>6} {'isReady':>8} {'nonBlack':>9} "
          f"{'total_s':>8} {'work_s':>7} {'判定':>6}")
    for wait, settle in combos:
        r = run_one(wait, settle, tmp / f"w{wait}s{settle}")
        rows.append(r)
        if not r.get("ok"):
            print(f"{wait:>6} {settle:>6} {'ERR':>8} {'-':>9} "
                  f"{r['wall_s']:>8} {'-':>7} {'失败':>6}")
            continue
        passed = (r["is_ready"] and r["nonblack"] >= BASELINE_NONBLACK - 0.02)
        print(f"{wait:>6} {settle:>6} {str(r['is_ready']):>8} {r['nonblack']:>9} "
              f"{r['total_ms']/1000:>8.2f} {r['work_ms']/1000:>7.2f} "
              f"{'通过' if passed else '不达标':>6}")
        r["passed"] = passed
    good = [r for r in rows if r.get("passed")]
    best = min(good, key=lambda r: r["total_ms"]) if good else None
    return rows, best


def concurrency(tmp, levels):
    """并发跑多个截图进程，测吞吐上限。"""
    import concurrent.futures as cf
    print("\n=== 并发吞吐测试 ===")
    results = {}
    # 用最优等待（如未求出则 2000）
    wait, settle = 2000, 500
    for n in levels:
        zs = [2000 + i for i in range(n)]
        t0 = time.time()
        with cf.ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(run_one, wait, settle, tmp / f"c{n}_{i}", f"{z}-{z}")
                    for i, z in enumerate(zs)]
            outs = [f.result() for f in futs]
        dt = time.time() - t0
        okc = sum(1 for o in outs if o.get("ok"))
        tput = okc / dt * 60 if dt > 0 else 0
        results[n] = {"slices": n, "ok": okc, "seconds": round(dt, 2),
                      "slices_per_min": round(tput, 1)}
        print(f"  并发 {n}: {okc}/{n} 成功, {dt:.1f}s, 吞吐 {tput:.1f} 片/分钟")
    return results


def main():
    ap = argparse.ArgumentParser(description="截图瓶颈基准（延迟下限 / 吞吐上限）")
    ap.add_argument("--concurrency", type=int, nargs="*", default=None,
                    help="并发数列表，如 2 4")
    ap.add_argument("--both", action="store_true")
    ap.add_argument("--out", default=None, help="结果 JSON 落盘路径")
    a = ap.parse_args()

    tmp = Path(os.environ.get("BENCH_TMP", "/tmp/bench_shot"))
    tmp.mkdir(parents=True, exist_ok=True)

    rows, best = sweep(tmp)
    conc = concurrency(tmp, a.concurrency or [2, 4]) if (a.concurrency or a.both) else {}

    summary = {
        "baseline_nonblack": BASELINE_NONBLACK,
        "sweep": rows,
        "best": best,
        "concurrency": conc,
    }
    if best:
        print(f"\n延迟下限：wait={best['wait']}ms settle={best['settle']}ms "
              f"→ {best['total_ms']/1000:.2f} s/片")
        baseline = next((r for r in rows if r.get("wait") == 12000), None)
        if baseline and baseline.get("ok"):
            sp = baseline["total_ms"] / max(best["total_ms"], 1)
            print(f"对比默认 12000ms：{baseline['total_ms']/1000:.2f} s → "
                  f"{best['total_ms']/1000:.2f} s，提速 {sp:.1f}×")
            summary["speedup_vs_default"] = round(sp, 2)
    if conc:
        print(f"\n吞吐上限：{max(conc.values(), key=lambda v: v['slices_per_min'])['slices_per_min']} 片/分钟")

    out_p = Path(a.out) if a.out else ROOT / "test_data" / "bench_shot.json"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果 → {out_p}")


if __name__ == "__main__":
    sys.exit(main())
