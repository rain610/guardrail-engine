#!/usr/bin/env python
"""本机 shim：转发到工程仓里的可移植实现（唯一来源，避免两份实现漂移）。

**不要手改本文件** —— 由 `python <HARNESS>/sandbox/install_shim.py` 生成（占位符 __HARNESS__ 会被替换）。

目标解析顺序（与正本一致）：
  ① 环境变量 `AGENTGUARD_DIR`（旧名 `HARNESS_DIR` 同样可用）；② 指针文件 `~/.guardrail-engine/harness.path`（旧 `~/.agentguard/harness.path` 仍读）；③ 生成时写入的路径。

**目标缺失时的行为（2026-09-13 外部评审 G2 后改进）**：
  不阻塞（exit 0，保持"宁可不过门禁也别卡住同事"的承诺），但**出声** ——
  stderr 打一次提示 + 落一个 `~/.agentguard/shim-missing.log` 去重标记（恢复后自动清除）。
  原先完全静默 → 换机器/挪目录后 7 家客户端的编辑期门禁会**整体无声失效**，
  与这个项目自身的反静默哲学相悖。回归锁：
  tests/test_client_config_check.py::TestShimSilentFailure
"""
import os
import runpy
import sys

# H1（2026-09-13 外部评审 v3）：宿主管道可能是 GBK/ASCII，中文提示会抛 UnicodeEncodeError——
# 而它被下面的 except 吞掉 → **反静默机制自己静默**。与钩子正本一致，先切 UTF-8(errors=replace)。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

GEN_PATH = r"__HARNESS__"
# 与 guardrail_engine/_shell.py:config_home() 同口径（本文件随 hook 分发，不能 import 包）：
# 新目录存在就用它，否则沿用旧目录；都没有时按新目录创建。
_HOME = os.path.expanduser("~")
_NEW_DIR = os.path.join(_HOME, ".guardrail-engine")
_OLD_DIR = os.path.join(_HOME, ".agentguard")
AGENTS_DIR = _NEW_DIR if os.path.isdir(_NEW_DIR) else (_OLD_DIR if os.path.isdir(_OLD_DIR) else _NEW_DIR)
POINTER = os.path.join(AGENTS_DIR, "harness.path")
FLAG = os.path.join(AGENTS_DIR, "shim-missing.log")
REL = os.path.join("integrations", "hooks", "agentguard-gate.py")
RENOTIFY_DAYS = 7          # H3：标记带时间戳，超过这个天数（仍没恢复）再提醒一次，避免长期静默


def resolve():
    cands = []
    env = (os.environ.get("GUARDRAIL_ENGINE_DIR") or os.environ.get("AGENTGUARD_DIR") or os.environ.get("HARNESS_DIR") or "").strip()
    if env:
        cands.append(env)
    try:
        if os.path.isfile(POINTER):
            cands.append(open(POINTER, encoding="utf-8").read().strip())
    except Exception:
        pass
    cands.append(GEN_PATH)
    for c in cands:
        if c and os.path.isfile(os.path.join(c, REL)):
            return os.path.join(c, REL)
    return None


TARGET = resolve()
if TARGET is None:
    msg = ("[harness shim] 找不到门禁实现（GUARDRAIL_ENGINE_DIR / AGENTGUARD_DIR / HARNESS_DIR / ~/.guardrail-engine|.agentguard/harness.path / 生成时路径都失效）："
           "本次编辑期门禁被跳过（未阻塞）。修复：重跑 <ENGINE>/sandbox/install_shim.py，或设置 GUARDRAIL_ENGINE_DIR（旧名 AGENTGUARD_DIR / HARNESS_DIR 兼容）。")
    # H2：**先出声，后落盘**，各自 best-effort —— 宁可多喊一次，也不能因为"写标记失败/编码异常"
    # 把提示一起吞掉（那正是"反静默机制自己静默"的坑）。
    import time
    should_speak = False
    try:
        if not os.path.isfile(FLAG):
            should_speak = True
        else:                                  # H3：久未恢复 → 再提醒一次（按 RENOTIFY_DAYS 节流）
            try:
                age_days = (time.time() - os.path.getmtime(FLAG)) / 86400.0
                should_speak = age_days >= RENOTIFY_DAYS
            except Exception:
                should_speak = True
    except Exception:
        should_speak = True
    if should_speak:
        try:
            print(msg, file=sys.stderr)
        except Exception:
            pass
    try:
        os.makedirs(AGENTS_DIR, exist_ok=True)
        with open(FLAG, "w", encoding="utf-8") as fh:
            fh.write("%s\n生成时间: %s\n解析链: GUARDRAIL_ENGINE_DIR/AGENTGUARD_DIR/HARNESS_DIR → %s → %s\n"
                     % (msg, time.strftime("%Y-%m-%d %H:%M:%S"), POINTER, GEN_PATH))
    except Exception:
        pass
    sys.exit(0)

try:
    if os.path.isfile(FLAG):                  # 恢复正常 → 清掉去重标记，下次故障还能再提示
        os.remove(FLAG)
except Exception:
    pass

sys.argv = [TARGET] + sys.argv[1:]
runpy.run_path(TARGET, run_name="__main__")
