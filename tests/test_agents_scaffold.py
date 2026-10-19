# -*- coding: utf-8 -*-
"""`sandbox/agents_scaffold.py` 的回归锁 —— 一个**会写文件**的工具，三条线必须钉死。

为什么要有这个文件（2026-09-21）
--------------------------------------------------------------------------------
`agents_audit.py` 的安全来自「它没有任何写入路径」。生成器打破了那个性质（它要落盘），
所以它的安全**不能靠"审下来没问题"，只能靠这三条断言**：

  甲、**绝不写客户仓** —— `--emit` 指到受管仓（含其子目录）里必须当场拒绝。
                        这条是 §六 ① 的机械保证，不是提示语。
  乙、**不许丢知识** —— 落盘前逐行自检；差一行整仓不写。这里连"自检自己有没有失效"
                        一起钉：故意喂一个会丢行的产物，必须报出来。
  丙、**逐仓保真** —— 行尾（CRLF/LF）与 BOM 跟源文件一致；源文件跑完一个字节都没动。
                        行尾统一会产出「整文件重写」的 diff，审阅的人第一眼就看不下去。

安全约定：全部夹具在 `TemporaryDirectory` 里，用完自动收（不 mkdtemp 裸留）。
"""
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sandbox"))
import agents_audit as AA   # noqa: E402
import agents_scaffold as SC  # noqa: E402

GATE_LINE = "改完代码仓库根执行：`python <H>/harness/agents/auto-gate.py`"
PROJ_SENTENCE = "某平台 PC 端，Vue3 + TypeScript。712 = 712 所。"
VAULT_LINE = "知识库：`D:/Vault/712/`（工单必读 `00.md`）"

SRC = "\n".join([
    "# 测试仓 AGENTS.md",
    "",
    "## 项目",
    PROJ_SENTENCE,
    "",
    "## 门禁（机械强制）",
    GATE_LINE,
    "",
    "## 文档",
    VAULT_LINE,
    "",
])


def _sink():
    """一张最小归宿表：门禁那行有归宿，其余没有。键是**归一化后**的行。"""
    return {AA.norm(GATE_LINE): {"file": "templates/AGENTS_pc_slim.md",
                                 "anchor": "auto-gate.py", "why": "夹具", "lines": []}}


def _fingerprint(path):
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name
        self.repo = os.path.join(self.d, "repo", "AGENTS.md")
        os.makedirs(os.path.dirname(self.repo))
        self.out = os.path.join(self.d, "out")

    def write_repo(self, text, eol="\n", bom=False):
        data = text.replace("\n", eol).encode("utf-8")
        if bom:
            data = b"\xef\xbb\xbf" + data
        with open(self.repo, "wb") as f:
            f.write(data)
        return self.repo

    def judged(self, text=None, eol="\n", bom=False):
        """走**真判据**（`AA.judge`）得到一条 judged 记录，不手搓 dict。"""
        self.write_repo(text if text is not None else SRC, eol=eol, bom=bom)
        return AA.judge("t_pc", self.repo, _sink(), set())

    def run_emit(self, cands, check_only=False, force=False):
        """两股流都收：拒绝理由是走 stderr 的（工具的诊断一贯走 stderr）。"""
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = SC.emit(self.out, cands, {"sink": _sink()},
                         force=force, check_only=check_only)
        return rc, buf.getvalue() + err.getvalue()

    def produced(self, key="t_pc"):
        # 必须 with 收口：靠 GC 兜底关文件，句柄会在**别的**用例名下被回收，于是日志里
        # 出现「Exception ignored in: <_io.FileIO …>」+ ResourceWarning。
        #
        # ⚠ 2026-09-21 实测更正（原先这里写「本机普通档看不见」——**那句是错的**）：
        # 本机**看得见**，带不带 `-W error::ResourceWarning` 都看得见（实测各 14 行命中）。
        # 真不对称在**判据**上：本机 `unittest` 打完这些行**照样收在 `OK`**（噪音，翻过去就没了）；
        # CI 那一步拿 `grep` 把同一批行**变成 rc=1**（`ci.yml:87`）。
        # 所以别再说「本机看不见」—— 本机看得见，只是**没人在本机拿它判红**。
        with io.open(os.path.join(self.out, key, "AGENTS.md"),
                     encoding="utf-8", newline="") as f:
            return f.read()


class TestNeverWritesIntoAClientRepo(_Fixture):
    """甲、§六 ① 的机械保证：`--emit` 指进受管仓就拒绝，连子目录一起。"""

    def test_outdir_equal_to_a_managed_repo_is_refused(self):
        real = SC.managed_roots
        SC.managed_roots = lambda: [os.path.normcase(os.path.realpath(os.path.dirname(self.repo)))]
        self.addCleanup(setattr, SC, "managed_roots", real)
        ok, why = SC.safe_outdir(os.path.dirname(self.repo))
        self.assertFalse(ok, "指到受管仓根上竟然通过了")
        self.assertIn("受管仓", why)

    def test_outdir_inside_a_managed_repo_is_refused(self):
        real = SC.managed_roots
        SC.managed_roots = lambda: [os.path.normcase(os.path.realpath(self.d))]
        self.addCleanup(setattr, SC, "managed_roots", real)
        ok, _why = SC.safe_outdir(os.path.join(self.d, "src", "sub"))
        self.assertFalse(ok, "受管仓的**子目录**必须一起挡（产物同样会随仓提交进去）")

    def test_a_sibling_directory_is_allowed(self):
        """反向：不在任何受管仓里就不许误拒 —— 否则这工具没法用。"""
        real = SC.managed_roots
        SC.managed_roots = lambda: [os.path.normcase(os.path.realpath(os.path.dirname(self.repo)))]
        self.addCleanup(setattr, SC, "managed_roots", real)
        ok, why = SC.safe_outdir(self.out)
        self.assertTrue(ok, why)

    def test_emit_refuses_and_writes_nothing(self):
        real = SC.managed_roots
        SC.managed_roots = lambda: [os.path.normcase(os.path.realpath(self.d))]
        self.addCleanup(setattr, SC, "managed_roots", real)
        r = self.judged()
        rc, out = self.run_emit(SC.candidates({"judged": [r]}))
        self.assertEqual(rc, 2, "越线必须当场失败")
        self.assertFalse(os.path.exists(self.out), "越线了还建了目录：%s" % os.listdir(self.d))
        self.assertIn("拒绝", out + "")

    def test_emit_does_not_touch_the_source_repo(self):
        r = self.judged()
        before = _fingerprint(self.repo)
        self.run_emit(SC.candidates({"judged": [r]}))
        self.assertEqual(_fingerprint(self.repo), before, "生成器动了源文件")


