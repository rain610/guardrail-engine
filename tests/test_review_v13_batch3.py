# -*- coding: utf-8 -*-
r"""外部评审 v13（2026-09-16）第三批落地回归锁 —— 静默通过类。

主题与第二批（接线类）不同：这批锁的都是**「看起来跑了、其实没跑」**的形态 ——
每一处缺陷的表现都是 rc=0 + 一句「通过」，而不是报错。
覆盖：严重 5 / 高 7 / 中 14 / 中 15 / 中 16 / 中 20。

  严重 5  MCP 的 gate 名只做字符集校验 → `{"gate": "--list"}` 拼成命令行旗标，
          被 run_gate 在查 gate 之前分流掉，渲染成 ✅ rc=0 PASS 并落进审计台账。
  高 7    三个检查脚本对「不存在 / 非目标」的入参静默 continue（后端则静默退化成全量扫描，
          R5/R6 从 error 降成观察项），头行还宣称检查了 N 个文件。
  中 14   `script:` 遇到绝对路径会丢弃工程根 → 根外任意脚本原样执行。
  中 15   「已登记但 enabled: false」返回 rc=0，与「跑了且通过」在渲染和台账里都不可区分。
  中 16   台账 entry 字段：两个入口 setdefault（外层值永远胜出）、两个入口压根不设。
  中 20   `_config` 对 git 文件名 `.strip()` → 首尾带空格的合法文件名被改成不存在的路径。

开源裁剪（2026-10-20）：pc / h5 / backend 三个业务门禁脚本随业务仓一起删除，高 7 中
直接驱动它们的 7 个用例随之失效（对应的 fail-closed 实现也已不存在）。仍然保留的是与
具体端点无关的**收集器语义**（暂存后删除、真删除两条边界），以及其余四项缺陷 ——
它们的反向对照统一改用 `checks/example_code_check.py`（开源版唯一可执行的规则脚本）。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = os.path.join(ROOT, "checks")
HARNESS_LIB = os.path.join(ROOT, "harness")
PKG = os.path.join(ROOT, "agentguard")
HOOKS = os.path.join(HARNESS_LIB, "hooks")
AGENTS = os.path.join(HARNESS_LIB, "agents")
PY = sys.executable

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _sh(cmd, cwd=None, env=None, timeout=240):
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    # 父进程若在提交期跑测试，暂存 overlay 会串进来（读暂存副本而非工作区）——显式摘掉
    for _k in ("HARNESS_STAGED_REPO", "HARNESS_STAGED_OVERLAY", "GIT_INDEX_FILE"):
        e.pop(_k, None)
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, env=e,
                          stdin=subprocess.DEVNULL, timeout=timeout)


def _git(repo, *a):
    return _sh(["git", "-C", repo] + list(a))


def _write(path, text, newline=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def _read(path):
    # 用 Path.read_text：不留未关闭句柄（严格模式 -W error::ResourceWarning 下必须干净）
    import pathlib
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def _vue(script="let x = 1;", vfor='  <div v-for="i in list" :key="i">{{ i }}</div>'):
    return ('<template>\r\n' + vfor + '\r\n</template>\r\n<script setup>\r\n'
            + script + '\r\n</script>\r\n')


# 快跑（HARNESS_FAST=1，2026-09-17 接线）：本文件的慢在**每例现建 git 仓 / 临时工程 +
# 真起子进程跑 run_gate / cli.py**（开源裁剪后 13 例实测约 8.5s，多数 ≥1s）。
# 装饰判据落到类上时同样用「该类有单例 ≥1s」：TestZhong15DisabledGateIsNotPass（2 例共
# 1.6s，最慢 0.81s）**不接** —— 省不下多少秒，而它钉的是「disabled 的 gate 不许被记成 pass」。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


class _Repo:
    """临时 git 仓（与 batch2 同款；这里再加一个"文件名带前导空格"的写入能力）。"""

    def __init__(self, tmp):
        self.path = os.path.join(tmp, "repo")
        os.makedirs(self.path)
        _git(self.path, "init", "-q")
        _git(self.path, "config", "user.email", "v13b3@t")
        _git(self.path, "config", "user.name", "v13b3")
        self._n = 0

    def commit(self, rel, text):
        self._n += 1
        _write(os.path.join(self.path, rel), text)
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-qm", "base%d" % self._n)

    def loose(self, rel, text):
        _write(os.path.join(self.path, rel), text)

    def file(self, rel):
        return os.path.join(self.path, rel)


class _Fixture(unittest.TestCase):
    """公共底座：临时仓 + 一份只含本测试用得到的临时配置 + 台账隔离。"""

    #: 会被本套件改写、必须还原的环境变量（HARNESS_ENTRY 那两个是 中 16 的被测对象）
    _ENV_KEYS = ("HARNESS_CONFIG", "HARNESS_LOG_FILE", "HARNESS_STATS_FILE",
                 "HARNESS_ENTRY", "HARNESS_ENTRY_VIA")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v13b3_")
        self.addCleanup(_rmtree_force, self.tmp)
        self.repo = _Repo(self.tmp)
        self.ledger = os.path.join(self.tmp, "gate_log.jsonl")
        self._saved = {}
        for k in self._ENV_KEYS:
            self._saved[k] = os.environ.pop(k, None)

        def _restore():
            for k, v in self._saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.addCleanup(_restore)
        for p in (CHECKS, HARNESS_LIB, PKG):
            if p not in sys.path:
                sys.path.insert(0, p)

    def _cfg_path(self, gates, name="cfg.yaml"):
        """写一份临时 harness 配置。gates = [(gate名, script, 额外行), ...]。

        默认 enabled: true；额外行里自己写了 `enabled:` 就听它的（避免重复键）。
        """
        repo = self.repo.path.replace("\\", "/")
        lines = ["repos:", "  chain: %s" % repo, "gates:"]
        for gname, script, extra in gates:
            lines += ["  %s:" % gname, "    repo: chain",
                      "    script: %s" % script]
            if "enabled:" not in extra:
                lines.append("    enabled: true")
            lines.append(extra.rstrip("\n"))
        cfg = os.path.join(self.tmp, name)
        _write(cfg, "\n".join(lines) + "\n")
        return cfg

    def _env(self, cfg, extra=None):
        e = {"HARNESS_CONFIG": cfg, "HARNESS_LOG_FILE": self.ledger,
             "HARNESS_STATS_FILE": os.path.join(self.tmp, "stats.json")}
        if extra:
            e.update(extra)
        return e

    def _records(self):
        if not os.path.isfile(self.ledger):
            return []
        with io.open(self.ledger, encoding="utf-8") as f:
            return [json.loads(l) for l in f.read().splitlines() if l.strip()]

    def _run_gate(self, gate, args=(), env=None):
        return _sh([PY, os.path.join(HARNESS_LIB, "run_gate.py"), gate] + list(args), env=env)


# ============================================================ 严重 5：MCP 的 gate 名白名单
@_slow("每例现建 git 仓 + 起 MCP 子进程真跑（3 例 4.1s，最慢 2.66s）")
class TestSevere5McpGateNameWhitelist(_Fixture):
    """严重 5：`{"gate": "--list"}` 不是「无效名字」而是「有效旗标」—— 落在正则的盲区里。

    字符集 `^[A-Za-z0-9_.\\-]+$` 拦得住 `; rm -rf /`，拦不住 `--list`：它全是合法字符，
    拼出来就是 `run_gate.py --list`，被调度器在**查 gate 之前**分流成"打印清单"，
    于是 AI 得到「门禁通过」、台账得到一条 rc=0 —— 一条规则都没跑。
    """

    def _mcp(self):
        import mcp
        return mcp

    def test_flag_like_gate_name_is_rejected_without_touching_ledger(self):
        os.environ["HARNESS_CONFIG"] = self._cfg_path(
            [("v13b3_ex", "checks/example_code_check.py", "    mode: strict")])
        os.environ["HARNESS_LOG_FILE"] = self.ledger
        mcp = self._mcp()
        for bad in ("--list", "-l", "-h", "--summary"):
            text, is_err = mcp.tool_run_gate({"gate": bad})
            self.assertTrue(is_err, "%s 被当成合法 gate 名放行：%s" % (bad, text))
            # 断言渲染层没有出裁决词（不看裸 'PASS' 二字：拒绝文案里引用它作为反例）
            self.assertNotIn("✅", text, "%s 渲染出了通过标记：%s" % (bad, text))
            self.assertNotIn("门禁通过", text, "%s 渲染成了通过：%s" % (bad, text))
        self.assertEqual(self._records(), [],
                         "旗标冒充 gate 名仍然落了台账（假 PASS 进了审计链）")

    def test_unknown_gate_name_is_rejected(self):
        os.environ["HARNESS_CONFIG"] = self._cfg_path(
            [("v13b3_ex", "checks/example_code_check.py", "    mode: strict")])
        mcp = self._mcp()
        text, is_err = mcp.tool_run_gate({"gate": "v13b3_does_not_exist"})
        self.assertTrue(is_err, text)
        self.assertIn("unknown gate", text)

    def test_whitelist_does_not_wedge_the_real_path(self):
        """反向对照：白名单不能把正常 gate 一起挡掉（否则上面两条可能只是"全拦了"）。

        开源版用示例门禁当「正常 gate」：违规 console.log 必须照旧 rc=1 BLOCKED。
        """
        self.repo.commit("src/a.vue", _vue("let x = 1;"))
        self.repo.loose("src/bad.vue", _vue('console.log("leak");'))
        cfg = self._cfg_path([("v13b3_ex", "checks/example_code_check.py",
                               "    mode: strict")])
        os.environ["HARNESS_CONFIG"] = cfg
        os.environ["HARNESS_LOG_FILE"] = self.ledger
        mcp = self._mcp()
        text, is_err = mcp.tool_run_gate({"gate": "v13b3_ex",
                                          "files": [self.repo.file("src/bad.vue")]})
        self.assertFalse(is_err, text)
        self.assertIn("rc=1", text)
        self.assertIn("BLOCKED", text)
        recs = self._records()
        self.assertTrue(recs, "正常路径必须照旧落台账")
        self.assertEqual(recs[-1].get("entry"), "mcp")


# ============================================================ 中 15：未启用的 gate 不等于通过
class TestZhong15DisabledGateIsNotPass(_Fixture):
    """中 15：`enabled: false` → rc=0「未启用，跳过」，两条出口都对它撒谎。"""

    DISABLED = "    enabled: false\n"

    def _cfg(self):
        return self._cfg_path([("v13b3_off", "checks/example_code_check.py", self.DISABLED)])

    def test_ledger_marks_it_skipped(self):
        r = self._run_gate("v13b3_off", env=self._env(self._cfg()))
        self.assertEqual(r.returncode, 0, "退出码契约不动（0/1/2/3 跨三入口）\n" + (r.stdout or ""))
        self.assertIn("未启用", r.stdout)
        recs = self._records()
        self.assertEqual(len(recs), 1, "跳过的 gate 也必须在台账留痕：%r" % recs)
        self.assertTrue(recs[0].get("skipped"),
                        "跳过的记录与「跑了且通过」在台账里没有区别：%r" % recs[0])
        self.assertEqual(recs[0].get("scope"), "skipped")

    def test_mcp_renders_skipped_not_pass(self):
        os.environ["HARNESS_CONFIG"] = self._cfg()
        os.environ["HARNESS_LOG_FILE"] = self.ledger
        import mcp
        text, is_err = mcp.tool_run_gate({"gate": "v13b3_off"})
        self.assertIn("SKIPPED", text)
        self.assertNotIn("rc=0 PASS", text, "未启用的 gate 被渲染成「门禁通过」：%s" % text)
        self.assertFalse(is_err, "未启用不是调用错误，不该报 isError：%s" % text)


# ============================================================ 高 7：改动文件收集的边界
class TestChangedFilesCollection(_Fixture):
    """高 7 在开源裁剪后的存活部分：与端点无关的「什么算存在」边界。

    原先还锁 pc / h5 / backend 三个脚本的 fail-closed（不存在或非目标的入参 rc=2、
    不得退化成全量扫描、暂存后删除的文件仍要检查）；三个脚本随业务仓删除，
    对应的实现与用例一起移除。剩下这条锁的是收集器本身，任何端点都要遵守。
    """

    def test_deleted_file_is_not_collected(self):
        """**真的删除**（tracked → 工作区删掉）不该被交到检查器手上。

        删除的文件没有内容可查，但路径照样出现在 `diff --name-only` 里；收集端漏了这一层，
        「路径不存在」就会被下游判成配置错误，把「删掉一个文件」变成一次假拦截。
        """
        self.repo.commit("src/a.vue", _vue())
        self.repo.commit("src/b.vue", 'let b = 1;\n')
        os.remove(self.repo.file("src/b.vue"))
        import _config
        files, err = _config.changed_files_ex(self.repo.path, (".vue",), gate_name="v13b3_unreg")
        self.assertEqual(err, "", err)
        self.assertFalse([p for p in files if os.path.basename(p) == "b.vue"],
                         "已删除的文件仍被收集（下游会判成「路径不存在」→ rc=2）：%r" % files)


# ============================================================ 中 14：script 的信任边界
@_slow("每例现建 git 仓 + 真跑 run_gate 子进程（2 例 2.8s，最慢 2.06s）")
class TestZhong14ScriptMustStayInsideRoot(_Fixture):
    """中 14：`os.path.join(ROOT, script)` 遇到绝对路径会**丢弃 ROOT** —— 根外脚本原样执行。

    触达面是本机能写 config/harness.local.yaml（该文件在 .gitignore 内）或能设
    HARNESS_CONFIG 的人/agent。业务仓贡献者够不着，但这是执行边界，该收死。
    """

    def test_absolute_script_outside_root_is_refused(self):
        evil = os.path.join(self.tmp, "evil.py")
        _write(evil, "print('pwned')\n")
        cfg = self._cfg_path([("v13b3_ex", evil.replace("\\", "/"), "    mode: strict")])
        r = self._run_gate("v13b3_ex", env=self._env(cfg))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2, "根外脚本被执行了（或至少没被拒）\n" + out)
        self.assertIn("工程根之外", out)
        self.assertNotIn("pwned", out)
        self.assertEqual(self._records(), [], "被拒的调用不该落判定台账")

    def test_relative_script_inside_root_still_runs(self):
        """反向对照：根内相对路径照旧（别把正常配置一起拒了）。"""
        self.repo.commit("src/a.vue", _vue('console.log("leak");'))
        cfg = self._cfg_path([("v13b3_ex", "checks/example_code_check.py",
                               "    mode: strict")])
        r = self._run_gate("v13b3_ex", [self.repo.file("src/a.vue")], env=self._env(cfg))
        self.assertEqual(r.returncode, 1, (r.stdout or "") + (r.stderr or ""))
        self.assertNotIn("工程根之外", r.stdout)


# ============================================================ 中 16：台账 entry 字段
@_slow("每例现建 git 仓 + 真跑四个入口（3 例 5.0s，最慢 2.37s）")
class TestZhong16LedgerEntryIsTheRealEntry(_Fixture):
    """中 16：来源入口错记（setdefault 让外层值胜出）与漏记（干脆不设）各两处。"""

    def _assert_entry(self, want, via="cli"):
        recs = self._records()
        self.assertTrue(recs, "本次运行没落台账：%r" % recs)
        self.assertEqual(recs[-1].get("entry"), want,
                         "台账把真实执行者记错了：%r" % recs[-1])
        self.assertEqual(recs[-1].get("via"), via,
                         "上游入口丢了（写 setdefault 时它会顶替真实执行者）：%r" % recs[-1])

    def test_auto_gate_records_itself_and_keeps_upstream(self):
        self.repo.commit("src/a.vue", _vue("let x = 1;"))
        self.repo.loose("src/b.vue", _vue("let y = 2;"))
        cfg = self._cfg_path([("v13b3_ex", "checks/example_code_check.py",
                               "    mode: strict")])
        r = _sh([PY, os.path.join(AGENTS, "auto-gate.py"), "--repo", self.repo.path],
                env=self._env(cfg, {"HARNESS_ENTRY": "cli"}))
        self.assertIn(r.returncode, (0, 1), (r.stdout or "") + (r.stderr or ""))
        self._assert_entry("auto-gate")

    def test_post_edit_records_itself(self):
        """post-edit 原先**根本不设** HARNESS_ENTRY → 编辑期的判定在台账里没有来源。"""
        self.repo.commit("src/a.vue", _vue("let x = 1;"))
        self.repo.loose("src/b.vue", _vue("let y = 2;"))
        cfg = self._cfg_path([("v13b3_ex", "checks/example_code_check.py",
                               "    mode: strict")])
        r = _sh([PY, os.path.join(HOOKS, "post-edit.py"), "v13b3_ex", "--repo", self.repo.path],
                env=self._env(cfg, {"HARNESS_ENTRY": "cli"}))
        self.assertIn(r.returncode, (0, 1), (r.stdout or "") + (r.stderr or ""))
        self._assert_entry("post-edit")

    def test_all_four_entries_hard_assign(self):
        """四个入口对同一个变量的纪律必须一致 —— 少一个就有一类记录是空的。

        这条用结构锁而不是行为锁：pre-commit 的入口要跑通 guard + 暂存态整条链
        （tests/test_hooks_chain.py 已覆盖其行为），而这里要钉的是"四个文件写法一致"
        这件静态事实。setdefault 是缺陷本体：外层已有值时它**永远不生效**。
        """
        entries = {"pre-commit.py": "hook", "post-edit.py": "post-edit",
                   "pre-edit.py": "pre-edit"}
        for fn, name in entries.items():
            src = _read(os.path.join(HOOKS, fn))
            self.assertIn('os.environ["HARNESS_ENTRY"] = "%s"' % name, src,
                          "%s 没有硬赋值自己的入口名" % fn)
            self.assertNotIn('setdefault("HARNESS_ENTRY"', src,
                             "%s 用 setdefault → 外层残留值会顶替真实入口" % fn)
        src = _read(os.path.join(AGENTS, "auto-gate.py"))
        self.assertIn('os.environ["HARNESS_ENTRY"] = "auto-gate"', src)
        self.assertNotIn('setdefault("HARNESS_ENTRY"', src)


# ============================================================ 中 20：文件名不许被 strip
@_slow("每例现建 git 仓 + 真跑提交期入口（2 例 3.5s，最慢 2.43s）")
class TestZhong20FilenameSpacesSurvive(_Fixture):
    """中 20：`_config` 里 `p.strip()` 吃掉了 git 报出的**前导空格**（Windows 合法文件名）。

    单看这一步只是"路径算错"；接上高 7 的 `continue` 就成了一条完整漏检链：
    改了文件 → 路径不存在 → 静态跳过 → rc=0 通过。所以下面既锁单点、也锁端到端。
    """

    SPACED = " a.js"

    def test_changed_files_keeps_leading_space(self):
        self.repo.commit("src/a.vue", _vue())
        self.repo.loose(self.SPACED, 'console.log("leak");\n')
        import _config
        files, err = _config.changed_files_ex(self.repo.path, (".js",), gate_name="v13b3_unreg")
        self.assertEqual(err, "", err)
        hit = [p for p in files if os.path.basename(p) == self.SPACED]
        self.assertTrue(hit, "前导空格被 strip 掉了，收集到的是不存在的路径：%r" % files)
        self.assertTrue(os.path.exists(hit[0]), "收集到的路径必须真实存在：%r" % hit[0])

    def test_end_to_end_post_edit_blocks_on_spaced_filename(self):
        """端到端：带空格的未跟踪违规文件，编辑期门禁必须拦下（修复前是 rc=0「无改动」）。"""
        self.repo.commit("src/a.vue", _vue("let x = 1;"))
        self.repo.loose(self.SPACED, 'console.log("leak");\n')
        cfg = self._cfg_path([("v13b3_ex", "checks/example_code_check.py",
                               "    mode: strict")])
        r = _sh([PY, os.path.join(HOOKS, "post-edit.py"), "v13b3_ex", "--repo", self.repo.path],
                env=self._env(cfg))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1,
                         "带空格的违规文件全链路静默通过（strip → 路径不存在 → 跳过）\n" + out)
        self.assertIn("NO-CONSOLE", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
