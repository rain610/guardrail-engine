# -*- coding: utf-8 -*-
"""评审 v10 §7-2（V10-2）：从 `27450c5` 恢复被我覆盖掉的 install/调度锁。

背景：我在轮次九新建 `tests/test_review_v9_batch2.py`（R10/R11 探针表）时**同名覆盖**了
上一轮的同名文件 → 4 条锁（custom hooksPath 备份枚举 / worktree 备份落点 / worktree 链式查找 /
链式调用顺序）在仓里消失，而外部评审用 repo-wide grep 抓到了（本轮唯一 P2 丢锁回归）。
现恢复为**独立文件名**，杜绝再次同名覆盖。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import mkdtemp_cleanup as _mkdtemp    # noqa: E402

# 2026-09-21：模块级 mkdtemp 没有 setUp/tearDown 可挂 —— 原先每跑一次留一个空目录
# （实测存量 v9b3log 272 / v9b2log 201 / v9log 219 份）。走进程退出强删助手。
# ⚠ 必须**先判再建**，不能写 `setdefault(..., _mkdtemp(...))`：实参在调用前求值，
# 那样即使 setdefault 最后没采纳（别的模块已经设过这个变量），目录也已经建出来了 ——
# 进程被 kill 时 atexit 不跑，就白留一份。
if "HARNESS_LOG_FILE" not in os.environ:
    os.environ["HARNESS_LOG_FILE"] = os.path.join(_mkdtemp("harness-v9b2log-"),
                                                 "gate_log.jsonl")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
SH = shutil.which("sh") or (
    r"C:\Program Files\Git\bin\sh.exe" if os.path.exists(r"C:\Program Files\Git\bin\sh.exe") else None)

MARKER = "ORIGINAL-HOOK-RAN"

# 快跑（HARNESS_FAST=1，2026-09-17 接线）：本文件**每一条**都要真起 git 仓 + 真跑
# install_hooks.sh / `git hook run`（4.4~9.2s），没有可以留在快跑档的纯文件锁 ——
# 所以三条类整体跳过，而不是逐例挑。
# **跳过的代价要说清**：这是"接管端仓钩子时会不会静默吞掉原有钩子"的唯一看守
# （自定义 hooksPath 枚举 / worktree 下备份落 common-dir / 分发器在 worktree 里找得到备份）。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _sh(cmd, cwd, env=None):
    return subprocess.run([SH] + cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd, stdin=subprocess.DEVNULL,
                          env=env, timeout=180)


def _git(repo, *a):
    return subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=120)


def _init(path):
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    with open(os.path.join(path, "a.vue"), "w", encoding="utf-8", newline="") as f:
        f.write("<template><eagle-tag /></template>\r\n<script>const a = 1</script>\r\n")
    _git(path, "add", "a.vue")
    _git(path, "commit", "-qm", "init")


def _write_original(hooks_dir):
    os.makedirs(hooks_dir, exist_ok=True)
    p = os.path.join(hooks_dir, "pre-commit")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/bin/sh\necho %s\nexit 0\n" % MARKER)
    os.chmod(p, 0o755)
    return p


def _cfg_env(repo, gate="gxgk_pc", script="checks/pc_code_check.py"):
    cfg = os.path.join(tempfile.mkdtemp(prefix="v9b2cfg_"), "c.yaml")
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("repos:\n  chain: %s\ngates:\n  %s:\n    enabled: true\n    repo: chain\n"
                "    script: %s\n" % (repo, gate, script))
    e = dict(os.environ)
    e["HARNESS_CONFIG"] = cfg
    e["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
    return e


@unittest.skipIf(not SH, "sh 不可用（Git Bash 未安装或 bin 未在 PATH）")
@_slow("真起 git 仓 + 真跑 install_hooks.sh（9.2s）")
class TestCustomHooksPathIsNotSwallowed(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="v9b2repo_")
        _init(self.repo)
        self.custom = tempfile.mkdtemp(prefix="v9b2custom_")
        self.orig = _write_original(self.custom)
        _git(self.repo, "config", "core.hooksPath", self.custom)

    def test_custom_hookspath_hooks_are_backed_up(self):
        r = _sh([os.path.join(ROOT, "sandbox", "install_hooks.sh"), "gxgk_pc"], cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.custom.replace("\\", "/")[-20:], r.stdout.replace("\\", "/"),
                      "安装输出没说明枚举来源是自定义 hooksPath：\n" + r.stdout)
        # 备份必须落在 common-dir 下（与分发器的查找规则一致）
        common = _git(self.repo, "rev-parse", "--git-common-dir").stdout.strip()
        if not os.path.isabs(common):
            common = os.path.join(self.repo, common)
        backed = os.path.join(common, "agentguard-original-hooks", "pre-commit")
        self.assertTrue(os.path.isfile(backed),
                        "自定义 hooksPath 里的原 hook 没被备份 → 接管即静默吞掉：%s" % backed)
        with open(backed, encoding="utf-8", errors="replace") as f:
            self.assertIn(MARKER, f.read())


@unittest.skipIf(not SH, "sh 不可用")
@_slow("真起 git 仓 + 真跑 install_hooks.sh + git hook run（8.6s）")
class TestChainInvokesOriginal(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="v9b2chain_")
        _init(self.repo)
        _write_original(os.path.join(self.repo, ".git", "hooks"))
        self.env = _cfg_env(self.repo)

    def test_dispatcher_chains_original_then_gate(self):
        r0 = _sh([os.path.join(ROOT, "sandbox", "install_hooks.sh"), "gxgk_pc"], cwd=self.repo,
                 env=self.env)
        self.assertEqual(r0.returncode, 0, r0.stdout + r0.stderr)
        # 真触发分发器（git hook run 会走 core.hooksPath）
        r = _git(self.repo, "hook", "run", "pre-commit")
        out = r.stdout + r.stderr
        self.assertIn("[chain] 调用端仓原有 pre-commit", out, "分发器没有链式调用原 hook：\n" + out)
        self.assertIn(MARKER, out, "原 hook 没真正跑起来（只有链式提示、没有它的输出）：\n" + out)


@unittest.skipIf(not SH, "sh 不可用")
@_slow("真起 main 仓 + worktree + 真跑安装与提交（每例 4.4~7.3s）")
class TestWorktreeBackupUsesCommonDir(unittest.TestCase):
    def test_worktree_install_backs_up_into_common_dir(self):
        main = tempfile.mkdtemp(prefix="v9b2main_")
        _init(main)
        _write_original(os.path.join(main, ".git", "hooks"))
        wt = os.path.join(tempfile.mkdtemp(prefix="v9b2wt_"), "wt")
        g = _git(main, "worktree", "add", "-q", "-b", "wtb", wt)
        self.assertEqual(g.returncode, 0, g.stderr)

        r = _sh([os.path.join(ROOT, "sandbox", "install_hooks.sh"), "gxgk_pc"], cwd=wt,
                env=_cfg_env(wt))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        backed = os.path.join(main, ".git", "agentguard-original-hooks", "pre-commit")
        self.assertTrue(os.path.isfile(backed),
                        "worktree 安装没把原 hook 备份到 common-dir（分发器在 worktree 里找不到）：%s"
                        % backed)

    def test_dispatcher_finds_backup_in_worktree(self):
        """反向验证：worktree 里提交时，分发器仍能找到 common-dir 下的备份并链式调用。"""
        main = tempfile.mkdtemp(prefix="v9b2main2_")
        _init(main)
        _write_original(os.path.join(main, ".git", "hooks"))
        wt = os.path.join(tempfile.mkdtemp(prefix="v9b2wt2_"), "wt")
        _git(main, "worktree", "add", "-q", "-b", "wtb2", wt)
        env = _cfg_env(wt)
        _sh([os.path.join(ROOT, "sandbox", "install_hooks.sh"), "gxgk_pc"], cwd=wt, env=env)
        r = subprocess.run(["git", "hook", "run", "pre-commit"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=wt,
                           stdin=subprocess.DEVNULL, env=env, timeout=120)
        out = r.stdout + r.stderr
        self.assertIn("[chain] 调用端仓原有 pre-commit", out,
                      "worktree 提交时分发器没找到备份（备份查找没走 common-dir）：\n" + out)
        self.assertIn(MARKER, out, "原 hook 在 worktree 场景没跑：\n" + out)


if __name__ == "__main__":
    unittest.main()
