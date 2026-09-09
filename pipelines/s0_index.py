#!/usr/bin/env python
"""S0 索引层 —— 用 c3 的 segment_properties 表决定"爬哪里"。

这张表只有几 MB，却含 46,637 个 segment 的 13 个数值特征 + 32 类标签，
是整条流水线里最便宜、也应该最先跑的一步。

用法:
    python s0_index.py --list-tags
    python s0_index.py --tag spiny-stellate --min-voxels 1e8 --top 20
    python s0_index.py --tag spiny-stellate --out rois.json
"""
import argparse
import gzip
import json
import sys

import requests

from common import PROPS_URL, human


def load_properties(url=PROPS_URL):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    raw = r.content
    if raw[:2] == b"\x1f\x8b":          # 服务端没自动解压时兜底
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def parse(props_json):
    """返回 [(seg_id, {feature: value}, [tag, ...]), ...]"""
    inline = props_json.get("inline", {})
    plist = {p["id"]: p for p in inline.get("properties", [])}
    if not plist:
        return [], []

    # ids 在 inline 下（不是顶层）；某些版本放在名为 'id' 的 property 里
    ids = inline.get("ids")
    if ids is None:
        ids = props_json.get("ids")
    if ids is None:
        ids = [int(v) for v in plist.get("id", {}).get("values", [])]

    tag_names = plist.get("tags", {}).get("tags", [])
    tag_vals = plist.get("tags", {}).get("values", [])

    rows = []
    for i, sid in enumerate(ids):
        feats = {}
        for pid, p in plist.items():
            if pid == "tags" or pid == "id":
                continue
            vals = p.get("values", [])
            if i < len(vals):
                feats[pid] = vals[i]
        tags = [tag_names[t] for t in tag_vals[i]] if i < len(tag_vals) else []
        rows.append((int(sid), feats, tags))
    return rows, tag_names


def main():
    ap = argparse.ArgumentParser(description="S0：按细胞类型/特征筛 ROI")
    ap.add_argument("--list-tags", action="store_true", help="只打印标签词表后退出")
    ap.add_argument("--tag", action="append", default=[], help="按标签筛选，可重复（AND）")
    ap.add_argument("--layer", help="按皮层分层筛选，如 L4")
    ap.add_argument("--min-voxels", type=float, default=0)
    ap.add_argument("--max-voxels", type=float, default=float("inf"))
    ap.add_argument("--min-syn", type=float, default=0, help="最少输入突触数 NSI")
    ap.add_argument("--top", type=int, default=10, help="打印前 N 条")
    ap.add_argument("--out", help="把筛选结果写成 JSON")
    args = ap.parse_args()

    props = load_properties()
    rows, tag_names = parse(props)

    if args.list_tags:
        print(f"共 {len(tag_names)} 个标签：")
        for i, t in enumerate(tag_names):
            print(f"  [{i:2}] {t}")
        print(f"\n有属性的 segment：{len(rows)} 条")
        return

    want = set(args.tag)
    if args.layer:
        want.add(args.layer)

    sel = []
    for sid, feats, tags in rows:
        if want and not want.issubset(set(tags)):
            continue
        nv = feats.get("NVx", 0)
        if not (args.min_voxels <= nv <= args.max_voxels):
            continue
        if feats.get("NSI", 0) < args.min_syn:
            continue
        sel.append({"id": sid, "tags": tags, **feats})

    sel.sort(key=lambda r: -r.get("NVx", 0))
    print(f"筛选条件 tag={args.tag or '-'} layer={args.layer or '-'} "
          f"体素数∈[{args.min_voxels:.3g}, {args.max_voxels:.3g}]")
    print(f"命中 {len(sel)} / {len(rows)} 条\n")

    hdr = f"{'segment ID':>14} {'体素数':>12} {'NSI':>6} {'NAx':>6} {'NSp':>6}  标签"
    print(hdr)
    print("-" * len(hdr))
    for r in sel[:args.top]:
        print(f"{r['id']:>14} {r.get('NVx', 0):>12,} {r.get('NSI', 0):>6} "
              f"{r.get('NAx', 0):>6} {r.get('NSp', 0):>6}  {','.join(r['tags'])}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(sel, f, indent=1)
        print(f"\n已写出 {len(sel)} 条到 {args.out}")


if __name__ == "__main__":
    sys.exit(main())
