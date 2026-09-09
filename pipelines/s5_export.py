#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S5 · 把「我们的算法结果」导出成 Neuroglancer 能直接读的 Precomputed 数据源，
并拼出一条可分享的 viewer 链接。

背景（已确认的需求）
--------------------
领导要在 Neuroglancer 前端「新增一个位置展示我们的效果图」。
我们做算法优化，交付指标是**神经连接准确率**，因此：

  - 效果图本体 = 我们的重建 / 分割结果（对标 c3 的彩色 ID 图，uint64）
  - 展示重点是**连接级的对错**，不是体素级像不像 → 见 s6_connect.py

为什么走 Precomputed 而不是改前端
--------------------------------
Neuroglancer 是纯前端程序，只要数据能按 Precomputed / n5 / zarr 通过 HTTP 取到，
就能作为一个**新图层**加进 state，**一行前端代码都不用改**。
（代价：做不到「A 面板只显示 EM、B 面板只显示我们的结果」这种按面板分流，
那是原生限制；真要三路并排见 s6 之后的 wrapper 方案。）

预留位置
--------
1. state["layers"] 里给「连接正确性标注层」留了槽位。s6_connect.py 实现后，
   把 connect_url 传进 build_state() 即可自动插入，本文件不用再改。
2. 数据托管：本地 file:// 目录**在线 Neuroglancer 读不到**，必须先托管成
   HTTP(S) 并配 CORS —— --serve-url 就是为此准备的。
