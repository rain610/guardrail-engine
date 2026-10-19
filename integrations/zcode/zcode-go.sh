#!/usr/bin/env bash
# 用 opencode-go 通道跑 ZCode CLI（2026-09-13 实测打通）
#
# 为什么需要它：ZCode 的 openai 内置 kind 只认 OPENAI_BASE_URL、**无处配置自定义请求头**，
# 而 opencode-go 必须带 `x-opencode-session`。所以中间放一个小代理补头+注入密钥
# （~/.zcode/opencode-go-proxy.py，密钥运行时从 Hermes 的 .env 取，不落文件）。
#
# 用法：
#   sh ~/.zcode/zcode-go.sh --cwd <仓库> -p "<提示词>"     # 非交互跑一条
#   sh ~/.zcode/zcode-go.sh                                # TUI
set -u

PORT="${ZCODE_GO_PROXY_PORT:-8791}"
PROXY="$HOME/.zcode/opencode-go-proxy.py"
LOG="${LOCALAPPDATA:-$HOME}/Temp/og-proxy.log"

# 代理没起就起（幂等；用 cygpath 给原生 python 传 Windows 路径——MSYS 路径它不认）
if ! netstat -ano 2>/dev/null | grep -q "[.:]${PORT} .*LISTENING"; then
  PY="$(command -v python || command -v python3)"
  ( "$PY" "$(cygpath -m "$PROXY" 2>/dev/null || echo "$PROXY")" --port "$PORT" >> "$LOG" 2>&1 & )
  sleep 2
fi

export ELECTRON_RUN_AS_NODE=1
export OPENAI_BASE_URL="http://127.0.0.1:${PORT}/v1"
export OPENAI_API_KEY="dummy"          # 真密钥由代理注入

exec "D:/Program Files/ZCode/ZCode.exe" "D:/Program Files/ZCode/resources/glm/zcode.cjs" "$@"
