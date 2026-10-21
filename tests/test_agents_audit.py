# -*- coding: utf-8 -*-
"""`sandbox/agents_audit.py` 回归锁 —— 受管仓瘦身判据的**唯一**来源，所以要钉死。

为什么需要（2026-09-19）
--------------------------------------------------------------------------------
「33 个受管仓瘦身」的判据是「这一行有没有归宿」，而归宿表（`sandbox/sink_table.txt`）
是**手写的**。手写的判据有两种坏法，都无声：

  ① **指空** —— 改了 `AGENTS_*_slim.md` 的措辞、忘了同步锚点，那条归宿就永远求不到。
     后果不是误删（那几行会退回「待迁」），而是**静默少删** —— 使用者只会以为
     "审下来就是没得删"。所以 `verify()` 要出声，本文件再把「出声」钉住。
  ② **匹配太宽** —— 一旦用子串匹配，`1. Controller 只做参数校验…` 那条会连带把
     使用者自己写的相近句子一起删掉。删受管仓的知识不可恢复（§六 ①），所以判据是
     **归一化后的精确相等**：差一个标点就落到「待迁」= 留而不删。

本文件锁四组事：
  **甲、归宿表会拒绝说不清的写法** —— 缺锚点的组、孤零零的 [行]、不认识的前缀，
                             解析器一律报错（不是静默忽略）。
  **乙、判据两侧都守住** —— 指空 → 出声且退回待迁；差一个标点 → 待迁；归一化必须
                         同时认正斜杠与反斜杠两种引擎仓路径写法（★ 首跑实测踩到过：
                         只替换反斜杠 → 受管仓那 11 行整片落进「待迁」）。
  **丙、提案不许自己丢内容** —— 标题只在块里没有留守行时才跟着删（否则出现无主孤行）；
                         保留行逐字留（只 rstrip），`知识库：` 整行留、**不从中抠片段**
                         （初版按反引号抠路径，把「（工单必读 `00_工单必读.md`）」丢了）。
  **丁、工具只读且清单可信** —— 跑一遍不动任何受管仓；真实归宿表的每条锚点当下都求证得到
                         （改模板措辞却没同步本表时，这条锁先响）；没接入的仓要点名。

安全约定：所有夹具在 `TemporaryDirectory` 里，用完自动收（本文件不 mkdtemp 裸留）。
"""
import io
import os
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
import agents_audit as AA  # noqa: E402

SINK_REAL = os.path.join(ROOT, "sandbox", "sink_table.txt")

LINE_HOME = "1. 有归宿的规则行"
LINE_ORPHAN = "2. 没归宿的规则行"
LINE_VAULT = "知识库：`D:/Vault/x/`（工单必读 `00.md`）"
# 这条刻意写成**受管仓里那种正斜杠绝对路径**：夹具里带着本机 ROOT，于是它既是被判定的
# 那一行，也是「归一化真的生效了」的证据 —— 换台机器 clone 到别处，这条照样得判成可删。
GATE_LINE = ("改完代码仓库根执行：`python %s/harness/agents/auto-gate.py`"
             % ROOT.replace("\\", "/"))
REPO_MD = "\n".join([
    "# 测试仓 AGENTS.md",
    "",
    "## 门禁（机械强制）",
    GATE_LINE,
    "",
    "## 核心约束（门禁管不到的判断层）",
    LINE_HOME,
    LINE_ORPHAN,
    "",
    "## 文档",
    LINE_VAULT,
    "",
])

# A 组 22 个受管仓的实际形状：唯一留守内容就是 `## 项目` 下面那句**项目身份**。
PROJECT_SENTENCE = "EHS 综合管理平台 PC 端，Vue3 + TypeScript + Vite。712 = 712 所。"
PROJECT_MD = "\n".join([
    "# 测试仓 AGENTS.md",
    "",
    "## 项目",
    PROJECT_SENTENCE,
    "",
    "## 门禁（机械强制）",
    GATE_LINE,
    "",
    "## 文档",
    LINE_VAULT,
    "",
])


