# -*- coding: utf-8 -*-
"""`sandbox/global_agents.py` 回归锁 —— 全局守则的**唯一**渲染器。

为什么这份文件需要一把专门的锁（2026-09-19）
--------------------------------------------------------------------------------
`~/.agentguard/GLOBAL_AGENTS.md` 是各 AI 工具**自动加载**的入口路由，三份端级模板
（`golden-patterns/templates/AGENTS_{pc,h5,backend}_slim.md`）都指着它。而它有**两个写入方**：

  ① `sandbox/bootstrap_agent_env.sh` —— 整机装配第 1 步；
  ② `sandbox/install_shim.py`      —— 《安装说明》「二、Agent 接入」里**同事必然跑到**的那一步。

要让 ② 也会写，最直觉的做法是「再抄一份正文」——**那样会漂，而且漂得很难看**：

    哨兵行只要差一个字符（多一个空格、换个全/半角），先写的那份就会被后写的那份判成
    「没有哨兵的老文件」→ 走迁移分支，把整份旧内容**追加**到哨兵区之后；两个写入方交替跑
    （装 shim → 跑 bootstrap → 再装 shim…），文件每跑一次涨一倍，而屏幕上每次都打着绿色的 ✓。

所以这里锁四组事：
  **甲、正文只有一份** —— 正文标志行在**会随包分发的代码目录**里只许出现在 `global_agents.py`；
                         两个写入方都不许自带正文，且都必须引用本模块。
  **乙、哨兵三分支** —— created / rerendered（幂等、不涨）/ migrated（备份 + 旧内容一字不丢），
                       外加"区外是用户的"那一条；判据是**连写 N 次逐字节不变**（防翻倍）。
  **丙、管道契约**   —— stdout 上**只有动作词**（`bootstrap_agent_env.sh` 用 `$( )` 捕获它再
                       `case` 分派人类文案；多一句话，case 就落进"未知状态"分支，而屏幕上
                       看起来只是"文案变了"）。
  **丁、路径过期要出声** —— 守则里那三处绝对路径是渲染那一刻烘进去的。挪过仓 / 换过克隆之后
                       它还指着老地方，而此前 `--check` 一路 ✅（"看起来装好了其实没有"的同形）。
                       判据：反推得到烘入值（`render` 的逆运算）、两种写法不误报、失配**只提醒
                       不判红**。

安全约定：所有写操作都在**临时目录**里做（`write(root, path=...)` 显式传路径），
只有末了那条管道契约测试跑真 CLI，且重定向 HOME/USERPROFILE —— 绝不碰本机真实的
`~/.agentguard/GLOBAL_AGENTS.md`。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

# UTF-8 输出兜底（本模块带非 ASCII 文案，GBK 控制台上直接跑会崩）。
# 锁：tests/test_entry_level.py::TestAllEntriesHaveUtf8Fallback（清单机器推导，别手删）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "sandbox", "global_agents.py")
SHIM = os.path.join(ROOT, "sandbox", "install_shim.py")
BOOTSTRAP = os.path.join(ROOT, "sandbox", "bootstrap_agent_env.sh")

sys.path.insert(0, os.path.join(ROOT, "sandbox"))
import global_agents  # noqa: E402  （要先把 sandbox/ 放进 sys.path）


def _msys_bash():
    """跑 `bootstrap_agent_env.sh` 用的 bash。**不能拿 `sh`** —— 2026-09-19 CI 实测的坑。

    脚本第 16 行用 `${BASH_SOURCE[0]}` 定位工程根，而 Ubuntu 的 `/bin/sh` 是 **dash**，
    那一行直接 `Bad substitution`：脚本在 `harness: /` 上就死了，后面三条判据全变成
    「看一个根本没跑起来的脚本」—— 而屏幕上只是三条普通的红。
    （本机 git-bash 下 `sh` 恰好是 bash 的马甲，所以**本机绿、CI 红**，又一次
    「夹具写死了开发机状态」，与 `c2b6e1b` 那两条同形。）
    Windows 上还多一层：`shutil.which("bash")` 常拿到 System32 的 **WSL bash**，
    它看不懂 `C:/...` → 一律走 Git for Windows 的绝对路径（同 test_review_v6/v8_batch2）。
    """
    if os.name == "nt":
        for cand in (r"C:/Program Files/Git/bin/bash.exe",
                     r"C:/Program Files/Git/usr/bin/bash.exe"):
            if os.path.isfile(cand):
                return cand
        return None
    return shutil.which("bash")

# 「会随包分发、同事机器上会跑的」代码所在处。**不含 tests/**（测试要断言正文，那是另一回事）
# 也不含 docs/ 与 dist/（文档引用正文、包里是渲染产物）。
CODE_DIRS = ("sandbox", "harness", "checks", "guardrail_engine", "agentguard", "integrations")

# 端级模板**是要下发到每个受管仓的**（M2 的产物）—— 它们正是「正文会不会被再抄一份」的
# 高风险地带：模板看着"只是几行摘要"，往里面补一句规则正文是最自然不过的动作。
# 所以跟代码同一条判据：守则正文只许有一处，模板里一个标志行都不许有。
TEMPLATE_DIR = os.path.join("golden-patterns", "templates")

# 正文标志行：取两处**结构标题**，它们不是随口一句话，改动它们等于改了守则的骨架。
BODY_MARKERS = ("## AgentGuard 受管仓库守则（最高优先级）",
                "## 通用编码准则（Karpathy 四准则）")

# 快跑（HARNESS_FAST=1，2026-09-19 接线）：本文件只有末了一个类慢 —— 它要**真跑整脚本**
# （第 0/1b/3 节遍历 33 个受管仓，单跑实测 25.3 / 28.7s）。其余各类都是纯逻辑与文件锁，
# 最慢单例 <0.1s，所以按"同类里混着纯源码级锁就改用**方法级**"那条规矩，只装饰那一个类。
# 登记：tests/test_coverage.py::TestIsolationAndScripts::test_slow_test_files_wire_the_fast_switch
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _code_files():
    """会分发出去的文本：CODE_DIRS 下的 .py/.sh + 端级模板目录下的 .md（跳过 __pycache__）。"""
    for d in CODE_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, d)):
            dirnames[:] = [n for n in dirnames if n != "__pycache__"]
            for fn in filenames:
                if fn.endswith((".py", ".sh")):
                    yield os.path.join(dirpath, fn)
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, TEMPLATE_DIR)):
        dirnames[:] = [n for n in dirnames if n != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


class TestBodyLivesInExactlyOnePlace(unittest.TestCase):
    """甲、正文只有一份 —— 本文件存在的**首要**理由（见模块 docstring 的翻倍场景）。"""

    def test_body_markers_appear_in_exactly_one_shipped_file(self):
        """★ 本文件存在的首要理由。

        扫的范围是**会分发出去的东西**：引擎代码（.py/.sh）+ **端级模板**（.md，M2 的产物、
        要下发到每个受管仓）。后者是"再抄一份正文"的高风险地带 —— 模板看着只是几行摘要，
        往里补一句规则正文是最自然的动作，而补完就有了第二份可漂的正文。
        """
        hits = {}
        for path in _code_files():
            txt = _read(path)
            got = [m for m in BODY_MARKERS if m in txt]
            if got:
                hits[os.path.relpath(path, ROOT).replace("\\", "/")] = got
        self.assertEqual(
            sorted(hits), ["sandbox/global_agents.py"],
            "守则正文只许存在于 sandbox/global_agents.py 一处，实际出现在：%s\n"
            "要改正文就改那个文件；要在别处'再写一份'，先读 global_agents.py 的模块头 ——"
            "两份正文会让哨兵互不认账，两个写入方交替跑会把文件每跑一次涨一倍。" % sorted(hits))

    def test_writers_do_not_carry_their_own_copy(self):
        """两个写入方都不许自带正文 —— 单测上一条只保证"只有一份"，这条保证那份是**被复用的**。"""
        for path in (SHIM, BOOTSTRAP):
            txt = _read(path)
            rel = os.path.relpath(path, ROOT).replace("\\", "/")
            for m in BODY_MARKERS:
                self.assertNotIn(m, txt, "%s 里出现了守则正文 —— 正文在 global_agents.py，"
                                          "此处只许引用" % rel)
            self.assertIn("global_agents", txt,
                          "%s 应当通过 global_agents 模块写全局守则（别自己拼）" % rel)

    def test_bootstrap_captures_the_action_word(self):
        """bootstrap 靠 `$( )` 捕获动作词再 case 分派 —— 这条管道断了要在这里先响。"""
        txt = _read(BOOTSTRAP)
        self.assertIn("global_agents.py", txt)
        self.assertRegex(txt, r"GLOBAL_ACTION=\$\(",
                         "bootstrap 丢了 `GLOBAL_ACTION=$(...)` 捕获：改了它的写法就来看这里")

    def test_module_sits_next_to_its_importer(self):
        """`global_agents.py` 必须与 `install_shim.py` **同目录**。

        为什么值得一条锁：install_shim 靠 `sys.path[0]`（= 自身所在目录）import 它，且是
        `try/except ImportError` 兜住的 —— 模块被挪去 `sandbox/lib/` 之后，本机 clone 的人
        `--check` / `--uninstall` 都还正常，**只有 install 会失败**，而那正是同事照着
        《安装说明》第一次跑的那条路。挪动目录很自然（"工具类都放 lib/"），后果却只在
        别人机器上出现 —— 所以在这里钉死，挪的时候至少撞一次。

        （注：这两个文件都**不在**分发包 `sandbox/pack_release.py` 的出货清单里 ——
        包里只有 bootstrap / fix_paths / uninstall / upgrade 四支。安装说明里的
        `<HARNESS>/sandbox/install_shim.py` 指的是**从 git clone 的引擎仓**。）
        """
        self.assertEqual(os.path.dirname(MODULE), os.path.dirname(SHIM),
                         "global_agents.py 与 install_shim.py 必须同目录（install_shim 按 "
                         "sys.path[0] import 它，挪走了只有 install 会静默失败）")
        self.assertIn("sys.path", _read(SHIM),
                      "install_shim 靠显式 sys.path 补位来 import 同目录模块；改法见那里的注释")


# 本机路径的两种写法：`D:/x` 盘符形态、`/d/x` git-bash 形态。正文里**一个都不许有**。
# `(?<![A-Za-z0-9])` 不能省：`python3 __HARNESS__/…` 那种占位符形态不该被误判成盘符路径。
_BODY_DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
# MSYS 形态：`(?<![\w.>])` 三个都不能省 —— `\w` 挡住 `__HARNESS__/harness` 与「约束/边界」
# 这类中文夹斜杠（Python 的 `\w` 认 CJK，故 `束` 也算词字符）；**`>` 挡住 `<HARNESS>/a/b`**
# 那种占位符形态（实测漏了 `>` 会把 `/a/b` 当成 git-bash 路径）。
_BODY_MSYS_PATH = re.compile(r"(?<![\w.>])/[a-z]/")


class TestBodyCarriesNoMachinePath(unittest.TestCase):
    """正文里不许出现**任何人的本机路径** —— 这份正文是要发给每一台机器的。

    为什么值得单独一条（2026-09-22）：正文里的绝对路径**在维护机上看着完全正常**，
    坏的是别人那份 —— 与 `stale_note` 那个洞同形，也与同一天查出的
    「22 份产物全部带着 `D:/team-vault/…`」同源。本机真需要烘路径的地方走
    `PLACEHOLDER` 机制（渲染那一刻才填），所以判据是"有没有**字面量**"，不是"有没有路径"。

    ⚠ 与 `test_body_markers_appear_in_exactly_one_shipped_file` 是**两个方向**：
      那条管"正文只有一份"，这条管"那一份里没有别人的机器"。
    """

    def test_no_drive_letter_path_in_body(self):
        bad = [l for l in global_agents.BODY.split("\n") if _BODY_DRIVE_PATH.search(l)]
        self.assertEqual(
            [], bad,
            "守则正文里有盘符形态的本机路径：\n  %s\n"
            "正文会渲染到**每台机器**上，同事那份里这个路径是死的。"
            "要用本机路径的地方一律写成 %s（渲染时才填）—— 见模块头。"
            % ("\n  ".join(bad), global_agents.PLACEHOLDER))

    def test_no_git_bash_path_in_body(self):
        """`/d/…` 同样是本机路径，别让盘符那条成了唯一判据（`looks_like_msys` 就在防它）。"""
        bad = [l for l in global_agents.BODY.split("\n") if _BODY_MSYS_PATH.search(l)]
        self.assertEqual([], bad, "守则正文里有 git-bash 形态的本机路径：\n  %s"
                         % "\n  ".join(bad))

    def test_the_predicate_is_not_vacuous(self):
        """注入证伪：真给一条本机路径，两个判据都必须命得中；占位符形态不许误报。

        没有这一步，一个恒不命中的正则会让上面两条永远绿 —— 正是这组锁要防的那种失效。
        """
        self.assertTrue(_BODY_DRIVE_PATH.search("见 `D:/Rain/project/agentguard/README.md`"),
                        "盘符路径判据命不中")
        self.assertTrue(_BODY_MSYS_PATH.search("见 `/d/Rain/project/x.md`"),
                        "git-bash 路径判据命不中")
        for ok in ("python %s/harness/run_gate.py --list" % global_agents.PLACEHOLDER,
                   "退出码：0=通过 / 1=拦截修复 / 2=配置错",
                   "1. Think Before Coding：先梳理约束/边界",
                   "见 `<HARNESS>/x/y.md`（占位符后接二级目录 —— 少了 `>` 这条就假阳性）"):
            self.assertFalse(_BODY_DRIVE_PATH.search(ok) or _BODY_MSYS_PATH.search(ok),
                             "占位符/正文形态被误判成本机路径了：%s" % ok)


class TestSpliceBranches(unittest.TestCase):
    """乙、哨兵三分支（纯函数对拍，不碰磁盘）。"""

    def test_created_when_target_absent(self):
        out, action, need_backup = global_agents.splice(None, "BLOCK\n", "20000101")
        self.assertEqual(action, "created")
        self.assertFalse(need_backup, "新建没有旧内容可备份")
        self.assertTrue(out.startswith(global_agents.BEGIN))
        self.assertTrue(out.rstrip("\n").endswith(global_agents.END))

    def test_rerendered_keeps_outside_untouched(self):
        old = "我的头\n" + global_agents.BEGIN + "\nOLD\n" + global_agents.END + "\n我的尾\n"
        out, action, need_backup = global_agents.splice(old, "NEW\n", "20000101")
        self.assertEqual(action, "rerendered")
        self.assertFalse(need_backup, "哨兵在 = 正常路径，不需要备份")
        self.assertTrue(out.startswith("我的头\n"), "哨兵区之前一字不动")
        self.assertTrue(out.endswith("我的尾\n"), "哨兵区之后一字不动")
        self.assertIn("\nNEW\n", out)
        self.assertNotIn("OLD", out, "哨兵区内应当被整段换掉")

    def test_migrated_never_drops_old_content(self):
        """老文件（无哨兵）→ 旧内容**一字不丢**，且要备份。

        宁可多留也不能删：无法机械判断哪些是同事自己加的规矩，静默丢掉别人写的东西
        是不可接受的（这与本仓"撤回=改名不删"是同一条底线）。
        """
        legacy = "# 我自己写的守则\n- 提交信息用中文\n"
        out, action, need_backup = global_agents.splice(legacy, "BLOCK\n", "20000101")
        self.assertEqual(action, "migrated")
        self.assertTrue(need_backup)
        self.assertIn(legacy, out, "旧内容必须原样保留")
        self.assertIn(global_agents._MIGRATE_NOTE, out, "要写明下面那段是什么、为什么留着")
        self.assertLess(out.index(global_agents.END), out.index(legacy),
                        "旧内容应当在哨兵区**之后**（渲染出来才读得顺）")


class TestNoDoublingOnDisk(unittest.TestCase):
    """乙（落盘部分）—— 连写 N 次逐字节不变。**这就是防翻倍的那条锁。**"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "GLOBAL_AGENTS.md")
        self.root = "D:/Rain/project/agentguard"

    def test_write_is_idempotent(self):
        first = None
        for i in range(5):
            action, backup = global_agents.write(self.root, path=self.path,
                                                 date_tag="20000101")
            self.assertIsNone(backup, "正常路径不该产生备份")
            now = _read(self.path)
            if first is None:
                self.assertEqual(action, "created")
                first = now
            else:
                self.assertEqual(action, "rerendered", "第 %d 次应当是重渲染" % (i + 1))
                self.assertEqual(now, first,
                                 "第 %d 次写出的内容与第一次不同 —— 幂等破了（翻倍的第一步）"
                                 % (i + 1))

    def test_user_text_outside_sentinels_survives_and_block_refreshes(self):
        global_agents.write(self.root, path=self.path, date_tag="20000101")
        mine = "\n## 我自己加的规矩\n- 提交信息用中文\n"
        _write(self.path, _read(self.path) + mine)
        global_agents.write("D:/OTHER/repo", path=self.path, date_tag="20000101")
        after = _read(self.path)
        self.assertIn("提交信息用中文", after, "哨兵区之外是用户的，harness 永不触碰")
        self.assertEqual(after.count(global_agents.BEGIN), 1, "哨兵只许有一对")
        self.assertEqual(after.count(global_agents.END), 1)
        self.assertNotIn("D:/Rain/project/agentguard", after, "哨兵区内应当换成新路径")

    def test_migrated_then_next_write_is_plain_rerender(self):
        """迁移只发生一次 —— 第二次就回到 rerendered（否则就是"每跑一次涨一次"）。"""
        legacy = "# 老文件\n- 有 __HARNESS__ 占位符没渲染\n"
        _write(self.path, legacy)
        action, backup = global_agents.write(self.root, path=self.path, date_tag="20000101")
        self.assertEqual(action, "migrated")
        self.assertEqual(_read(backup), legacy, "备份必须与旧文件逐字节相同")
        one = _read(self.path)
        action, backup = global_agents.write(self.root, path=self.path, date_tag="20000101")
        self.assertEqual(action, "rerendered", "迁移只该发生一次")
        self.assertIsNone(backup)
        self.assertEqual(_read(self.path), one, "第二次不该再追加任何东西")

    def test_root_with_sed_metacharacters_is_baked_verbatim(self):
        """回归锁：原实现是 `sed -i "s|__HARNESS__|$HARNESS_WIN|g"`，路径含 `|` 或 `&` 会被吃掉。

        仓放在 `~/a&b/` 下就是活例（`&` 在 sed 替换里代表"整个匹配"）。现在用
        `str.replace`，不解释元字符 —— 这条锁住那个修复。
        """
        weird = "D:/a&b|c/d"
        global_agents.write(weird, path=self.path, date_tag="20000101")
        after = _read(self.path)
        self.assertIn(weird, after, "带 sed 元字符的路径必须原样烘进去")
        self.assertNotIn(global_agents.PLACEHOLDER, after.split(global_agents.BEGIN)[1]
                         .split(global_agents.END)[0], "哨兵区内不该残留占位符")

    def test_render_trailing_slash_is_stripped(self):
        self.assertEqual(global_agents.render("D:/x/y/"), global_agents.render("D:/x/y"))
        self.assertEqual(global_agents.render("D:\\x\\y"), global_agents.render("D:/x/y"))