class TestRefusesToLoseKnowledge(_Fixture):
    """乙、自检必须真的会响 —— 包括「自检自己失效」这一种。"""

    def test_lost_lines_catches_a_dropped_keep_line(self):
        self.assertEqual(SC.lost_lines(["a", "b"], ["a", "b"], []), [])
        self.assertEqual(SC.lost_lines(["a"], ["a", "b"], []), ["b"])

    def test_lost_lines_agrees_with_product_line_in_both_directions(self):
        """★ 自检若用另一套写法，就是「自己给自己发合格证」。

        2026-10-05 起换算**不再在本模块镜像** —— `lost_lines` 直接问
        `agents_audit.product_line()`（`target_form` 拼装时调的是同一个函数）。
        这里两向钉住：把源行换算后的形态喂进去必须**判为没丢**，
        喂源行**原样**必须**判为丢了** —— 否则这条自检对换算整个失明。
        """
        # 期望值**写死在夹具里** —— 从被测函数现取会让这条断言变成同义反复。
        cases = [
            # 搬迁行：摘掉 `知识库：` 标签
            ("知识库：`D:/x/`（工单必读 `00.md`）", "`D:/x/`（工单必读 `00.md`）"),
            # 留守行：只换知识库根，其余逐字不动
            ("工单必读在 `D:/KnowledgeVault/customers/ntrx/`",
             "工单必读在 `<VAULT>/customers/ntrx/`"),
        ]
        for src, want in cases:
            self.assertEqual(want, AA.product_line(src), "换算式子变了：%s" % src)
            self.assertEqual([], SC.lost_lines([want], [src], []),
                             "自检没认出 `product_line` 的产物形态：%s" % src)
            self.assertEqual([want], SC.lost_lines([src], [src], []),
                             "自检对「没换算就比对」（产物里是源行原样）竟然放行了：%s" % src)
        # 既没标签也没知识库根的那种：换算前后同形，两侧怎么比都过。
        self.assertEqual("别的行", AA.product_line("别的行"))
        self.assertEqual([], SC.lost_lines(["别的行"], ["别的行"], []))

    def test_a_repo_that_would_lose_a_line_is_not_written(self):
        """把 `## 项目` 那句从产物里挖掉 → 这一仓必须**一个文件都不落**。"""
        r = self.judged()
        real = AA.target_form
        AA.target_form = lambda *a, **k: ["# 标题", "", "## 端级规则", "见 …"]   # 全丢了
        self.addCleanup(setattr, AA, "target_form", real)
        rc, out = self.run_emit([dict(r, why=[])])
        self.assertEqual(rc, 1, "丢了行竟然按成功退出")
        self.assertIn("自检不过", out)
        self.assertFalse(os.path.exists(os.path.join(self.out, "t_pc", "AGENTS.md")),
                         "自检不过还落盘了")
        self.assertIn(PROJ_SENTENCE, out, "报错里要点出丢的是哪一行")


class TestCandidateCriteria(_Fixture):
    """候选判据：圈出来的必须是「本仓独有 ≤1 行且无表格行」那一类。"""

    def test_the_thin_repo_is_a_candidate(self):
        c = SC.candidates({"judged": [self.judged()]})
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["why"], [], "薄仓不该被拒：%s" % c[0]["why"])

    def test_a_repo_with_real_knowledge_is_refused(self):
        text = SRC.replace(GATE_LINE, GATE_LINE + "\n本仓独有的一条知识\n再来一条")
        c = SC.candidates({"judged": [self.judged(text)]})
        self.assertTrue(any("本仓独有" in w for w in c[0]["why"]), c[0]["why"])

    def test_a_repo_whose_kept_lines_include_a_table_row_is_refused(self):
        """表体留下、表头被结构行规则吃掉 = 孤儿表。宁可不产出。"""
        text = SRC.replace(GATE_LINE, GATE_LINE + "\n| 需要什么 | 打开 |\n| a | b |")
        c = SC.candidates({"judged": [self.judged(text)]})
        self.assertTrue(any("表格行" in w for w in c[0]["why"]), c[0]["why"])

    def test_only_filters_by_key(self):
        r = self.judged()
        self.assertEqual(SC.candidates({"judged": [r]}, only={"nope"}), [])
        self.assertEqual(len(SC.candidates({"judged": [r]}, only={"t_pc"})), 1)


class TestOutputKeepsTheSourceEncoding(_Fixture):
    """丙、逐仓保真：行尾与 BOM 跟源文件走。"""

    def _emit_one(self, eol, bom):
        r = self.judged(eol=eol, bom=bom)
        rc, _out = self.run_emit(SC.candidates({"judged": [r]}))
        self.assertEqual(rc, 0)
        with open(os.path.join(self.out, "t_pc", "AGENTS.md"), "rb") as f:
            raw = f.read()
        return raw

    def test_crlf_source_stays_crlf(self):
        raw = self._emit_one("\r\n", False)
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n\n", raw.replace(b"\r\n", b""), "混进了裸 LF")

    def test_lf_source_stays_lf(self):
        raw = self._emit_one("\n", False)
        self.assertNotIn(b"\r", raw, "LF 的源被写成了 CRLF —— 那 4 个仓的 diff 会变成整文件重写")

    def test_bom_is_preserved(self):
        self.assertTrue(self._emit_one("\n", True).startswith(b"\xef\xbb\xbf"))

    def test_bom_is_not_invented(self):
        self.assertFalse(self._emit_one("\n", False).startswith(b"\xef\xbb\xbf"))

    def test_read_source_round_trips(self):
        self.write_repo(SRC, eol="\r\n", bom=True)
        text, eol, bom = SC.read_source(self.repo)
        self.assertEqual(eol, "\r\n")
        self.assertTrue(bom)
        self.assertIn(PROJ_SENTENCE, text)