"""
import argparse
import json
import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import DATASET, EM_URL, SEG_URL, check_range  # noqa: E402

# mip1 体素物理尺寸（nm）：x/y 8nm，z 33nm
RESOLUTION = (8, 8, 33)
CHUNK = (128, 128, 32)

# 默认在线 viewer；本地自托管时改 --base-url
DEFAULT_BASE_URL = "https://neuroglancer-demo.appspot.com"

# 我们的结果在 viewer 里的默认配色，跟 c3 的随机彩色明显区分开
OURS_COLOR = "#ff00ff"


def load_ours(path):
    """加载我们的算法产出，返回 numpy 数组。

    TODO(待确认): 产出的真实格式。目前只支持 .npy；
    若是 zarr / h5 / tiff / 分片文件，在这里加分支即可，下游不用动。
    """
    import numpy as np
    if not os.path.exists(path):
        raise SystemExit(f"找不到我们的结果文件：{path}")
    if path.endswith(".npy"):
        return np.load(path, mmap_mode="r")
    raise SystemExit(
        f"暂不支持的格式：{path}\n"
        f"请在 pipelines/s5_export.py 的 load_ours() 里加一个分支（TODO）。")


def infer_layer_type(arr):
    """从 dtype 推断 Neuroglancer 图层类型。

    uint64 → segmentation（ID 图，对标 c3）
    uint8/uint16 → image（灰度，对标 EM）
    """
    dt = str(arr.dtype)
    if dt.startswith("uint64") or dt.startswith("int64"):
        return "segmentation"
    if dt.startswith(("uint8", "uint16", "float")):
        return "image"
    raise SystemExit(f"无法从 dtype={dt} 推断图层类型，请用 --layer-type 显式指定")


def export_precomputed(arr, out_dir, offset, resolution=RESOLUTION,
                       chunk=CHUNK, layer_type=None, compress="gzip"):
    """把数组写成 Neuroglancer precomputed 卷。

    arr      : x 最快维的数组，shape (dx, dy, dz) 或 (dx, dy, dz, 1)
    offset   : (x0, y0, z0) 该块在数据集全局坐标里的起点（mip1 体素）
    """
    import numpy as np
    from cloudvolume import CloudVolume

    arr = np.asarray(arr)
    if layer_type is None:
        layer_type = infer_layer_type(arr)

    # image 层 cloud-volume 需要 (x, y, z, channels)
    if layer_type == "image" and arr.ndim == 3:
        arr = arr[..., None]

    dx, dy, dz = arr.shape[:3]
    x0, y0, z0 = offset
    # 复用 common 的边界校验，越界在写盘前就报清楚
    check_range(x0, x0 + dx, y0, y0 + dy, z0, z0 + dz)

    os.makedirs(out_dir, exist_ok=True)
    info = CloudVolume.create_new_info(
        num_channels=1,
        layer_type=layer_type,
        data_type=str(arr.dtype),
        encoding="raw",
        resolution=list(resolution),
        voxel_offset=[int(x0), int(y0), int(z0)],
        chunk_size=tuple(int(c) for c in chunk),
        volume_size=[int(dx), int(dy), int(dz)],
    )
    vol = CloudVolume(f"precomputed://file://{os.path.abspath(out_dir)}",
                      info=info, compress=compress, progress=False)
    vol.commit_info()
    vol[x0:x0 + dx, y0:y0 + dy, z0:z0 + dz] = arr
    return {"dir": os.path.abspath(out_dir), "layer_type": layer_type,
            "shape": [int(dx), int(dy), int(dz)], "offset": [int(x0), int(y0), int(z0)]}


def build_state(ours_url, shape, offset, layer_type="segmentation",
                name="our result", connect_url=None, position=None):
    """组装 Neuroglancer viewer 状态。

    图层自下而上：EM 原图 → c3 分割（半透明参考）→ 我们的结果 → [连接正确性标注]

    connect_url 就是**预留的槽位**：s6_connect.py 实现后传进来即自动插入；
    传 None 时该槽位留空，state 依然可用。
    """
    x0, y0, z0 = offset
    dx, dy, dz = shape
    if position is None:
        position = [x0 + dx / 2.0, y0 + dy / 2.0, z0 + dz / 2.0]

    layers = [
        {"type": "image", "source": EM_URL, "tab": "source", "name": "4nm EM"},
        {"type": "segmentation", "source": SEG_URL, "tab": "segments",
         "name": "c3 segmentation", "selectedAlpha": 0.3},
    ]

    ours = {"type": layer_type, "source": ours_url, "name": name,
            "tab": "segments" if layer_type == "segmentation" else "source"}
    if layer_type == "segmentation":
        # 固定配色，避免和 c3 的随机彩色混淆
        ours["segmentDefaultColor"] = OURS_COLOR
        ours["selectedAlpha"] = 0.6
    else:
        ours["opacity"] = 0.6
    layers.append(ours)

    # ==== 预留槽位：连接正确性标注层（s6_connect.py 实现后启用）====
    if connect_url:
        try:
            import s6_connect
            layers.append(s6_connect.build_connect_layer(connect_url))
        except NotImplementedError:
            print("[warn] s6_connect 尚未实现，跳过连接正确性层（槽位已预留）",
                  file=sys.stderr)

    return {
        "dimensions": {"x": [8e-9, "m"], "y": [8e-9, "m"], "z": [3.3e-8, "m"]},
        "position": position,
        "crossSectionScale": 20.0,
        "projectionScale": 65536.0,
        "layers": layers,
        "layout": {"type": "xy-3d"},
        "selectedLayer": {"layer": name, "visible": True},
    }


def to_url(state, base_url=DEFAULT_BASE_URL):
    """state → 可分享的 Neuroglancer 链接。"""
    return f"{base_url.rstrip('/')}/#!" + quote(
        json.dumps(state, separators=(",", ":"), ensure_ascii=False), safe="")


def _parse_xyz(s):
    return [int(v) for v in str(s).replace(",", " ").split()]


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="s5_export",
        description="把我们的算法结果导出为 Neuroglancer 可读的 Precomputed，并生成 viewer 链接")
    ap.add_argument("--ours", required=True, help="我们的结果文件（.npy）")
    ap.add_argument("--offset", required=True, help="该块全局起点 x,y,z（mip1 体素）")
    ap.add_argument("--out", required=True, help="Precomputed 输出目录")
    ap.add_argument("--name", default="our result", help="在 viewer 里显示的图层名")
    ap.add_argument("--layer-type", default=None,
                    help="segmentation / image；不给则按 dtype 自动推断")
    ap.add_argument("--serve-url", default=None,
                    help="该目录托管后的 HTTP 地址；不给则用 http://127.0.0.1:8080/<目录名>")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Neuroglancer 站点")
    ap.add_argument("--state-json", default=None, help="把 state 另存为 JSON")
    ap.add_argument("--connect-url", default=None,
                    help="预留：连接正确性标注层地址（s6 实现后用）")
    a = ap.parse_args(argv)

    arr = load_ours(a.ours)
    offset = _parse_xyz(a.offset)
    if len(offset) != 3:
        raise SystemExit("--offset 需要三个数：x,y,z")
    layer_type = a.layer_type or infer_layer_type(arr)

    info = export_precomputed(arr, a.out, offset, layer_type=layer_type)
    print(f"[export] layer_type = {info['layer_type']}")
    print(f"[export] shape      = {info['shape']}  offset = {info['offset']}")
    print(f"[export] precomputed → {info['dir']}")

    serve = a.serve_url or f"http://127.0.0.1:8080/{os.path.basename(info['dir'])}"
    if not a.serve_url:
        print("[warn] 未给 --serve-url，按本地 8080 假设。"
              "在线 Neuroglancer 读不到 file://，请把该目录托管成 HTTP(S) 并配 CORS。",
              file=sys.stderr)

    state = build_state(f"precomputed://{serve}", info["shape"], info["offset"],
                        layer_type=info["layer_type"], name=a.name,
                        connect_url=a.connect_url)
    url = to_url(state, a.base_url)

    if a.state_json:
        with open(a.state_json, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(f"[state] → {a.state_json}")
    print()
    print("viewer 链接：")
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