class TestInspectStates(unittest.TestCase):
    """体检的五种状态（`install_shim --check` 与 `global_agents --check` 共用同一套判据）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "GLOBAL_AGENTS.md")

    def test_missing(self):
        ok, lines, brief = global_agents.inspect(self.path)
        self.assertFalse(ok)
        self.assertEqual(brief, "未生成")
        self.assertTrue(any("未生成" in l for l in lines))

    def test_no_sentinel_is_red_and_says_migration_will_keep_content(self):
        _write(self.path, "# 别人的文件\n")
        ok, lines, brief = global_agents.inspect(self.path)
        self.assertFalse(ok)
        self.assertIn("缺哨兵", brief)
        self.assertTrue(any("旧内容原样保留" in l for l in lines),
                        "要当场说清重跑不会丢东西，否则没人敢重跑")

    def test_healthy(self):
        global_agents.write("D:/h", path=self.path, date_tag="20000101")
        ok, lines, brief = global_agents.inspect(self.path)
        self.assertTrue(ok)
        self.assertEqual(brief, "已生成")
        self.assertFalse(global_agents.leftover_placeholder(self.path))

    def test_leftover_placeholder_outside_is_warned_not_failed(self):
        """区外的残留占位符：**只提醒、不判红** —— 那是刻意保留的旧内容，不能自动删。"""
        _write(self.path, "# 老内容\n路径还是 __HARNESS__/old\n")
        global_agents.write("D:/h", path=self.path, date_tag="20000101")
        warn = global_agents.leftover_placeholder(self.path)
        self.assertIsNotNone(warn, "区外残留占位符必须提醒")
        self.assertIn(global_agents.PLACEHOLDER, warn)
        ok, _lines, brief = global_agents.inspect(self.path)
        self.assertTrue(ok, "区外残留不判红（否则同事只能靠手改才能变绿）")
        self.assertIn("未渲染占位符", brief)


class TestStaleRootIsReported(unittest.TestCase):
    """丁、挪过仓之后要有个声音 —— 哨兵区里烘的路径还算不算数。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "GLOBAL_AGENTS.md")
        self.root = "D:/Rain/project/agentguard"

    def _render(self, root):
        """写出并返回哨兵区正文（`split()` 的中间那段）—— 跟渲染器同一条路，不自造正文。"""
        global_agents.write(root, path=self.path, date_tag="20000101")
        return global_agents.split(_read(self.path))[1]

    def test_baked_roots_reverses_render(self):
        """★ 反推：`render()` 是"占位符 → 路径"，这里必须是"路径 → 占位符位置"的逆运算。

        正文里三处占位符填的是**同一个**值，所以结果是去重后的一条。
        """
        self.assertEqual(global_agents.baked_roots(self._render("D:/x/y")), ["D:/x/y"])

    def test_baked_roots_sees_every_occurrence_independently(self):
        """★ 三处**各自**都要夹得到 —— 只夹中第一处也能让上一条通过（去重后同样是单元素）。

        这条是把其中一处手改成别的路径，看它能不能报出**两条**。夹不到的地方会被静默跳过
        （宁可漏报不可误报），所以"有一处失效"在真实文件上就是"改脏的那处永远没人报"。
        """
        block = self._render(self.root)
        # 挑 auto-gate 那处占位符，不挑 run_gate 那处：本文件源码里只要出现 `run_gate` 字样，
        # 就会撞上 tests/test_coverage.py::test_tests_set_harness_log_file 的**子串探针**
        # （那条锁的判据是"文件里有没有 run_gate 这个词"，用来兜住"测试真去 spawn 了 run_gate"）。
        # 这里它只是"被改脏的路径"这个字面量，本文件不 spawn 任何东西 —— 换一处同样证明得了
        # 逆运算，且不必为一个假阳性去设 HARNESS_LOG_FILE（那会带进进程级副作用）。
        dirty = block.replace("%s/harness/agents/auto-gate.py" % self.root,
                              "D:/OLD/harness/agents/auto-gate.py")
        self.assertNotEqual(dirty, block, "锚点变了：正文措辞改过就来看这里")
        self.assertEqual(global_agents.baked_roots(dirty), [self.root, "D:/OLD"])

    def test_same_root_is_silent(self):
        self.assertIsNone(global_agents.stale_note(self.path, self.root))

    def test_backslash_form_does_not_false_alarm(self):
        """install_shim 传进来的 `ROOT` 在 Windows 上是**反斜杠**形态，而渲染写的是正斜杠。

        直接比字符串会对每一台 Windows 机器误报 —— 这条锁住归一化（口径同 install_shim
        的"指针 vs GEN_PATH"）。
        """
        self._render(self.root)
        self.assertIsNone(global_agents.stale_note(self.path, self.root.replace("/", "\\")))

    def test_moved_repo_is_reported_but_does_not_turn_check_red(self):
        """★ 本组锁存在的理由：挪仓后要出声，但**不拦**（说清是谁的错不是体检的事）。"""
        self._render(self.root)
        note = global_agents.stale_note(self.path, "D:/OTHER/clone")
        self.assertIsNotNone(note, "挪了仓必须出声")
        self.assertIn(self.root, note, "要说清烘进去的是哪个路径")
        self.assertIn("D:/OTHER/clone", note, "也要说清当前仓在哪")
        self.assertIn("install_shim.py", note, "要给出可执行的修法")
        self.assertIn("哨兵区之外不受影响", note, "重跑会覆盖哨兵区 —— 这句话不说，人不敢重跑")
        ok, lines, brief = global_agents.inspect(self.path, "D:/OTHER/clone")
        self.assertTrue(ok, "路径失配**不判红**：两个克隆并存时指着另一个仓未必是错，体检不裁决")
        self.assertEqual(brief, "已生成（哨兵区路径与本仓不一致，见下）")
        self.assertTrue(any(note in l for l in lines), "提醒句要在体检报告里，不能只在摘要里提一句")

    def test_silent_when_root_unknown_or_no_sentinel(self):
        """不知道当前仓是谁 → 沉默；没有哨兵 → 那是别人的文件，不归这里管。"""
        self._render(self.root)
        self.assertIsNone(global_agents.stale_note(self.path, None))
        _write(self.path, "# 别人的文件\n")
        self.assertIsNone(global_agents.stale_note(self.path, "D:/OTHER/clone"))

    def test_two_notes_can_coexist_in_brief(self):
        """区外残留占位符 + 路径失配同时成立时，摘要不许把其中一条吃掉。"""
        self._render(self.root)
        _write(self.path, _read(self.path) + "\n# 老内容\n路径还是 __HARNESS__/old\n")
        ok, lines, brief = global_agents.inspect(self.path, "D:/OTHER/clone")
        self.assertTrue(ok)
        self.assertIn("未渲染占位符", brief)
        self.assertIn("路径与本仓不一致", brief)
        self.assertEqual(len([l for l in lines if l.startswith("⚠")]), 2)


