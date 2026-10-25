# -*- coding: utf-8 -*-
"""行尾归一 + 指纹口径锁（2026-09-18 发版复盘：「本机打的包与 CI 打的包必然内容不同」）

这条锁要钉住的东西
================================================================================
同一份提交，本机（Windows，`core.autocrlf=true`）与 CI（Linux 检出）各打一份包，
解开后逐成员对下来 **8 处**不同：5 处行尾、2 处工具链版本、1 处被前两类带跑的派生件
（`dist-info/RECORD` 是别的成员的字节哈希）。实测现场与归类写在 `sandbox/eol_text.py`
模块头，这里只管**钉住修法**。

它为什么值得一条锁：G3 那条「同一个版本号下已经存在过一份**内容不同**的包」是同事
「装了新包却还在跑旧包」的**唯一**前兆，而它的唯一判据是整包指纹。平台噪音让它
逢包必响 —— 人很快学会无视它，真正该看见的那一次也就看不见了。漏报与噪音是同一种失败。

锁的层次（每一条都对应一个**具体的错法**）
================================================================================
  ① 对齐规则本身 —— 只有索引侧 `lf` 才折，其余一律原样。
     错法：`!= "crlf"` 之类的写法会把 `mixed`/`none`/二进制一起卷进来（多折=改内容）。
  ② 真 git 临时仓库 —— 索引 LF / 工作树 CRLF 时读出来必须是 LF。
     错法：读了工作树字节（那正是要修的东西）；这条不靠替身，真起一个仓库。
  ③ 指纹口径 —— 只差行尾的两份，指纹必须**相同**；差一个字符的，必须**不同**。
     后半条是**阴性对照**：只断言"相同"的话，一个恒返回同一个值的实现照样全绿。
  ④ 两个支路同口径 —— 同一个包，从**磁盘**算与从**zip**算必须得到同一份清单。
     错法：只归一磁盘支路 → 本机重打包与 CI 那份包仍然算不到一起（而 CI 那份正是
     "上一次的包"，比对的对象就是它）。
  ⑤ 第 ⑧ 条不假红 —— 包内工具是 LF、仓库工作树是 CRLF 时，不得报"不是同一份"。
     错法：校验器按**工作树字节**比 → 在 Windows 上把一份**本来正确**的包判成坏的。
     假红与假绿同害：都会把这条锁训成噪音。

依赖：纯标准库（`zipfile` / `subprocess`）。②⑤ 需要 git —— **没有 git 就 skip，不假绿**。
"""
from __future__ import unicode_literals

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402
import zipfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SANDBOX = os.path.join(ROOT, "sandbox")


