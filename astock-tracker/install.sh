#!/usr/bin/env bash
# A股 Tracker 一键安装脚本
# 用法: bash install.sh
# 作用: 自动安装全部依赖(akshare 必需 + tushare 可选实时)、配置目录、跑自检。
# 设计: 幂等(可重复运行)、失败有回退(清华源→阿里源→官方源)、不中断在可选步骤。

set -u
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
MIRRORS=(
  "https://pypi.tuna.tsinghua.edu.cn/simple"
  "https://mirrors.aliyun.com/pypi/simple"
  ""  # 空=官方源,最后兜底
)

echo "=== A股 Tracker 安装开始 ==="
echo "skill 目录: $SKILL_DIR"
echo "Python: $($PY --version 2>&1)"
echo ""

# 通用 pip 安装函数:依次尝试各镜像源,任一成功即返回
pip_install () {
  local pkg="$1"
  local optional="${2:-no}"
  for m in "${MIRRORS[@]}"; do
    if [ -z "$m" ]; then
      echo "  尝试官方源安装 $pkg ..."
      $PY -m pip install -U "$pkg" --break-system-packages -q 2>/dev/null && { echo "  ✓ $pkg 安装成功(官方源)"; return 0; }
    else
      echo "  尝试 $m 安装 $pkg ..."
      $PY -m pip install -U "$pkg" -i "$m" --break-system-packages -q 2>/dev/null && { echo "  ✓ $pkg 安装成功"; return 0; }
    fi
  done
  if [ "$optional" = "optional" ]; then
    echo "  ⚠ $pkg 安装失败(可选依赖,跳过;相关功能会自动降级)"
    return 0
  else
    echo "  ✗ $pkg 安装失败(必需依赖)——请检查网络或手动安装"
    return 1
  fi
}

# 1. 必需依赖:akshare(备源 + 千股千评/新闻等 Tushare 未覆盖维度)
echo "[1/4] 安装 akshare(必需)..."
pip_install akshare || { echo "致命:akshare 安装失败,无法继续"; exit 1; }

# 2. 可选依赖:tushare(盘中实时 realtime_quote;不装则实时降级到 akshare)
echo ""
echo "[2/4] 安装 tushare(可选,用于盘中实时数据)..."
pip_install tushare optional

# 3. 数据目录
echo ""
echo "[3/4] 准备数据目录..."
DATA_DIR="${ASTOCK_DIR:-$HOME/.astock-tracker}"
mkdir -p "$DATA_DIR"
echo "  数据目录: $DATA_DIR"
TOKEN_VAL=""
if [ -n "${TUSHARE_TOKEN:-}" ]; then
  echo "  ✓ 检测到环境变量 TUSHARE_TOKEN"
  TOKEN_VAL="$TUSHARE_TOKEN"
elif [ -f "$DATA_DIR/tushare_token.txt" ]; then
  echo "  ✓ 检测到 token 文件 $DATA_DIR/tushare_token.txt"
  TOKEN_VAL="$(cat "$DATA_DIR/tushare_token.txt" | tr -d '[:space:]')"
else
  echo "  ⚠ 未配置 Tushare token。skill 仍可用(自动走 akshare),但配置后更稳定。"
  echo "    配置方法(二选一):"
  echo "      export TUSHARE_TOKEN=你的token"
  echo "      echo '你的token' > $DATA_DIR/tushare_token.txt"
fi

# 关键:把 token 同步注入 tushare SDK 自己的存储(~/.tushare.csv),
# 否则盘中实时接口 realtime_quote 走 SDK 鉴权时读不到 token,会提示"需配置凭证"。
if [ -n "$TOKEN_VAL" ]; then
  $PY -c "
import sys
try:
    import tushare as ts
    ts.set_token('$TOKEN_VAL'.strip())
    print('  ✓ token 已同步注入 tushare SDK(收盘后数据与盘中实时接口均可鉴权)')
except Exception as e:
    print('  ⚠ tushare SDK 未安装,实时接口暂不可用(收盘后数据不受影响):', str(e)[:50])
" 2>/dev/null
fi

# 4. 自检
echo ""
echo "[4/4] 接口连通性自检..."
$PY "$SKILL_DIR/scripts/fetch.py" selfcheck 2>/dev/null | $PY -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print('  verdict:', d.get('verdict', '未知'))
    print('  usable:', d.get('usable'))
    ts = d.get('tushare', {})
    print('  Tushare 配置:', ts.get('configured'), '| 可用维度:', ts.get('available_dims', 0))
    sys.exit(0 if d.get('usable') else 2)
except Exception as e:
    print('  自检解析失败:', str(e)[:80])
    sys.exit(3)
"
RC=$?
echo ""
if [ $RC -eq 0 ]; then
  echo "=== ✅ 安装完成,数据链路可用,可以开始使用 ==="
elif [ $RC -eq 2 ]; then
  echo "=== ⚠ 安装完成但自检未通过 ==="
  echo "   多为网络或 token 问题。若配了 token 仍失败,确认积分≥2000 且 token 正确;"
  echo "   未配 token 时确认网络能访问数据源。akshare 接口失效可尝试已自动升级到最新版。"
else
  echo "=== ⚠ 自检异常(可能沙箱无网或依赖未就绪),请手动跑一次 selfcheck 排查 ==="
fi
