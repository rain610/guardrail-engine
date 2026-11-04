# -*- coding: utf-8 -*-
"""init 所有权标记的**读侧** —— 安装副本形态下 guard 的信任锚。

为什么需要（2026-11-04 评审 P0）
--------------------------------
README 主推 `pip install . → guardrail-engine init → hooks install`。`init` 把引擎复制到可写
目录，这份副本**不是 git 工作树**（没有 .git）。于是 `harness_guard` 用"引擎仓有没有未提交的
受保护改动"判篡改时，git 直接报错（rc=129 Not a git repository）→ 旧版一律 fail-closed rc=2 →
第一次 `git commit` 就被拦。用户会以为工具装坏了，退到 `--no-verify`，门禁实际上被整体关闭。

修法：`init` 在所有权标记里记下副本"判定代码"的内容哈希 `engine_sha`（信任锚）。guard 在副本
形态下（ROOT 读不出 git 改动 + 存在本标记）重算 `engine_sha` 与锚比对：一致=没被动过→放行，
不一致=被改弱→拦下。本模块就是 guard 侧的读取器；写侧在 `guardrail_engine/cli.py::_write_init_marker`。

为什么标记文件名在这里又写一份
------------------------------
它和 `guardrail_engine/_shell.py` 的 `INIT_MARKER` / `LEGACY_INIT_MARKER` 是同一组常量，但分处
两个包：`harness/` 不依赖 `guardrail_engine/`（引擎层 vs 壳/CLI 层），`cli` 也不 import `harness/`
（它一律 shell out 到 `harness/run_gate.py`）。所以只能各写一份 —— 与 `sandbox/upgrade.py` 里那份
同理，由 `tests/packaging/test_upgrade.py::TestInitMarkerContract` 用 AST 对拍防漂移。漂移的后果
**没有报错**：init 写 A、guard 找 B ⇒ 副本永远认不出 ⇒ 又退回一律 fail-closed，而测试全程绿。
"""
import json
import os
import re

MARKER_NAMES = (".guardrail-engine-init.json", ".agentguard-init.json")

_SHA_RE = re.compile(r"^[0-9a-f]{16}$")


def find_marker(root):
    """返回 root 下第一个存在的 init 标记路径；一个都没有则返回 ""。"""
    for name in MARKER_NAMES:
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return p
    return ""


def read_engine_sha(root):
    """读 init 标记里的 engine_sha（16-hex，小写）；无标记 / 读不出 / 非法 → 返回 ""。

    返回 "" 有两种含义，调用方必须用 `find_marker` 区分：
      · 没有标记 → 这不是安装副本（err 是真·git 故障，应交回 fail-closed）；
      · 有标记但锚为空/非法 → 是副本却无可用信任锚 → 必须 fail-closed（不能假装查过）。
    """
    p = find_marker(root)
    if not p:
        return ""
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    sha = str((data or {}).get("engine_sha") or "").strip().lower()
    return sha if _SHA_RE.match(sha) else ""
