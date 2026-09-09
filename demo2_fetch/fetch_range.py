#!/usr/bin/env python
"""demo2: 按 xyz 范围直接从 H01 Precomputed 桶取数（爬虫方案）。

用法:
  python fetch_range.py --x 246000-246700 --y 201300-201900 --z 2048-2052 \
      [--out DIR] [--mip 1] [--align]

范围单位 = Neuroglancer 顶栏 dimensions 单位 (8nm/8nm/33nm 体素坐标)。
x/y 是半开区间 [A, B)，z 是闭区间 A..B（内部转成 [A, B+1)）。

产物 (写到 --out):
  em.npy               uint8  [x, y, z]
  seg.npy              uint64 [x, y, z]
  em_z_XXXX.png        每个 z 一张灰度 EM
  overlay_z_XXXX.png   分割哈希配色 40% 透明叠在 EM 上 (seg==0 不上色)
  seg_ids.json         唯一分割 ID + 体素数，按体素数降序
  stats.json           范围 / 形状 / 字节 / 耗时 / 均值 / 老板 URL 中的 segment 命中

不 import 项目 backend，保持 demo 独立。
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import numpy as np
from PIL import Image

EM_URL = "precomputed://gs://h01-release/data/20210601/4nm_raw"
SEG_URL = "precomputed://gs://h01-release/data/20210601/c3"

# 顶栏坐标单位 (dimensions): 8nm / 8nm / 33nm
DIM_NM = (8.0, 8.0, 33.0)

# 老板给的完整 URL（任务描述说 190 个 segment，实际解析出 182 个），脚本启动时解析出 segments 列表
BOSS_URL = "https://h01-dot-neuroglancer-demo.appspot.com/#!%7B%22dimensions%22:%7B%22x%22:%5B8e-9%2C%22m%22%5D%2C%22y%22:%5B8e-9%2C%22m%22%5D%2C%22z%22:%5B3.3e-8%2C%22m%22%5D%7D%2C%22position%22:%5B246351.0625%2C201619.078125%2C2050.5%5D%2C%22crossSectionScale%22:1.5075176832744441%2C%22projectionScale%22:86754.42555467124%2C%22projectionDepth%22:-100%2C%22layers%22:%5B%7B%22type%22:%22image%22%2C%22source%22:%22precomputed://gs://h01-release/data/20210601/4nm_raw%22%2C%22tab%22:%22source%22%2C%22name%22:%224nm%20EM%22%7D%2C%7B%22type%22:%22segmentation%22%2C%22source%22:%7B%22url%22:%22precomputed://gs://h01-release/data/20210601/c3%22%2C%22subsources%22:%7B%22default%22:true%2C%22bounds%22:true%2C%22properties%22:true%2C%22mesh%22:true%7D%2C%22enableDefaultSubsources%22:false%7D%2C%22panels%22:%5B%7B%22flex%22:1.55%2C%22size%22:306%2C%22tab%22:%22segments%22%7D%5D%2C%22segments%22:%5B%2212857642016%22%2C%2213046505828%22%2C%2213951650786%22%2C%2214794814799%22%2C%2215539757452%22%2C%222120664699%22%2C%2221348269172%22%2C%2221635455184%22%2C%2222948012913%22%2C%2227216368299%22%2C%2228104985461%22%2C%2228862498148%22%2C%2229210178754%22%2C%222950980117%22%2C%2229672577804%22%2C%2229730222317%22%2C%2229977680734%22%2C%2230211441416%22%2C%2230224642109%22%2C%2230501462297%22%2C%2230501870437%22%2C%2230516077733%22%2C%2230573824944%22%2C%2230588382028%22%2C%2230605127877%22%2C%2230865392105%22%2C%2230941549387%22%2C%2231068084020%22%2C%223106947392%22%2C%2231082523968%22%2C%2231083559920%22%2C%2231140649483%22%2C%2231274759074%22%2C%2231349195650%22%2C%2231576899712%22%2C%2231591499830%22%2C%2232385180272%22%2C%223296832824%22%2C%2233049905842%22%2C%223308836336%22%2C%2233269227364%22%2C%2233544164420%22%2C%2233663789818%22%2C%2233779853124%22%2C%2233779985123%22%2C%223512752660%22%2C%223513951463%22%2C%223588284587%22%2C%223631312996%22%2C%223645170418%22%2C%2237626413608%22%2C%223773716567%22%2C%223788376389%22%2C%223788551051%22%2C%223790844645%22%2C%223833230503%22%2C%2238611762212%22%2C%2238660748322%22%2C%2238689468673%22%2C%223907682045%22%2C%223937466277%22%2C%223949920978%22%2C%2239540618731%22%2C%223994337380%22%2C%224006748353%22%2C%224068088366%22%2C%224079680017%22%2C%224094078022%22%2C%224094501608%22%2C%224127236696%22%2C%224140858954%22%2C%224152247000%22%2C%224152861452%22%2C%224153108523%22%2C%224154686065%22%2C%224185377684%22%2C%2241933255298%22%2C%2242002712851%22%2C%2242108919642%22%2C%224254761989%22%2C%224255432378%22%2C%224312742466%22%2C%224326699742%22%2C%224326818082%22%2C%224327955860%22%2C%224370590371%22%2C%224372430258%22%2C%224372868243%22%2C%224474214654%22%2C%224475484255%22%2C%224475995296%22%2C%224489268853%22%2C%224500948443%22%2C%224501838736%22%2C%224503782000%22%2C%224504145716%22%2C%224516965446%22%2C%224519199538%22%2C%224519243274%22%2C%224530384028%22%2C%224573881259%22%2C%224575925341%22%2C%224617509729%22%2C%224619654531%22%2C%224619873240%22%2C%224723890949%22%2C%224735542348%22%2C%224766773877%22%2C%224780076410%22%2C%224809949027%22%2C%224823076755%22%2C%224839473227%22%2C%224852629019%22%2C%224896065848%22%2C%224924801658%22%2C%224926903194%22%2C%224938804262%22%2C%224955754739%22%2C%224970736617%22%2C%224997163340%22%2C%225028934840%22%2C%225042588326%22%2C%225055801374%22%2C%225103080167%22%2C%225114905708%22%2C%225115534039%22%2C%225115869424%22%2C%225144808371%22%2C%225159205830%22%2C%225174375994%22%2C%225231832117%22%2C%225247730833%22%2C%225248446458%22%2C%225291856456%22%2C%225317129594%22%2C%225348623756%22%2C%225364977088%22%2C%225376877616%22%2C%225377446229%22%2C%225377927964%22%2C%225406487849%22%2C%225480120932%22%2C%225492969654%22%2C%225565128350%22%2C%225595249249%22%2C%225623693160%22%2C%225624832585%22%2C%225682402703%22%2C%225845146472%22%2C%225855979281%22%2C%225887240031%22%2C%225900396223%22%2C%225902484088%22%2C%225942899024%22%2C%225975984947%22%2C%225989857178%22%2C%226163710175%22%2C%226177990420%22%2C%226209498692%22%2C%226220712283%22%2C%226222230785%22%2C%226263276083%22%2C%226278138984%22%2C%226309603153%22%2C%226322687028%22%2C%22635743109%22%2C%226384842151%22%2C%226411635598%22%2C%226412233719%22%2C%226484377108%22%2C%226613785802%22%2C%226614928166%22%2C%226631059913%22%2C%226643164727%22%2C%226658406895%22%2C%226702705861%22%2C%226745384646%22%2C%226777258586%22%2C%226804898281%22%2C%226863842503%22%2C%227037463002%22%2C%227067117794%22%5D%2C%22segmentQuery%22:%22#neuron%20#spiny-stellate%22%2C%22colorSeed%22:4270253886%2C%22name%22:%22c3%20segmentation%22%7D%5D%2C%22showSlices%22:false%2C%22layout%22:%22xy-3d%22%7D"


# ---------------------------------------------------------------- 参数解析

def parse_range(s: str, name: str):
    try:
        a, b = s.split("-")
        a, b = int(a), int(b)
    except ValueError:
        raise SystemExit(f"--{name} 需要形如 A-B 的整数范围, 收到 {s!r}")
    if b < a:
        raise SystemExit(f"--{name}: 上界 {b} 小于下界 {a}")
    if a < 0:
        raise SystemExit(f"--{name}: 下界 {a} 不能为负")
    if name != "z" and b == a:
        raise SystemExit(f"--{name}: 半开区间 [{a}, {b}) 为空, 上界必须大于下界")
    return a, b


def boss_segments(url: str):
    """从老板 URL 的 #! 片段里解出 segmentation 层的 segments 列表。"""
    state = json.loads(unquote(url.split("#!", 1)[1]))
    for layer in state.get("layers", []):
        if layer.get("type") == "segmentation":
            return [int(s) for s in layer.get("segments", [])], state
    return [], state


