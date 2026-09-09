#!/usr/bin/env python
"""全方法对比 —— 同一范围跑一遍所有路线，实测耗时/字节/保真度，输出对比表。

    python compare_all.py                      # 默认测试范围
    python compare_all.py --x 246000-246700 --y 201300-201900 --z 2050 --out out/cmp

对比的路线：
    M0  属性表索引   —— 只拉 segment_properties，零体素
    M1  浏览器截图   —— 方案 1，Playwright + CDP
    M2  直接取数     —— 方案 2，cloud-volume
    M3  本地重绘     —— 从 M2 的 npy 本地出图
    M4  （静态）LLM 操控浏览器 —— 引用 FEASIBILITY §6.2 的估算，不实跑
"""
import argparse
import json
import os
import sys
import time

import numpy as np
from PIL import Image

import s0_index
import s2_fetch
import s3_render
import s4_shot
from common import DEMO1_DIR, ROOT, human

# FEASIBILITY §6.2 的估算值（不实跑，单张 45–120 s，$0.06–0.30）
M4_EST = {"seconds_per_slice": 82.5, "usd_per_slice": 0.18,
          "failure_rate": "5–20%", "reproducible": False}

# 大块持续吞吐的实测值（来自 verify_2026-09-07/bench_*.py，2048×2048×32 对齐块）。
# 小 ROI 的一次性取数含 1–2 s 固定开销（open_info），线性外推会严重低估吞吐，
# 所以外推一律用下面这组"跑热之后"的速率。
SUSTAINED = {
    "M2_decoded_MBps": 64.0,        # EM+SEG 一起，解码后字节
    "M2_wire_MBps": 2.34,           # 线上字节（本机链路 4 MB/s 上限）
    "M2_wire_MBps_cloud": 400.0,    # GCP 同区 VM 的保守估计（100 MB/s+）
    "M1_ms_per_slice": 15000.0,
    "M1_png_MB_per_slice": 1.0,
    "M3_ms_per_slice_256": 25.6,
}


def pearson(a, b):
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    a = a - a.mean(); b = b - b.mean()
    return float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))


def shot_to_grid(path, w, h):
    """截图 PNG → (h, w) 的 float 数组（灰度 / RGB）。"""
    im = Image.open(path).convert("RGB")
    return np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.float64)


def boundary_iou(m1, m2, tol=2):
    def dil(x):
        o = x.copy()
        for k in range(1, tol + 1):
            o[:-k, :] |= x[k:, :]; o[k:, :] |= x[:-k, :]
            o[:, :-k] |= x[:, k:]; o[:, k:] |= x[:, :-k]
        return o
    def bnd(m):
        return (m ^ np.roll(m, 1, 0)) | (m ^ np.roll(m, 1, 1))
    d1, d2 = dil(bnd(m1)), dil(bnd(m2))
    return float((d1 & d2).sum() / max((d1 | d2).sum(), 1))