class TestStdoutIsOnlyTheActionWord(unittest.TestCase):
    """丙、管道契约 —— `bootstrap_agent_env.sh` 用 `$( )` 捕获 stdout 的**唯一**一行。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        self.target = os.path.join(self.home, ".guardrail-engine", "GLOBAL_AGENTS.md")

    def _run(self, *args):
        env = dict(os.environ)
        env["HOME"] = self.home            # POSIX expanduser
        env["USERPROFILE"] = self.home     # Windows expanduser
        r = subprocess.run([sys.executable, MODULE] + list(args), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=env, cwd=ROOT)
        return r.returncode, r.stdout, r.stderr

    def test_first_run_prints_created_only(self):
        rc, out, err = self._run("D:/Rain/project/agentguard")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "created",
                         "stdout 上只许有动作词，实际：%r —— bootstrap 的 case 会因此落进"
                         "「未知状态」分支，而屏幕上看起来只是文案变了" % out)
        self.assertTrue(os.path.isfile(self.target))

    def test_second_run_prints_rerendered_only(self):
        self._run("D:/Rain/project/agentguard")
        rc, out, err = self._run("D:/Rain/project/agentguard")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "rerendered")

    def test_migrate_keeps_stdout_clean_and_puts_the_backup_line_on_stderr(self):
        _write(self.target, "# 老文件\n")
        rc, out, err = self._run("D:/Rain/project/agentguard")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.strip(), "migrated", "备份提示走 stderr，不许混进 stdout")
        self.assertIn("pre-sentinel-", err, "备份路径要说给用户听（走 stderr）")
        self.assertIn("旧内容原样保留", err)

    def test_msys_path_gets_a_loud_warning(self):
        """`/d/dev/harness` → 会**跑成功**但烘进去的是坏路径。必须当场喊。

        这是本文档新开的那条手敲路径带来的坑（同事在 git-bash 里的直觉写法就是 `/d/...`），
        而它坏在**别人**机器上、本机 git-bash 里打开看着还挺对 —— 与本仓反复在治的
        "看起来成功了，其实没有"同形。
        """
        self.assertTrue(global_agents.looks_like_msys("/d/dev/harness"))
        self.assertTrue(global_agents.looks_like_msys("/c/Users/x"))
        self.assertFalse(global_agents.looks_like_msys("D:/dev/harness"))
        self.assertFalse(global_agents.looks_like_msys("/home/x/harness"),
                         "Linux 常规路径不该误报")
        rc, out, err = self._run("/d/dev/harness")
        self.assertEqual(rc, 0, "只提醒不拦（Linux 上 /a/b 这种单字母顶层目录合法存在）")
        self.assertEqual(out.strip(), "created", "stdout 上仍只许有动作词")
        self.assertIn("git-bash 形态", err)
        self.assertIn("where.sh --win", err, "要给出可执行的修法，不能只说'错了'")

    def test_missing_root_is_a_usage_error_not_a_crash(self):
        # 统一解析链收口后（2026-10-11），无参数时走 find_root() 解析链：
        # env → config/harness.local.yaml → harness.path → cwd。
        # 在引擎仓内跑时 find_root() 能成功 → 渲染成功（rc=0）。
        # 只有当整条链都失败时才 rc=2。
        rc, out, err = self._run()
        # 在引擎仓内跑：find_root() 能定位到 → 渲染成功
        # 不在引擎仓内跑：find_root() 失败 → rc=2
        if rc == 0:
            # 第一次跑是 created，第二次是 rerendered
            self.assertIn(out.strip(), ("created", "rerendered"),
                          "无参数时应走解析链并渲染")
        else:
            self.assertEqual(rc, 2, "解析链全失败时才是用法错")
            self.assertEqual(out, "", "用法错误时 stdout 上不该有东西")
            self.assertIn("找不到", err)

    def test_check_does_not_write(self):
        rc, out, err = self._run("--check")
        self.assertEqual(rc, 1, "未生成时 --check 应当不通过")
        self.assertFalse(os.path.exists(self.target), "--check 只体检，绝不写文件")
        self.assertIn("未生成", out + err)


@_slow("真跑整脚本（第 0/1b/3 节遍历 33 个受管仓，单跑实测 25.3 / 28.7s）")
class TestBootstrapShellWriters(unittest.TestCase):
    """`bootstrap_agent_env.sh` 第 1 节写全局引导薄壳的三条判据（2026-09-19 实测抓出的三处病）。

    三处都是本仓反复在治的同一种病 —— **看起来成功了，其实没有**：
      ① 薄壳里写出去的路径是 git-bash 形态 `/c/...`，可读它的正是 **Windows 上的 AI 工具**。
         屏幕上一切正常（`✓` 照打），只是那个入口路由打不开。同一个坑本文件末尾指针那段
         早已治过（注释里写着「git-bash 的 `/d/...` 那边的程序打不开」），第 1 节漏了。
      ② 备份守卫判的是「含不含 `GLOBAL_AGENTS.md` 这个串」，而那串在薄壳正文里**本来就有**
         ⇒ 守卫恒假 ⇒ 备份分支**永不触发**；又因 `ln -s` 对已存在的文件必然失败，每次都落到
         `cat >` **无条件截断重写**。合起来的后果：**已经转成薄壳之后，你再往里手写的规矩
         会被静默清掉、连备份都没有**。阴性对照（拿 git HEAD 那版在完整树里实跑同一场景）：
         备份 0 个、手写行消失；修后：备份 1 个、内容一字不丢。
      ③ `ln -s` 在未开 winsymlinks 的 git-bash 上对**文件**也**返回 0 却做一次拷贝** ——
         与第 2 节 `make_dir_link` 里那个目录版是同一个坑。拿退出码当成功，新机器上就会在
         `~/.config/opencode/AGENTS.md` 留下一份 GLOBAL_AGENTS.md 的**正文副本**（实测 29 行），
         而它躺在工具**会自动加载**的位置上、且再也不会刷新 —— 正是本模块头号要防的
         「第二份可漂的正文」，只不过漂进了用户 HOME，仓库里那几条锁看不见（它们只扫仓内）。
    判据一律落在**结果**上（写进文件的是什么、备份里有什么），不看退出码、不看屏幕文案 ——
    这三处的旧代码**每一处都能打印绿色的 ✓**。

    安全约定：整跑在一个**临时 HOME** 里做，本机真实的 `~/.claude` / `~/.codex` / `~/.config`
    一律不碰；台账文件按本仓规矩重定向到临时目录（否则探针记录会写进真实
    `reports/gate_log.jsonl`，N-2 实测污染过 124 条）。
    """

    @classmethod
    def setUpClass(cls):
        cls.sh = _msys_bash()
        if cls.sh is None:
            # 说清为什么是 bash 而不是 sh —— 否则后人"顺手改回 sh"就把 CI 又弄红了
            raise unittest.SkipTest(
                "找不到可用的 bash（脚本靠 ${BASH_SOURCE[0]} 定位工程根，"
                "Ubuntu 的 /bin/sh=dash 会 Bad substitution；Windows 上须是 Git 的 bash）")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.home = os.path.join(cls.tmp.name, "home")
        # 先造出最真实的中间态：**已经转成薄壳、用户又在后面加了自己的规矩**。
        # 三条判据里有两条（改写后的路径形态、手写内容保没保住）要靠它才验得出来。
        cls.claude = os.path.join(cls.home, ".claude", "CLAUDE.md")
        _write(cls.claude,
               "# claude 全局引导（薄壳，指向统一权威源）\n"
               "本文件内容已统一迁移至：`/c/旧机器的HOME/.agentguard/GLOBAL_AGENTS.md`"
               "（所有 AI 工具共用一份，改一处全部生效）。\n"
               "\n## 我自己加的\n禁止在周五发版。\n")
        env = dict(os.environ)
        env["HOME"] = cls.home            # POSIX expanduser
        env["USERPROFILE"] = cls.home     # Windows expanduser
        env["HARNESS_LOG_FILE"] = os.path.join(cls.tmp.name, "gate_log.jsonl")
        env["HARNESS_STATS_FILE"] = os.path.join(cls.tmp.name, "stats.json")
        cls.r = subprocess.run([cls.sh, BOOTSTRAP], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env, cwd=ROOT,
                               timeout=300)

    def test_bootstrap_exits_zero(self):
        self.assertEqual(self.r.returncode, 0, self.r.stdout + self.r.stderr)

    @classmethod
    def _sh_has(cls, tool):
        """某个工具**在被测脚本所处的那个 shell 里**在不在。

        为什么不能直接用 `shutil.which(...)`（2026-09-23 评审报告 M1 第 2 例）：那问的是
        **worker python 进程的 PATH**，而被测脚本跑在 `cls.sh` 里 —— **两者的 PATH 可以不同**，
        并且是系统性地不同：MSYS 的 `bin\\bash.exe` 启动时会自动前置
        `/mingw64/bin:/usr/bin`（实测），于是脚本看得见 `cygpath`，python 看不见。
        旧写法按「看不见」走 WSL/POSIX 分支，去断言一条 `/` 开头的路径 —— 而脚本**正确地**
        写的是 Windows 形态，于是**脚本越对、测试越红**（判据问错了对象）。
        与 `_msys_bash` 的注释同一条教训：判据要问"真正执行的那个东西"。
        """
        if not cls.sh:
            return False
        r = subprocess.run([cls.sh, "-c", "command -v %s" % tool], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        return r.returncode == 0

    def test_shell_path_is_written_in_windows_form(self):
        """① 写进文件的路径形态 —— 判据是那一行的**内容**，不是屏幕上打了个 ✓。"""
        m = re.search(r"迁移至：`([^`]+)`", _read(self.claude))
        self.assertIsNotNone(m, "薄壳里没找到指针那一行")
        got = m.group(1)
        if not self._sh_has("cygpath"):
            # WSL/Linux：那边 POSIX 形态才是对的，本项不适用（与 sandbox/where.sh --win 同判据）
            self.assertTrue(got.startswith("/"), got)
            return
        self.assertRegex(got, r"^[A-Za-z]:[/\\]",
                         "薄壳里烘的是 git-bash 形态（%s）—— 而读它的是 Windows 上的工具" % got)
        self.assertFalse(got.startswith("/c/"), got)

    def test_handwritten_lines_survive_into_a_backup(self):
        """② 备份判据 —— 手写内容必须落进 .bak，而不是被静默清掉。"""
        d = os.path.dirname(self.claude)
        baks = [os.path.join(d, n) for n in os.listdir(d) if ".bak-" in n]
        self.assertEqual(len(baks), 1,
                         "手写内容没被备份（旧版实测备份 0 个：内容被覆盖且不留痕迹）")
        self.assertIn("禁止在周五发版", _read(baks[0]),
                      "备份里没有用户写的那几行 —— 备份了个寂寞")

    def test_fresh_target_never_becomes_a_body_copy(self):
        """③ 链接判据 —— 新机器上那条不许变成 GLOBAL_AGENTS.md 的**正文副本**。

        ⚠ 判据要**按平台读对**（2026-09-19 CI 实测）：**真链接恰恰是这套机制想要的结果**
        （活指针，正文改一处它跟着变）。Linux/CI 上 `ln -s` 会真的建出符号链接，读它就是
        读正文 —— 那不是「副本」，别把它判成红。要防的只有**冒充链接的普通文件**：
        `ln -s`（本机 git-bash 未开 winsymlinks 时）返回 0 却拷了一份，从此再也不会刷新，
        而它正躺在工具会自动加载的位置上 —— 这才是本模块头号要防的第二份正文。
        """
        p = os.path.join(self.home, ".config", "opencode", "AGENTS.md")
        self.assertTrue(os.path.exists(p), "新目标应当被创建（opencode 的目录原来不存在）")
        if os.path.islink(p):
            self.assertTrue(os.path.isfile(p), "链接指空 —— 等于没写")
            return
        txt = _read(p)
        for marker in BODY_MARKERS:      # 复用甲组那组标志行，别自造第二套
            self.assertNotIn(marker, txt,
                             "~/.config/opencode/AGENTS.md 是 GLOBAL_AGENTS.md 的正文副本"
                             "（`ln -s` 返回 0 却拷贝）—— 正是本模块头号要防的第二份正文")


if __name__ == "__main__":
    unittest.main()