# ---------------------------------------------------------------- chunk 网格算术 (独立实现，思路同 backend/core/chunks.py)

def _ceil_div(a, b):
    return -(-a // b)


def grid_range(lo, hi, chunk, offset=0):
    """半开体素区间 -> 半开 chunk 索引区间。网格锚在 voxel_offset 上。"""
    if hi <= lo:
        return 0, 0
    return (lo - offset) // chunk, _ceil_div(hi - offset, chunk)


def chunk_cost(bounds, chunk, itemsize, offset=(0, 0, 0)):
    """bounds=(lo[3], hi[3]) 半开。返回请求体素/被迫传输的体素/读放大。"""
    lo, hi = bounds
    counts = [max(0, c1 - c0) for c0, c1 in
              (grid_range(lo[i], hi[i], chunk[i], offset[i]) for i in range(3))]
    requested = int(np.prod([max(0, hi[i] - lo[i]) for i in range(3)]))
    n_chunks = int(np.prod(counts))
    fetched = n_chunks * int(np.prod(chunk))
    aligned = all((lo[i] - offset[i]) % chunk[i] == 0 and (hi[i] - offset[i]) % chunk[i] == 0
                  for i in range(3))
    return {
        "chunk_size": [int(c) for c in chunk],
        "requested_voxels": requested,
        "requested_bytes": requested * itemsize,
        "chunks_per_axis": counts,
        "chunks_touched": n_chunks,
        "fetched_voxels": fetched,
        "fetched_bytes_decoded": fetched * itemsize,
        "amplification": (fetched / requested) if requested else 0.0,
        "aligned": bool(aligned),
    }


def align_outward(bounds, chunk, offset=(0, 0, 0), limit=None):
    """向外吸附到 chunk 网格 (超集)，limit=volume shape 用于夹到数据集范围内。"""
    lo, hi = bounds
    out_lo, out_hi = [], []
    for i in range(3):
        c0, c1 = grid_range(lo[i], hi[i], chunk[i], offset[i])
        a, b = offset[i] + c0 * chunk[i], offset[i] + c1 * chunk[i]
        if limit is not None:
            a = max(a, offset[i])
            b = min(b, offset[i] + int(limit[i]))
        out_lo.append(int(a))
        out_hi.append(int(b))
    return out_lo, out_hi


# ---------------------------------------------------------------- 配色

def hash_colors(ids: np.ndarray) -> np.ndarray:
    """uint64 ID -> 确定性 RGB (splitmix64 风格混合)，同一 ID 每次运行同色。"""
    x = ids.astype(np.uint64).copy()
    with np.errstate(over="ignore"):
        x = x + np.uint64(0x9E3779B97F4A7C15)
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
    r = (x & np.uint64(0xFF)).astype(np.uint8)
    g = ((x >> np.uint64(8)) & np.uint64(0xFF)).astype(np.uint8)
    b = ((x >> np.uint64(16)) & np.uint64(0xFF)).astype(np.uint8)
    rgb = np.stack([r, g, b], axis=-1).astype(np.int16)
    # 避免太暗的颜色叠在 EM 上看不见：把每个通道压到 64..255
    rgb = 64 + (rgb * 191) // 255
    return rgb.astype(np.uint8)


def overlay_slice(em2d: np.ndarray, seg2d: np.ndarray, alpha=0.4) -> np.ndarray:
    """em2d [x,y] uint8, seg2d [x,y] uint64 -> RGB [y,x] (图像行=y)。"""
    ids, inv = np.unique(seg2d, return_inverse=True)
    palette = hash_colors(ids)                       # [n_ids, 3]
    color = palette[inv.reshape(seg2d.shape)]         # [x,y,3]
    base = np.repeat(em2d[..., None], 3, axis=-1).astype(np.float32)
    mask = (seg2d != 0)[..., None]
    mixed = np.where(mask, base * (1 - alpha) + color.astype(np.float32) * alpha, base)
    return np.clip(mixed, 0, 255).astype(np.uint8).transpose(1, 0, 2)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--x", required=True, help="x 范围 A-B (8nm 体素单位, 半开)")
    ap.add_argument("--y", required=True, help="y 范围 A-B (8nm 体素单位, 半开)")
    ap.add_argument("--z", required=True, help="z 范围 A-B (切片号, 含两端)")
    ap.add_argument("--out", default="out", help="输出目录 (默认 ./out)")
    ap.add_argument("--mip", type=int, default=1, help="图像层 mip (1=8nm 与范围单位一致; 0=4nm, 范围 x/y 自动 ×2)")
    ap.add_argument("--align", action="store_true", help="把范围向外吸附到 chunk 网格")
    args = ap.parse_args()

    from cloudvolume import CloudVolume  # 延迟 import，--help 更快

    t_total = time.perf_counter()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    x0, x1 = parse_range(args.x, "x")
    y0, y1 = parse_range(args.y, "y")
    z0, z1 = parse_range(args.z, "z")
    z1 += 1  # z 闭区间 -> 半开
    req_lo, req_hi = [x0, y0, z0], [x1, y1, z1]   # 8nm 单位, 半开
    print(f"[req] 8nm 体素范围 x{req_lo[0]}-{req_hi[0]} y{req_lo[1]}-{req_hi[1]} z{req_lo[2]}-{req_hi[2]} (半开)")

    segs_boss, boss_state = boss_segments(BOSS_URL)
    stats = {
        "requested_range_dim_units": {"x": [x0, x1], "y": [y0, y1], "z": [z0, z1],
                                      "note": "半开区间; 单位 = dimensions 8nm/8nm/33nm 体素; z 闭区间输入已 +1"},
        "physical_size_um": {"x": (x1 - x0) * DIM_NM[0] / 1000, "y": (y1 - y0) * DIM_NM[1] / 1000,
                             "z": (z1 - z0) * DIM_NM[2] / 1000},
        "image_mip": args.mip, "align": bool(args.align),
        "layers": {}, "timing_s": {},
    }

    # ------------------------------------------------------------ 打开两层 (只读 info)
    t = time.perf_counter()
    cv_em = CloudVolume(EM_URL, mip=args.mip, use_https=True, progress=False, fill_missing=True)
    cv_seg = CloudVolume(SEG_URL, mip=0, use_https=True, progress=False, fill_missing=True)
    stats["timing_s"]["open_info"] = round(time.perf_counter() - t, 3)

    # 图像层 mip 与 8nm 单位的比例: mip1 -> 1, mip0 -> 2 (x/y), z 不变
    em_res = [int(v) for v in cv_em.resolution]
    seg_res = [int(v) for v in cv_seg.resolution]
    em_scale = [DIM_NM[i] / em_res[i] for i in range(3)]     # dim 单位 -> em 体素
    seg_scale = [DIM_NM[i] / seg_res[i] for i in range(3)]   # dim 单位 -> seg 体素
    if any(abs(s - round(s)) > 1e-9 for s in em_scale + seg_scale):
        raise SystemExit(f"分辨率不是整数倍: em_scale={em_scale} seg_scale={seg_scale}")
    em_scale = [int(round(s)) for s in em_scale]
    seg_scale = [int(round(s)) for s in seg_scale]

    def to_layer(lo, hi, scale):
        return [lo[i] * scale[i] for i in range(3)], [hi[i] * scale[i] for i in range(3)]

    em_lo, em_hi = to_layer(req_lo, req_hi, em_scale)
    seg_lo, seg_hi = to_layer(req_lo, req_hi, seg_scale)

    def layer_meta(cv, lo, hi, name):
        chunk = [int(c) for c in cv.chunk_size]
        off = [int(o) for o in cv.voxel_offset]
        shape = [int(s) for s in cv.volume_size]
        # 先按 info 里的 volume_size 校验范围，给出比 cloud-volume 回溯更清楚的错误
        for i, ax in enumerate("xyz"):
            if lo[i] < off[i] or hi[i] > off[i] + shape[i]:
                raise SystemExit(
                    f"[{name}] {ax} 范围 {lo[i]}-{hi[i]} (层体素, 半开) 超出数据集范围 "
                    f"{off[i]}-{off[i] + shape[i]} (z 切片号有效值 0-{shape[2] - 1})")
        itemsize = np.dtype(cv.dtype).itemsize
        m = {
            "url": cv.cloudpath, "mip": int(cv.mip), "resolution_nm": [int(v) for v in cv.resolution],
            "dtype": str(cv.dtype), "encoding": cv.scale.get("encoding"),
            "chunk_size": chunk, "voxel_offset": off, "volume_size": shape,
            "requested_bounds_layer_voxels": {"lo": lo, "hi": hi},
            "cost_unaligned": chunk_cost((lo, hi), chunk, itemsize, off),
        }
        if args.align:
            a_lo, a_hi = align_outward((lo, hi), chunk, off, shape)
            m["aligned_bounds_layer_voxels"] = {"lo": a_lo, "hi": a_hi}
            m["cost_aligned"] = chunk_cost((a_lo, a_hi), chunk, itemsize, off)
            req_v = m["cost_unaligned"]["requested_voxels"]
            m["read_amplification"] = {
                "note": "aligned_read_voxels / requested_voxels; 对齐后传输的 chunk 与不对齐时完全一样，只是不再丢弃边缘体素",
                "aligned_read_voxels_over_requested": round(m["cost_aligned"]["requested_voxels"] / req_v, 4) if req_v else 0,
                "chunk_transfer_over_requested_unaligned": round(m["cost_unaligned"]["amplification"], 4),
                "chunk_transfer_over_read_aligned": round(m["cost_aligned"]["amplification"], 4),
            }
            lo, hi = a_lo, a_hi
        else:
            m["read_amplification"] = {
                "note": "chunk_transfer / requested_voxels (未对齐: 传输整 chunk, 边缘部分被丢弃)",
                "chunk_transfer_over_requested_unaligned": round(m["cost_unaligned"]["amplification"], 4),
            }
        m["read_bounds_layer_voxels"] = {"lo": lo, "hi": hi}
        print(f"[{name}] mip{cv.mip} res={m['resolution_nm']} chunk={chunk} 读 x{lo[0]}-{hi[0]} y{lo[1]}-{hi[1]} z{lo[2]}-{hi[2]} "
              f"chunks={m['cost_unaligned']['chunks_touched'] if not args.align else m['cost_aligned']['chunks_touched']} "
              f"amp={m['read_amplification']}")
        return m, lo, hi

    em_meta, em_lo, em_hi = layer_meta(cv_em, em_lo, em_hi, "EM")
    seg_meta, seg_lo, seg_hi = layer_meta(cv_seg, seg_lo, seg_hi, "SEG")

    # ------------------------------------------------------------ 真正取数
    t = time.perf_counter()
    em = np.asarray(cv_em[em_lo[0]:em_hi[0], em_lo[1]:em_hi[1], em_lo[2]:em_hi[2]])
    em = np.squeeze(em, axis=3) if em.ndim == 4 else em     # cloud-volume 返回 [x,y,z,channel]
    em = np.ascontiguousarray(em.astype(np.uint8))
    stats["timing_s"]["fetch_em"] = round(time.perf_counter() - t, 3)
    print(f"[EM] shape={em.shape} {em.nbytes/1e6:.1f} MB decoded, {stats['timing_s']['fetch_em']} s")

    t = time.perf_counter()
    seg = np.asarray(cv_seg[seg_lo[0]:seg_hi[0], seg_lo[1]:seg_hi[1], seg_lo[2]:seg_hi[2]])
    seg = np.squeeze(seg, axis=3) if seg.ndim == 4 else seg
    seg = np.ascontiguousarray(seg.astype(np.uint64))
    stats["timing_s"]["fetch_seg"] = round(time.perf_counter() - t, 3)
    print(f"[SEG] shape={seg.shape} {seg.nbytes/1e6:.1f} MB decoded, {stats['timing_s']['fetch_seg']} s")

    # ------------------------------------------------------------ 保存 npy
    t = time.perf_counter()
    np.save(out / "em.npy", em)
    np.save(out / "seg.npy", seg)
    stats["timing_s"]["save_npy"] = round(time.perf_counter() - t, 3)

    # ------------------------------------------------------------ 统计
    t = time.perf_counter()
    ids, counts = np.unique(seg, return_counts=True)
    order = np.argsort(-counts)
    seg_list = [{"id": int(ids[i]), "voxels": int(counts[i])} for i in order]
    (out / "seg_ids.json").write_text(json.dumps({
        "note": "id 0 = 背景/未标注; 体素数按分割层 mip0 (8x8x33nm) 计",
        "bounds_layer_voxels": {"lo": seg_lo, "hi": seg_hi},
        "n_unique": len(seg_list), "n_unique_nonzero": int((ids != 0).sum()),
        "ids": seg_list,
    }, indent=1))
    id_set = set(int(i) for i in ids)
    count_of = {int(ids[i]): int(counts[i]) for i in range(len(ids))}
    hits = [{"id": s, "voxels": count_of[s]} for s in segs_boss if s in id_set]
    hits.sort(key=lambda d: -d["voxels"])
    stats["timing_s"]["stats"] = round(time.perf_counter() - t, 3)

    # ------------------------------------------------------------ PNG
    t = time.perf_counter()
    # overlay 只能在 EM 与 SEG 的公共区域上做；两层分辨率可能不同 (mip0 时 EM 是 4nm)
    # 把公共区域用 8nm dim 单位表示，再分别换算回各自层
    com_lo = [max(em_lo[i] // em_scale[i], seg_lo[i] // seg_scale[i]) for i in range(3)]
    com_hi = [min(em_hi[i] // em_scale[i], seg_hi[i] // seg_scale[i]) for i in range(3)]
    n_png = 0
    for zi in range(em.shape[2]):
        z_abs = em_lo[2] + zi
        Image.fromarray(em[:, :, zi].T).save(out / f"em_z_{z_abs:04d}.png")
        n_png += 1
        if not (com_lo[2] <= z_abs < com_hi[2]):
            continue
        # EM 在公共区域内的子块 (EM 体素坐标)
        ex0, ex1 = com_lo[0] * em_scale[0] - em_lo[0], com_hi[0] * em_scale[0] - em_lo[0]
        ey0, ey1 = com_lo[1] * em_scale[1] - em_lo[1], com_hi[1] * em_scale[1] - em_lo[1]
        em2d = em[ex0:ex1, ey0:ey1, zi]
        sx0, sx1 = com_lo[0] * seg_scale[0] - seg_lo[0], com_hi[0] * seg_scale[0] - seg_lo[0]
        sy0, sy1 = com_lo[1] * seg_scale[1] - seg_lo[1], com_hi[1] * seg_scale[1] - seg_lo[1]
        sz = z_abs * seg_scale[2] - seg_lo[2]
        seg2d = seg[sx0:sx1, sy0:sy1, sz]
        # 分辨率不同 (mip0): 把 seg 最近邻放大到 EM 网格
        fx, fy = em_scale[0] // seg_scale[0], em_scale[1] // seg_scale[1]
        if fx > 1 or fy > 1:
            seg2d = np.repeat(np.repeat(seg2d, fx, axis=0), fy, axis=1)
        seg2d = seg2d[:em2d.shape[0], :em2d.shape[1]]
        Image.fromarray(overlay_slice(em2d, seg2d)).save(out / f"overlay_z_{z_abs:04d}.png")
        n_png += 1
    stats["timing_s"]["save_png"] = round(time.perf_counter() - t, 3)
    stats["timing_s"]["total"] = round(time.perf_counter() - t_total, 3)
    # 实际读取范围的物理尺寸 (--align 时大于请求范围)
    stats["read_physical_size_um"] = {
        "x": (em_hi[0] - em_lo[0]) * em_res[0] / 1000, "y": (em_hi[1] - em_lo[1]) * em_res[1] / 1000,
        "z": (em_hi[2] - em_lo[2]) * em_res[2] / 1000, "note": "按图像层实际读取范围 (em) 计; 未 --align 时等于 physical_size_um"}
    if em.max() == 0:
        print("[warn] EM 全黑 (max=0): 该范围可能在成像区域之外, 或 chunk 缺失被 fill_missing 填 0", file=sys.stderr)

    # ------------------------------------------------------------ stats.json
    em_f = em.astype(np.float32)
    stats["layers"]["em"] = em_meta
    stats["layers"]["seg"] = seg_meta
    stats["actual_shape_xyz"] = {"em": list(em.shape), "seg": list(seg.shape)}
    stats["bytes"] = {
        "note": "decoded = 解码后数组字节 (体素×dtype); chunk_transfer_decoded = 被迫传输的整 chunk 解码字节; "
                "线上真实传输更小 (EM 是 JPEG, SEG 是 compressed_segmentation)，此处为估算",
        "em_decoded": int(em.nbytes), "seg_decoded": int(seg.nbytes),
        "em_chunk_transfer_decoded": (em_meta.get("cost_aligned") or em_meta["cost_unaligned"])["fetched_bytes_decoded"],
        "seg_chunk_transfer_decoded": (seg_meta.get("cost_aligned") or seg_meta["cost_unaligned"])["fetched_bytes_decoded"],
        "em_npy_on_disk": (out / "em.npy").stat().st_size,
        "seg_npy_on_disk": (out / "seg.npy").stat().st_size,
        "wire_estimate_note": "H01 EM JPEG 约 0.2-0.4 B/voxel, compressed_segmentation 约 0.05-0.3 B/voxel (经验值)",
        "em_wire_estimate": int(em_meta["cost_unaligned"]["fetched_voxels"] * 0.3),
        "seg_wire_estimate": int(seg_meta["cost_unaligned"]["fetched_voxels"] * 0.15),
    }
    stats["em"] = {"mean": round(float(em_f.mean()), 3), "std": round(float(em_f.std()), 3),
                   "min": int(em.min()), "max": int(em.max()),
                   "all_black": bool(em.max() == 0)}
    stats["seg"] = {"n_unique_ids": int(len(ids)), "n_unique_nonzero": int((ids != 0).sum()),
                    "background_voxels": count_of.get(0, 0),
                    "background_fraction": round(count_of.get(0, 0) / seg.size, 4),
                    "top5": seg_list[:5]}
    stats["boss_segments"] = {
        "url_total": len(segs_boss),
        "url_position_dim_units": boss_state.get("position"),
        "url_segment_query": boss_state["layers"][1].get("segmentQuery"),
        "present_in_range": len(hits),
        "present_ids": hits,
    }
    stats["outputs"] = {"dir": str(out), "png_files": n_png}
    (out / "stats.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False))

    print(f"[done] {out}  em mean={stats['em']['mean']} std={stats['em']['std']}  "
          f"unique seg={stats['seg']['n_unique_ids']}  boss hits={len(hits)}/{len(segs_boss)}  "
          f"total {stats['timing_s']['total']} s")


if __name__ == "__main__":
    main()
