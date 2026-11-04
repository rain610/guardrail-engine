# -*- coding: utf-8 -*-
r"""外部评审 v13（2026-09-16）第二批落地回归锁 —— 开源裁剪后的存活部分。

本模块锁的是一条主线：**机制写好了，接线漏了一半**。
每条修复都必须有"真实入口 + 真实退出码"的行为锁，不 mock 规则内部。

开源裁剪（2026-10-20）：业务门禁 pc/h5/backend 及其规则集已删除，本模块只保留
与示例门禁（example_code_check.py）等义的部分：
  严重 2  .js/.ts/.tsx 纯脚本文件必须真的过一遍正则规则族；.css 刻意不进代码段。
  中 12   harness_guard 的 [harness] 标记只对工程仓自身生效，端仓不得借此绕过。
高 A（注释不当命中）/ 高 B（legacy 存量降级）/ 中 10（disabled_rules 穿透语法层）/
中 11（后端 disabled_rules / 行内豁免）随对应规则脚本删除；行内豁免的等义锁在
tests/test_entry_level.py。
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CHECKS = os.path.join(ROOT, "checks")
HARNESS_LIB = os.path.join(ROOT, "harness")
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


# 快跑（HARNESS_FAST=1，2026-09-17 接线）：本文件的慢在**每例现建 git 仓 + 真起子进程跑
# run_gate / cli.py / 钩子**（实测墙钟 29.6s，18 例里 15 例 ≥1s，合计 28.0s）。
# 装饰判据落到类上时同样用「该类有单例 ≥1s」：TestZhong12GuardNotBypassedByTag（3 例共
# 1.6s，最慢 0.60s）**不接**。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


class _Repo:
    """临时 git 仓：commit(基线) / stage(本次改动) / 不 add 的裸文件（新建未跟踪）。"""

    def __init__(self, tmp):
        self.path = os.path.join(tmp, "repo")
        os.makedirs(self.path)
        _git(self.path, "init", "-q")
        _git(self.path, "config", "user.email", "v13@t")
        _git(self.path, "config", "user.name", "v13")
        self._n = 0

    def commit(self, rel, text):
        self._n += 1
        _write(os.path.join(self.path, rel), text, newline="")
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-qm", "base%d" % self._n)

    def stage(self, rel, text):
        _write(os.path.join(self.path, rel), text, newline="")
        _git(self.path, "add", "-A")

    def file(self, rel):
        return os.path.join(self.path, rel)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v13b2_")
        self.addCleanup(_rmtree_force, self.tmp)
        self.repo = _Repo(self.tmp)
        if CHECKS not in sys.path:
            sys.path.insert(0, CHECKS)

    def _cfg(self, name, script, extra="", mode="strict"):
        """写一份只有本测试用得到的临时 harness 配置，返回环境变量。"""
        cfg = os.path.join(self.tmp, "cfg.yaml")
        _write(cfg,
                "repos:\n  chain: %s\ngates:\n  %s:\n    enabled: true\n"
                "    repo: chain\n    script: %s\n    mode: %s\n%s"
                % (self.repo.path.replace("\\", "/"), name, script, mode, extra))
        return {"HARNESS_CONFIG": cfg,
                "HARNESS_LOG_FILE": os.path.join(self.tmp, "gate_log.jsonl"),
                "HARNESS_STATS_FILE": os.path.join(self.tmp, "stats.json")}

    def _env_example(self, extra=""):
        return self._cfg("v13b2", "checks/example_code_check.py", extra)


# ============================================================ 严重 2：纯脚本文件
@_slow("每例现建 git 仓 + 真跑示例门禁子进程（4 例，几秒）")
class TestSevere2ScriptFilesAreChecked(_Fixture):
    """严重 2 的行为锁（开源裁剪后落到示例门禁）：.js/.ts/.tsx 也必须真的过正则规则族。

    修复前（业务门禁版本）`ctx.script` 只对 SFC 有值 → 这些文件的 script 恒空
    → 整族规则静默不跑。示例门禁是开源版唯一的规则执行器，这几条锁保证
    它按扩展名收集文件的边界不被改坏。
    """

    def _example(self, env, path, name="v13b2"):
        return _sh([PY, os.path.join(CHECKS, "example_code_check.py"),
                    "--gate", name, "--repo", self.repo.path, path], env=env)

    def test_js_file_violations_block(self):
        """新写的 .js 里违规必须被拦下（旧实现整类文件被漏掉）。"""
        self.repo.commit("src/a.js", "let a = 1;\r\nlet b = 2;\r\n")
        self.repo.stage("src/a.js",
                        'let a = 1;\r\nconsole.log("leak");\r\n'
                        'let ip = "http://10.0.0.1/api";\r\n')
        r = self._example(self._env_example(), self.repo.file("src/a.js"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1, "纯 .js 文件的违规必须拦下\n" + out[-800:])
        self.assertIn("NO-CONSOLE", out)
        self.assertIn("NO-HARDIP", out)

    def test_ts_file_gets_regex_rules_without_false_syntax_error(self):
        """TS 也要过正则规则 —— 且示例门禁没有 node 语法层，不得凭空报语法错误。"""
        self.repo.commit("src/t.ts", "let a = 1;\r\n")
        self.repo.stage("src/t.ts",
                        "interface Foo {\r\n  a: number;\r\n}\r\n"
                        "const x: number = 1;\r\nconsole.log(x);\r\n")
        r = self._example(self._env_example(), self.repo.file("src/t.ts"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("NO-CONSOLE", out, ".ts 上的正则规则没跑到\n" + out[-800:])
        self.assertNotIn("语法错误", out, "示例门禁没有语法层，不该出现语法错误结论\n" + out[-800:])
        self.assertEqual(r.returncode, 1, out[-800:])

    def test_tsx_file_not_flagged_as_syntax_error(self):
        """JSX 同理：.tsx 必须被当普通文本扫描，不得出现语法错误结论。"""
        self.repo.commit("src/c.tsx", "let a = 1;\r\n")
        self.repo.stage("src/c.tsx", "let a = 1;\r\nconst A = () => <div>hi</div>;\r\n")
        r = self._example(self._env_example(), self.repo.file("src/c.tsx"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertNotIn("语法错误", out, "JSX 被当成语法错误\n" + out[-800:])
        self.assertEqual(r.returncode, 0, out[-800:])

    def test_css_is_deliberately_not_a_code_region(self):
        """样式文件**刻意**不进代码段：内容里的 console.log / IP 都不该被当违规。

        走 `--all` 口径 —— 示例门禁的全量收集按扩展名白名单收文件；显式点名的路径
        则按「用户明确要求查这个文件」处理，不做扩展名过滤（两条分支的契约不同）。
        """
        self.repo.commit("src/s.css", ".a {\r\n  color: red;\r\n}\r\n")
        self.repo.stage("src/s.css",
                        ".b { content: 'console.log(1) 10.0.0.1'; }\r\n")
        r = _sh([PY, os.path.join(CHECKS, "example_code_check.py"),
                 "--gate", "v13b2", "--repo", self.repo.path, "--all"],
                env=self._env_example())
        out = (r.stdout or "") + (r.stderr or "")
        self.assertNotIn("NO-CONSOLE", out, "样式文件不该进代码段\n" + out[-800:])
        self.assertNotIn("NO-HARDIP", out, "样式文件不该进代码段\n" + out[-800:])
        self.assertEqual(r.returncode, 0, out[-800:])


class TestZhong12GuardNotBypassedByTag(_Fixture):
    """中 12：`harness_guard` 里存在一份极性相反的分支 ——
    "非 self 模式 + 提交消息含 [harness] → 放行"，与紧随其后的注释（不放行）正好相反。

    这里把工程仓的"未提交受保护文件"用桩固定住（真读的话结果取决于跑测试时的工作区状态，
    那不是锁，是碰运气），只把**判据**（模式 + 标记）暴露出来测。
    """

    PROTECTED = "checks/example_code_check.py"

    def _run_guard(self, argv, dirty=True):
        if HARNESS_LIB not in sys.path:
            sys.path.insert(0, HARNESS_LIB)
        import _config
        import harness_guard
        # 桩点随哨兵换名一起搬：2026-09-16 换成 changed_files_ex，2026-11-04（P1-2）再换成
        # changed_paths_ex（含删除/改名）。还桩着旧名字等于**这条锁悄悄失去作用** —— 真去读工程仓，
        # 结果就取决于跑测试时工作区脏不脏，那不是锁，是碰运气（本类 docstring 里已写明这个顾虑）。
        # 语义也随之变：changed_paths_ex 返回**相对**路径集合，guard 直接 _in_protected(p)，不再 relpath。
        orig = _config.changed_paths_ex
        _config.changed_paths_ex = (
            (lambda repo, *a, **k: ({self.PROTECTED}, ""))
            if dirty else (lambda repo, *a, **k: (set(), "")))
        old_argv, old_cwd = sys.argv, os.getcwd()
        try:
            sys.argv = ["harness_guard.py"] + list(argv)
            os.chdir(self.tmp)      # 非工程根 → 不触发"本仓提交"的 self 模式识别
            return harness_guard.main()
        finally:
            sys.argv = old_argv
            os.chdir(old_cwd)
            _config.changed_paths_ex = orig

    def test_tag_in_message_does_not_pass_in_end_repo(self):
        """端仓库提交消息里带 [harness] ≠ harness 维护 → 门禁文件脏时仍须拦截。"""
        rc = self._run_guard(["demo_pc", "[harness] 顺手带个标记"])
        self.assertEqual(rc, 1,
                         "端仓库提交只要消息里带 [harness] 就放行 —— 现成的绕过口子")

    def test_self_mode_with_tag_still_passes(self):
        """反向对照：工程仓自身的 [harness] 维护提交必须照旧放行（别把合法出口一起堵了）。"""
        rc = self._run_guard(["--self", "[harness] 维护"])
        self.assertEqual(rc, 0, "self 模式的维护标记被误伤 —— 会自指死结")

    def test_clean_engine_passes_without_tag(self):
        rc = self._run_guard(["demo_pc"], dirty=False)
        self.assertEqual(rc, 0, "门禁文件干净时不该拦任何提交")


if __name__ == "__main__":
    unittest.main(verbosity=2)
