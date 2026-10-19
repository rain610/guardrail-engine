#!/bin/sh
# 打印本机 agentguard 工程根目录（给文档/脚本引用，避免把绝对路径写死）。
#   sh sandbox/where.sh        → git-bash/POSIX 风格路径
#   sh sandbox/where.sh --win  → Windows 风格正斜杠（喂给 Windows 程序的参数用）
# 2026-09-12 新增（可移植性）：分享给同事时文档里统一写 <HARNESS>，用本脚本求值。
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ "$1" = "--win" ]; then
    if command -v cygpath >/dev/null 2>&1; then cygpath -m "$ROOT"; else echo "$ROOT"; fi
else
    echo "$ROOT"
fi
