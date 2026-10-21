# -*- coding: utf-8 -*-
"""测试夹具：门禁「工程副本」—— 复制引擎到临时目录 + 建一个**干净的** git 基线。

**什么时候需要它**

哨兵 `checks/harness_guard.py` 的判据是「harness 工程仓有没有**未提交的**受保护文件改动」。
评审 v13 严重 4 修好之前，这条判据在提交链路上恒不触发（`_run_guard` 写死 cwd=ROOT，
把**每一次**端仓提交都判成"本仓提交" → 撞上"读不到提交消息即放行"）。修好之后它**真的生效**了
—— 连带后果是：所有"模拟一次提交"的用例，只要跑测试的人**正好在改门禁文件**（维护中的常态），
就会先被哨兵拦在门口。

那不是被测行为失败，却会把红灯记到被测行为头上。两种省事做法都不对：

  · 直接跳过 —— 覆盖恰好在维护者最需要它的时候（正在改门禁时）整片消失；
  · 让用例继续对着真工程仓跑 —— 测试之间互相投毒（本文件制造的脏会连累别的用例），
    而且依赖开发者工作区的偶然状态（v13 对 `test_hooks_chain` 提的正是这一类）。

副本把两件事一起解决：引擎里的 ROOT 一律由 `__file__` 推导，所以副本里跑的是**同一份代码、
同一套判据**；而副本的脏/净由用例自己决定，因此**总能**跑到被测行为，无需 skip、无需前置条件。

**为什么可以只复制三棵子树**：`checks/`（规则与哨兵）、`harness/`（调度器、hook 入口、配置读取）、
`config/`（gate 策略）。提交链路与 run_gate 只依赖这三棵；缺 `sandbox/` 会让
`harness_self_check` 的几项降级为 ⚠（子集布局，`tests/test_review_v8_batch3.py` 已单独锁过）。
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SUBTREES = ("checks", "harness", "config")
_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")

# 台账隔离（第四轮 N-2：测试跑出来的判定记录写进真实 reports/gate_log.jsonl，实测污染 124 条，
# `tests/test_coverage.py` 为此加了源码级锁）。副本里的 pre-commit **会真的跑到 run_gate**，
# 所以这条隔离是必需的，不是抄格式：不隔离等于每跑一次副本就往真台账里灌一批记录。
# 用模块级临时目录、而不是副本内部的 reports/：副本是一次性的（用完可能已被 cleanup），
# 而一个进程里可能创建多份副本 —— 日志位置必须在整个进程生命周期内稳定可达。
_LOG_DIR = tempfile.mkdtemp(prefix="engine-copy-logs-")
# 2026-09-21：原先用 `shutil.rmtree(ignore_errors=True)` —— 这个目录**故意**留在原地不随副本删，
# 而进程退出时那次删除同样会撞上只读文件而静默失败（实测存量 12 份）。走强删助手。
atexit.register(_rmtree_force, _LOG_DIR)
os.environ.setdefault("HARNESS_LOG_FILE", os.path.join(_LOG_DIR, "gate_log.jsonl"))

# 未跟踪的新文件即构成「未提交的受保护文件改动」（`_in_protected`：checks/ 下任何 .py）。
_PROBE = "checks/_engine_copy_probe.py"


class EngineCopy:
    """一份可以随便弄脏的工程副本。用完 `cleanup()`（或交给 `shared()` 自动清理）。"""

    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="engine-copy-")
        for name in _SUBTREES:
            src = os.path.join(ROOT, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(self.path, name), ignore=_IGNORE)
        # 2026-09-17（评审 v13 复查遗留项② 施工中发现）：`config/` 是整棵复制的，于是
        # **开发者本机那份 gitignore 掉的 harness.local.* 被一起带进副本** —— 而它正是
        # 「本机把某个 gate 关了 / 把 mode 降了」的载体（`_guard_policy` 明确允许本地关 gate）。
        # 副本存在的全部理由是"脏/净由用例自己决定"，继承一份机器相关的覆盖层直接推翻这个前提：
        # 维护者本机一旦关过某个 gate，同一个用例在别人机器上就是另一种行为。
        # 剔除两种格式（TOML 主格式 + 旧版 YAML）；用例要覆盖层就自己写（batch8 就是这么做的）。
        for name in ("harness.local.toml", "harness.local.yaml"):
            inherited = os.path.join(self.path, "config", name)
            if os.path.exists(inherited):
                os.remove(inherited)
        self._git("init", "-q")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "baseline")

    def _git(self, *a):
        return subprocess.run(["git", "-C", self.path] + list(a), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)

    def p(self, rel):
        return os.path.join(self.path, rel.replace("/", os.sep))

    @property
    def guard(self):
        return self.p("checks/harness_guard.py")

    @property
    def hook(self):
        return self.p("harness/hooks/pre-commit.py")

    # ---- 脏 / 净 -------------------------------------------------------------
    def make_dirty(self):
        """新增一个**未跟踪的**受保护文件 = "工程仓有未提交的门禁文件改动"。

        **只新增、不改写既有文件**：cleanup 只需删掉它，不可能误伤副本里的任何内容。
        （v13 严重 2 的教训：测试用 `git checkout -- <探针>` 还原，把开发者正在改的
        `pc_code_check.py` 静默回滚了 —— 测试不该是破坏性操作。）
        """
        with open(self.p(_PROBE), "w", encoding="utf-8") as f:
            f.write("# engine-copy sentinel probe（只存在于副本里）\n")
        return _PROBE

    @property
    def probe_name(self):
        return os.path.basename(_PROBE)

    def cleanup(self):
        # 2026-09-21：原先 `shutil.rmtree(..., ignore_errors=True)` **删不掉**这份副本 ——
        # git 把 `.git/objects` 标成只读，PermissionError 被 ignore_errors 静默吞掉，
        # 于是每跑一次套件就在 %TEMP% 留一份带 .git 的完整副本（实测存量 1515 份）。
        # 症状与"清理成功"完全一样，所以只能靠数目录才发现。
        _rmtree_force(self.path)


_SHARED = None


def shared():
    """进程内共享的**干净**工程副本（进程退出时自动删除）。

    给"只需要哨兵放行"的提交链路用例用 —— 它们要的是"工程仓干净"这个前提，不必各建一份。
    **谁要弄脏，请自己 `EngineCopy()` 建私有的**：共享副本一旦被弄脏，同一次运行里其它
    用例会被连带拦下（那正是本夹具要消灭的"测试之间互相投毒"）。
    """
    global _SHARED
    if _SHARED is None:
        _SHARED = EngineCopy()
        atexit.register(_SHARED.cleanup)
    return _SHARED


def add_tests_dir_to_path():
    """让同目录的测试模块能 `import _engine_copy`（discover 之外的手工调用也要能用）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
