#!/usr/bin/env python
"""交叉比对 demo1(截图) 与 demo2(cloud-volume 直接取数) 在同一范围的输出，并画对比图。

用法: /Users/mac/.workbuddy/binaries/python/envs/default/bin/python compare_demos.py
产物: demos/comparison.png, demos/comparison_metrics.json
"""
import json
import os
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
D1 = os.path.join(ROOT, "demo1_screenshot", "out")
D2 = os.path.join(ROOT, "demo2_fetch", "out", "plain")
OUT_FIG = os.path.join(ROOT, "comparison.png")
OUT_JSON = os.path.join(ROOT, "comparison_metrics.json")
ZS = [2048, 2049, 2050, 2051, 2052]

em_vol = np.load(os.path.join(D2, "em.npy"))    # [x,y,z]
seg_vol = np.load(os.path.join(D2, "seg.npy"))  # [x,y,z]
W, H, NZ = em_vol.shape  # 700, 600, 5
stats2 = json.load(open(os.path.join(D2, "stats.json")))
meta_em = json.load(open(os.path.join(D1, "em", "meta.json")))
meta_seg = json.load(open(os.path.join(D1, "seg", "meta.json")))
meta_segall = json.load(open(os.path.join(D1, "seg_all", "meta.json")))


def pearson(a, b):
    a = a.astype(np.float64).ravel(); b = b.astype(np.float64).ravel()
    a -= a.mean(); b -= b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else 0.0


def zscore(a):
    a = a.astype(np.float64)
    s = a.std()
    return (a - a.mean()) / (s if s > 0 else 1)


def norm_mad(a, b):
    """归一化后的绝对差均值 (z-score 空间, 0 表示完全一致, 完全无关 ≈ 1.13)."""
    return float(np.abs(zscore(a) - zscore(b)).mean())


def to_gray_resized(path, size):
    return np.asarray(Image.open(path).convert("L").resize(size, Image.LANCZOS))


def edge_map(lbl):
    """标签图的边界 (相邻标签不同)."""
    e = np.zeros(lbl.shape, bool)
    e[:, 1:] |= lbl[:, 1:] != lbl[:, :-1]
    e[1:, :] |= lbl[1:, :] != lbl[:-1, :]
    return e


def dilate(m, r=1):
    out = m.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def grad_mag(img):
    g = img.astype(np.float64)
    gx = np.zeros_like(g); gy = np.zeros_like(g)
    gx[:, 1:] = g[:, 1:] - g[:, :-1]
    gy[1:, :] = g[1:, :] - g[:-1, :]
    return np.hypot(gx, gy)


def shift_search(ref, img, max_shift=12, step=2):
    """粗略平移搜索, 返回 (best_corr, dx, dy)."""
    best = (-2, 0, 0)
    for dy in range(-max_shift, max_shift + 1, step):
        for dx in range(-max_shift, max_shift + 1, step):
            a = ref[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)]
            b = img[max(0, -dy):H + min(0, -dy), max(0, -dx):W + min(0, -dx)]
            c = pearson(a, b)
            if c > best[0]:
                best = (c, dx, dy)
    return best


