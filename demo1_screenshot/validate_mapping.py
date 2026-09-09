"""校验 screenshot_range.js 的 像素->坐标 公式：用 cloud-volume 拉同一范围的 c3 标签，
按公式重采样到截图分辨率，比较"每个标签区域内截图颜色是否一致"。公式对了残差最小，
平移 1 px 残差就应该明显变大；用错的 z 片残差也应该变大。

用法: python validate_mapping.py out/seg_all   （需要 --seg --segments all 的输出）
"""
import json, sys, os
import numpy as np
from PIL import Image
from cloudvolume import CloudVolume

out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), 'out', 'seg_all')
meta = json.load(open(os.path.join(out_dir, 'meta.json')))
s = meta['crossSectionScale']
(xA, xB), (yA, yB) = meta['args']['x'], meta['args']['y']
vol = CloudVolume('precomputed://gs://h01-release/data/20210601/c3', mip=0, use_https=True, progress=False, fill_missing=True)


def residual(chroma, lab):
    H, W = chroma.shape[:2]
    px, py = np.arange(W), np.arange(H)
    vx = np.clip(np.floor((px + 0.5) * s).astype(int), 0, lab.shape[0] - 1)
    vy = np.clip(np.floor((py + 0.5) * s).astype(int), 0, lab.shape[1] - 1)
    L = lab[vx[None, :], vy[:, None]]
    out = {}
    for sh in [(0, 0), (1, 0), (0, 1), (2, 0), (0, 2), (4, 4)]:
        Ls = np.roll(np.roll(L, sh[0], 0), sh[1], 1)
        ids, inv = np.unique(Ls, return_inverse=True)
        inv = inv.reshape(H, W)
        cnt = np.bincount(inv.ravel(), minlength=len(ids)).astype(float)
        mean = np.stack([np.bincount(inv.ravel(), weights=chroma[..., c].ravel(), minlength=len(ids)) / np.maximum(cnt, 1) for c in range(3)], -1)
        out[sh] = float(np.abs(chroma - mean[inv]).sum(-1).mean())
    return out


ok = True
for rec in meta['slices']:
    z = rec['z']
    img = np.asarray(Image.open(os.path.join(out_dir, os.path.basename(rec['files']['range']))).convert('RGB')).astype(float)  # 用 basename：meta 里的路径可能是相对运行时 cwd 的
    chroma = img - img.mean(-1, keepdims=True)
    lab = np.asarray(vol[xA:xB, yA:yB, z:z + 1])[..., 0, 0]
    r = residual(chroma, lab)
    lab_prev = np.asarray(vol[xA:xB, yA:yB, z - 1:z])[..., 0, 0]
    r_prev = residual(chroma, lab_prev)[(0, 0)]
    best = min(r, key=r.get)
    good = best == (0, 0) and r[(0, 0)] < r_prev
    ok &= good
    print(f"z={z} residual@0={r[(0,0)]:.2f} @1px={r[(1,0)]:.2f}/{r[(0,1)]:.2f} @2px={r[(2,0)]:.2f} @4px={r[(4,4)]:.2f}  用 z-1 的标签={r_prev:.2f}  {'OK' if good else 'MISMATCH'}")
print('ALL OK' if ok else 'SOME MISMATCH')
sys.exit(0 if ok else 1)