def main():
    ap = argparse.ArgumentParser(description="全方法对比")
    ap.add_argument("--x", default="246000-246700")
    ap.add_argument("--y", default="201300-201900")
    ap.add_argument("--z", default="2050", help="单切片号（截图范围用）")
    ap.add_argument("--out", default="out/cmp_all")
    ap.add_argument("--skip-shot", action="store_true", help="跳过 16 s 的截图步骤")
    a = ap.parse_args()

    rng = lambda s: [int(v) for v in s.replace(":", "-").split("-")]
    x0, x1 = rng(a.x); y0, y1 = rng(a.y); z = rng(a.z)[0]
    os.makedirs(a.out, exist_ok=True)
    W, H = x1 - x0, y1 - y0        # 体素网格尺寸（mip1 = 8nm）

    R = {"range": {"x": [x0, x1], "y": [y0, y1], "z": z},
         "voxel_grid": [W, H], "methods": {}}

    # ---------- M0 属性表 ----------
    print("[M0] 属性表索引 …")
    t0 = time.time()
    props = s0_index.load_properties()
    rows, tags = s0_index.parse(props)
    dt0 = time.time() - t0
    nbytes0 = len(json.dumps(props))
    R["methods"]["M0_index"] = {
        "seconds": round(dt0, 3), "wire_bytes": nbytes0,
        "segments": len(rows), "tags": len(tags),
        "output": "segment ID + 13 特征 + 32 类标签",
        "reversible": "n/a（本来就是结构化数据）", "reproducible": True,
        "deps": "requests",
    }
    print(f"     {len(rows)} 条 / {len(tags)} 类标签  {human(nbytes0)}  {dt0:.2f} s")

    # ---------- M2 直接取数 ----------
    print("[M2] 直接取数（cloud-volume）…")
    r2 = s2_fetch.fetch(x0, x1, y0, y1, z, z + 1, want_em=True, want_seg=True,
                        align=False, save=True, out=a.out)
    em = np.load(os.path.join(a.out, "em.npy"))[:, :, 0].T.astype(np.float64)
    seg = np.load(os.path.join(a.out, "seg.npy"))[:, :, 0].T
    dec2 = sum(v["decoded_bytes"] for v in r2["layers"].values())
    R["methods"]["M2_fetch"] = {
        "seconds": r2["fetch_seconds"], "wire_bytes": r2["wire_bytes"],
        "requests": r2["requests"], "decoded_bytes": dec2,
        "chunks": r2.get("chunks_touched"), "amplification": r2["amplification"],
        "output": "em.npy(uint8) + seg.npy(uint64) + 统计",
        "reversible": "是（原始灰度 + uint64 ID 全保留）", "reproducible": True,
        "deps": "cloud-volume",
    }
    print(f"     {r2['fetch_seconds']} s  线上 {human(r2['wire_bytes'])}  "
          f"落盘 {human(dec2)}  {r2['chunks_touched']} chunk")

    # ---------- M3 本地重绘 ----------
    print("[M3] 本地重绘 …")
    r3 = s3_render.run(a.out, os.path.join(a.out, "local_figs"),
                       kinds=("em", "overlay"), scale=1)
    out3 = sum(os.path.getsize(f) for f in r3["files"])
    R["methods"]["M3_render"] = {
        "seconds": r3["total_seconds"], "wire_bytes": 0, "output_bytes": out3,
        "files": len(r3["files"]), "ms_per_slice": r3["ms_per_slice"],
        "output": "PNG（EM 灰度 / 分割叠加）",
        "reversible": "否（PNG 是渲染产物，但源 npy 还在）",
        "reproducible": True, "deps": "numpy + pillow",
    }
    print(f"     {r3['total_seconds']} s  {r3['ms_per_slice']} ms/片  {human(out3)}")

    # ---------- M1 截图 ----------
    if a.skip_shot:
        print("[M1] 跳过（--skip-shot）")
        R["methods"]["M1_shot"] = {"skipped": True}
        shot_em = shot_seg = None
        out1 = 0
    else:
        print("[M1] 浏览器截图（EM + 分割各一次，约 32 s）…")
        d_em = os.path.join(a.out, "shot_em"); d_seg = os.path.join(a.out, "shot_seg")
        t0 = time.time()
        s_e = s4_shot.run(x0, x1, y0, y1, z, z, d_em, seg=False)
        s_s = s4_shot.run(x0, x1, y0, y1, z, z, d_seg, seg=True, segments="all")
        dt1 = time.time() - t0
        shot_em = os.path.join(d_em, f"z_{str(z).zfill(4)}.png")
        shot_seg = os.path.join(d_seg, f"z_{str(z).zfill(4)}.png")
        out1 = sum(os.path.getsize(os.path.join(dp, f))
                   for dp in (d_em, d_seg) for f in os.listdir(dp) if f.endswith(".png"))
        R["methods"]["M1_shot"] = {
            "seconds": round(dt1, 2), "slices": s_e["slices"] + s_s["slices"],
            "ms_per_slice": round(dt1 * 1000 / max(s_e["slices"] + s_s["slices"], 1), 1),
            "output_bytes": out1,
            "wire_bytes_est": None,
            "output": "PNG（所见即所得的渲染画面）",
            "reversible": "否（uint64 ID 不可逆，EM 经 shader+JPEG 已失真）",
            "reproducible": "同站点同版本下可复现，但依赖 appspot 版本",
            "deps": "Node 22 + Playwright + Chromium + SwiftShader",
        }
        print(f"     {dt1:.1f} s  {R['methods']['M1_shot']['ms_per_slice']} ms/片  {human(out1)}")

    # ---------- 一致性 ----------
    if shot_em and os.path.exists(shot_em):
        print("\n[一致性] 截图 vs 体素 / 本地重绘 …")
        g_se = shot_to_grid(shot_em, W, H).mean(axis=2)
        g_ss = shot_to_grid(shot_seg, W, H)
        local_mask = seg != 0
        shot_mask = (g_ss.max(axis=2) - g_ss.min(axis=2)) > 12
        inter = (local_mask & shot_mask).sum(); union = (local_mask | shot_mask).sum()

        loc_ov = np.asarray(Image.open(
            os.path.join(a.out, "local_figs", "overlay_z0000.png")).convert("RGB"),
            dtype=np.float64)

        R["agreement"] = {
            "em_pearson_shot_vs_voxel": round(pearson(em, g_se), 4),
            "em_mean_voxel": round(float(em.mean()), 3),
            "em_mean_shot": round(float(g_se.mean()), 3),
            "em_mean_drift": round(float(g_se.mean() - em.mean()), 3),
            "seg_area_iou": round(float(inter / max(union, 1)), 4),
            "seg_boundary_iou_2px": round(boundary_iou(local_mask, shot_mask), 4),
            "coverage_local": round(float(local_mask.mean()), 4),
            "coverage_shot": round(float(shot_mask.mean()), 4),
            "overlay_gray_pearson_local_vs_shot":
                round(pearson(loc_ov.mean(axis=2), g_ss.mean(axis=2)), 4),
            "overlay_rgb_pearson_local_vs_shot": round(pearson(loc_ov, g_ss), 4),
            "em_std_voxel": round(float(em.std()), 3),
            "em_std_shot_plain": round(float(g_se.std()), 3),
            "em_std_shot_overlay": round(float(g_ss.mean(axis=2).std()), 3),
            "em_std_local_overlay": round(float(loc_ov.mean(axis=2).std()), 3),
        }
        for k, v in R["agreement"].items():
            print(f"     {k:42} {v}")

    # ---------- 外推到 1 TB ----------
    print("\n[外推] 各方法拿到 1 TB 产出的耗时 …")
    mb = lambda b: b / 1e6
    m2_rate = dec2 / max(r2["fetch_seconds"], 1e-9)          # 解码字节/秒
    m3_rate = out3 / max(r3["total_seconds"], 1e-9)
    tb = 1e12
    extr = {}
    if m2_rate > 0:
        extr["M2_fetch_1TB_decoded_hours"] = round(tb / m2_rate / 3600, 2)
    if out1 > 0 and R["methods"].get("M1_shot", {}).get("ms_per_slice"):
        # 每张截图的 PNG 字节 → 1 TB 需要多少张 → 总耗时
        per_slice_bytes = out1 / max(R["methods"]["M1_shot"]["slices"], 1)
        n_slices = tb / per_slice_bytes
        extr["M1_shot_1TB_PNG_hours"] = round(
            n_slices * R["methods"]["M1_shot"]["ms_per_slice"] / 1000 / 3600, 1)
    extr["M3_render_1TB_PNG_hours"] = round(tb / m3_rate / 3600, 3)

    # 用大块持续速率重算（小 ROI 的含固定开销，偏悲观，仅供对照）
    s = SUSTAINED
    realistic = {
        "M2_fetch_1TB_decoded_hours_local": round(tb / (s["M2_decoded_MBps"] * 1e6) / 3600, 2),
        "M2_fetch_1TB_wire_hours_local": round(tb / (s["M2_wire_MBps"] * 1e6) / 3600, 2),
        "M2_fetch_1TB_wire_hours_cloud": round(tb / (s["M2_wire_MBps_cloud"] * 1e6) / 3600, 2),
        "M1_shot_1TB_PNG_hours": round(
            (tb / (s["M1_png_MB_per_slice"] * 1e6)) * s["M1_ms_per_slice"] / 1000 / 3600, 1),
        "M3_render_1e6_slices_hours": round(
            1e6 * s["M3_ms_per_slice_256"] / 1000 / 3600, 2),
    }
    extr["_note_small_roi"] = "上面三项按本次小 ROI 线性外推，含 1–2 s 固定开销，偏悲观"
    extr["_sustained_estimate"] = realistic
    R["extrapolation_1TB"] = extr
    for k, v in extr.items():
        if k.startswith("_"):
            continue
        print(f"     {k:36} {v} h")
    print("     —— 按大块持续速率重算（推荐采用）——")
    for k, v in realistic.items():
        print(f"     {k:36} {v} h")

    json.dump(R, open(os.path.join(a.out, "comparison_all.json"), "w"), indent=1)
    print(f"\n完整结果 → {os.path.join(a.out, 'comparison_all.json')}")


if __name__ == "__main__":
    sys.exit(main())