def _load(name, filename):
    """按路径加载（`sandbox/` 不是包）。取**真身**而不是抄一遍逻辑 —— 抄一遍就是给副本设锁。"""
    spec = importlib.util.spec_from_file_location(name, os.path.join(SANDBOX, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


et = _load("_eol_text_under_test", "eol_text.py")
packer = _load("_pack_release_under_test", "pack_release.py")
vr = _load("_verify_release_under_test", "verify_release.py")


def _read_bytes(p):
    with open(p, "rb") as f:
        return f.read()


def _write_bytes(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def _b(s):
    """中文内容的字节串：Python 3 的 `b"…"` 只许 ASCII，带中文的字面量直接是语法错误。"""
    return s.encode("utf-8")


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="packeol_")

    def tearDown(self):
        _rmtree_force(self.tmp)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)


# --------------------------------------------------------------------- ① 对齐规则

class TestAlignRule(unittest.TestCase):
    """只有索引说 `lf` 才折 —— 少折查得出来，猜错查不出来。"""

    def test_index_lf_is_collapsed(self):
        self.assertEqual(et.align_to_index(b"a\r\nb\r\n\r\n", "lf"), b"a\nb\n\n")

    def test_other_tokens_are_left_alone(self):
        raw = b"a\r\nb\r\n"
        for tok in ("crlf", "mixed", "none", "-text", "", None):
            self.assertEqual(et.align_to_index(raw, tok), raw,
                             "索引侧标记 %r 不该动内容 —— 它要么是「这个人就要 CRLF」、"
                             "要么是「说不清」，两种都不该由我们替他决定" % (tok,))

    def test_lone_cr_is_not_touched(self):
        """只折 `\\r\\n`，不把单独一个 `\\r` 也当行尾（那是 Mac Classic，本仓没有）。"""
        self.assertEqual(et.align_to_index(b"a\rb\r\n", "lf"), b"a\rb\n")

    def test_binary_sniff_is_git_s_own_criterion(self):
        """`looks_binary` 与 git `text=auto` 同判据：**前 8KB** 里有 NUL 才算二进制。"""
        self.assertTrue(et.looks_binary(b"\x89PNG\r\n\x1a\n\x00\x00"))
        self.assertFalse(et.looks_binary(b"a\r\nb\r\n"))
        self.assertFalse(et.looks_binary(b"x" * 9000 + b"\x00"),
                         "NUL 出现在 8KB 之后 —— git 也判它文本，两处口径必须一致")

    def test_canon_keeps_binary_verbatim(self):
        raw = b"\x00\r\n\x00\r\n"
        self.assertEqual(et.canon_bytes(raw), raw, "二进制里折 CRLF 就是**改内容**")
        self.assertEqual(et.canon_bytes(b"a\r\nb"), b"a\nb")


# --------------------------------------------------------------------- ② 真 git 仓库

@unittest.skipIf(not shutil.which("git"), "无 git：本条的判据就是 git 索引，不假绿")
class TestRealGitCheckout(_Tmp):
    """索引 LF + 工作树 CRLF（= 本机 `core.autocrlf=true` 的常态）→ 读出来必须是 LF。"""

    def setUp(self):
        super(TestRealGitCheckout, self).setUp()
        self.repo = self.p("repo")
        os.makedirs(self.repo, exist_ok=True)
        self._git("init", "-q")
        _write_bytes(self.p("repo", "doc.md"), b"line1\nline2\n")
        _write_bytes(self.p("repo", "bin.dat"), b"\x00\r\n\x00\r\n")
        self._git("add", "-A", "-f")
        self._git("commit", "-qm", "base")
        # 工作树改成 CRLF —— 索引不动（这正是 `core.autocrlf=true` 下的日常形态）
        _write_bytes(self.p("repo", "doc.md"), b"line1\r\nline2\r\n")
        self.eol = et.index_eol_map(self.repo)

    def _git(self, *args):
        r = subprocess.run(["git", "-C", self.repo,
                            "-c", "user.email=t@local", "-c", "user.name=t",
                            "-c", "commit.gpgsign=false", "-c", "core.hooksPath="] + list(args),
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, "git %s 失败：%s" % (args[0], r.stderr))
        return r

    def test_map_reads_the_index_side(self):
        self.assertEqual(self.eol.get("doc.md"), "lf",
                         "取到的必须是**索引侧**标记：判据是「git 会把它存成什么」，"
                         "不是「它现在躺在盘上是什么」（后者正是要修的东西）")
        self.assertEqual(self.eol.get("bin.dat"), "-text")

    def test_read_shipped_gives_lf_not_worktree_bytes(self):
        got = et.read_shipped(self.p("repo", "doc.md"), self.repo, self.eol)
        self.assertEqual(got, b"line1\nline2\n",
                         "读出了工作树的 CRLF —— 那本机打的包与 CI 的包就还是两份内容")
        self.assertEqual(_read_bytes(self.p("repo", "doc.md")), b"line1\r\nline2\r\n",
                         "夹具前提：工作树此刻确实是 CRLF（否则本条测了个空）")

    def test_copy_shipped_writes_lf(self):
        dst = self.p("out", "doc.md")
        et.copy_shipped(self.p("repo", "doc.md"), dst, self.repo, self.eol)
        self.assertEqual(_read_bytes(dst), b"line1\nline2\n")

    def test_binary_is_untouched(self):
        self.assertEqual(et.read_shipped(self.p("repo", "bin.dat"), self.repo, self.eol),
                         b"\x00\r\n\x00\r\n", "二进制被折了行尾 = 内容被改了")

    def test_untracked_file_is_left_alone(self):
        """不在索引里 = 不知道 git 会怎么存它 → 原样。不猜。"""
        _write_bytes(self.p("repo", "new.txt"), b"a\r\nb\r\n")
        self.assertEqual(et.read_shipped(self.p("repo", "new.txt"), self.repo), b"a\r\nb\r\n")


# --------------------------------------------------------------------- ③ 指纹口径

def _wheel(path, eol=b"\n", generator=b"setuptools (84.0.0)", record=b"x", pkg=b"V = 1\n"):
    """造一个最小轮子：四个成员正好覆盖指纹口径的四条规则。"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("agentguard/__init__.py", pkg)
        z.writestr("agentguard-1.0.dist-info/METADATA",
                   b"Metadata-Version: 2.4" + eol + b"Name: agentguard" + eol)
        z.writestr("agentguard-1.0.dist-info/WHEEL",
                   b"Wheel-Version: 1.0" + eol + b"Generator: " + generator + eol)
        z.writestr("agentguard-1.0.dist-info/RECORD", record)
    return path


class TestFingerprintIgnoresPlatformNoise(_Tmp):
    """③ 只差行尾 → 指纹相同；差一个字符 → 指纹必须不同（阴性对照）。"""

    def _stage(self, name, text_bytes):
        d = self.p(name)
        _write_bytes(os.path.join(d, "OPENCLAW.md"), text_bytes)
        _write_bytes(os.path.join(d, "升级.py"), b"print(1)\n")
        return d

    def test_eol_only_difference_is_invisible(self):
        a = packer.pack_fingerprint(self._stage("a", _b("# 标题\n正文\n")))
        b = packer.pack_fingerprint(self._stage("b", _b("# 标题\r\n正文\r\n")))
        self.assertEqual(a, b,
                         "只差行尾的两份内容算出了不同的指纹 → 本机重打包照样亮"
                         "「同版本号、内容不同」，G3 那条告警被训成噪音")

    def test_a_real_change_still_moves_it(self):
        """阴性对照：没有这条，一个恒返回同一值的实现也能让上面那条全绿。"""
        a = packer.pack_fingerprint(self._stage("a", _b("# 标题\n正文\n")))
        c = packer.pack_fingerprint(self._stage("c", _b("# 标题\n正文。\n")))
        self.assertNotEqual(a, c, "改了一个字符指纹却不动 —— 那指纹就是装饰")

    def test_binary_difference_is_not_canonicalized_away(self):
        a = self._stage("a", b"\x00\r\n")
        b = self._stage("b", b"\x00\n")
        self.assertNotEqual(packer.pack_fingerprint(a), packer.pack_fingerprint(b),
                            "二进制里折 CRLF 就是改内容，改内容必须动指纹")


class TestWheelContentFingerprint(_Tmp):
    """轮子成员的三条口径：折行尾、剔 RECORD、去 `Generator:` 行。"""

    def test_member_eol_is_ignored(self):
        a = packer.wheel_content_sha1(_wheel(self.p("a.whl"), eol=b"\n"))
        b = packer.wheel_content_sha1(_wheel(self.p("b.whl"), eol=b"\r\n"))
        self.assertEqual(a, b,
                         "Windows 上 setuptools 以文本模式写 METADATA（连**头字段**都带 \\r），"
                         "CI 上不带 —— 这一处不折，两地的轮子指纹永远不同")

    def test_toolchain_generator_line_is_ignored(self):
        a = packer.wheel_content_sha1(_wheel(self.p("a.whl"), generator=b"setuptools (84.0.0)"))
        b = packer.wheel_content_sha1(_wheel(self.p("b.whl"), generator=b"setuptools (79.0.1)"))
        self.assertEqual(a, b, "`Generator:` 写的是构建机的 setuptools 版本（实测 79/84 两值），"
                               "它不在这份源码的管辖内，不该混进「这份包是不是我做的」这个判断")

    def test_only_that_line_is_dropped(self):
        got = et.canon_wheel_member("x-1.0.dist-info/WHEEL",
                                    b"Wheel-Version: 1.0\nGenerator: setuptools (84.0.0)\n"
                                    b"Root-Is-Purelib: true\n")
        self.assertEqual(got, b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n",
                         "整份 WHEEL 被换成常量了 —— 那等于不再看这个文件")

    def test_record_is_excluded(self):
        a = packer.wheel_content_sha1(_wheel(self.p("a.whl"), record=b"aaa"))
        b = packer.wheel_content_sha1(_wheel(self.p("b.whl"), record=b"bbb"))
        self.assertEqual(a, b, "RECORD 是别的成员的字节哈希，别的成员一变它必然跟着变 —— "
                               "留在指纹里只是把同一个根因算两遍")

    def test_code_change_still_moves_it(self):
        """阴性对照：上面三条都是"必须相同"，没有这条，一个恒返回常量的实现也能全绿。"""
        a = packer.wheel_content_sha1(_wheel(self.p("a.whl"), pkg=b"V = 1\n"))
        b = packer.wheel_content_sha1(_wheel(self.p("b.whl"), pkg=b"V = 2\n"))
        self.assertNotEqual(a, b, "轮子里的源码变了指纹却不动 —— 那 G3 就再也拦不住漂移")


# --------------------------------------------------------------------- ④ 两个支路同口径

class TestBothBranchesAgree(_Tmp):
    """同一个包，从**磁盘**算与从**zip**算必须是同一份清单。

    `pack_files()` 的两个支路各服务一个对象：磁盘支路算"本次这份"，zip 支路算"上一次那份"
    ——而"上一次那份"在 CI 形态下正是别人（Linux）打出来的。只归一一边的话，比对的两个数
    仍然来自两套口径，等于没修。
    """

    def _build(self):
        stage = self.p("stage", "pkg-1.0")
        _write_bytes(os.path.join(stage, "OPENCLAW.md"), _b("# 标题\n正文\n"))
        _write_bytes(os.path.join(stage, "安装说明.md"), _b("# 说明\n"))
        _write_bytes(os.path.join(stage, "agentguard-1.0-py3-none-any.whl"),
                     _read_bytes(_wheel(self.p("src.whl"))))
        zp = self.p("pkg-1.0.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            arc = "pkg-1.0/OPENCLAW.md"
            # zip 里那份**故意写成 CRLF** —— 模拟「上一份包是 Windows 上打的」
            z.writestr(arc, _b("# 标题\r\n正文\r\n"))
            z.writestr("pkg-1.0/安装说明.md", _b("# 说明\n"))
            z.writestr("pkg-1.0/agentguard-1.0-py3-none-any.whl",
                       _read_bytes(_wheel(self.p("src.whl"))))
        return stage, zp

    def test_disk_and_zip_yield_the_same_inventory(self):
        stage, zp = self._build()
        from_disk = packer.pack_files(stage)
        from_zip = packer.pack_files(zp)
        self.assertEqual(sorted(from_disk), sorted(from_zip))
        for k in sorted(from_disk):
            self.assertEqual(from_disk[k], from_zip[k],
                             "%s 在两个支路上算出了不同的哈希 —— 一边归一了、另一边没有，"
                             "于是「本机这份」与「CI 那份」永远比不到一起" % k)

    def test_the_two_stages_really_share_a_fingerprint(self):
        stage, zp = self._build()
        self.assertEqual(packer.pack_fingerprint(stage), packer.fingerprint(packer.pack_files(zp)),
                         "整包指纹对不上 —— 这正是「本机重打包 vs CI 那份包」的现场")


# --------------------------------------------------------------------- ⑤ 第 ⑧ 条不假红

@unittest.skipIf(not shutil.which("git"), "无 git：本条的夹具就是一个 git 检出，不假绿")
class TestShippedToolCheckIsNotFalselyRed(_Tmp):
    """包内工具是 LF（打包器写出来的），仓库工作树是 CRLF（Windows 检出）—— 是**同一份**。

    第 ⑧ 条（`_check_shipped_tools`）的价值是"改了工具、忘了重打包"时拦住；它一旦按
    **工作树字节**比，就会在 Windows 上把一份本来正确的包判成坏的。假红不是"保守"，
    它和假绿一样磨损可信度。
    """

    def setUp(self):
        super(TestShippedToolCheckIsNotFalselyRed, self).setUp()
        if not shutil.which("git"):
            return
        self.repo = self.p("repo")
        os.makedirs(os.path.join(self.repo, "sandbox"), exist_ok=True)
        src = os.path.join(self.repo, "sandbox", "upgrade.py")
        _write_bytes(src, b"print('tool')\n")
        for args in (["init", "-q"], ["add", "-A", "-f"], ["commit", "-qm", "base"]):
            r = subprocess.run(["git", "-C", self.repo, "-c", "user.email=t@local",
                                "-c", "user.name=t", "-c", "commit.gpgsign=false",
                                "-c", "core.hooksPath="] + args,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, "夹具 git %s 失败：%s" % (args[0], r.stderr))
        _write_bytes(src, b"print('tool')\r\n")          # 工作树改成 CRLF，索引不动
        self.src = src

    def _problems(self, stage_bytes):
        stage = self.p("stage")
        _write_bytes(os.path.join(stage, "upgrade.py"), stage_bytes)
        saved_root, saved_tools = vr.ROOT, vr.SHIPPED_TOOLS
        vr.ROOT = self.repo
        vr.SHIPPED_TOOLS = {"upgrade.py": os.path.join("sandbox", "upgrade.py")}
        try:
            pr = vr.Problems()
            vr._check_shipped_tools(stage, pr)
            return pr.items
        finally:
            vr.ROOT, vr.SHIPPED_TOOLS = saved_root, saved_tools

    def test_lf_in_pack_against_crlf_worktree_is_not_reported(self):
        self.assertEqual(_read_bytes(self.src), b"print('tool')\r\n",
                         "夹具前提：工作树此刻确实是 CRLF（否则本条测了个空）")
        self.assertEqual(self._problems(b"print('tool')\n"), [],
                         "把一份**正确的**包判成坏的 —— 校验器按工作树字节比了，"
                         "而工作树的行尾是检出平台的事，不是包的事")

    def test_a_genuinely_different_tool_is_still_reported(self):
        """阴性对照：不假红的前提是它仍然认得**真的**不一样。"""
        self.assertTrue(self._problems(b"print('OTHER')\n"),
                        "真换了一支脚本都不报 —— 那第 ⑧ 条就不是锁了")