per_z = []
panels = {}  # z -> dict of PIL images
for i, z in enumerate(ZS):
    em2 = em_vol[:, :, i].T  # -> (H=600, W=700), 行=y 列=x
    seg2 = seg_vol[:, :, i].T
    em1 = to_gray_resized(os.path.join(D1, "em", f"z_{z}.png"), (W, H))

    corr = pearson(em1, em2)
    mad = norm_mad(em1, em2)
    # 诊断: 翻转/平移
    corr_flipud = pearson(em1[::-1, :], em2)
    corr_fliplr = pearson(em1[:, ::-1], em2)
    corr_t = pearson(np.asarray(Image.open(os.path.join(D1, "em", f"z_{z}.png")).convert("L").resize((H, W), Image.LANCZOS)), em2.T)
    best_shift = shift_search(em2, em1)

    # 分割: 截图(all segments) vs overlay
    ov2 = np.asarray(Image.open(os.path.join(D2, f"overlay_z_{z}.png")).convert("RGB"))  # (600,700,3)
    sa1 = np.asarray(Image.open(os.path.join(D1, "seg_all", f"z_{z}.png")).convert("RGB").resize((W, H), Image.LANCZOS))
    seg_gray_corr = pearson(sa1.mean(2), ov2.mean(2))
    # 结构指标: demo2 标签边界 vs 截图颜色梯度 (颜色随机不同, 只比边界位置)
    e2 = dilate(edge_map(seg2), 1)
    # 截图颜色边界: 用色度 (去掉亮度) 的梯度, 避免 EM 纹理干扰
    chroma = sa1.astype(np.float64) - sa1.astype(np.float64).mean(2, keepdims=True)
    g1 = sum(grad_mag(chroma[:, :, c]) for c in range(3))
    thr = np.percentile(g1, 100 * (1 - e2.mean()))  # 取与 demo2 边界同样比例的强边缘
    e1 = dilate(g1 > thr, 1)
    edge_iou = float((e1 & e2).sum() / max(1, (e1 | e2).sum()))
    edge_corr = pearson(e1.astype(np.float64), e2.astype(np.float64))
    # 对照: 随机平移 30px 后的 IoU 作为基线
    e2s = np.roll(e2, (30, 30), (0, 1))
    edge_iou_baseline = float((e1 & e2s).sum() / max(1, (e1 | e2s).sum()))

    sl1 = next(s for s in meta_em["slices"] if s["z"] == z)
    per_z.append({
        "z": z,
        "em_pearson": round(corr, 4),
        "em_norm_mad": round(mad, 4),
        "em_pearson_best_shift": {"corr": round(best_shift[0], 4), "dx_px": best_shift[1], "dy_px": best_shift[2]},
        "em_pearson_if_flipud": round(corr_flipud, 4),
        "em_pearson_if_fliplr": round(corr_fliplr, 4),
        "em_pearson_if_transposed": round(corr_t, 4),
        "seg_overlay_gray_pearson": round(seg_gray_corr, 4),
        "seg_boundary_iou": round(edge_iou, 4),
        "seg_boundary_iou_shifted30_baseline": round(edge_iou_baseline, 4),
        "seg_boundary_pearson": round(edge_corr, 4),
        "demo1_slice_ms": sl1["timings_ms"]["total"],
        "demo1_mean_gray_range": round(sl1["load"]["range_meanGray"], 2),
        "demo2_mean_gray": round(float(em2.mean()), 2),
    })
    panels[z] = {
        "em1": Image.fromarray(em1),
        "em2": Image.fromarray(em2),
        "seg1": Image.fromarray(sa1),
        "ov2": Image.fromarray(ov2),
    }
    print(json.dumps(per_z[-1], ensure_ascii=False))

# ---------------- 画图 ----------------
FONT_CANDIDATES = ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"]
font_path = next(p for p in FONT_CANDIDATES if os.path.exists(p))


def font(sz):
    return ImageFont.truetype(font_path, sz)


TW, TH = 420, 360  # 缩略图尺寸 (700x600 -> 0.6)
PAD = 14
LEFT = 150
TOP = 110
COLS = ["截图 EM (方案1)", "直接取数 EM (方案2)", "截图 + 分割 (方案1, 全部 segment)", "取数 overlay (方案2)"]
FOOT_H = 330
FW = LEFT + 4 * (TW + PAD) + PAD
FH = TOP + len(ZS) * (TH + PAD) + FOOT_H
fig = Image.new("RGB", (FW, FH), (250, 250, 250))
dr = ImageDraw.Draw(fig)

dr.text((PAD, 14), "Neuroglancer 截图 (方案1) vs cloud-volume 直接取数 (方案2) 交叉比对", font=font(28), fill=(20, 20, 20))
dr.text((PAD, 52), "范围 x 246000-246700, y 201300-201900, z 2048-2052 (8nm/8nm/33nm 体素单位; 5.6 µm × 4.8 µm × 5 片). 截图已重采样到方案2 体素尺寸 700×600 后比对.",
        font=font(15), fill=(80, 80, 80))
for c, name in enumerate(COLS):
    x = LEFT + c * (TW + PAD)
    dr.text((x, TOP - 30), name, font=font(18), fill=(30, 30, 30))

for r, z in enumerate(ZS):
    y = TOP + r * (TH + PAD)
    m = per_z[r]
    dr.text((PAD, y + 8), f"z = {z}", font=font(24), fill=(20, 20, 20))
    dr.text((PAD, y + 48), f"EM 相关系数\nr = {m['em_pearson']:.3f}", font=font(15), fill=(40, 40, 40))
    dr.text((PAD, y + 96), f"归一化 MAD\n{m['em_norm_mad']:.3f}", font=font(15), fill=(40, 40, 40))
    dr.text((PAD, y + 144), f"分割边界 IoU\n{m['seg_boundary_iou']:.3f} (基线 {m['seg_boundary_iou_shifted30_baseline']:.3f})", font=font(13), fill=(40, 40, 40))
    dr.text((PAD, y + 192), f"截图耗时 {m['demo1_slice_ms']/1000:.1f} s", font=font(13), fill=(90, 90, 90))
    for c, key in enumerate(["em1", "em2", "seg1", "ov2"]):
        x = LEFT + c * (TW + PAD)
        im = panels[z][key].convert("RGB").resize((TW, TH), Image.LANCZOS)
        fig.paste(im, (x, y))
        dr.rectangle([x, y, x + TW - 1, y + TH - 1], outline=(180, 180, 180))

