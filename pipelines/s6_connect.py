#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S6 · 连接正确性标注层（骨架：接口先行，核心判定待口径确认后填）

为什么单独要这一层
------------------
我们是做算法优化的，交付指标是**神经连接准确率**。
连接组学里最反直觉的一点：体素级差异跟连接正确率基本不相关。
  - **Merge 假合并**：两段本不相连的神经元被并成一个 → 它们互借对方的突触
    → 凭空造出**假连接**（FP）。0.1% 体素的 merge 可能造出上百条假边。
  - **Split 假分裂**：一段完整神经元被切断 → 上游与突触断开
    → 真实**连接丢失**（FN）。
  - 反之，轮廓粗糙但拓扑不断，连接可以全对。

所以第三视图只放「我们的分割」是不够的，评审看不出连接对不对。
真正有价值的是把突触位点按 **判对 / 漏检 / 误检** 着色标出来。

当前状态
--------
- 数据模型与接口**已定死**（先定契约，避免后面返工）
- 三个核心函数**留空**，等算法同学确认口径后填：
    load_synapses()     突触从哪来
    classify()          怎么判定对错（三选一口径）
    export_annotations() 写成 Neuroglancer precomputed annotation
- 已可用：SHADER_BY_TYPE（着色器）、marker_volume()（降级方案）、
  build_connect_layer()（图层字典，S5 的预留槽位就调它）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 数据模型
TP, FN, FP = 0, 1, 2

TYPE_LABELS = {
    TP: "TP 判对",
    FN: "FN 漏检（split 造成真实连接丢失）",
    FP: "FP 误检（merge 造成假连接）",
}

# 按 type 着色的 Neuroglancer shader，可直接用
SHADER_BY_TYPE = """void main() {
  switch (prop_type()) {
    case 0: setColor(vec3(0.0, 1.0, 0.0)); break;   // TP 绿
    case 1: setColor(vec3(1.0, 0.0, 0.0)); break;   // FN 红
    case 2: setColor(vec3(1.0, 0.6, 0.0)); break;   // FP 橙
    default: discard;
  }
  setPointMarkerSize(8.0);
  setPointMarkerBorderWidth(0.0);
}"""


# ---------------------------------------------------------------- 核心（待实现）
def load_synapses(source=None, bounds=None):
    """加载突触位点。

    TODO(待确认来源)：
      - H01 官方已有 `c3/synapses/precomputed`（你那个 state 里的
        `synapse annotations` 层，默认 visible:false），含 pre/post 归属；
      - 或者用我们自己算法检测出的突触。
      两者混用会得出完全不同的「准确率」，必须先定死。

    bounds : (x0,x1,y0,y1,z0,z1) 只取范围内的

    返回 list[dict]：{"x","y","z","pre","post","score"}
    """
    raise NotImplementedError(
        "load_synapses() 待实现：先确认突触来源\n"
        "  - 官方 c3/synapses/precomputed（precomputed annotation，读需要 neuroglancer python 包）\n"
        "  - 或我们自己算法检测的突触\n"
        "口径不定死，算出来的「准确率」没有意义。")


def classify(ours, gt, synapses, level="edge"):
    """判定每个突触在我们结果里的对错 —— 本模块的核心，接口先定死。

    ours     : 我们的分割（uint64 ID 图）
    gt       : 参考分割（c3）
    synapses : load_synapses() 的返回
    level    : 判定口径，三选一：
        "detect"   突触**是否存在**（检测层）
        "partner"  pre→post **伙伴指派**是否正确
        "edge"     神经元级**连边**是否存在（默认，最贴近「连接准确率」）

    判定思路（填实现时照这个走）：
      - 突触两侧的体素在 ours 里属于**同一个物体**、在 gt 里属于**不同物体** → merge → FP
      - 突触两侧在 gt 里同属一个物体、在 ours 里被切开 → split → FN
      - 一致 → TP

    TODO(待算法确认)：
      1. level 到底取哪一级（三者指标与可视化都不同）
      2. **真值来源**：拿 c3 当真值算出的只是「与 c3 的一致率」，不是真准确率
         （c3 自身就有 merge/split）。真 GT 需要独立人工标注集。

    返回 list[dict]：{"x","y","z","type","pre","post","note"}
        type ∈ {TP=0, FN=1, FP=2}
    """
    raise NotImplementedError(
        "classify() 待实现：需要先确认\n"
        "  1) 「连接准确率」是哪一级：detect / partner / edge\n"
        "  2) 真值用 c3 还是独立人工标注集（用 c3 只得到一致率，不是准确率）")


def export_annotations(points, out_dir, offset, resolution=(8, 8, 33)):
    """把点集写成 Neuroglancer precomputed annotation 层。

    TODO(待实现)：需要按 neuroglancer_annotations_v1 写
      /info（含 properties: prop_type 为 uint8 枚举）
      /spatial0/<morton_key>（二进制：count + 3×float32 位置 + 属性 + id）
    建议用 `neuroglancer` python 包写，别手写二进制。

    在此之前可用 marker_volume() 降级：把点烧成 uint8 标记卷，走 S5 的导出通道，
    Neuroglancer 里当 image 层叠加显示（看得到位置，但没有悬停属性）。
    """
    raise NotImplementedError(
        "export_annotations() 待实现：建议用 neuroglancer python 包写 "
        "neuroglancer_annotations_v1 格式。\n"
        "临时降级可用同文件的 marker_volume()。")


# ---------------------------------------------------------------- 已可用
def marker_volume(points, shape, offset, radius=3):
    """降级方案：把点烧成 uint8 标记卷（1=TP 2=FN 3=FP，0=空）。

    这样能复用 S5 的 export_precomputed()，不用等 annotation 写入实现。
    立方体膨胀只是为了让单点在切片里看得见。
    """
    import numpy as np
    vol = np.zeros(shape, dtype=np.uint8)
    ox, oy, oz = offset
    r = int(radius)
    for p in points:
        cx, cy, cz = int(p["x"]) - ox, int(p["y"]) - oy, int(p["z"]) - oz
        if not (0 <= cx < shape[0] and 0 <= cy < shape[1] and 0 <= cz < shape[2]):
            continue
        x0, x1 = max(cx - r, 0), min(cx + r + 1, shape[0])
        y0, y1 = max(cy - r, 0), min(cy + r + 1, shape[1])
        z0, z1 = max(cz - r, 0), min(cz + r + 1, shape[2])
        vol[x0:x1, y0:y1, z0:z1] = np.uint8(int(p["type"]) + 1)
    return vol


def build_connect_layer(source_url, name="connectivity check"):
    """构造「连接正确性」图层字典 —— S5 里预留的槽位就调这个函数。

    目前按 annotation 层设计（点 + shader 上色）。
    若走 marker_volume() 降级，把 type 改成 image 即可，其它不用动。
    """
    return {
        "type": "annotation",
        "source": source_url,
        "tool": "annotatePoint",
        "shader": SHADER_BY_TYPE,
        "name": name,
        "tab": "annotations",
    }


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="s6_connect", description="连接正确性标注层（骨架）")
    ap.add_argument("--check", action="store_true", help="查看当前已实现/待实现的清单")
    a = ap.parse_args(argv)
    print(__doc__)
    print("已实现：SHADER_BY_TYPE / marker_volume() / build_connect_layer()")
    print("待实现：load_synapses() / classify() / export_annotations()")
    return 0


if __name__ == "__main__":
    sys.exit(main())
