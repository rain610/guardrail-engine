# -*- coding: utf-8 -*-
r"""提交期「只查暂存内容」回归锁（2026-09-15 外部评审 v3 **P0**）
================================================================================
背景（实测复现）：pre-commit 只从 index 取**文件名**，却把工作区路径交给检查器 →
检查器 open(工作区) → 「暂存坏版本 + 工作区改回好版本」**rc=0 放过**，
而 `git diff --cached` 里躺着坏版本。影响面 = 全部端仓、全部 gate 规则。

修法：pre-commit 用 `git checkout-index` 把暂存内容展开到 overlay，导出
HARNESS_STAGED_OVERLAY / HARNESS_STAGED_REPO；`rule_base.read_bytes()` 只重映射
「读内容」这一层（ctx.rel / changed_lines / is_new_file 等 git 语义不变）。

本文件四形态（B 是 P0 回归锁，C 是**反向锁**：修过头变成"连工作区也不看"会误放）：
  A 暂存坏 + 工作区坏  → rc=1（对照）
  B 暂存坏 + 工作区好  → rc=1（P0 回归锁：必须读 index 版本）
  C 暂存好 + 工作区坏  → rc=0（反向锁：提交期绝不读工作区）
  D 暂存删除 + 工作区残留坏 → rc=0（已暂存删除的文件不进检查面，否则又读回工作区）
  E/F 非 ASCII 路径（中文文件名）→ 仍拦违规 / 合规不假红（2026-09-16 实测缺陷回归锁）

注意：PC 门禁查行尾，夹具必须 CRLF（用 LF 会被 PC-EOL 拦成 rc=1，那不是本文件的判据）。

快跑（HARNESS_FAST=1，2026-09-17 接线）：A~F 六条都要**真起 git 仓 + 真跑 pre-commit**
（A~D 每例 5.7~6.9s；E/F 各 16s —— ASCII 对照 + 两类中文名各跑一遍），快跑档跳过。
**跳过的代价得说清**：B 是 P0 回归锁、E 是「改个中文名就能绕过门禁」那条假绿锁 ——
此后它们只在全量档（CI 与 `run_tests.py --full`）守着。
**不清空**：`TestStagedReadLayerFailsClosed`（4 例，纯文件读取的读层锁）照跑 ——
否则"谁把 overlay 重映射删了"恰恰会在最快的档位下无人发现（test_review_v13_batch6 同款道理）；
`TestStagedOnlyChangedLines`（1.1s）也留着，让快跑档仍有一条真跑 overlay 的样例。
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _engine_copy import shared as _engine     # noqa: E402
from _tmpclean import mkdtemp_cleanup as _mkdtemp    # noqa: E402

# 测试隔离（与 tests/test_hooks_chain.py 同款）：run_gate 的运行日志钉到临时文件，
# 不污染真实 reports/gate_log.jsonl（项目有源码级锁要求本文件必须设它）。
# 2026-09-21：模块级 mkdtemp 没有 setUp/tearDown 可挂 —— 原先每跑一次留一个空目录
# （实测存量 v9b3log 272 / v9b2log 201 / v9log 219 份）。走进程退出强删助手。
# ⚠ 必须**先判再建**，不能写 `setdefault(..., _mkdtemp(...))`：实参在调用前求值，
# 那样即使 setdefault 最后没采纳（本文件 line 36 的 `_engine_copy` 已经设过这个变量），
# 目录也已经建出来了 —— 进程被 kill 时 atexit 不跑，就白留一份。
if "HARNESS_LOG_FILE" not in os.environ:
    os.environ["HARNESS_LOG_FILE"] = os.path.join(_mkdtemp("harness-v9log-"),
                                                 "gate_log.jsonl")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

BAD = "<template><eagle-tag /></template>\r\n<script>console.log(1)</script>\r\n"
GOOD = "<template><eagle-tag /></template>\r\n<script>const a = 1</script>\r\n"

FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _run(cmd, cwd=None, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd or ROOT,
                          stdin=subprocess.DEVNULL, env=env, timeout=180)


class _Repo:
    def __init__(self):
        self.path = tempfile.mkdtemp(prefix="v9chain_")
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def git(self, *a):
        return _run(["git", "-C", self.path] + list(a))

    def write(self, rel, text):
        p = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def env(self):
        """临时 HARNESS_CONFIG（gate 指向本临时仓）+ 策略覆盖逃生门。

        逃生门理由：采纳评审 P0-1 后，本地/环境覆盖层改**已注册 gate 的策略字段**一律被忽略
        → 夹具会被架空 → run_gate 拿 base 的真实仓路径（CI 上不存在）→ 假红。
        """
        cfg = os.path.join(tempfile.mkdtemp(prefix="v9cfg_"), "c.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("repos:\n  chain: %s\ngates:\n  gxgk_pc:\n    enabled: true\n"
                    "    repo: chain\n    script: checks/pc_code_check.py\n" % self.path)
        e = dict(os.environ)
        e["HARNESS_CONFIG"] = cfg
        e["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        return e

    def hook(self):
        """跑**工程副本**里的 pre-commit，不是真工程仓里的那份。

        2026-09-16（评审 v13 严重 4 的连带影响）：哨兵修好后，判据「工程仓有未提交的受保护
        文件改动」**真的生效**了 —— 而本文件的每个用例都是"模拟一次提交"，于是只要跑测试的人
        正在改门禁文件（维护中的常态），用例就会先被哨兵挡在门口。那与被测行为无关，却会记成红；
        改成"脏了就 skip"又会让覆盖在维护者最需要时整片消失。副本（tests/_engine_copy.py）
        让本文件**总能**跑到被测行为，且不依赖工作区状态。
        """
        return _run([PY, _engine().hook, "gxgk_pc", "--repo", self.path], env=self.env())


@_slow("真起 git 仓 + 真跑 pre-commit（每例 5.7~6.9s，含暂存副本展开）")
class TestStagedContentIsChecked(unittest.TestCase):
    def test_A_staged_bad_worktree_bad_blocks(self):
        r = _Repo()
        r.write("src/a.vue", BAD)
        r.git("add", "src/a.vue")
        out = r.hook()
        self.assertEqual(out.returncode, 1, "对照：staged 违规未被拦截\n" + out.stdout)

    def test_B_staged_bad_worktree_good_still_blocks(self):
        """**P0 回归锁**：工作区改回好版本，也要按暂存区的坏版本拦下。"""
        r = _Repo()
        r.write("src/b.vue", BAD)
        r.git("add", "src/b.vue")
        r.write("src/b.vue", GOOD)          # 工作区改回合规（暂存区仍是坏版本）
        staged = r.git("show", ":src/b.vue").stdout
        self.assertIn("console.log", staged, "前置条件：暂存区里必须是坏版本")

        out = r.hook()
        self.assertEqual(out.returncode, 1,
                         "P0 复现：门禁读了工作区、放过了暂存区的坏版本\n" + out.stdout)
        self.assertIn("b.vue", out.stdout, "拦截输出必须点名文件")
        self.assertIn("console.log", out.stdout, "拦截原因应指向 console.log（说明读的是暂存内容）")

    def test_C_staged_good_worktree_bad_passes(self):
        """**反向锁**：提交期只认 index —— 工作区未暂存的违规不得拦（也不得被检查）。"""
        r = _Repo()
        r.write("src/c.vue", GOOD)
        r.git("add", "src/c.vue")
        r.write("src/c.vue", BAD)           # 工作区有违规，但没进暂存区
        out = r.hook()
        self.assertEqual(out.returncode, 0,
                         "反向锁失败：提交期读了工作区未暂存的内容（会拦无关改动）\n" + out.stdout)

    def test_D_staged_deletion_is_not_checked(self):
        """已暂存删除的文件不进检查面：否则检查器只能读回工作区残留副本（P0 的另一面）。"""
        r = _Repo()
        r.write("src/d.vue", GOOD)
        r.git("add", "src/d.vue")
        r.git("commit", "-qm", "init")
        r.git("rm", "--cached", "-q", "src/d.vue")   # 暂存删除
        r.write("src/d.vue", BAD)                    # 工作区残留坏文件
        out = r.hook()
        self.assertEqual(out.returncode, 0,
                         "已暂存删除的文件被检查了（读到了工作区残留副本）\n" + out.stdout)


@_slow("真起 git 仓 + 真跑 pre-commit ×3（每例 16s：ASCII 对照 + 两类中文名各一遍）")
class TestNonAsciiStagedPaths(unittest.TestCase):
    r"""非 ASCII 路径的暂存展开（2026-09-16 实测缺陷的回归锁）

    背景：pre-commit 取暂存清单那处 `text=True` 却**没写 encoding** → Python 按本机 locale
    （中文 Windows = GBK）解码 git 输出的 UTF-8 路径。**同一个缺陷有两种症状**，取决于
    文件名那几个字的 UTF-8 字节能否被 GBK 解出来（实测确认，不是推断）：

      ① GBK 合法（如 `推广接入`）→ 解成 mojibake（`README_鎺ㄥ箍鎺ュ叆.md`）→ 下游
         `checkout-index` 报 "is not in the cache" → **rc=2 fail-closed**，提交被拦，
         而提示只说"配置/环境问题"。这是本机真实撞到的那次。
      ② GBK 非法（如 `中文文件名`）→ 解码在 subprocess 读线程里抛 UnicodeDecodeError →
         `r.stdout` 读成空 → `staged=[]` → `if not files: return 0` → **rc=0 静默放行**，
         暂存的违规文件**根本没被检查**。这条比 ① 严重得多：它给的是假绿。

    所以判据不能赌字节形态，只能用**不变式**：*路径名不得影响判定结果* ——
    同一份内容，ASCII 名与任意非 ASCII 名必须得到同样的 rc。E 覆盖违规（不得假绿），
    F 覆盖合规（不得假红），两类文件名在两条锁里都跑。
    """

    # 两类文件名，分别命中上面 ① ② 两种症状（E/F 都各跑一遍，不赌哪类更常见）
    NAMES = ("src/推广接入.vue", "src/中文文件名.vue")

    def _rc_for(self, rel, content):
        r = _Repo()
        r.write(rel, content)
        r.git("add", rel)
        return r.hook()

    def test_E_violation_is_blocked_whatever_the_path_name(self):
        """违规内容 + 任意文件名 → 必须 rc=1。

        缺陷期实测：`src/中文文件名.vue` 那条是 **rc=0**（暂存清单读成空 → 跳过检查放行），
        即"改了带中文名的文件就能绕过门禁"。这是本组锁里最该咬住的一条。
        """
        base = self._rc_for("src/e_ascii.vue", BAD)
        self.assertEqual(base.returncode, 1, "对照失效：ASCII 名违规竟未被拦\n" + base.stdout)
        for rel in self.NAMES:
            with self.subTest(rel=rel):
                out = self._rc_for(rel, BAD)
                self.assertNotEqual(
                    out.returncode, 0,
                    "非 ASCII 路径的暂存违规被**静默放行**（暂存清单解码失败 → 读成空 → 跳过检查）\n"
                    + out.stdout + out.stderr)
                self.assertEqual(out.returncode, 1,
                                 "非 ASCII 路径的暂存违规未被拦截\n" + out.stdout + out.stderr)

    def test_F_good_content_never_fails_closed_whatever_the_path_name(self):
        """合规内容 + 任意文件名 → 必须 rc=0，**尤其不得 rc=2**。

        缺陷期实测：`src/推广接入.vue` 那条是 **rc=2**（checkout-index 找不到 mojibake
        之后的路径名 → fail-closed）。本仓 31 个非 ASCII 路径对这类名字全部中招 ——
        表现为"谁改了带中文名的文件谁提交不了"。
        """
        base = self._rc_for("src/f_ascii.vue", GOOD)
        self.assertEqual(base.returncode, 0, "对照失效：ASCII 名合规竟被拦\n" + base.stdout)
        for rel in self.NAMES:
            with self.subTest(rel=rel):
                out = self._rc_for(rel, GOOD)
                self.assertNotEqual(
                    out.returncode, 2,
                    "路径解码错误导致暂存副本展开失败（fail-closed 假红，把合规提交拦下）\n"
                    + out.stdout + out.stderr)
                self.assertEqual(out.returncode, 0,
                                 "非 ASCII 路径的合规提交被拦\n" + out.stdout + out.stderr)


class TestStagedReadLayerFailsClosed(unittest.TestCase):
    """读取层自身的两向锁：仓内路径副本缺失必须**报错**，仓外路径不受影响。"""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "checks"))
        import rule_base                                   # noqa: E402
        self.rb = rule_base
        self.repo = tempfile.mkdtemp(prefix="v9rb_")
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        p = os.path.join(self.repo, "src", "x.vue")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(GOOD)

    def tearDown(self):
        for k in ("HARNESS_STAGED_OVERLAY", "HARNESS_STAGED_REPO"):
            os.environ.pop(k, None)

    def test_missing_overlay_copy_raises(self):
        os.environ["HARNESS_STAGED_OVERLAY"] = tempfile.mkdtemp(prefix="v9ov_")   # 空 overlay
        os.environ["HARNESS_STAGED_REPO"] = self.repo
        with self.assertRaises(FileNotFoundError):
            self.rb.read_bytes(os.path.join(self.repo, "src", "x.vue"))

    def test_outside_repo_path_unaffected(self):
        os.environ["HARNESS_STAGED_OVERLAY"] = tempfile.mkdtemp(prefix="v9ov_")
        os.environ["HARNESS_STAGED_REPO"] = self.repo
        outside = os.path.join(tempfile.mkdtemp(prefix="v9out_"), "note.txt")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("hello")
        self.assertEqual(self.rb.read_bytes(outside), b"hello")

    def test_overlay_copy_is_preferred(self):
        ov = tempfile.mkdtemp(prefix="v9ov_")
        os.makedirs(os.path.join(ov, "src"), exist_ok=True)
        with open(os.path.join(ov, "src", "x.vue"), "w", encoding="utf-8", newline="") as f:
            f.write(BAD)                                    # 副本=坏，工作区=好
        os.environ["HARNESS_STAGED_OVERLAY"] = ov
        os.environ["HARNESS_STAGED_REPO"] = self.repo
        self.assertIn(b"console.log", self.rb.read_bytes(os.path.join(self.repo, "src", "x.vue")))

    def test_no_env_reads_worktree(self):
        """编辑期/全量：不带任何环境变量时必须读工作区（不能把提交期语义泄漏进来）。"""
        self.assertIn(b"const a = 1",
                      self.rb.read_bytes(os.path.join(self.repo, "src", "x.vue")))


class TestStagedOnlyChangedLines(unittest.TestCase):
    """提交期 changed_lines 只认 index：与工作区 diff 取并集会把没暂存的行也算进来（豁免虚大）。"""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "checks"))
        import rule_base                                   # noqa: E402
        self.rb = rule_base
        self.repo = tempfile.mkdtemp(prefix="v9cl_")
        self.file = os.path.join(self.repo, "a.py")
        for k in ("HARNESS_STAGED_OVERLAY", "HARNESS_STAGED_REPO"):
            os.environ.pop(k, None)
        _run(["git", "-C", self.repo, "init", "-q"])
        # ⚠ 必须显式设身份：没有它时 Linux/CI 上 `git commit` 直接失败（author identity unknown），
        # 之后 `git diff --cached` 变成「对空树」→ 整个文件都算新增 → 本用例在 Linux 上假红
        # （Windows 靠全局 git 身份侥幸通过 —— 又一个"本机绿 ≠ CI 绿"）。
        _run(["git", "-C", self.repo, "config", "user.email", "t@t"])
        _run(["git", "-C", self.repo, "config", "user.name", "t"])
        with open(self.file, "w", encoding="utf-8", newline="") as f:
            f.write("l1\nl2\nl3\nl4\nl5\nl6\n")
        _run(["git", "-C", self.repo, "add", "a.py"])
        c = _run(["git", "-C", self.repo, "commit", "-qm", "init"])
        # 初始提交必须真成功：它失败时本用例的前提（"已跟踪的存量文件"）就不成立，
        # 继续跑只会得到误导性的断言 —— 出声比"看起来在测"重要。
        self.assertEqual(c.returncode, 0, "初始提交失败（本用例前提不成立）：%s" % (c.stderr or c.stdout))

    def tearDown(self):
        for k in ("HARNESS_STAGED_OVERLAY", "HARNESS_STAGED_REPO"):
            os.environ.pop(k, None)

    def test_union_vs_staged_only(self):
        # ① 暂存区：在顶部插入一行
        with open(self.file, "w", encoding="utf-8", newline="") as f:
            f.write("staged\nl1\nl2\nl3\nl4\nl5\nl6\n")
        _run(["git", "-C", self.repo, "add", "a.py"])
        # ② 工作区：在尾部再改一处（不暂存）
        with open(self.file, "w", encoding="utf-8", newline="") as f:
            f.write("staged\nl1\nl2\nl3\nl4\nl5\nworktree\n")

        union = set(self.rb.compute_changed_lines(self.repo, "a.py"))
        os.environ["HARNESS_STAGED_OVERLAY"] = tempfile.mkdtemp(prefix="v9ov_")
        os.environ["HARNESS_STAGED_REPO"] = self.repo
        staged = set(self.rb.compute_changed_lines(self.repo, "a.py"))

        self.assertTrue(staged, "提交期至少应包含 index 里新增的那一行")
        self.assertTrue(staged < union,
                        "提交期 changed_lines 必须严格小于"
                        "「staged ∪ 工作区」并集（工作区未暂存的行不该算本次改动）：staged=%s union=%s"
                        % (sorted(staged), sorted(union)))


if __name__ == "__main__":
    unittest.main()