# ---------------- 底部关键数字 ----------------
fy = TOP + len(ZS) * (TH + PAD) + 10
dr.line([(PAD, fy), (FW - PAD, fy)], fill=(200, 200, 200), width=1)
fy += 12
t1 = meta_em["total_ms"] / 1000
per_slice_1 = np.mean([s["timings_ms"]["total"] for s in meta_em["slices"]]) / 1000
bytes_1_range = sum(os.path.getsize(os.path.join(D1, "em", f"z_{z}.png")) for z in ZS)
bytes_1_full = sum(os.path.getsize(os.path.join(D1, "em", f"full_z_{z}.png")) for z in ZS)
bytes_1_segall = sum(os.path.getsize(os.path.join(D1, "seg_all", f"z_{z}.png")) for z in ZS)
t2 = stats2["timing_s"]
b2 = stats2["bytes"]
nmpp = meta_em["nm_per_pixel"]["x"]
lines_1 = [
    "方案1  Neuroglancer 无头浏览器截图 (Playwright + CDP)",
    f"  每片耗时: 平均 {per_slice_1:.1f} s (goto + 固定等待 12 s + 就绪轮询; 5 片共 {t1:.1f} s), 带分割再跑一遍又是 {meta_segall['total_ms']/1000:.1f} s",
    f"  像素尺寸: 视口 1400×800, 切片面板 1400×754, 范围裁剪 880×754 px; crossSectionScale {meta_em['crossSectionScale']:.4f} 体素/px → {nmpp:.2f} nm/px (非整数倍, 需重采样)",
    f"  字节数: 范围裁剪 PNG 5 片 {bytes_1_range/1e6:.2f} MB, 整幅 PNG 5 片 {bytes_1_full/1e6:.2f} MB, 分割截图 5 片 {bytes_1_segall/1e6:.2f} MB (RGB 渲染结果, 无原始体素值/标签 ID)",
    "  分割: 只能得到渲染后的颜色, 拿不到 segment ID; 老板 URL 里 190 个 segment 在此范围内一个都不出现, 所以 --seg 截图与纯 EM 截图逐字节相同, 本图第三列用的是 segments=all",
]
lines_2 = [
    "方案2  cloud-volume 直接读 precomputed chunk",
    f"  每片耗时: EM+SEG 5 片共 {t2['fetch_em']+t2['fetch_seg']:.1f} s 取数 (≈ {(t2['fetch_em']+t2['fetch_seg'])/5:.2f} s/片), 含 info/保存共 {t2['total']:.1f} s; 对齐读 32 片整 chunk 也只要 {json.load(open(os.path.join(ROOT,'demo2_fetch','out','aligned','stats.json')))['timing_s']['fetch_em']:.1f} s",
    f"  体素尺寸: EM mip1 8×8×33 nm, 700×600×5 体素 (与 dimensions 单位一一对应, 无需重采样); SEG mip0 8×8×33 nm uint64 同尺寸, 174 个非零 ID",
    f"  字节数: EM 解码 {b2['em_decoded']/1e6:.2f} MB, SEG 解码 {b2['seg_decoded']/1e6:.2f} MB; 触及 42 个 128×128×32 chunk, 未对齐读放大 ×{stats2['layers']['em']['cost_unaligned']['amplification']:.1f} (整 chunk 解码 EM {b2['em_chunk_transfer_decoded']/1e6:.1f} MB / SEG {b2['seg_chunk_transfer_decoded']/1e6:.1f} MB); 线上估算 EM≈{b2['em_wire_estimate']/1e6:.1f} MB, SEG≈{b2['seg_wire_estimate']/1e6:.1f} MB",
]
mean_corr = float(np.mean([m["em_pearson"] for m in per_z]))
mean_iou = float(np.mean([m["seg_boundary_iou"] for m in per_z]))
summary = f"结论: 5 片 EM 皮尔逊相关平均 {mean_corr:.3f}, 分割边界 IoU 平均 {mean_iou:.3f} (随机平移基线 ≈ {np.mean([m['seg_boundary_iou_shifted30_baseline'] for m in per_z]):.3f}), 两种方案取到的是同一块组织、同一坐标系; 方案2 快 ~{per_slice_1/((t2['fetch_em']+t2['fetch_seg'])/5):.0f}×, 且拿到的是原始体素与 segment ID."
yy = fy
for ln in lines_1:
    dr.text((PAD, yy), ln, font=font(15 if ln.startswith("  ") else 17), fill=(30, 30, 30)); yy += 24
yy += 8
for ln in lines_2:
    dr.text((PAD, yy), ln, font=font(15 if ln.startswith("  ") else 17), fill=(30, 30, 30)); yy += 24
yy += 10
dr.text((PAD, yy), summary, font=font(16), fill=(160, 30, 30))

fig.save(OUT_FIG, optimize=True)
json.dump({"figure": OUT_FIG, "font": font_path, "per_z": per_z, "mean_em_pearson": mean_corr, "mean_seg_boundary_iou": mean_iou}, open(OUT_JSON, "w"), ensure_ascii=False, indent=1)
print("saved", OUT_FIG, fig.size, os.path.getsize(OUT_FIG))
