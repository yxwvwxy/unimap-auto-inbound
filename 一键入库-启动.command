#!/bin/bash
# 双击此文件即可启动：打开浏览器登录 UniUni，然后监听 Google Sheet 队列
cd "$(dirname "$0")" || exit 1

echo "=========================================="
echo "  一键入库 — 本机监听"
echo "=========================================="
echo ""
echo "接下来："
echo "1. 会弹出浏览器 → 完成 Microsoft / UniUni 登录"
echo "2. 点橙色 EDIT ORDER，看到单号搜索框"
echo "3. 回到【这个黑色窗口】按 Enter"
echo "4. 看到 Watching... / Local trigger listening 后，这个窗口要一直开着"
echo "5. 去 SI 网站点「一键入库」，或去 Google Sheet 选单号点菜单"
echo ""
echo "出现 [Process completed] = 已停止，需要重新双击本文件"
echo "=========================================="
echo ""

if [[ ! -d .venv ]]; then
  echo "正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv || { echo "失败: python3 -m venv"; read -r; exit 1; }
  .venv/bin/pip install -r requirements.txt || { echo "失败: pip install"; read -r; exit 1; }
  .venv/bin/playwright install chromium || { echo "失败: playwright install"; read -r; exit 1; }
fi

.venv/bin/python main.py --watch
code=$?

echo ""
echo "------------------------------------------"
echo "监听已结束（退出码: $code）。"
echo "若要继续跑单，关闭本窗口后重新双击「一键入库-启动」。"
echo "按 Enter 关闭窗口..."
read -r
exit "$code"
