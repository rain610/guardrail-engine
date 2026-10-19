# -*- coding: utf-8 -*-
r"""外部评审 v9 V9-5 落地锁：R10/R11 边界探针表（AutoClaw 报告 §6 全表）。

表中每条都来自外部评审的实测表，修复前后对照（我方已按表修正规则）：
  跨行特性        修复前 FP=1 → 期望 0
  注释伪属性      修复前 FP=1 → 期望 0
  allman 缺特性   修复前 FN=0 → 期望 1
  注释里的 [Length] 修复前 FP=1 → 期望 0
  字符串里的 [Length] 修复前 FP=1 → 期望 0
其余（标准/内联/缺特性/可空/族内三种/族外边界/作用域/大小写）为回归项。
"""
import os
import pathlib
import shutil as _shutil
import sys
import unittest

# 快跑（HARNESS_FAST=1，2026-09-17 批 4 接线）：本文件 4 例里只有一条慢 ——
# `TestHooksPathArchive::test_archives_previous_value` **单例 6.57s**（真起两个临时 git 仓、
# 真跑两遍 `sandbox/install_hooks.sh`）；另 3 例（R10/R11 探针表 + 作用域）合计 **0.005s**，
# 是纯进程内规则表比对。接线前完全没接（全文 0 处 HARNESS_FAST），快跑档单跑 6.6~8.0s。
# 落点 = 只给 `TestHooksPathArchive` 加**类级**（该类只有这一条例，类级 = 方法级）。
# ★ **R10/R11 两个类一个字都不动**：`TestR10ProbeTable::test_table` 正是变异 **M10-allman**
#   的唯一判据（checks/backend_ruleset.py 的 R10 allman 正则被削成要求 `{` 同行 → 漏报）。
#   它便宜到 0.001s —— 为省这 0.001s 把判据在快跑档里清掉，是拿护盾换零头。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "checks"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 被测脚本（sandbox/install_hooks.sh）**离不开**的 POSIX 工具。挑 shell 时要逐个验它找得到 ——
# 见 _resolve_sh 的注释：`usr\bin\sh.exe` 冷启动时 PATH 里没有自己的 bin 目录，这些**全都找不到**。
_NEEDED_TOOLS = ("dirname", "basename", "mkdir", "cp", "rm", "grep")