class TestEmittedShape(_Fixture):
    """产物本身：项目身份在端级指针之前，且逐行都在。"""

    def test_project_block_precedes_the_end_level_pointer(self):
        r = self.judged()
        self.run_emit(SC.candidates({"judged": [r]}))
        out = self.produced()
        self.assertLess(out.index("## 项目"), out.index("## 端级规则"))
        self.assertLess(out.index("## 端级规则"), out.index("## 本仓独有"))

    def test_every_kept_line_is_in_the_product(self):
        r = self.judged()
        self.run_emit(SC.candidates({"judged": [r]}))
        out = self.produced()
        for l in r["keep_lines"]:
            self.assertIn(l.rstrip(), out, "搬丢了：%s" % l)
        self.assertIn("`D:/Vault/712/`（工单必读 `00.md`）", out,
                      "搬迁行只许摘掉 `知识库：` 标签，括号那句必须留")

    def test_manifest_lists_every_deleted_line_with_its_home(self):
        r = self.judged()
        self.run_emit(SC.candidates({"judged": [r]}))
        with io.open(os.path.join(self.out, "_manifest.md"), encoding="utf-8") as f:
            mf = f.read()
        self.assertIn(GATE_LINE, mf)
        self.assertIn("templates/AGENTS_pc_slim.md", mf, "删掉的行没标归宿")
        self.assertIn(PROJ_SENTENCE, mf, "待迁行没进清单")

    def test_check_only_writes_nothing(self):
        r = self.judged()
        rc, out = self.run_emit(SC.candidates({"judged": [r]}), check_only=True)
        self.assertEqual(rc, 0)
        self.assertIn("干跑", out)
        self.assertFalse(os.path.exists(os.path.join(self.out, "t_pc")), "干跑还落盘了")

    def test_a_non_empty_foreign_directory_is_refused(self):
        r = self.judged()
        os.makedirs(self.out)
        with io.open(os.path.join(self.out, "别人的东西.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        rc, _out = self.run_emit(SC.candidates({"judged": [r]}))
        self.assertEqual(rc, 2, "往别人造过的目录里写要拒绝（或明确 --force）")


# ── 知识库根只能以 `<VAULT>` 占位出货（2026-09-22）─────────────────────────────
# ⚠ 夹具必须用**真的**知识库根（`AA.VAULT_ROOTS` 里的那个），不能用上面那个 `VAULT_LINE`
#   （`D:/Vault/712/`）—— 它不在 `VAULT_ROOTS` 里，压根走不到 `portable()`。
#   `TestEmittedShape.test_every_kept_line_is_in_the_product` 里那句「括号那句必须留」
#   用的就是它，于是那条断言锁的是一个**没被这次改动碰过**的代码路径：
#   看起来有覆盖，实际零覆盖。所以下面 `setUp` 里当场把「夹具踩在 VAULT_ROOTS 上」
#   断言出来 —— 判据一旦空转，它自己先红，而不是安静地绿着。
# 本机路径的**形态**（不认名单）：盘符形态、git-bash 形态。
# `(?<![A-Za-z0-9])` 不能省：产物里 `见 \`<HARNESS>/golden-patterns/…\`` 那种占位符形态
# 不该被误判成盘符路径。
_DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
# `(?<![\w.>])` 三个都不能省：`\w` 挡中文夹斜杠（Python 的 `\w` 认 CJK，故 `约束/边界`
# 会命中），`.` 挡 `../x/`；**`>` 挡 `<HARNESS>/a/b` 那种占位符**——实测漏了 `>` 就会把
# `<HARNESS>/x/y.md` 里的 `/x/y.md` 当成 git-bash 路径（假阳性）。判据一旦开始误报，
# 人就不看它了，那比不报更糟（同 `global_agents.stale_note` 的取舍）。
_MSYS_PATH = re.compile(r"(?<![\w.>])/[a-z]/")
# 取**整条**路径（不是只取 `D:/` 那三个字符）—— 用来做「产物有没有新增本机路径」的集合比较。
# 收尾的字符类把反引号、括号、中文标点都当边界，所以 `` `D:/Vault/712/`（工单必读…） ``
# 夹出来的是 `D:/Vault/712/`，不含后面那半句。
_MACHINE_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|(?<![\w.>])/[a-z]/)[^\s`'\"「」（）()\[\]，。；、|]*")


def _machine_paths(text):
    """文本里出现的全部本机路径（盘符形态 + git-bash 形态），按出现顺序。"""
    return _MACHINE_PATH.findall(text)

REAL_VAULT_LINE = ("知识库：`D:/KnowledgeVault/concepts/customers/ntrx/`"
                   "（工单必读 `00_工单必读.md`）")
VAULT_SRC = "\n".join([
    "# 测试仓 AGENTS.md",
    "",
    "## 项目",
    PROJ_SENTENCE,
    "",
    "## 门禁（机械强制）",
    GATE_LINE,
    "",
    "## 文档",
    REAL_VAULT_LINE,
    "",
])


class TestVaultRootNeverShipsAsAMachinePath(_Fixture):
    """知识库根**只许**以 `<VAULT>` 占位出货 —— 不许出现维护机的绝对路径。

    起因（**真出过**，2026-09-22 实测）：`target_form` 早先只把**引擎仓**路径换成
    `<HARNESS>`，知识库根这一类漏了 —— **22 份产物全部**带着 `D:/KnowledgeVault/…`。
    那是维护机上的目录：同事 clone 客户仓后这一行指向一个不存在的地方，而屏幕上一切正常
    （与本仓反复在治的「看起来装好了其实没有」同形，与 `auto-gate.py` 那条本机路径同源）。

    ⚠ 本类锁的是**出货形态**，不是"有没有人记得填"。填法写在用户级守则哨兵区之外
      （`sandbox/global_agents.py` 的 BODY 只说 `<VAULT>` 是什么、绝不内嵌谁的本机路径）——
      所以这里只断言占位，不断言任何人本机的取值。
    """

    def setUp(self):
        super().setUp()
        # 空转自检：夹具必须真的踩在 VAULT_ROOTS 上（见上面那段注释的来由）。
        self.assertTrue(
            any(root in REAL_VAULT_LINE for root in AA.VAULT_ROOTS),
            "夹具里的路径不在 AA.VAULT_ROOTS 里 —— 这组锁会**空转**"
            "（旧的「括号必须留」那条就是这么失效的）。改 VAULT_ROOTS 时同步改这个夹具。")

    def test_a_real_vault_root_ships_as_a_placeholder(self):
        r = self.judged(VAULT_SRC)
        self.run_emit(SC.candidates({"judged": [r]}))
        out = self.produced()
        self.assertNotIn("D:/KnowledgeVault", out,
                         "产物里还有维护机的知识库根路径 —— 同事那份 clone 里这行是死的")
        self.assertIn("<VAULT>/concepts/customers/ntrx/", out,
                      "知识库根没换成占位（`portable()` 没生效？）")

    def test_the_parenthetical_survives_alongside_the_placeholder(self):
        """★ 只许换路径那一段，同一行里剩下的必须逐字在。

        与上一条是**两个方向**：初版曾按反引号把路径抠出来再填，于是
        `（工单必读 `00_工单必读.md`）` 那半句被静默丢了 —— 那才是真正的内容损失（§六 ①）。
        换路径容易，**换完还记得原句长什么样**才是这条在守的。
        """
        r = self.judged(VAULT_SRC)
        self.run_emit(SC.candidates({"judged": [r]}))
        self.assertIn("（工单必读 `00_工单必读.md`）", self.produced())

    def test_product_line_handles_a_real_vault_root(self):
        """`product_line` 是"源行 → 产物行"的**唯一出处**（`target_form` 与自检共用它）。"""
        self.assertEqual(
            "`<VAULT>/concepts/customers/ntrx/`（工单必读 `00_工单必读.md`）",
            AA.product_line(REAL_VAULT_LINE))

    def test_portable_touches_only_the_vault_root(self):
        """`portable` 只认知识库根，别的路径逐字不动。

        不是洁癖：它作用在**搬迁行**上，而搬迁行是客户知识（§六 ①「逐字保留」）。
        判据放宽一寸，改掉的就是别人仓里那句话的意思。
        """
        self.assertEqual(VAULT_LINE, AA.portable(VAULT_LINE),
                         "把不在 VAULT_ROOTS 里的路径也改了")
        self.assertEqual("没有路径的一行", AA.portable("没有路径的一行"))

    def test_the_product_introduces_no_new_machine_path(self):
        """★ 产物不许**新增**本机路径 —— 判据按**集合关系**，不是"产物里必须空"。

        ⚠ 为什么不是"必须空"（这条是实测撞出来的）：`SRC` 里那个 `D:/Vault/712/` 是
          **合成**的知识库根，不在 `VAULT_ROOTS` 里 —— 工具**不认识它**，于是逐字留着。
          那是**对的**：`portable()` 是"认识才换"，不认识就留（安全侧，§六 ①「留而不删」）；
          而且按形态整段替换会连 `/concepts/customers/ntrx/` 这段**有意义的后缀**一起丢掉，
          比不换更糟。所以这里断言的是「产物里的每个本机路径，源文件里本来就有」，
          另对**真的知识库根**那种写法（`VAULT_SRC`）加一条更强的：一个都不许剩。

        为什么这条比只锁 `VAULT_ROOTS` 字面量更强（2026-09-22 的教训）：
        **清点「某类东西还剩几份」之前，先数「这类东西有哪些名字」** ——
        上一次只数了「harness 路径」一个名字，知识库根那一类整批漏掉、22 份产物带病出货
        而没人看见。名单式判据**天然会再漏下一个名字**；这条按形态判，还捎带管住另一路：
        将来谁往 `target_form` 里新加一段、顺手烘了本机路径（没走占位），它也会响。

        ⚠ 边界（说清它管不到什么）：本锁只跑**夹具**，**不扫真的 29 个客户仓** ——
          那会让套件依赖"本机 clone 了哪些仓"，正是 `c2b6e1b`「夹具写死了开发机状态」
          那一类（三次都是本机绿、CI 红）。真仓那一路由 `agents_audit.py` 出报告给人看。
        """
        for src in (SRC, VAULT_SRC):
            r = self.judged(src)
            self.run_emit(SC.candidates({"judged": [r]}))
            out = self.produced()
            in_src = set(_machine_paths(src))
            new = sorted(set(_machine_paths(out)) - in_src)
            self.assertEqual([], new,
                             "产物**新增**了源文件里没有的本机路径（同事那份 clone 里是死的）：\n  "
                             + "\n  ".join(new))
        # 真的知识库根那种写法：必须一个都不剩
        r = self.judged(VAULT_SRC)
        self.run_emit(SC.candidates({"judged": [r]}))
        left = _machine_paths(self.produced())
        self.assertEqual([], left,
                         "真的知识库根没被换掉，产物里还剩：%s" % left)

    def test_the_form_predicates_are_not_vacuous(self):
        r"""注入证伪：两个形态判据必须命得中真路径，且不许误报占位符形态。

        ⚠ 本 docstring 的 `r` 前缀不能省：里面写着 `(?<![\w.>])`，非 raw 的 `\w`
          会触发 `DeprecationWarning: invalid escape sequence`，被
          `tests/test_source_hygiene.py` 逮到（2026-09-22 真被逮过一次）。

        ⚠ 末了一个样本（`<HARNESS>/x/y.md`）是**实测撞出来的假阳性**：lookbehind 少了
          `>` 时，`/x/y.md` 会被当成 git-bash 路径。所以它必须留在这儿 —— 谁把
          `(?<![\w.>])` 改回 `(?<![\w.])`，这条立刻响。
        """
        self.assertTrue(_DRIVE_PATH.search("见 `D:/Rain/project/x/AGENTS.md`"))
        self.assertTrue(_MSYS_PATH.search("见 `/d/Rain/project/x.md`"))
        for ok in ("见 `<HARNESS>/golden-patterns/templates/AGENTS_pc_slim.md`",
                   "`<VAULT>/concepts/customers/ntrx/`",
                   "1. Think Before Coding：先梳理约束/边界",
                   "见 `<HARNESS>/x/y.md`（占位符后面接二级目录）"):
            self.assertFalse(_DRIVE_PATH.search(ok) or _MSYS_PATH.search(ok),
                             "占位符/正文形态被误判成本机路径了：%s" % ok)
            self.assertEqual([], _machine_paths(ok) if "HARNESS" in ok else [],
                             "整条路径判据把占位符形态当路径了：%s" % ok)

    def test_the_lock_is_not_vacuous(self):
        """★ 注入证伪：掏空 `VAULT_ROOTS` → 产物必须重新带上本机路径。

        不做这一步，"产物里没有 `D:/KnowledgeVault`" 可能是**任何**原因造成的
        （夹具没写进去、那一行被丢掉、断言写错），而不是这次修复造成的。
        掏空后两边（`target_form` 与自检用的 `product_line`）同时失效，故自检仍会放行、
        文件照落 —— 于是这条注入**确实**能把上面第一条打红。
        """
        real = AA.VAULT_ROOTS
        AA.VAULT_ROOTS = ()
        self.addCleanup(setattr, AA, "VAULT_ROOTS", real)
        r = self.judged(VAULT_SRC)
        self.run_emit(SC.candidates({"judged": [r]}))
        self.assertIn("D:/KnowledgeVault", self.produced(),
                      "掏空 VAULT_ROOTS 后产物仍无本机路径 —— 上面那条不是因修复才绿的")


# ★ 2026-10-05：同一类疾病还有**另外三处**，上面那组锁一处也盖不到。
# 它的夹具 `REAL_VAULT_LINE` 是 `知识库：` 开头的**搬迁**行 —— 走的正是 `_target_lines`
# 里唯一一处调过 `portable()` 的分支。于是出现过一个「锁是绿的、缺陷照常出货」的现场：
# 首行标题 / `## 项目` 块 / 留守行三处都只做 `rstrip()`，里面带知识库根就**逐字进产物**。
# 7 个客户仓（gxgk / hy_hzy 三端 + hanbogaoxin_backend）是头一回踩到。
VAULT_STAY_LINE = ("工单必读在 `D:/KnowledgeVault/concepts/customers/ntrx/00_工单必读.md`，"
                   "先看它再动手")
# ⚠ 这里**不能**再带一个 `## 项目` 块：那两个夹具都得留在"本仓独有 ≤1 行"的候选区里
#   （`candidates()` 的判据），否则 `emit` 会以「本仓独有 2 行」直接拒收，锁就空转了。
VAULT_STAY_SRC = "\n".join([
    "# 测试仓 AGENTS.md",
    "",
    "## 文档",
    VAULT_STAY_LINE,
    "",
])
# 标题与 `## 项目` 各自再塞一条 —— 三处都是"只做 rstrip"的老路径，一起钉。
# `## 文档` 那条走搬迁行（且用的是**不认识**的 `D:/Vault/712/`），
# 这样 stay 只数到 1 行、仍是个合格候选，夹具不会因为"本仓独有 >1 行"被拒。
VAULT_TITLE_AND_PROJ_SRC = "\n".join([
    "# 测试仓 AGENTS.md（知识库 `D:/KnowledgeVault/concepts/customers/ntrx/`）",
    "",
    "## 项目",
    "知识库根 `D:/KnowledgeVault/concepts/customers/ntrx/` —— " + PROJ_SENTENCE,
    "",
    "## 文档",
    VAULT_LINE,
    "",
])


class TestVaultRootNeverShipsInAStayLine(_Fixture):
    """知识库根**在留守行（及标题 / `## 项目`）里**也必须换 `<VAULT>` —— 另一半。

    起因（**真出过**，2026-10-05）：`_target_lines` 把 `portable()` 只挂在处理**搬迁行**
    的那个分支上；首行标题 / `## 项目` 块 / 留守行三处都只做 `rstrip()` ⇒ 同一条知识库
    路径，写在搬迁行里变成 `<VAULT>`，写在留守行里**逐字进产物**。

    ⚠ 为什么上面那组锁盖不住：夹具是 `知识库：` 开头的**搬迁**行 ⇒ 锁是绿的、缺陷照常
      出货。所以 `setUp` 当场断言夹具确实踩在 `VAULT_ROOTS` 上、且**不是**搬迁行 ——
      判据一旦空转、或者夹具退化成搬迁行（那样本组就与上面那组重合、重新变成
      "看起来有覆盖"），它自己先红，而不是安静地绿着。

    ★ 修法有两半，各由一条独立的路径咬着（"两向都验过"）：
      ① `target_form` 末尾那一次 `product_line()` 收口；
      ② 生成器自检（`lost_lines`）那一侧的换算 —— 少了 ②，产物是对的但自检会把它
         **误判成丢知识**而整仓不写（假警），`_emit` 里那句 `rc == 0` 当场红。
    """

    def setUp(self):
        super().setUp()
        for name, fx in (("VAULT_STAY_LINE", VAULT_STAY_LINE),
                         ("VAULT_TITLE_AND_PROJ_SRC", VAULT_TITLE_AND_PROJ_SRC)):
            self.assertTrue(any(root in fx for root in AA.VAULT_ROOTS),
                            "%s 里没有真的知识库根 —— 本组锁会空转。改 VAULT_ROOTS 时同步改夹具。"
                            % name)
        self.assertFalse(VAULT_STAY_LINE.strip().startswith(AA.MOVE_PREFIXES),
                         "夹具退化成了**搬迁行** —— 那正是本组要区分的另一半，"
                         "写成 `知识库：` 开头它就与上面那组重合了。")
        self.assertTrue(any(l.strip().startswith(AA.MOVE_PREFIXES)
                            for l in VAULT_TITLE_AND_PROJ_SRC.split("\n")),
                        "第二个夹具得留一条**搬迁行**，否则知识库那段会被候选判据剔掉")

    def _emit(self, src):
        """走真判据 + 真 emit；**rc 必须是 0** —— 自检若与产物换算不一致，这里就红。"""
        r = self.judged(src)
        rc, out = self.run_emit(SC.candidates({"judged": [r]}))
        self.assertEqual(rc, 0, "这一仓被自检拦下了（产物换算与自检换算不一致？）：\n" + out)
        return self.produced()

    def test_a_vault_root_in_a_stay_line_ships_as_a_placeholder(self):
        out = self._emit(VAULT_STAY_SRC)
        self.assertNotIn("D:/KnowledgeVault", out,
                         "留守行里的知识库根原样进了产物 —— 同事 clone 后这行是死的")
        self.assertIn("<VAULT>/concepts/customers/ntrx/00_工单必读.md", out,
                      "留守行里的知识库根没换成占位")

    def test_the_stay_line_survives_around_the_placeholder(self):
        """★ 只许换路径那一段；同一行里剩下的必须逐字在（§六 ① 内容不许丢）。

        与上一条是**两个方向**：换掉路径容易，**换完还记得原句长什么样**才是这条在守的。
        """
        out = self._emit(VAULT_STAY_SRC)
        self.assertIn("工单必读在 `<VAULT>/concepts/customers/ntrx/00_工单必读.md`，"
                      "先看它再动手", out)

    def test_the_title_and_the_project_sentence_are_covered_too(self):
        """首行标题与 `## 项目` 那句是**另外两条**老路径，一起钉住。"""
        out = self._emit(VAULT_TITLE_AND_PROJ_SRC)
        self.assertNotIn("D:/KnowledgeVault", out,
                         "标题 / `## 项目` 里的知识库根也漏了 —— 它们同样是"
                         "「只做 rstrip」的老路径")
        self.assertEqual(2, out.count("<VAULT>/concepts/customers/ntrx/"),
                         "标题与 `## 项目` 那两条路径没都换：\n%s" % out)

    def test_the_lock_is_not_vacuous(self):
        """★ 注入证伪：掏空 `VAULT_ROOTS` → 留守行里的本机路径必须重新出现。

        不做这一步，「产物里没有 `D:/KnowledgeVault`」可能是**任何**原因造成的
        （夹具没写进去、那一行被丢掉、断言写错），而不是这次修复造成的。
        掏空后 `product_line` 两侧同时失效，自检仍会放行、文件照落 ——
        于是这条注入**确实**能把上面第一条打红。
        """
        real = AA.VAULT_ROOTS
        AA.VAULT_ROOTS = ()
        self.addCleanup(setattr, AA, "VAULT_ROOTS", real)
        out = self._emit(VAULT_STAY_SRC)
        self.assertIn("D:/KnowledgeVault", out,
                      "掏空 VAULT_ROOTS 后留守行仍无本机路径 —— 上面那条不是因修复才绿的")

    def test_the_generator_self_check_agrees_on_a_stay_line(self):
        """★ 另一半修法（自检那一侧）单独咬住 —— 少了它，**正确**的产物会被判成"丢知识"。

        走**真** `target_form` 产出、再喂给**真** `lost_lines`：两边必须对同一条留守行
        得到同一个形态。2026-10-05 之前 `lost_lines` 只换算搬迁行，这里会把
        `` 工单必读在 `D:/KnowledgeVault/…` `` 判成"丢了"（**假警**）⇒ 整仓不写 ——
        产物本来是对的，工具却拒绝出货（假警比漏报更贵：它教人不再相信这个自检）。
        """
        r = self.judged(VAULT_STAY_SRC)
        new = AA.target_form(VAULT_STAY_SRC.split("\n"), r["end"], r["keep_lines"], r["move"])
        self.assertIn("工单必读在 `<VAULT>/concepts/customers/ntrx/00_工单必读.md`，"
                      "先看它再动手", new, "真 `target_form` 没把留守行换成占位")
        self.assertEqual([], SC.lost_lines(new, r["keep_lines"], r["move"]),
                         "自检把**正确**的产物判成丢了东西 —— 这一仓会被整仓拒写")


class TestProductsCarryABirthCertificate(_Fixture):
    """丁、产物必须带**出生证明** —— 让「对着更早的源生成」这件事出得来声。

    起因（**真出过**，2026-10-02 实测）：`D:/Rain/agents_scaffold_out/` 那 22 份产物是
    **对着更早的源**生成的（源文件后来补的那半句，产物里没有），而**没有任何东西会报出来**：
    产物看上去完全正常，乙 的逐行自检也照样过 —— 因为自检比的是「记录下来的那几行」，
    而那份记录本身就是旧的。判据再准也救不了：它只看**当下**的源，不看产物是哪来的。

    所以每次 `--emit` 写一份 `_sources.json`，`--verify` 拿它复核。判据分两层，缺一层都答不了：
      源指纹对上 → 源没换过；产物指纹对上 → 产物没被人手改过。
    """

    # ---- 夹具
    def src_sha(self, path=None):
        with open(path or self.repo, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def cert(self):
        with io.open(os.path.join(self.out, SC.SOURCES), encoding="utf-8") as f:
            return json.load(f)

    def cert_raw(self):
        with io.open(os.path.join(self.out, SC.SOURCES), encoding="utf-8") as f:
            return f.read()

    def emitted(self):
        """生成一次（走真判据、真 emit），返回那条 judged 记录。"""
        r = self.judged()
        rc, out = self.run_emit(SC.candidates({"judged": [r]}))
        self.assertEqual(rc, 0, out)
        return r

    def run_verify(self, paths=None):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = SC.verify_products(self.out, {"t_pc": self.repo} if paths is None else paths)
        return rc, buf.getvalue() + err.getvalue()

    # ---- 记了什么
    def test_the_certificate_records_both_hashes(self):
        """源的字节哈希 + 产物自己的字节哈希，两个都要记 —— 只记一个就有一种情形查不出来。"""
        self.emitted()
        e = self.cert()["sources"]["t_pc"]
        p = os.path.join(self.out, "t_pc", "AGENTS.md")
        self.assertEqual(e["source_sha256"], self.src_sha(),
                         "记下的源哈希不是当下那份源的（那复核就永远说不上话）")
        self.assertEqual(e["source_bytes"], os.path.getsize(self.repo))
        self.assertEqual(e["product_sha256"], self.src_sha(p),
                         "产物自己的哈希记错了 —— 「产物被手改」就查不出来")
        self.assertEqual(e["product_bytes"], os.path.getsize(p))

    def test_the_certificate_carries_no_machine_path(self):
        """★ 出生证明里一个本机路径都不许有。

        存了绝对路径，这张证明就只在生成它的那台机器上读得懂（同 `agents_audit.py`
        把维护机路径换成 `<VAULT>` 的那个理由）。所以仓路径复核时按**键名**现从本机
        覆盖层重取 —— 代价是"本机没配这个仓"也会报不合格，那是 fail-closed 想要的。
        """
        self.emitted()
        left = _machine_paths(self.cert_raw())
        self.assertEqual([], left, "出生证明里带了本机路径：%s" % left)

    def test_verify_says_fresh_when_nothing_moved(self):
        self.emitted()
        rc, out = self.run_verify()
        self.assertEqual(rc, 0, out)
        self.assertIn("FRESH", out)

    # ---- 复核必须真的会响
    def test_verify_catches_a_source_edited_after_emit(self):
        """★ 这条就是整个功能存在的理由：源在生成之后被改过，产物就是旧的。

        2026-10-02 那 22 份产物正是这个形状，而当时**没有任何东西会报出来**。
        """
        self.emitted()
        self.write_repo(SRC.replace("## 文档", "## 文档（后来补的）"))
        rc, out = self.run_verify()
        self.assertEqual(rc, 1, "源改了却报绿 —— 这正是出生证明存在的理由：\n%s" % out)
        self.assertIn("源已改", out)

    def test_verify_catches_a_product_edited_by_hand(self):
        """反向：源没动、产物被人手改了 —— 只记源不记产物就查不出这一种。"""
        self.emitted()
        with open(os.path.join(self.out, "t_pc", "AGENTS.md"), "a", encoding="utf-8") as f:
            f.write("手加的一行\n")
        rc, out = self.run_verify()
        self.assertEqual(rc, 1, out)
        self.assertIn("产物被改", out)

    def test_verify_catches_a_missing_or_unlocatable_source(self):
        """没法确认 != 没事（fail-closed）：源没了、或本机压根没配这个仓，都算不合格。"""
        self.emitted()
        os.remove(self.repo)
        rc, out = self.run_verify()
        self.assertEqual(rc, 1, "源文件没了却报绿：%s" % out)
        self.assertIn("源缺失", out)
        rc, out = self.run_verify(paths={})
        self.assertEqual(rc, 1, "本机没配这个仓却报绿：%s" % out)
        self.assertIn("无法定位", out)

    def test_verify_flags_engine_drift(self):
        """★ 产物也可能是对着更早的**引擎**生成的（生成器 / 判据模块 / 归宿表）。"""
        self.emitted()
        c = self.cert()
        c["engine"]["scaffold"] = "0" * 64
        with io.open(os.path.join(self.out, SC.SOURCES), "w", encoding="utf-8") as f:
            f.write(json.dumps(c, ensure_ascii=False, indent=2))
        rc, out = self.run_verify()
        self.assertEqual(rc, 1, out)
        self.assertIn("引擎已变", out)

    def test_an_unregistered_product_directory_is_reported(self):
        """目录里冒出证明里没有的仓 → 这批产物整体不再有据可查，算不合格而不是无视。"""
        self.emitted()
        os.makedirs(os.path.join(self.out, "ghost"))
        with io.open(os.path.join(self.out, "ghost", "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write("x\n")
        rc, out = self.run_verify()
        self.assertEqual(rc, 1, out)
        self.assertIn("未登记", out)

    def test_verify_without_a_certificate_is_a_config_error(self):
        """★ 没有出生证明**不许报绿** —— 那正是最常见的现场（2026-10-04 之前的目录）。"""
        rc, out = self.run_verify()
        self.assertEqual(rc, 2, "无凭可核却报绿：%s" % out)
        self.assertIn("出生证明", out)

    # ---- 只读 / 局部重发
    def test_verify_is_read_only(self):
        """复核工具一旦能写，就没人敢在产物旁边跑它了（同 `agents_audit.py` 的契约）。"""
        self.emitted()

        def snap():
            out = {}
            for dirpath, _dirs, files in os.walk(self.out):
                for n in files:
                    q = os.path.join(dirpath, n)
                    st = os.stat(q)
                    out[q] = (st.st_mtime_ns, st.st_size)
            return out

        before = snap()
        self.run_verify()
        self.assertEqual(snap(), before, "复核动了目录里的文件")

    def test_check_only_writes_no_certificate(self):
        r = self.judged()
        rc, _out = self.run_emit(SC.candidates({"judged": [r]}), check_only=True)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, SC.SOURCES)),
                         "干跑（--check）还写了出生证明")

    def test_a_re_emit_of_one_repo_keeps_the_others(self):
        """`--only k2` 重发一个仓，不该把别的仓的出生证明抹掉（抹了 = 一片假「未登记」）。"""
        r1 = self.judged()
        self.run_emit(SC.candidates({"judged": [r1]}))
        repo2 = os.path.join(self.d, "repo2", "AGENTS.md")
        os.makedirs(os.path.dirname(repo2))
        with open(repo2, "wb") as f:
            f.write(SRC.encode("utf-8"))
        r2 = AA.judge("t_pc2", repo2, _sink(), set())
        rc, out = self.run_emit(SC.candidates({"judged": [r2]}))
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(self.cert()["sources"]), ["t_pc", "t_pc2"],
                         "重发一个仓把别的仓的出生证明抹了")

    # ---- CLI 接线 + 注入证伪
    def test_the_cli_flag_is_wired(self):
        """`--verify` 必须真的接到 `main()` 上，且用法行里写着它。

        （`--only` 那次教训：用法行写的是空格写法、解析器只认等号写法 —— 文档教的写法
        必须跑得通，所以这条连 `__doc__` 一起断言。）
        """
        self.assertIn("--verify", SC.__doc__, "用法行里没教这个写法")
        self.emitted()
        real = SC._config_paths
        SC._config_paths = lambda: {"t_pc": self.repo}
        self.addCleanup(setattr, SC, "_config_paths", real)
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = SC.main(["--verify", self.out])
        self.assertEqual(rc, 0, buf.getvalue() + err.getvalue())

    def test_the_lock_is_not_vacuous(self):
        """★ 注入证伪：把 `fingerprint` 掏成常量 → 「源已改」那条必须失效。

        不做这一步，"复核能发现源改了"可能是**任何**原因造成的（夹具没写、断言写错、
        路径不对），而不是这条判据在起作用。生成与复核**共用**这一个函数，所以掏空后
        两边同时失效：自检仍放行、文件照落，"源已改"再也报不出来。
        """
        real = SC.fingerprint
        SC.fingerprint = lambda path: "0" * 64
        self.addCleanup(setattr, SC, "fingerprint", real)
        self.emitted()
        self.write_repo(SRC.replace("## 文档", "## 文档（后来补的）"))
        rc, out = self.run_verify()
        self.assertEqual(rc, 0, "掏空指纹后仍报出「源已改」—— 上面那条不是因它才绿的：\n%s" % out)


class TestManifestIsMergedNotOverwritten(_Fixture):
    """戊、逐行归宿清单是**逐仓**的文件 —— 一次 `--only` 只该换掉它碰过的那些段。

    病灶（2026-10-04，本批自己点名的那条残留）：`_manifest.md` 原先 `open(..., "w")` 一把
    覆盖，而 `emit` 只产出**本轮**那几个仓的段 ⇒ `--only k1` 跑完，清单里只剩 k1，
    其余 21 个仓看起来凭空消失。

    ★ 它**不会**被别的东西兜住：产物还在目录里、出生证明也还在（那边早就是合并写）——
    于是两份文件互相打脸，而这比漏报更贵：**假警教人不再相信清单**。
    """

    # ---- 夹具
    def repo2(self, key="t_pc2"):
        """再造一个仓（同 `TestProductsCarryABirthCertificate` 的写法）→ judged 记录。"""
        p = os.path.join(self.d, "repo2", "AGENTS.md")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(SRC.encode("utf-8"))
        return AA.judge(key, p, _sink(), set())

    def both(self):
        """一次写两个仓 —— 之后任何一次单仓重发都该只动它自己那一段。"""
        r1, r2 = self.judged(), self.repo2()
        rc, out = self.run_emit(SC.candidates({"judged": [r1, r2]}))
        self.assertEqual(rc, 0, out)
        return r1, r2

    def manifest(self):
        with io.open(os.path.join(self.out, SC.MANIFEST), encoding="utf-8") as f:
            return f.read()

    def section(self, text, key):
        """从清单全文里抠出某个仓那一段（下一个段头之前）。"""
        i = text.index("## %s（" % key)
        j = text.find("\n## ", i)
        return (text[i:] if j < 0 else text[i:j]).rstrip("\n")

    # ---- 合并
    def test_a_second_run_of_one_repo_keeps_the_other_section(self):
        """★ 这条就是本类的全部理由：只重发 t_pc，t_pc2 那一段必须**逐字**还在。"""
        self.both()
        first = self.manifest()
        h5 = self.section(first, "t_pc2")
        rc, out = self.run_emit(SC.candidates({"judged": [self.judged()]}))
        self.assertEqual(rc, 0, out)
        second = self.manifest()
        self.assertIn("## t_pc2（", second,
                      "只重发一个仓，另一个仓的段就被整片抹掉了：\n%s" % second)
        self.assertEqual(h5, self.section(second, "t_pc2"), "没碰过的那一段不该变")

    def test_the_rewritten_section_is_the_new_one(self):
        """另一半：该换的那一段**必须真换**（否则"保住了别的段"可以靠什么都不做来骗到）。"""
        self.both()
        old = self.section(self.manifest(), "t_pc")
        self.assertIn(PROJ_SENTENCE, old)
        r = self.judged(SRC.replace(PROJ_SENTENCE, PROJ_SENTENCE + "（改了）"))
        rc, out = self.run_emit(SC.candidates({"judged": [r]}))
        self.assertEqual(rc, 0, out)
        new = self.section(self.manifest(), "t_pc")
        self.assertIn(PROJ_SENTENCE + "（改了）", new, "重发的那一段没换成本轮的内容")
        # 比对**整行**（`- \`…\``）而不是那句话：新句子里带着旧句子当子串，
        # 拿子串判"旧的没了"永远为真 —— 那是一条空判据。
        self.assertNotIn("- `%s`" % PROJ_SENTENCE, new, "旧内容还留在新段里")

    def test_a_run_that_writes_nothing_keeps_the_whole_manifest(self):
        """整仓被拒（判据不合格）时一个段都没产出 —— 更不能把清单清成只剩表头。"""
        self.both()
        first = self.manifest()
        # 多塞两行留守知识 ⇒ stay > 1 ⇒ 判据不合格 ⇒ 这一仓不写、也没有段
        r = self.judged(SRC.replace("## 文档", "## 本仓独有\n\n- 甲\n- 乙\n\n## 文档"))
        self.assertTrue(SC.candidates({"judged": [r]})[0]["why"], "夹具没有被拒绝，测不到东西")
        rc, out = self.run_emit(SC.candidates({"judged": [r]}))
        self.assertEqual(rc, 1, out)
        self.assertEqual(first, self.manifest(), "一次什么都没写的运行把清单改动了")

    def test_a_new_key_is_appended_and_nothing_is_duplicated(self):
        r1 = self.judged()
        self.run_emit(SC.candidates({"judged": [r1]}))
        self.assertNotIn("## t_pc2（", self.manifest())
        rc, out = self.run_emit(SC.candidates({"judged": [self.repo2()]}))
        self.assertEqual(rc, 0, out)
        mf = self.manifest()
        self.assertLess(mf.index("## t_pc（"), mf.index("## t_pc2（"), "新仓该接在末尾")
        for k in ("t_pc", "t_pc2"):
            self.assertEqual(mf.count("## %s（" % k), 1, "同一个仓出现了两段：\n%s" % mf)

    def test_the_head_note_survives_a_merge(self):
        """表头（第一段之前）用**旧的**那份 —— 人在上面加的备注不该被合并冲掉。"""
        r1 = self.judged()
        rc, out = self.run_emit(SC.candidates({"judged": [r1]}))
        self.assertEqual(rc, 0, out)
        p = os.path.join(self.out, SC.MANIFEST)
        with io.open(p, encoding="utf-8") as f:
            text = f.read()
        with open(p, "w", encoding="utf-8") as f:
            f.write(text.replace("判据来自", "（2026-10-04 人看过了）\n\n判据来自"))
        rc, out = self.run_emit(SC.candidates({"judged": [self.repo2()]}))
        self.assertEqual(rc, 0, out)
        self.assertIn("人看过了", self.manifest(), "表头里人加的备注被合并冲掉了")

    def test_an_unrecognizable_manifest_is_rebuilt_not_half_merged(self):
        """认不出的清单 → **重写一份完整的**，不是把半个文件拼成"看着完整"的。"""
        os.makedirs(self.out, exist_ok=True)
        with open(os.path.join(self.out, SC.MARKER), "w", encoding="utf-8") as f:
            f.write("marker\n")
        with open(os.path.join(self.out, SC.MANIFEST), "w", encoding="utf-8") as f:
            f.write("这不是清单，是另一张东西\n随便什么\n")
        rc, out = self.run_emit(SC.candidates({"judged": [self.judged()]}))
        self.assertEqual(rc, 0, out)
        mf = self.manifest()
        self.assertTrue(mf.startswith("# A 组 AGENTS.md 拟改产物清单"), mf[:80])
        self.assertNotIn("这不是清单", mf, "认不出的旧清单被原样拼进来了")

    # ---- 注入证伪
    def test_the_lock_is_not_vacuous(self):
        """★ 把 `merge_manifest` 换回**旧写法**（只渲染本轮那几段）→ 上面第一条必须失效。

        不做这一步，"重发一个仓不会抹掉别的仓"可能是**任何**原因造成的（夹具只造了一个仓、
        断言写错、路径不对）。换回旧写法后 t_pc2 那一段当场消失 —— 那正是这条锁在拦的东西。
        """
        real = SC.merge_manifest
        SC.merge_manifest = lambda old_text, sections, head_lines: SC._render_manifest(
            head_lines, list(sections.values()))       # 旧写法：一把覆盖
        self.addCleanup(setattr, SC, "merge_manifest", real)
        self.both()
        self.run_emit(SC.candidates({"judged": [self.judged()]}))
        self.assertNotIn("## t_pc2（", self.manifest(),
                         "换回旧写法后别的仓的段居然还在 —— 上面那条不是因 `merge_manifest` 才绿的")


if __name__ == "__main__":
    unittest.main()
