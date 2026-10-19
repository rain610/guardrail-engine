# -*- coding: utf-8 -*-
"""外部评审 v5 的回归锁（G1–G9 + Q③）。

每一个类对应评审报告 v5 的一个条目，锁的是"这个洞不会再回来"——
而不只是"这段代码现在长这样"：
  · 能用行为证明的用行为（untracked 判定 / 判据分级 / 分发器缺失 / 协议一致性）；
  · 行为代价过大或依赖客户端的，用"结构反向锁"（禁止退回旧写法），并在注释里
    写明为什么这样锁。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.path.insert(0, os.path.join(ROOT, "sandbox"))


def _read(rel):
    # G7：用 Path.read_text（不留未关闭句柄；严格模式 -W error::ResourceWarning 下必须干净）
    import pathlib
    return pathlib.Path(os.path.join(ROOT, rel)).read_text(encoding="utf-8", errors="replace")


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30)


class TestG1LegacyNewFileNotDowngraded(unittest.TestCase):
    """G1：legacy 门的**新建（untracked）文件**不得被当存量降级放行。"""

    def test_is_untracked_true_for_new_file_then_false_after_add(self):
        import rule_base
        d = tempfile.mkdtemp(prefix="g1lock-")
        try:
            _git(["init", "-q"], d)
            with io.open(os.path.join(d, "new.vue"), "w", encoding="utf-8", newline="") as f:
                f.write("<template></template>\n")
            self.assertTrue(rule_base.is_untracked(d, "new.vue"),
                            "新建未暂存文件必须被识别为 is_untracked（G1 的判据根基）")
            _git(["add", "new.vue"], d)
            self.assertFalse(rule_base.is_untracked(d, "new.vue"),
                             "已暂存后不应再算新建（否则每提交都把老文件当新文件拦）")
        finally:
            _rmtree_force(d)

    def test_is_untracked_false_when_git_unavailable_or_empty(self):
        import rule_base
        self.assertFalse(rule_base.is_untracked("", "a.vue"))
        self.assertFalse(rule_base.is_untracked("/definitely/not/a/repo", "a.vue"),
                         "git 不可用/路径无效时必须静默 False（不额外拦人）")

    def test_downgrade_condition_excludes_new_file(self):
        """legacy 降级的判据必须排除新建文件（旧写法只看 changed_lines，untracked 恒空
        → 新代码被当存量放行）。

        2026-09-16（评审 v13 补遗 B）更新：判据的**唯一实现**已从 PC/H5 两个 ruleset
        收敛到 `rule_base.Rule.severity`（原先 PC/H5/后端各一份、文件级规则一份没有，
        同一处语义三份实现）。因此本锁的锚点随之移到真正的源头，并**反向**断言两个
        ruleset 不再各留一份 —— 正是"改一处另两处不跟"的复发探测器。
        `WholeFileRule` 刻意不带 `changed_lines` 判据（文件级结论无法归因到行），
        故下面的定位只认第一处，且用行为锁覆盖其语义（见
        test_review_v13_batch2.TestGaoBWholeFileLegacyDowngrade）。
        """
        src = _read("checks/rule_base.py")
        i = src.find("and not ctx.changed_lines")
        self.assertTrue(i > 0, "rule_base 找不到按行口径的 legacy 降级条件（改结构请同步更新本锁）")
        self.assertIn("is_new_file", src[i:i + 200],
                      "降级条件里必须带 is_new_file 判据")
        for rel, ruleset in (("checks/pc_ruleset.py", "PC"), ("checks/h5_ruleset.py", "H5")):
            self.assertNotIn("def severity", _read(rel),
                             "%s ruleset 又自己实现了一份 severity —— 补遗 B 收敛后不该再有；"
                             "保留两份实现就等于「改一处另两处不跟」的旧病复发" % ruleset)

    def test_check_scripts_populate_is_new_file(self):
        """**行为锁**（2026-09-15 从源码文本锁升级；起因：评审 v3 把提交期口径从
        is_untracked 换成 is_new_in_index 后，文本锁失配——它只证明"写了那行字"）。

        真正的契约是行为：未跟踪的新文件 → ctx.is_new_file 必须为 True（否则 legacy 门
        会把新代码当存量降级）；已提交的文件 → False。
        """
        for k in ("HARNESS_STAGED_OVERLAY", "HARNESS_STAGED_REPO"):
            os.environ.pop(k, None)
        repo = tempfile.mkdtemp(prefix="v5new_")
        subprocess.run(["git", "-C", repo, "init", "-q"], capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "t@t"], capture_output=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "t"], capture_output=True)
        body = "<template>\n  <div>h</div>\n</template>\n<script>\nconst a = 1\n</script>\n"
        old_p = os.path.join(repo, "old.vue")
        new_p = os.path.join(repo, "new.vue")
        with io.open(old_p, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        subprocess.run(["git", "-C", repo, "add", "old.vue"], capture_output=True)
        subprocess.run(["git", "-C", repo, "commit", "-qm", "init"], capture_output=True)
        with io.open(new_p, "w", encoding="utf-8", newline="") as f:
            f.write(body)

        sys.path.insert(0, os.path.join(ROOT, "checks"))
        for mod_name in ("pc_code_check", "h5_code_check"):
            mod = __import__(mod_name)
            mod.REPO, mod.MODE, mod.TARGETED = repo, "strict", True
            ctx_new = mod.build_ctx(new_p, "strict")
            ctx_old = mod.build_ctx(old_p, "strict")
            self.assertTrue(ctx_new.is_new_file,
                            "%s：未跟踪新文件必须 is_new_file=True（否则 legacy 门把新代码当存量降级）"
                            % mod_name)
            self.assertFalse(ctx_old.is_new_file,
                             "%s：已提交的文件必须 is_new_file=False" % mod_name)


class TestG2DispatcherFailClosed(unittest.TestCase):
    """G2：分发器在实现文件缺失时**绝不能静默放行**（那会无声关闭全部提交期门禁）。"""

    def test_dispatcher_has_no_silent_pass_path(self):
        src = _read("harness/hooks-dir/pre-commit")
        self.assertIn("else", src, "分发器必须有缺失分支")
        i = src.find("else")
        self.assertIn("exit 2", src[i:], "缺失分支必须 fail-closed（exit 2），不能落到 exit 0")
        self.assertIn("缺失", src, "缺失分支必须讲清原因（不能只给退出码）")

    def test_dispatcher_missing_impl_blocks_commit(self):
        """行为：把实现文件藏起来 → 分发器必须非 0 退出（活体验证一次）。

        注意：Windows 上 `shutil.which("bash")` 常常拿到 System32 的 **WSL bash**，
        它看不懂 C:/ 路径（rc=127 "No such file"），那不是门禁的行为。所以这里显式找
        Git-for-Windows 的 MSYS bash；找不到就 skip（不把环境问题伪装成通过）。
        """
        # K5（评审 v6）：CI（ubuntu）上也要能跑这条活体锁，别整条 skip。
        msys = None
        if os.name == "nt":
            for cand in (r"C:/Program Files/Git/bin/bash.exe", r"C:/Program Files/Git/usr/bin/bash.exe"):
                if os.path.isfile(cand):
                    msys = cand
                    break
        else:
            msys = shutil.which("bash")     # POSIX：/bin/bash 直接可用
        if not msys:
            self.skipTest("无可用的非 WSL bash（Windows 上 System32 的 WSL bash 看不懂 C:/ 路径）")
        d = tempfile.mkdtemp(prefix="g2lock-")
        try:
            harness = os.path.join(d, "agentguard")
            os.makedirs(os.path.join(harness, "harness", "hooks-dir"))
            os.makedirs(os.path.join(harness, "harness", "hooks"))
            shutil.copy(os.path.join(ROOT, "harness", "hooks-dir", "pre-commit"),
                        os.path.join(harness, "harness", "hooks-dir", "pre-commit"))
            # 实现文件故意缺失 → 必须 fail-closed
            win = os.path.join(harness, "harness", "hooks-dir", "pre-commit").replace("\\", "/")
            msys_hook = ("/" + win[0].lower() + win[2:]) if os.name == "nt" else win
            env = dict(os.environ, HARNESS=harness.replace("\\", "/"))
            r = subprocess.run([msys, msys_hook], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env, timeout=60)
            self.assertNotEqual(r.returncode, 0,
                                "实现缺失时提交期门禁必须阻断（rc!=0），当前 rc=%s 输出=%r"
                                % (r.returncode, (r.stdout or "")[:200]))
            self.assertIn("缺失", (r.stdout or "") + (r.stderr or ""),
                          "阻断时要说清原因（不能只给一个退出码）")
        finally:
            _rmtree_force(d)

    def test_doctor_checks_dispatcher_integrity(self):
        src = _read("sandbox/doctor.sh")
        self.assertIn("pre-commit.py", src)
        self.assertIn("2.5/4", src, "doctor 必须有分发器完整性自检项（G2 的周期自检出口）")


class TestG3ResolveHarnessSelfRoot(unittest.TestCase):
    """G3：定位链要有"脚本自身位置"候选；真缺失时**出声一次**但不阻塞。"""

    def test_self_root_candidate_present_and_first(self):
        src = _read("integrations/hooks/agentguard-gate.py")
        i = src.find("def _resolve_harness")
        body = src[i:i + 900]
        self.assertIn("__file__", body, "候选链必须含脚本自身位置派生根（零配置、静态可得）")
        first_append = body.find("cands.append(")
        self.assertGreater(first_append, 0, "找不到候选链（改结构请同步更新本锁）")
        self.assertLess(body.find("__file__", first_append), body.find("HARNESS_DIR", first_append),
                        "自身位置应是**第一个被 append 的候选**（最可靠；文档串里的顺序不算）")

    def test_speaks_once_on_total_failure_but_not_blocking(self):
        src = _read("integrations/hooks/agentguard-gate.py")
        self.assertIn("_RESOLVE_WARNED", src, "全链失败必须出声（并只喊一次）")
        self.assertIn("放行不阻塞", src, "仍要守住「没装 harness 不卡同事」的承诺")

    def test_resolves_from_file_location_with_unrelated_cwd(self):
        """行为：cwd 无关 + 无环境变量时，靠脚本自身位置仍能定位工程根。"""
        env = dict(os.environ)
        for k in ("AGENTGUARD_DIR", "HARNESS_DIR", "EA_HARNESS_ROOT"):
            env.pop(k, None)
        code = ("import importlib.util as u,sys;sys.stdout.reconfigure(encoding='utf-8');"
                "s=u.spec_from_file_location('g', r'%s');m=u.module_from_spec(s);s.loader.exec_module(m);"
                "print(m.HARNESS or 'NONE')"
                % os.path.join(ROOT, "integrations", "hooks", "agentguard-gate.py").replace("\\", "/"))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, cwd=tempfile.gettempdir(), timeout=60)
        # K5（评审 v6）：不要断言目录名子串（改名克隆就假红）；断言"解析结果 == 本测试文件推导的工程根"
        want = os.path.normcase(os.path.normpath(ROOT))
        got = os.path.normcase(os.path.normpath((r.stdout or "").strip()))
        self.assertEqual(got, want,
                         "脚本自身位置派生根应命中本工程根（得到 %r，stderr=%r）" % (r.stdout, (r.stderr or "")[:200]))


class TestG4CheckerSummaryCountsWarn(unittest.TestCase):
    """G4：体检器合计行必须单列 ⚠（原先算进 ✅ → "待复核"在数字行里消失）。"""

    def test_summary_separates_warn(self):
        src = _read("sandbox/check_client_configs.py")
        self.assertIn("nwarn", src, "合计行必须有 warn 计数")
        i = src.find("合计 %d 家")
        self.assertIn("nwarn", src[i:i + 260], "warn 必须进入合计行输出")

    def test_warn_does_not_fail(self):
        """⚠ 仍不算失败（避免假红变噪声）—— ❌ 才让退出码为 1。

        2026-09-21 改锁：原先这里钉的是**整句表达式**（`return 1 if any(r[2] == "bad" …)`）。
        加项目级检查时那句话必然要改（项目级的 ❌ 也得算进去），于是它红了 —— 文本锁该有的
        行为。但把新表达式再写死一遍，下次同类改动还会红一次，而那次红的含义只是"字变了"、
        不是"⚠ 影响到退出码了"。所以改成锁**意图**：只有 bad 决定退出码。
        （本文件头注就写着「锁的是'这个洞不会再回来'——而不只是'这段代码现在长这样'」。）
        """
        src = _read("sandbox/check_client_configs.py")
        i = src.find("def main(")
        self.assertGreater(i, -1, "找不到 main()")
        body = src[i:]
        m = re.search(r"\n\s*bad = ([^\n]+)\n", body)
        self.assertIsNotNone(m, "找不到退出码判据那一行（改结构请同步更新本锁）")
        expr = m.group(1)
        self.assertNotIn('"warn"', expr, "⚠ 不许参与退出码（假红会变噪声）")
        self.assertNotIn('"skip"', expr, "未配置不算失败")
        self.assertIn('"bad"', expr, "❌ 必须让退出码为 1")
        self.assertIn("proj", expr,
                      "项目级的 ❌ 也必须算进退出码 —— 漏掉它，那 32 份 `zcode.json` 的锁就不响")
        self.assertRegex(body, r"return 1 if bad else 0", "退出码只能由 bad 决定")


class TestG5VerifySliceFilteredByGate(unittest.TestCase):
    """G5：verify 的日志切片必须按被测 gate 过滤（否则 F1 之后会把别人的账算成硬 FAIL）。"""

    def test_slice_greps_by_gate(self):
        src = _read("sandbox/verify_integrations.sh")
        lines = [l for l in src.splitlines() if "tail -n +" in l and "slice_" in l]
        self.assertTrue(lines, "找不到切片行（改结构请同步更新本锁）")
        self.assertIn("grep -F", lines[0], "切片必须过滤")
        self.assertIn("$GATE", lines[0], "过滤条件必须绑定当前被测 gate")

    def test_no_unfiltered_slice_left(self):
        src = _read("sandbox/verify_integrations.sh")
        for l in src.splitlines():
            if "tail -n +" in l and "slice_" in l:
                self.assertNotIn('"$LOG" >', l, "不允许退回「全量切片喂判定器」的旧写法")


class TestG6ReadmeClean(unittest.TestCase):
    """G6：README 不得含替换脚本反引用残留 / 过期计数。"""

    def test_no_backreference_leftovers(self):
        src = _read("README.md")
        for pat in ('"\\1.py"', "-k \\2", '"\\\\1'):
            self.assertNotIn(pat, src, "README 里残留了替换反引用（不可运行）：%s" % pat)

    def test_docs_consistency_suite_exists_and_checks_readme(self):
        """K3（评审 v6）：原来这里是 `assertTrue(src)` —— 恒真，等于没锁。换成真不变量。"""
        src = _read("tests/test_docs_consistency.py")
        self.assertIn("README", src, "文档一致性套件必须真的读 README 并断言")
        self.assertIn("def test_", src)


class TestG9WriteBlockedEdgeTightened(unittest.TestCase):
    """G9：wrote=False + 闲聊出现 Traceback/permission denied 不得误判成钩子故障。"""

    def _judge(self, out):
        import importlib.util
        spec = importlib.util.spec_from_file_location("vj", os.path.join(ROOT, "sandbox", "verify_judge.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.judge("claude", True, False, "", out)

    def test_chat_mentioning_traceback_is_not_suspect(self):
        r = self._judge("我上次看到过一个 Traceback，这次没写文件")
        self.assertEqual(r["verdict"], "probe_not_written",
                         "模型闲聊提到 Traceback 不该被当成钩子故障（G9）")
        self.assertFalse(r["fail"])

    def test_chat_mentioning_permission_denied_is_not_suspect(self):
        r = self._judge("Linux 里 permission denied 是常见问题")
        self.assertEqual(r["verdict"], "probe_not_written")
        self.assertFalse(r["fail"])

    def test_real_hook_traceback_still_suspect(self):
        r = self._judge("Traceback (most recent call last):\n  File x.py, line 1\nOSError")
        self.assertEqual(r["verdict"], "probe_write_suspect",
                         "真 traceback 仍必须判怀疑（收紧不能收掉真阳性）")
        self.assertTrue(r["fail"])

    def test_hook_permission_denied_with_context_still_suspect(self):
        r = self._judge("harness hook: permission denied writing gate_log.jsonl")
        self.assertEqual(r["verdict"], "probe_write_suspect",
                         "弱词 + 钩子语境仍算证据（共现规则）")


class TestQ3SharedAcceptValidation(unittest.TestCase):
    """Q③：accept 的输入校验必须**一层实现、两层调用**（防"改一处忘一处"）。"""

    def test_validate_accept_exists_and_used_twice(self):
        src = _read("sandbox/check_contracts.py")
        self.assertIn("def validate_accept(", src)
        self.assertGreaterEqual(src.count("validate_accept("), 3,
                                "至少：定义 1 处 + CLI 1 处 + accept_baseline 1 处")

    def test_cli_and_function_agree_on_bad_input(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("cc", os.path.join(ROOT, "sandbox", "check_contracts.py"))
        CC = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(CC)
        for label, kw in (("CI", dict(ci=True, interactive=True)),
                          ("非交互", dict(ci=False, interactive=False)),
                          ("短理由", dict(reason="太短", ci=False, interactive=True))):
            args = dict(reason="这是一段足够长的接受理由说明文本", ci=False, interactive=True)
            args.update(kw)
            err = CC.validate_accept(args["reason"], ci=args["ci"], interactive=args["interactive"])
            fresh = {ev: {"rc": 2, "stdout": {"kind": "empty"}, "stderr": {"kind": "text"}}
                     for ev in ("PreToolUse", "PostToolUse", "Stop")}
            ok, _, _ = CC.accept_baseline("claude", fresh, {"client": "x", **fresh}, args["reason"],
                                          False, interactive=args["interactive"], ci=args["ci"])
            self.assertTrue(bool(err) and not ok, "%s：两层必须同判拒收" % label)
        self.assertIsNone(CC.validate_accept("这是一段足够长的接受理由说明文本", ci=False, interactive=True))


class TestG8CoverageMissingRuleData(unittest.TestCase):
    """G8：覆盖度报告要把"缺规则级数据"的条数讲出来（把静默缺口语义化）。"""

    def test_count_is_reported(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("cov", os.path.join(ROOT, "harness", "coverage.py"))
        C = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(C)
        recs = [{"ts": "2026-09-13T20:00:00", "gate": "gxgk_pc", "rc": 1, "scope": "targeted", "ms": 10,
                 "rules": {"PC-CONSOLE": {"error": 1}}},
                {"ts": "2026-09-13T20:01:00", "gate": "gxgk_pc", "rc": 0, "scope": "targeted", "ms": 9}]
        rep = C.build_report(recs, {}, {})
        self.assertEqual(rep.get("n_no_rule_data"), 1, "缺规则级数据的条数必须被统计")
        self.assertIn("缺规则级数据", C.render_text(rep, "fake.jsonl"),
                      "文本报告里必须出现该计数（否则统计了也没人看见）")


if __name__ == "__main__":
    unittest.main()