def _shell_works(sh):
    """这个 shell 能不能真跑起我们的 POSIX 脚本 —— 判据是**它自己**找不找得到那批工具。

    不接受"文件存在"当判据（2026-09-23 评审报告 M1 第 3 例，本机实测复现）：
    `C:/Program Files/Git/usr/bin/sh.exe` 是 MSYS 的**裸二进制**，从没有 Git bin 的 PATH 冷启动时
    **不注入 `/usr/bin`**（实测它的 PATH 只剩 `/c/Windows/system32:/c/Windows:/cmd`）——
    于是 `install_hooks.sh` 第 19 行的 `$(dirname "$0")` 直接 `command not found`，
    HARNESS 被推导成 `/`，脚本的自检判 `//harness/hooks-dir/pre-commit` 不存在 → fail-closed 中止。
    对照：`bin\\bash.exe` / `bin\\sh.exe` 启动时**会**前置 `/mingw64/bin:/usr/bin`，同一 PATH 下正常。
    """
    try:
        import subprocess                     # 与本文件既有写法一致（_run 里也是就地 import）
        r = subprocess.run([sh, "-c", " && ".join("command -v %s" % t for t in _NEEDED_TOOLS)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60)
    except Exception:
        return False
    return r.returncode == 0


def _resolve_sh():
    """评审 v10 §2-B2：原锁用**裸 `sh`** —— 干净 Windows（Git\\bin 不在 PATH）直接 ERROR；
    非 Windows 的 POSIX sh 可用。按平台解析，找不到就 skip（出声），不给误导性 ERROR。

    2026-09-23（评审报告 M1 第 3 例）：**候选顺序不变，但每个候选都要真验一遍可用性**
    （`_shell_works`）—— 原先只判 `os.path.isfile`，于是首选的 `usr\\bin\\sh.exe` 在本机那种
    "PATH 里没有 Git usr\\bin"的形态下被选中，脚本必然失败，锁却把账算在"归档没做"上：
    **夹具挑了个跑不动的工具，却报成产品不达标**。跳过一个跑不动的候选是对的，
    全都不行才 skip（并且现在能说清为什么）。
    """
    if os.name != "nt":
        for c in (_shutil.which("sh"), _shutil.which("bash")):
            if c and _shell_works(c):
                return c
        return None
    for c in (r"C:/Program Files/Git/usr/bin/sh.exe", r"C:/Program Files/Git/bin/sh.exe",
              r"C:/Program Files/Git/usr/bin/bash.exe", r"C:/Program Files/Git/bin/bash.exe",
              _shutil.which("sh")):
        if c and os.path.isfile(c) and _shell_works(c):
            return c
    return None


_SH = _resolve_sh()

ENTITY = "src/Eagle.RMC.Entities/User.cs"


def _ctx(content, rel=ENTITY):
    import rule_base
    return rule_base.CheckContext(path=pathlib.Path(rel), rel=rel, raw=content.encode("utf-8"),
                                  content=content, script="", template="", mode="strict", repo="",
                                  changed_lines=frozenset((1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)),
                                  legacy_exempt=False, is_new_file=True, targeted=True)


def _hits(rule_id, body):
    import backend_ruleset as br
    rule = [r for r in br.BACKEND_RULES if getattr(r, "id", "") == rule_id][0]
    return rule.check(_ctx(body))


R10 = [
    ("标准两行：特性在上一行", 'public class U {\n  [Column(TypeName = "datetime")]\n  public DateTime CreateTime { get; set; }\n}\n', 0),
    ("内联写法", 'public class U {\n  [Column(TypeName="datetime")] public DateTime T { get; set; }\n}\n', 0),
    ("缺特性", "public class U {\n  public DateTime CreateTime { get; set; }\n}\n", 1),
    ("可空缺特性", "public class U {\n  public DateTime? CreateTime { get; set; }\n}\n", 1),
    ("★跨行特性（FP 修复）", 'public class U {\n  [Column(TypeName =\n           "datetime")]\n  public DateTime T { get; set; }\n}\n', 0),
    ("★注释伪属性（FP 修复）", "public class U {\n  // public DateTime CreateTime { get; set; }\n}\n", 0),
    ("★allman 大括号换行（FN 修复）", "public class U\n{\n  public DateTime CreateTime\n  {\n    get; set;\n  }\n}\n", 1),
    ("方法返回值不算属性（防新 FP）", "public class U {\n  public DateTime GetNow() { return DateTime.Now; }\n}\n", 0),
]

R11 = [
    ("[Length(50)]", 'public class U {\n  [Length(50)]\n  public string Name { get; set; }\n}\n', 1),
    ("[MaxLength(50)]", 'public class U {\n  [MaxLength(50)]\n  public string Name { get; set; }\n}\n', 1),
    ("[StringLength(50)]", 'public class U {\n  [StringLength(50)]\n  public string Name { get; set; }\n}\n', 1),
    ("★注释里的 [Length]（FP 修复）", "public class U {\n  // 曾经用 [Length(50)]，已删\n  public string Name { get; set; }\n}\n", 0),
    ("★字符串里的 [Length]（FP 修复）", 'public class U {\n  public string Msg = "字段 [Length(50)] 已移除";\n}\n', 0),
    ("族外边界 [MinLength(1)]", 'public class U {\n  [MinLength(1)]\n  public string Name { get; set; }\n}\n', 0),
    ("裸 [Length]（无括号）", "public class U {\n  [Length]\n  public string Name { get; set; }\n}\n", 0),
]


class TestR10ProbeTable(unittest.TestCase):
    def test_table(self):
        bad = []
        for name, body, want in R10:
            got = len(_hits("R10", body))
            if got != want:
                bad.append("%s：期望 %d 命中，实得 %d" % (name, want, got))
        self.assertEqual(bad, [], "R10 探针表不符：\n  " + "\n  ".join(bad))


class TestR11ProbeTable(unittest.TestCase):
    def test_table(self):
        bad = []
        for name, body, want in R11:
            got = len(_hits("R11", body))
            if got != want:
                bad.append("%s：期望 %d 命中，实得 %d" % (name, want, got))
        self.assertEqual(bad, [], "R11 探针表不符：\n  " + "\n  ".join(bad))


class TestEntityScope(unittest.TestCase):
    """作用域（评审表末段）：任何以 Entities 结尾的目录层级都算实体目录。"""

    def test_scope(self):
        import backend_ruleset as br
        import rule_base
        r10 = [r for r in br.BACKEND_RULES if getattr(r, "id", "") == "R10"][0]
        body = "public class U {\n  public DateTime T { get; set; }\n}\n"
        cases = [("src/Eagle.RMC.Entities/U.cs", True),
                 ("src/A/B/MainEntities/U.cs", True),
                 ("src/Eagle.Entities2/U.cs", False),
                 ("src/Services/U.cs", False),
                 ("src/eagle.rmc.entities/U.cs", False)]   # 大小写敏感（与评审表一致）
        for rel, want in cases:
            ctx = rule_base.CheckContext(path=pathlib.Path(rel), rel=rel, raw=body.encode(),
                                         content=body, mode="strict", changed_lines=frozenset(),
                                         legacy_exempt=False, is_new_file=True, targeted=True)
            got = bool(r10.applicable(ctx))
            self.assertEqual(got, want, "作用域判定不符：%s 期望 %s" % (rel, want))


@_slow("真起两个临时 git 仓 + 真跑 install_hooks.sh 两遍（单例 6.57s）")
@unittest.skipUnless(_SH, "本机找不到可用的 sh（Windows 需 Git 自带）→ 跳过")
class TestHooksPathArchive(unittest.TestCase):
    """V9-10：安装脚本必须把**安装前的 core.hooksPath 值**落档（此前只备份文件、只打印来源）。"""

    def _run(self, cmd, cwd=None):
        import subprocess
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=180)

    def _mk(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="v9hp_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        self._run(["git", "init", "-q"], cwd=d)
        return d

    def _archive_text(self, repo):
        for root, _dirs, files in os.walk(repo):
            if "PREVIOUS-hooksPath.txt" in files:
                return pathlib.Path(root, "PREVIOUS-hooksPath.txt").read_text(encoding="utf-8")
        return None

    def test_archives_previous_value(self):
        inst = os.path.join(ROOT, "sandbox", "install_hooks.sh")
        # 场景 A：本来没设置 → 存档写明 <未设置>
        a = self._mk()
        self._run([_SH, inst, "gxgk_pc"], cwd=a)
        txt = self._archive_text(a)
        self.assertIsNotNone(txt, "安装后没有 hooksPath 值存档（V9-10 回归）")
        self.assertIn("未设置", txt)
        # 场景 B（要点）：本来有**自定义** hooksPath → 原值必须被记下来
        b = self._mk()
        custom = os.path.join(b, "my-hooks")            # 自定义 hooks 目录（相对路径写法）
        os.makedirs(custom, exist_ok=True)
        self._run(["git", "config", "core.hooksPath", "my-hooks"], cwd=b)
        self._run([_SH, inst, "gxgk_pc"], cwd=b)
        txt2 = self._archive_text(b)
        self.assertIsNotNone(txt2)
        self.assertIn("my-hooks", txt2, "自定义 hooksPath 的原值没被落档：\n%s" % txt2)
        self.assertIn("还原", txt2)


if __name__ == "__main__":
    unittest.main()
