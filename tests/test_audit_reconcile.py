# -*- coding: utf-8 -*-
"""A1 对账工具回归锁（tests/test_audit_reconcile.py）。

场景：通过记录 / 无记录 / 非通过记录（疑似绕过） / 作者过滤 / 钩子标注 / 文件过滤。
全部用临时 git 仓 + 临时台账（--log 注入），不触碰真实 reports/；
台账时间由 commit 的 %ct 反推（本地时区），保证任意时区环境下稳定。
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "sandbox", "audit_reconcile.py")

# 快跑（HARNESS_FAST=1，2026-09-17 接线）：本文件只有一个类，且**每例都慢**——setup 里
# 现建 git 仓并提交（每例 1.8~3.4s），实测墙钟 15.5s / 7 例全部 ≥1s。无可清空的廉价例。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _git(repo, *args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=e)
    assert r.returncode == 0, "git %r -> rc=%s %s" % (args, r.returncode, r.stderr)
    return r.stdout or ""


@_slow("每例现建 git 仓 + 真跑对账脚本（7 例全部 ≥1s，合计 15.5s）")
class AuditReconcileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 显式清理：不清理会在 GC 期抛 ResourceWarning（被测的 CI 严格句柄门槛会红）
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.log = os.path.join(self.base, "gate_log.jsonl")
        self.out = os.path.join(self.base, "report.md")
        self.repo = os.path.join(self.base, "repo1")
        os.makedirs(self.repo)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "me@example.com")
        _git(self.repo, "config", "user.name", "Me")
        _git(self.repo, "config", "commit.gpgsign", "false")
        _git(self.repo, "config", "core.autocrlf", "false")

    def tearDown(self):
        self.tmp.cleanup()

    # ---- helpers ----
    def _commit(self, rel, content="code\n", email=None, name=None):
        path = os.path.join(self.repo, rel.replace("/", os.sep))
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        _git(self.repo, "add", "-A")
        env = {}
        if email:
            env["GIT_AUTHOR_EMAIL"] = email
            env["GIT_COMMITTER_EMAIL"] = email
        if name:
            env["GIT_AUTHOR_NAME"] = name
            env["GIT_COMMITTER_NAME"] = name
        _git(self.repo, "commit", "-q", "-m", "c " + rel, "--no-verify", env=env or None)
        return int(_git(self.repo, "log", "-1", "--format=%ct").strip())

    def _write_log(self, entries):
        with io.open(self.log, "w", encoding="utf-8", newline="\n") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    @staticmethod
    def _rec(gate, epoch, rc, scope="targeted"):
        return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch)),
                "gate": gate, "scope": scope, "rc": rc, "files": 1, "ms": 5}

    def _run(self, extra=None):
        cmd = [sys.executable, SCRIPT, "--repo", self.repo, "--gate", "t_pc",
               "--log", self.log, "--since", "60d", "--out", self.out, "--json"]
        cmd += (extra or [])
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    # ---- cases ----
    def test_pass_record(self):
        ct = self._commit("src/a.vue")
        self._write_log([self._rec("t_pc", ct - 90, 0)])
        data = self._run()
        s = data["summary"]
        self.assertEqual((s["ok"], s["no_record"], s["blocked"]), (1, 0, 0))
        self.assertEqual(data["items"][0]["status"], "OK")
        self.assertTrue(os.path.isfile(self.out))

    def test_no_record(self):
        self._commit("src/a.vue")
        self._write_log([])
        data = self._run()
        s = data["summary"]
        self.assertEqual((s["ok"], s["no_record"]), (0, 1))
        self.assertEqual(data["items"][0]["status"], "NO_RECORD")
        self.assertFalse(data["items"][0]["hooks_ok"])

    def test_blocked_then_committed(self):
        ct = self._commit("src/a.vue")
        self._write_log([self._rec("t_pc", ct - 60, 1)])
        data = self._run()
        s = data["summary"]
        self.assertEqual(s["blocked"], 1)
        self.assertIn("rc=1", data["items"][0]["note"])

    def test_author_filter(self):
        self._commit("src/a.vue")
        self._commit("src/b.vue", email="other@example.com", name="Other")
        self._write_log([])
        data = self._run()
        self.assertEqual(len(data["items"]), 1)  # 默认只算本机作者
        data2 = self._run(["--all-authors"])
        self.assertEqual(len(data2["items"]), 2)
        data3 = self._run(["--author", "other@"])
        self.assertEqual(len(data3["items"]), 1)

    def test_hooks_annotation(self):
        _git(self.repo, "config", "core.hooksPath",
             "D:/Rain/project/eaglermc_harness/harness/hooks-dir")
        self._commit("src/a.vue")
        self._write_log([])
        data = self._run()
        self.assertTrue(data["items"][0]["hooks_ok"])

    def test_pre_record(self):
        ct = self._commit("src/a.vue")
        # 记录时间晚于提交（不在窗口内）：该 gate 台账从更晚才开始 → 早于台账期
        self._write_log([self._rec("t_pc", ct + 3600, 0)])
        data = self._run()
        s = data["summary"]
        self.assertEqual(s["pre_record"], 1)
        self.assertEqual(s["suspicious"], 0)
        self.assertEqual(data["items"][0]["status"], "PRE_RECORD")

    def test_non_code_commit_ignored(self):
        self._commit("README.md", content="# doc\n")
        self._write_log([])
        data = self._run()
        s = data["summary"]
        self.assertEqual(s["code_commits"], 0)
        self.assertEqual(s["suspicious"], 0)


if __name__ == "__main__":
    unittest.main()