def _fingerprint(path):
    """(mtime, size) —— 判定"这趟跑动没动过这个文件"。"""
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


class _Fixture(unittest.TestCase):
    """一套临时夹具：一个受管仓 AGENTS.md + 一个"归宿文件" + 一张归宿表。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        self.home = os.path.join(d, "AGENTS_backend_slim.md")
        with io.open(self.home, "w", encoding="utf-8", newline="\n") as f:
            f.write("# 端级模板\n\n1. 有归宿的规则行\n")
        self.repo = os.path.join(d, "r", "AGENTS.md")
        os.makedirs(os.path.dirname(self.repo))
        with io.open(self.repo, "w", encoding="utf-8", newline="\n") as f:
            f.write(REPO_MD)
        self.sink_path = os.path.join(d, "sink.txt")

    def write_repo(self, text):
        with io.open(self.repo, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def write_sink(self, body):
        with io.open(self.sink_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        return self.sink_path

    def group(self, line, anchor="有归宿的规则行", home=None):
        return ("[归宿] %s\n[锚点] %s\n[为什么] 夹具\n[行] %s\n"
                % (home or self.home, anchor, line))

    def good_sink(self, extra=""):
        """默认表：门禁命令行（整块可走）+ 核心约束里的一行（另一行没归宿，留着）。

        两块的结果不同，正好同时钉住「标题跟着走」与「标题留下」两侧。
        """
        return self.write_sink("# 夹具\n" + self.group(GATE_LINE)
                               + "\n" + self.group(LINE_HOME) + extra)

    def judge(self, sink_path=None, bad_files=()):
        groups, errs = AA.load_sink(sink_path or self.sink_path)
        checked = AA.verify(groups, errs)
        bad = {g["file"] for g, ok, _ in checked if not ok}
        sink = {}
        for g in groups:
            for l in g["lines"]:
                sink.setdefault(l, g)
        r = AA.judge("t_backend", self.repo, sink, bad | set(bad_files))
        return r, sink, errs


class TestSinkTableRejectsVagueEntries(_Fixture):
    """甲、说不清的写法一律报错 —— 静默忽略等于"这张表偷偷少了半截"。"""

    def test_orphan_line_before_any_home_is_an_error(self):
        p = self.write_sink("[行] 孤零零的一条\n" + self.group(LINE_HOME))
        _groups, errs = AA.load_sink(p)
        self.assertTrue(any("出现在 [归宿] 之前" in e for e in errs), errs)

    def test_home_without_anchor_is_an_error(self):
        p = self.write_sink("[归宿] %s\n[行] %s\n" % (self.home, LINE_HOME))
        _groups, errs = AA.load_sink(p)
        self.assertTrue(any("缺 [锚点]" in e for e in errs), errs)

    def test_home_without_any_line_is_an_error(self):
        p = self.write_sink("[归宿] %s\n[锚点] 有归宿的规则行\n" % self.home)
        _groups, errs = AA.load_sink(p)
        self.assertTrue(any("一条 [行] 都没有" in e for e in errs), errs)

    def test_unknown_prefix_is_an_error_not_silently_skipped(self):
        p = self.write_sink(self.group(LINE_HOME) + "[备注] 我想加个字段\n")
        _groups, errs = AA.load_sink(p)
        self.assertTrue(any("不认识的行" in e for e in errs), errs)


class TestAnchorThatPointsNowhere(_Fixture):
    """乙之一、指空要出声，且那几行退回「待迁」—— 不误删，但也不静默少删。"""

    def test_broken_anchor_is_reported(self):
        self.write_sink(self.group(LINE_HOME, anchor="这句话模板里没有"))
        _r, _sink, errs = self.judge()
        self.assertTrue(any("指空" in e for e in errs), errs)

    def test_broken_anchor_falls_back_to_stay(self):
        """★ 指空的行必须**留在**待迁里 —— 那条归宿作废，不是"照删不误"。"""
        self.write_sink(self.group(LINE_HOME, anchor="这句话模板里没有"))
        r, _sink, _errs = self.judge()
        self.assertIn(LINE_HOME, [x.strip() for x in r["stay"]])
        self.assertEqual([], [x.strip() for x in r["drop"]])

    def test_home_file_missing_is_reported(self):
        self.write_sink(self.group(LINE_HOME, home=os.path.join(self.tmp.name, "nope.md")))
        _r, _sink, errs = self.judge()
        self.assertTrue(any("归宿文件不存在" in e for e in errs), errs)


class TestMatchingIsExactAndPathAgnostic(_Fixture):
    """乙之二、精确相等 + 两种路径写法都认 —— 判据不许只在这台机器上成立。"""

    def test_one_punctuation_difference_stays(self):
        """★ 差一个逗号就落到「待迁」：宁可漏删，不可错删（§六 ①）。"""
        self.write_sink(self.group("1. 有归宿的规则，行"))
        r, _sink, _errs = self.judge()
        self.assertEqual([], [x.strip() for x in r["drop"]])
        self.assertIn(LINE_HOME, [x.strip() for x in r["stay"]])

    def test_substring_does_not_match(self):
        """归宿表里只写了半行 → 不许当成匹配（子串匹配会连带删掉使用者自己写的相近句）。"""
        self.write_sink(self.group("有归宿的"))
        r, _sink, _errs = self.judge()
        self.assertEqual([], [x.strip() for x in r["drop"]])

    def test_a_line_carrying_the_local_engine_path_still_matches(self):
        """★ 端到端验归一化：受管仓那行写着本机绝对路径，仍必须判成「可删」。

        首跑踩到的正是这个：`ROOT` 由 abspath 得来是**反斜杠**，受管仓写的是正斜杠，
        只替换其中一种 → 受管仓那 11 行整片落进「待迁」，而**没人会发现**（方向安全，
        但等于这条判据只在这台机器上成立）。
        """
        self.good_sink()
        r, _sink, errs = self.judge()
        self.assertEqual([], [e for e in errs if "指空" in e], errs)
        self.assertIn(GATE_LINE, r["drop"], "带本机路径的那行没被判成可删")

    def test_both_slash_forms_of_the_engine_root_are_normalized(self):
        fwd = ROOT.replace("\\", "/")
        self.assertNotIn(fwd, AA.norm("python %s/harness/agents/auto-gate.py" % fwd),
                         "正斜杠写法没被归一化")
        self.assertNotIn(ROOT, AA.norm("python %s\\harness\\agents\\auto-gate.py" % ROOT),
                         "反斜杠写法没被归一化")
        self.assertEqual(AA.norm("python %s/x" % fwd), AA.norm("python %s/x" % ROOT))

    def test_whitespace_is_collapsed(self):
        self.assertEqual(AA.norm("  1.   有归宿  "), AA.norm("1. 有归宿"))


class TestProposalNeverDropsContent(_Fixture):
    """丙、提案自己的正确性 —— 丢内容的三种写法都在这里被钉住。"""

    def test_heading_stays_when_its_block_has_an_orphan(self):
        """★ 块里还有留守行 → 标题必须留下，否则那些行成了无主孤行。"""
        self.good_sink()
        r, _sink, _errs = self.judge()
        self.assertIn("## 核心约束（门禁管不到的判断层）", r["keep_lines"])
        self.assertNotIn("## 核心约束（门禁管不到的判断层）", r["drop"])

    def test_heading_goes_when_the_whole_block_is_covered(self):
        """块里一行不留 → 标题跟着走（否则目标形态里挂着一个空小节）。"""
        self.good_sink()
        r, _sink, _errs = self.judge()
        self.assertNotIn("## 门禁（机械强制）", r["keep_lines"])

    def test_kept_lines_are_verbatim_up_to_trailing_space(self):
        self.good_sink()
        r, _sink, _errs = self.judge()
        self.assertIn(LINE_ORPHAN, [x.strip() for x in r["keep_lines"]])
        for l in r["keep_lines"]:
            self.assertEqual(l, l.rstrip(), "保留行只许去掉行尾空白，不许改写")

    def test_vault_line_moves_whole_minus_its_label(self):
        """★ `知识库：` 整行留 —— 初版按反引号抠路径，把「（工单必读 `00.md`）」丢了。"""
        self.good_sink()
        r, _sink, _errs = self.judge()
        out = AA.target_form(REPO_MD.split("\n"), "backend", r["keep_lines"], r["move"])
        text = "\n".join(out)
        self.assertIn("（工单必读 `00.md`）", text, "标签后面的那句被丢了")
        self.assertIn("## 项目知识库", text)

    def test_title_is_kept_verbatim(self):
        self.good_sink()
        r, _sink, _errs = self.judge()
        out = AA.target_form(REPO_MD.split("\n"), "backend", r["keep_lines"], r["move"])
        self.assertEqual(out[0], "# 测试仓 AGENTS.md")


class TestProjectIdentityIsNotRepoExclusive(_Fixture):
    """丙之二、`## 项目` 那句是**项目身份**，不许挂到 `## 本仓独有` 底下（2026-09-21）。

    事实经过：A 组 22 个受管仓的唯一留守内容就是这句项目身份（`stay==1`）。而 `## 项目`
    在 `SCAFFOLD` 里、其正文是留守行 → `judge()` 按「块里还有留守行，标题留下」把标题留了
    下来。原样拼进目标形态就成了 `## 本仓独有` **紧跟** `## 项目`，读起来像「这句只有这个
    仓有」——意思正好反了。修法是把这一块整块提到 `## 端级规则` 之前。
    """

    def _target(self):
        self.write_repo(PROJECT_MD)
        self.good_sink()
        r, _sink, _errs = self.judge()
        return AA.target_form(PROJECT_MD.split("\n"), "pc", r["keep_lines"], r["move"]), r

    def test_judge_keeps_the_project_heading(self):
        """先钉住前提：标题之所以跑错地方，正是因为它被判成留守。"""
        _out, r = self._target()
        self.assertIn("## 项目", r["keep_lines"])

    def test_project_block_sits_above_the_end_level_pointer(self):
        out, _r = self._target()
        self.assertLess(out.index("## 项目"), out.index("## 端级规则"),
                        "`## 项目` 必须排在端级指针之前：%s" % out)
        self.assertLess(out.index("## 端级规则"), out.index("## 本仓独有（同事的机器上不会有的知识）"))

    def test_project_sentence_is_not_under_repo_exclusive(self):
        out, _r = self._target()
        sec = out[out.index("## 本仓独有（同事的机器上不会有的知识）"):]
        self.assertNotIn(PROJECT_SENTENCE, sec,
                         "项目身份那句落进了 `## 本仓独有` —— 正是本次要修的读反")

    def test_nothing_is_lost_in_the_move(self):
        """搬家不许丢东西：留守行（含那句身份）在产物里逐字都在。"""
        out, r = self._target()
        for l in r["keep_lines"]:
            self.assertIn(l.rstrip(), [x.rstrip() for x in out], "搬丢了：%s" % l)

    def test_repo_without_a_project_block_is_untouched(self):
        """反向：没有 `## 项目` 的仓，产物开头仍是「标题 / 空行 / 端级规则」——没多出空行。"""
        self.good_sink()
        r, _sink, _errs = self.judge()
        out = AA.target_form(REPO_MD.split("\n"), "backend", r["keep_lines"], r["move"])
        self.assertEqual(out[:3], ["# 测试仓 AGENTS.md", "", "## 端级规则"])


class TestTheShippedTableAndTool(unittest.TestCase):
    """丁、真实那张表 + 工具本身：锚点当下求证得到，且跑一遍不动任何受管仓。"""

    def _run_main(self, argv):
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            rc = AA.main(argv)
        finally:
            sys.stdout = real
        return rc, buf.getvalue()

    def _repo_files(self):
        import _config
        cfg = _config.load() or {}
        out = []
        for base in (cfg.get("repos") or {}).values():
            if isinstance(base, str) and base:
                p = os.path.join(base, "AGENTS.md")
                if os.path.isfile(p):
                    out.append(p)
        return out

    def test_shipped_sink_table_is_parseable_and_no_anchor_points_nowhere(self):
        """★ 改了 AGENTS_*_slim.md 的措辞却没同步 sink_table.txt —— 这条锁先响。

        响的后果不是"门禁拦你"，而是"那几行悄悄退回待迁、没人发现"，
        所以必须有一条锁替它出声。
        """
        groups, errs = AA.load_sink(SINK_REAL)
        self.assertTrue(groups, "归宿表读空了")
        self.assertEqual([], errs, "归宿表解析报错")
        AA.verify(groups, errs)
        self.assertEqual([], errs, "sink_table.txt 有指空的锚点 —— 同步它，或改回模板措辞")

    def test_audit_is_read_only_and_exits_zero(self):
        """★ 跑一遍真实体检：不许动任何受管仓（本工具没有任何写入路径）。

        ⚠ 2026-09-19 CI 实测：本夹具读的是**本机** `config` 的 repos 段。CI 上没有那个本地
        覆盖层 → 受管仓 0 个 → 这条不是"失败"而是**无从验起**（"不许动受管仓"在没有受管仓时
        是句空话）。原先写的是 `assertTrue(paths, "夹具不成立")`，于是它在 CI 上永远红 ——
        正是 `c2b6e1b` 修过的那一类「夹具写死了开发机状态」的第三次复发。
        改为**跳过并说明**：它在本机（有仓）仍照咬，在 CI 上不再冒充一条真警报。
        rc==0 那一半另有 `test_repos_without_agents_md_are_named_in_the_report` 守着（它不依赖受管仓）。
        """
        paths = self._repo_files()
        if not paths:
            self.skipTest("本机 repos 段里没有带 AGENTS.md 的仓（CI 常态）→ "
                          "「体检会不会动受管仓」这半条无从验起")
        before = {p: _fingerprint(p) for p in paths}
        rc, out = self._run_main([])
        self.assertEqual(0, rc, out[-800:])
        self.assertEqual(before, {p: _fingerprint(p) for p in paths}, "体检动了受管仓的文件")

    def test_repos_without_agents_md_are_named_in_the_report(self):
        """没接入的仓要在报告里点名 —— 静默跳过会让人以为"33 个都审过了"。"""
        rc, out = self._run_main([])
        self.assertEqual(0, rc, out[-800:])
        reps = AA.collect()
        self.assertEqual(reps["total_repos"],
                         len(reps["judged"]) + len(reps["missing"]) + len(reps["unset"]),
                         "受管仓没被三分完 —— 有仓被静默丢了")
        for k in reps["missing"]:
            self.assertIn(k, out, "缺 AGENTS.md 的仓没在报告里点名：%s" % k)
        for k in reps["unset"]:
            self.assertIn(k, out, "本机没配路径的仓没在报告里点名：%s" % k)

    def test_diff_mode_writes_nothing(self):
        """`--diff` 也只是打印 —— §六 ① 要求逐仓出 diff 给人过目，不自动改。"""
        reps = AA.collect()
        if not reps["judged"]:
            # 同上：CI 上本机无受管仓 → 没有仓可以出 diff，这条无从验起（不是红）。
            self.skipTest("本机没有可判定的仓（CI 常态）→ --diff 无从验起")
        key = reps["judged"][0]["key"]
        before = {p: _fingerprint(p) for p in self._repo_files()}
        rc, out = self._run_main(["--diff", key])
        self.assertEqual(0, rc, out[-800:])
        self.assertIn("@@", out, "没出 diff —— 这个仓一行可删的都没有？换一个仓再验")
        self.assertEqual(before, {p: _fingerprint(p) for p in self._repo_files()})


if __name__ == "__main__":
    unittest.main()
