#!/bin/bash
# 双击运行：打开一个已配好 h01 命令的终端
cd "$(dirname "$0")" || exit 1
export PATH="/Users/mac/.workbuddy/binaries/python/envs/default/bin:$PATH"

if ! command -v h01 >/dev/null 2>&1; then
  echo "未找到 h01，正在安装…"
  /Users/mac/.workbuddy/binaries/python/envs/default/bin/pip install -e . || {
    echo "安装失败"; read -r -p "按回车退出"; exit 1; }
fi

echo ""
echo "  h01 —— H01 连接组数据工具   项目目录: $(pwd)"
echo ""
echo "  命令："
echo "    h01 info    --npy <目录>              查看 npy 信息"
echo "    h01 render  --npy <目录> [--kind] [--jobs N]   本地重绘 npy → PNG"
echo "    h01 fetch   --x A-B --y A-B --z A-B --out DIR  取数（走网络）"
echo "    h01 shot    --x A-B --y A-B --z A-B --out DIR  批量截图（开浏览器）"
echo "    h01 limits  --center x,y,z            探测 xyz 范围上下限"
echo ""
echo "  可直接复制运行："
echo "    h01 info --npy test_data/demo2_fetch"
echo "    h01 render --npy test_data/demo2_fetch --kind em,overlay,boundary --out /tmp/figs"
echo "    h01 shot --x 247296-247808 --y 193408-193920 --z 2001-2003 --seg --out /tmp/shots"
echo ""
exec "$SHELL"
