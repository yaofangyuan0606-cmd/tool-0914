#!/usr/bin/env python
"""场景路由 —— 不同的建模目的走不同的流水线，不都截图。

    celltype   细胞类型 / 形态分类   S0 属性表（+ 可选骨架）        秒级，零体素
    seg-train  训练分割模型         S0 → S1 → S2(EM+SEG) → S3     小时级
    ssl        自监督预训练         S1 → S2(仅 EM)                小时级
    morph      mesh / 形态重建      S0 → S2(仅 SEG) → S3         小时级
    figure     汇报配图             S2 → S4 截图                  分钟级
    qa         前端回归 QA          S4 截图                       分钟级

用法:
    python run.py --scenario seg-train --x 246000-246700 --y 201300-201900 --z 2050-2051 --out out/seg_train
    python run.py --scenario celltype --tag spiny-stellate --top 50
    python run.py --scenario figure  --x ... --y ... --z 2050-2050 --out out/fig --seg --segments all
"""
import argparse
import json
import os
import sys

import s1_mask
import s2_fetch
import s3_render
import s4_shot

SCENARIOS = {
    "celltype": "S0 属性表 + （可选）骨架；不取体素、不截图",
    "seg-train": "S0 → S1 质控 → S2 取 EM+SEG → S3 本地渲染",
    "ssl": "S1 质控 → S2 仅取 EM",
    "morph": "S0 → S2 仅取 SEG → S3 本地渲染",
    "figure": "S2 取体素 → S4 浏览器截图（少量）",
    "qa": "S4 浏览器截图",
}


def _rng(s):
    return [int(v) for v in s.replace(":", "-").split("-")]


def main():
    ap = argparse.ArgumentParser(
        description="按场景路由流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    ap.add_argument("--x"); ap.add_argument("--y"); ap.add_argument("--z")
    ap.add_argument("--out", default="out/run")
    ap.add_argument("--align", action="store_true", help="S2 吸附到 chunk 网格（强烈建议）")
    ap.add_argument("--parallel", type=int, default=8)
    ap.add_argument("--seg", action="store_true", help="figure/qa：带分割叠加")
    ap.add_argument("--segments", default="boss")
    ap.add_argument("--tag", help="celltype：按标签筛")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--skip-mask", action="store_true", help="跳过 S1 质控")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    log = {"scenario": a.scenario, "desc": SCENARIOS[a.scenario], "out": a.out}
    print(f"=== 场景 {a.scenario}：{SCENARIOS[a.scenario]} ===\n")

    need_xyz = a.scenario != "celltype"
    if need_xyz:
        if not (a.x and a.y and a.z):
            ap.error(f"{a.scenario} 需要 --x --y --z")
        x0, x1 = _rng(a.x); y0, y1 = _rng(a.y); z0, z1 = _rng(a.z)

    if a.scenario == "celltype":
        import s0_index
        print("[S0] 拉属性表并按标签筛选…")
        rows, tags = s0_index.parse(s0_index.load_properties())
        want = {t for t in ([a.tag] if a.tag else [])}
        sel = [{"id": sid, "tags": t, **f} for sid, f, t in rows
               if not want or want.issubset(set(t))]
        sel.sort(key=lambda r: -r.get("NVx", 0))
        json.dump(sel, open(os.path.join(a.out, "rois.json"), "w"), indent=1)
        log["s0"] = {"hits": len(sel), "file": "rois.json"}
        print(f"     命中 {len(sel)} / {len(rows)} 条 → {a.out}/rois.json")
        print("\n提示：这一步不下载任何体素。要形态就接着拉 skeletons，"
              "要体素就换 --scenario seg-train/morph 并给 --x --y --z")
        json.dump(log, open(os.path.join(a.out, "run.json"), "w"), indent=1)
        return

    if a.scenario in ("seg-train", "ssl", "morph") and not a.skip_mask:
        print("[S1] 质控：读 masking 确认区域可用…")
        try:
            m = s1_mask.check(x0, x1, y0, y1, z0, z1, parallel=a.parallel)
            log["s1"] = m
            print(f"     主导值占比 {m['dominant_fraction']*100:.2f}%  "
                  f"取值种类 {m['distinct_values']}  线上 {m['wire_bytes']/1e6:.2f} MB  "
                  f"{m['seconds']} s")
            if m["distinct_values"] > 1 and m["dominant_fraction"] < 0.95:
                print("     ！区域内部掩码不均匀，建议缩小范围或换位置")
        except Exception as e:
            print(f"     S1 失败（继续）：{e}")

    if a.scenario in ("seg-train", "ssl", "morph"):
        em = a.scenario in ("seg-train", "ssl")
        seg = a.scenario in ("seg-train", "morph")
        print(f"[S2] 取体素 EM={em} SEG={seg} align={a.align} …")
        r = s2_fetch.fetch(x0, x1, y0, y1, z0, z1, want_em=em, want_seg=seg,
                           align=a.align, parallel=a.parallel,
                           save=True, out=a.out)
        log["s2"] = r
        for k, v in r["layers"].items():
            print(f"     {k:3} {tuple(v['shape_xyz'])}  {v['seconds']} s")
        print(f"     线上 {r['wire_bytes']/1e6:.1f} MB  读放大 {r['amplification']}×  "
              f"取数 {r['fetch_seconds']} s")
        if not a.align and r["amplification"] > 1.5:
            print(f"     ！未对齐：已传输 {r['transferred_voxels']:,} 体素却只留下 "
                  f"{r['read_voxels']:,}，加 --align 可零成本多拿 {r['amplification']:.1f} 倍")

    if a.scenario in ("seg-train", "morph"):
        print("[S3] 本地渲染（不开浏览器）…")
        kinds = ("em", "overlay")
        rr = s3_render.run(a.out, os.path.join(a.out, "figs"), kinds=kinds, scale=2)
        log["s3"] = rr
        print(f"     {len(rr['files'])} 张图  {rr['ms_per_slice']} ms/片  "
              f"（截图是 15000 ms/片）")

    if a.scenario in ("figure", "qa"):
        print(f"[S4] 浏览器截图（{a.seg and '带分割' or '仅 EM'}）…")
        r = s4_shot.run(x0, x1, y0, y1, z0, z1, a.out, seg=a.seg, segments=a.segments)
        log["s4"] = {k: v for k, v in r.items() if k != "meta"}
        print(f"     {r['slices_ok']}/{r['slices']} 片  {r['seconds']} s  "
              f"({r['ms_per_slice']} ms/片)")

    json.dump(log, open(os.path.join(a.out, "run.json"), "w"), indent=1)
    print(f"\n完成 → {a.out}（run.json 记录了每一步的耗时与字节）")


if __name__ == "__main__":
    sys.exit(main())
