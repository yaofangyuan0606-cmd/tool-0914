#!/usr/bin/env python
"""S4 截图层 —— 只在"渲染语义本身是交付物"时才用。

薄封装 pipelines/shot_batch.js（复用单 page、靠 hashchange 换 z，约 2 s/片 vs
方案 1 的 15 s/片）。负责拼命令、定位 node、收集 meta.json。

适用（且只适用）三种情况：
  1. 需要 Neuroglancer 的 colorSeed 配色 / selectedAlpha 混合 / invlerp 窗宽窗位
  2. 需要渲染层才有的元素：xy-3d 的 mesh 投影、annotation、skeleton 层
  3. 前端回归 QA，或作为 S2 产物的抽检

用法:
    python s4_shot.py --x 246000-246700 --y 201300-201900 --z 2050-2051 --out shots/
    python s4_shot.py ... --seg --segments all
"""
import argparse
import json
import os
import subprocess
import sys
import time

from common import find_node

HERE = os.path.dirname(os.path.abspath(__file__))


def run(x0, x1, y0, y1, z0, z1, out, seg=False, segments="boss",
        margin=0, width=1400, height=800, wait=12000, wait_next=300, settle=1500,
        url_file=None, timeout=900):
    """调用仓库内的 shot_batch.js 截图，返回汇总字典。

    node 不再写死某台机器的路径，统一用 common.find_node() 定位。
    """
    node = find_node()
    if not node:
        raise SystemExit("找不到 node：请先安装 Node.js（>=18），或设置环境变量\n"
                         "    export H01_NODE_BIN=/path/to/node        # node 可执行文件\n"
                         "    export H01_NODE_BIN=/path/to/node/bin   # 或它所在的 bin 目录")
    script = os.path.join(HERE, "shot_batch.js")
    if not os.path.exists(script):
        raise SystemExit(f"找不到 {script}（shot_batch.js 应随仓库附带）")
    out = os.path.abspath(out)      # node 脚本在 HERE 下运行，必须用绝对路径
    os.makedirs(out, exist_ok=True)

    cmd = [node, script,
           "--x", f"{x0}-{x1}", "--y", f"{y0}-{y1}", "--z", f"{z0}-{z1}",
           "--out", out, "--width", str(width), "--height", str(height),
           "--wait", str(wait), "--wait-next", str(wait_next), "--settle", str(settle),
           "--margin", str(margin)]
    if seg:
        cmd += ["--seg", "--segments", str(segments)]
    if url_file:
        cmd += ["--url", url_file]

    env = dict(os.environ)
    env["PATH"] = os.path.dirname(node) + os.pathsep + env.get("PATH", "")
    t0 = time.time()
    p = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True,
                       text=True, timeout=timeout)
    dt = time.time() - t0

    meta_p = os.path.join(out, "meta.json")
    meta = json.load(open(meta_p)) if os.path.exists(meta_p) else {}
    slices = meta.get("slices", [])
    ok = [s for s in slices if not s.get("error") and s.get("load", {}).get("ok")]
    return {
        "returncode": p.returncode,
        "seconds": round(dt, 2),
        "slices": len(slices),
        "slices_ok": len(ok),
        "ms_per_slice": round(dt * 1000 / max(len(slices), 1), 1),
        "out": out,
        "stdout_tail": p.stdout.strip().splitlines()[-3:],
        "stderr_tail": p.stderr.strip().splitlines()[-3:],
        "meta": meta,
    }


def main():
    ap = argparse.ArgumentParser(description="S4：浏览器截图（复用单 page，约 2 s/片）")
    ap.add_argument("--x", required=True)
    ap.add_argument("--y", required=True)
    ap.add_argument("--z", required=True, help="闭区间 A-B，含两端")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seg", action="store_true")
    ap.add_argument("--segments", default="boss")
    ap.add_argument("--margin", type=int, default=0)
    ap.add_argument("--wait", type=int, default=12000)
    ap.add_argument("--wait-next", type=int, default=300)
    ap.add_argument("--settle", type=int, default=1500)
    ap.add_argument("--url-file")
    a = ap.parse_args()

    rng = lambda s: [int(v) for v in s.replace(":", "-").split("-")]
    x0, x1 = rng(a.x); y0, y1 = rng(a.y); z0, z1 = rng(a.z)

    r = run(x0, x1, y0, y1, z0, z1, a.out, seg=a.seg, segments=a.segments,
            margin=a.margin, wait=a.wait, wait_next=a.wait_next, settle=a.settle,
            url_file=a.url_file)
    print(f"exit={r['returncode']}  {r['slices_ok']}/{r['slices']} 片成功  "
          f"{r['seconds']} s（{r['ms_per_slice']} ms/片）  → {r['out']}")
    for line in r["stdout_tail"]:
        print("  ", line)
    if r["returncode"] != 0:
        for line in r["stderr_tail"]:
            print("  !", line)


if __name__ == "__main__":
    sys.exit(main())
