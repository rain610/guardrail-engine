# -*- coding: utf-8 -*-
"""`agentguard init` 与「wheel 自带引擎」的契约锁（评审 2026-10-21 §5.2）。

三个问题分开锁：
  ① **清单单一来源**：`setup.py` 与 `guardrail_engine/_shell.py` 各有一份打包清单（构建脚本不能
     import 包），两处必须逐字相同 —— 只打一半的引擎，装完会在第一次 `run` 时才炸。
  ② **init 真的能用**：引擎落到可写目录、本机配置不进包、指针写得回去、幂等、--force 可逆。
  ③ **未初始化时说得清**：轮子形态（无引擎根）跑普通命令，要明确指向 `init`，
     而不是一句"root not found"让人去猜。
"""
import ast
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI = os.path.join(ROOT, "guardrail_engine", "cli.py")
SHELL = os.path.join(ROOT, "guardrail_engine", "_shell.py")
SETUP = os.path.join(ROOT, "setup.py")
PY = sys.executable

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _consts(path, names):
    """从模块顶层 `NAME = (...)` 里取常量（只认字面量，AST 不执行代码）。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if isinstance(tgt, ast.Name) and tgt.id in names:
            out[tgt.id] = ast.literal_eval(node.value)
    return out


def _run(args, env, cwd=None):
    r = subprocess.run(args, cwd=cwd or ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class TestBundleManifestIsSingleSource(unittest.TestCase):
    """① `setup.py` 与 `_shell.py` 的引擎清单必须一致 —— 漂移一半就会出半成品包。"""

    def test_manifests_match(self):
        shell = _consts(SHELL, {"ENGINE_DIRS", "ENGINE_FILES", "ENGINE_EXCLUDE"})
        setup = _consts(SETUP, {"ENGINE_DIRS", "ENGINE_FILES", "ENGINE_EXCLUDE"})
        self.assertEqual(sorted(shell), ["ENGINE_DIRS", "ENGINE_EXCLUDE", "ENGINE_FILES"],
                         "壳层的清单常量被改名/删掉了，本锁会空转")
        for k in ("ENGINE_DIRS", "ENGINE_FILES", "ENGINE_EXCLUDE"):
            self.assertEqual(setup.get(k), shell.get(k),
                             "setup.py 与 _shell.py 的 %s 不一致：打出来的包与 init 复制的内容"
                             "会分叉（一处加了新目录、另一处没加）" % k)

    def test_local_config_is_never_bundled(self):
        shell = _consts(SHELL, {"ENGINE_EXCLUDE"})
        for name in ("config/harness.local.toml", "config/harness.local.yaml"):
            self.assertIn(name, shell["ENGINE_EXCLUDE"],
                          "本机配置 %s 没被排除：打包/init 会把打包机的仓库路径发给所有人" % name)

    def test_setup_py_fails_loudly_on_missing_engine(self):
        with open(SETUP, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("raise RuntimeError", src,
                      "引擎源缺失时 setup.py 必须失败，而不是产出一个没有引擎的 wheel")
        self.assertIn('"guardrail_engine", "_engine"', src, "setup.py 没把引擎铺进 guardrail_engine/_engine/")


class _InitBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="init-lock-")
        self.addCleanup(_rmtree_force, self.tmp)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.dest = os.path.join(self.tmp, "engine")
        self.env = dict(os.environ)
        for k in ("GUARDRAIL_ENGINE_DIR", "AGENTGUARD_DIR", "HARNESS_DIR", "PYTHONPATH",
                  "HARNESS_LOG_FILE", "HARNESS_LOG_DIR"):
            self.env.pop(k, None)
        self.env["HOME"] = self.home
        self.env["USERPROFILE"] = self.home
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"   # 否则 self-check 会在副本里留 __pycache__

    def init(self, *extra, dest=None, cli=None):
        return _run([PY, cli or CLI, "init", "--dir", dest or self.dest] + list(extra),
                    self.env, cwd=self.tmp)

    def frozen_source(self):
        """把引擎树冻到临时目录当「源」→ 该副本里 cli.py 的路径。

        幂等判据是「源与副本逐字节相同」，而并行套件里
        `test_hooks_chain.TestSentinelBlocksDirtyEngine` 会**故意**往工程仓的
        `checks/example_code_check.py` 追加一行探针（验证哨兵能发现脏引擎）。
        拿活树当源时，两次 init 之间源被它改过 → 误报「内容不同」（2026-10-25 实测：
        WSL 全量并行跑复现）。冻结一份源，前提「源不动」才真的成立。
        """
        shell = _consts(SHELL, {"ENGINE_DIRS", "ENGINE_FILES", "ENGINE_EXCLUDE"})
        src = os.path.join(self.tmp, "src")
        for name in shell["ENGINE_DIRS"]:
            d = os.path.join(ROOT, name)
            if os.path.isdir(d):
                shutil.copytree(d, os.path.join(src, name),
                                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        for name in shell["ENGINE_FILES"]:
            p = os.path.join(ROOT, name)
            if os.path.isfile(p):
                os.makedirs(src, exist_ok=True)
                shutil.copy2(p, os.path.join(src, name))
        for rel in shell["ENGINE_EXCLUDE"]:
            p = os.path.join(src, rel)
            if os.path.exists(p):
                os.remove(p)
        # 壳文件与 _copy_engine 同布局：<根>/guardrail_engine/*.py + 迁移期 agentguard/*.py
        for pkg in ("guardrail_engine", "agentguard"):
            os.makedirs(os.path.join(src, pkg), exist_ok=True)
            for fn in ("__init__.py", "cli.py", "_shell.py", "mcp.py"):
                p = os.path.join(ROOT, pkg, fn)
                if os.path.isfile(p):
                    shutil.copy2(p, os.path.join(src, pkg, fn))
        return os.path.join(src, "guardrail_engine", "cli.py")

    def pointer(self):
        return os.path.join(self.home, ".guardrail-engine", "harness.path")


class TestInitCopiesEngine(_InitBase):
    """② init 的正面契约：文件齐 / 本机配置不进去 / 指针正确 / 幂等。"""

    def test_copies_a_runnable_engine_and_writes_pointer(self):
        rc, out = self.init()
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "harness", "run_gate.py")), out)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "checks", "example_code_check.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "VERSION")))
        # 插件/fix_paths 认的布局：`<HARNESS>/guardrail_engine/mcp.py` 必须在；
        # 迁移期还要有 `<HARNESS>/agentguard/mcp.py`，老注册不必改。
        for rel in ("guardrail_engine/mcp.py", "agentguard/mcp.py"):
            mcp_copy = os.path.join(self.dest, *rel.split("/"))
            self.assertTrue(os.path.isfile(mcp_copy),
                            "引擎根缺 %s —— MCP 注册会指向空文件" % rel)
            with open(mcp_copy, "rb") as f:
                got = f.read()
            with open(os.path.join(ROOT, *rel.split("/")), "rb") as f:
                self.assertEqual(got, f.read(), "init 复制出来的 %s 与包内那份不是同一份" % rel)
        for name in ("harness.local.toml", "harness.local.yaml"):
            self.assertFalse(os.path.exists(os.path.join(self.dest, "config", name)),
                             "本机配置 %s 被复制进引擎了 —— 那是打包机的仓库路径" % name)
        self.assertFalse(glob.glob(os.path.join(self.dest, "**", "__pycache__"), recursive=True),
                         "缓存目录被复制进引擎了")
        with open(self.pointer(), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), self.dest)
        self.assertIn("self-check   : list-gates rc=0", out)
        # 所有权标记：upgrade.py 只刷新带它的目录（指针也会被写成仓库路径，光看指针分不清）
        mk = self._marker()
        self.assertEqual(mk.get("kind"), "guardrail-engine-init")
        self.assertEqual(mk.get("source"), "repo", "从工程树 init 出来的副本应标 source=repo")
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            self.assertEqual(mk.get("version"), f.read().strip())

    def _marker(self, dest=None):
        p = os.path.join(dest or self.dest, ".guardrail-engine-init.json")
        self.assertTrue(os.path.isfile(p),
                        "init 没写所有权标记 —— upgrade.py 会把这份副本当成来历不明的目录拒绝刷新")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def test_second_run_is_idempotent(self):
        cli = self.frozen_source()
        rc, out = self.init(cli=cli)
        self.assertEqual(rc, 0, out)
        rc2, out2 = self.init(cli=cli)
        self.assertEqual(rc2, 0, out2)
        self.assertIn("引擎已是最新", out2, out2)
        self.assertFalse(glob.glob(self.dest + ".disabled-*"),
                         "幂等重跑不该产生归档目录")
        # 旧版 init 装出来的副本没有标记：幂等重跑必须顺手补上（否则 upgrade.py 认不出它）
        os.remove(os.path.join(self.dest, ".guardrail-engine-init.json"))
        rc3, out3 = self.init(cli=cli)
        self.assertEqual(rc3, 0, out3)
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            self.assertEqual(self._marker().get("version"), f.read().strip())

    def test_second_run_is_idempotent_when_root_resolves_to_the_copy(self):
        """干净环境（CI）专属：init 写完指针后，find_root() 会解析到副本本身。

        此时「源」必须取自本文件的安装位置（`<repo>/guardrail_engine/..`），不能取 ROOT ——
        否则第二次 init 变成「源=目标」，按重叠拒绝并 rc=2。本地因为有
        `config/harness.local.toml`，解析链先命中配置，所以这条只在干净环境暴露。
        """
        cli = self.frozen_source()
        rc, out = self.init(cli=cli)
        self.assertEqual(rc, 0, out)
        env = dict(self.env, GUARDRAIL_ENGINE_DIR=self.dest)
        rc2, out2 = _run([PY, cli, "init", "--dir", self.dest], env, cwd=self.tmp)
        self.assertEqual(rc2, 0, out2)
        self.assertIn("引擎已是最新", out2, out2)
        self.assertNotIn("重叠", out2, out2)

    def test_existing_foreign_dir_requires_force_and_archives(self):
        os.makedirs(self.dest)
        marker = os.path.join(self.dest, "someone-elses-work.txt")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("不要删我\n")
        rc, out = self.init()
        self.assertEqual(rc, 2, "已有目录且内容不同却没拦住：\n%s" % out)
        self.assertIn("--force", out)
        self.assertTrue(os.path.isfile(marker), "被拒绝时不该动目标目录")

        rc2, out2 = self.init("--force")
        self.assertEqual(rc2, 0, out2)
        archives = glob.glob(self.dest + ".disabled-*")
        self.assertEqual(len(archives), 1, "--force 没有留下可回滚的归档：%s" % out2)
        self.assertTrue(os.path.isfile(os.path.join(archives[0], "someone-elses-work.txt")),
                        "归档里丢了原目录的内容（这不是可逆归档）")
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "harness", "run_gate.py")))
        # 标记只贴在**新的** init 副本上：归档里那份外来目录不是 init 管的，不该被冒认
        self._marker()
        self.assertFalse(os.path.exists(os.path.join(archives[0], ".guardrail-engine-init.json")),
                         "归档里的外来目录被写了 init 标记 —— 下次有人对归档目录跑 init 会被误认")

    def test_refuses_to_copy_into_itself(self):
        rc, out = self.init(dest=ROOT)
        self.assertEqual(rc, 2, "把工程根当目标目录却没拦住（归档会搬走源本身）：\n%s" % out)
        self.assertIn("重叠", out)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "harness", "run_gate.py")),
                        "拒绝重叠时误动了源目录")


class TestPointerResolvesAfterInit(_InitBase):
    """② 续：指针写完后，`_shell.find_root()` 在「包不在引擎树内」的形态下必须找到它。"""

    def _shell_form(self):
        """把四个壳模块复制成独立包（模拟 pip 安装形态：没有 VERSION / 没有 harness/）。"""
        parent = os.path.join(self.tmp, "pkg")
        for name in ("guardrail_engine", "agentguard"):
            pkg = os.path.join(parent, name)
            os.makedirs(pkg)
            src = os.path.join(ROOT, name)
            for n in os.listdir(src):
                if n.endswith(".py"):
                    shutil.copyfile(os.path.join(src, n), os.path.join(pkg, n))
        return parent

    def _root(self, pkg_parent):
        code = ("import sys; sys.path.insert(0, %r);"
                " import json, guardrail_engine._shell as s;"
                " json.dump(s.find_root(), sys.stdout)" % pkg_parent)
        rc, out = _run([PY, "-c", code], self.env, cwd=self.tmp)
        self.assertEqual(rc, 0, out)
        return json.loads(out.splitlines()[-1])

    def test_uninitialized_wheel_form_has_no_root(self):
        self.assertIsNone(self._root(self._shell_form()),
                          "夹具没造对：复制出来的壳仍能定位到引擎根")

    def test_pointer_written_by_init_is_resolved(self):
        rc, out = self.init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._root(self._shell_form()), self.dest,
                         "init 写了指针，但 find_root() 没解析它（装完不能用）")

    def test_uninitialized_wheel_tells_user_to_init(self):
        """③ 轮子形态跑普通命令：必须指向 init，而不是让人去猜 GUARDRAIL_ENGINE_DIR。"""
        pkg_parent = self._shell_form()
        pkg = os.path.join(pkg_parent, "guardrail_engine")
        os.makedirs(os.path.join(pkg, "_engine", "harness"))
        with open(os.path.join(pkg, "_engine", "harness", "run_gate.py"),
                  "w", encoding="utf-8") as f:
            f.write("# 只为让 packaged_engine() 认出来\n")
        code = ("import sys; sys.path.insert(0, %r);"
                " from guardrail_engine.cli import main; sys.exit(main(['list']))" % pkg_parent)
        rc, out = _run([PY, "-c", code], self.env, cwd=self.tmp)
        self.assertEqual(rc, 2, out)
        self.assertIn("init", out, "未初始化时没告诉用户怎么继续：\n%s" % out)

    def test_source_tree_self_locates_over_foreign_pointer(self):
        """P1-4：包**躺在引擎树内**时，自定位（`__file__` 向上）必须优先于用户级指针。

        否则一个陈旧/外来指针会把本地检出「劫持」到别的引擎目录 —— 跑的是别的仓的门禁，
        保护循环当场失效。造两棵都带 harness/run_gate.py 的树：srctree（包就在其中）与
        foreign（指针指向它）；断言 find_root() 落在 srctree，而不是被指针带去 foreign。
        """
        srctree = os.path.join(self.tmp, "srctree")
        os.makedirs(os.path.join(srctree, "harness"))
        with open(os.path.join(srctree, "harness", "run_gate.py"), "w", encoding="utf-8") as f:
            f.write("# 让 find_root 认出 srctree 是引擎树\n")
        pkg = os.path.join(srctree, "guardrail_engine")
        os.makedirs(pkg)
        src = os.path.join(ROOT, "guardrail_engine")
        for n in os.listdir(src):
            if n.endswith(".py"):
                shutil.copyfile(os.path.join(src, n), os.path.join(pkg, n))
        foreign = os.path.join(self.tmp, "foreign")
        os.makedirs(os.path.join(foreign, "harness"))
        with open(os.path.join(foreign, "harness", "run_gate.py"), "w", encoding="utf-8") as f:
            f.write("# foreign engine\n")
        ptrdir = os.path.join(self.home, ".guardrail-engine")
        os.makedirs(ptrdir, exist_ok=True)
        with open(os.path.join(ptrdir, "harness.path"), "w", encoding="utf-8") as f:
            f.write(foreign + "\n")
        got = self._root(srctree)
        self.assertEqual(os.path.realpath(got or ""), os.path.realpath(srctree),
                         "源码树内的包被外来指针劫持：find_root() 应自定位到 srctree（P1-4）")


if __name__ == "__main__":
    unittest.main()
