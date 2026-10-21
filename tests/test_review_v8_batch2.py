# -*- coding: utf-8 -*-
"""Codex 批判性评审第 2 批的回归锁：hooksPath 接管（B1）+ local/env 策略字段（B2）。

B1：core.hooksPath 是**整目录接管**，仓里原有的 pre-commit/commit-msg/pre-push 会静默失效。
    处置：安装时枚举+备份到 <gitdir>/agentguard-original-hooks/，分发器在执行门禁**之前**
    链式调用同名备份（接管 ≠ 吞掉别人的质量流程）；doctor 检"是否有原 hook 未被备份"。
B2：local/HARNESS_CONFIG 走任意键深合并 → 本机即可 enabled:false / 降 mode / 开 legacy_baseline，
    与 guard 哨兵"防自我放行"的目标矛盾。处置：策略字段只认 base，覆盖则出声并忽略
    （只有 repos/paths 路径映射允许覆盖，且覆盖也要留痕）。
"""
import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))


def _read(rel):
    return pathlib.Path(os.path.join(ROOT, rel)).read_text(encoding="utf-8", errors="replace")


# 快跑（HARNESS_FAST=1，2026-09-17 接线）：本文件的慢**全在 TestB1 的三条端到端**上
# （真起 git 仓 + 真跑 install_hooks.sh / 真提交触发链式备份：12.8 / 6.0 / 2.1s，合计 20.8s，
# 全文件 21.1s）。因此这里**用方法级而不是类级**：同一个类里还有三条纯源码级契约锁
# （dispatchers/commit-msg 只读文件、0.00~0.03s）—— 见 batch6 同款道理，源码级锁快跑档
# 照跑，否则"有人把链式调用删了"恰恰会在最快的档位下无人发现。
# TestB2PolicyOverrides 整个不接（6 例最慢 0.05s）—— M18 的判据
# test_escape_hatch_reports_what_it_let_through 就在这个类里，接掉它等于那条变异在
# 快跑档里没人守。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _msys_bash():
    if os.name == "nt":
        for c in (r"C:/Program Files/Git/bin/bash.exe", r"C:/Program Files/Git/usr/bin/bash.exe"):
            if os.path.isfile(c):
                return c
        return None
    return shutil.which("bash")


class TestB1HooksPathChain(unittest.TestCase):
    """B1：接管前备份 + 链式调用 + doctor 检测。"""

    def test_dispatchers_have_chain_block(self):
        for name in ("pre-commit", "commit-msg"):
            src = _read("harness/hooks-dir/%s" % name)
            self.assertIn("agentguard-original-hooks", src,
                          "%s 分发器必须能链式调用端仓原有 hook（否则接管即吞掉）" % name)

    def test_commit_msg_chain_is_reachable(self):
        """commit-msg 有多个提前 exit：链式块必须在它们**之前**（否则端仓里永不执行）。"""
        src = _read("harness/hooks-dir/commit-msg")
        i_chain = src.find("agentguard-original-hooks")
        # 收窄到那条"提交的不是 harness 仓就退出"的判定（文件里另有更早的 `|| exit 0`，
        # 泛搜会误判）——这条之后端仓就再也跑不到了。
        i_early = src.find('[ "$TOP" = "$ENGROOT" ] || exit 0')
        self.assertGreater(i_chain, 0)
        self.assertLess(i_chain, i_early,
                        "链式块必须在「本仓判定 → exit 0」之前，否则端仓里根本跑不到")

    @_slow("真起端仓 + 真跑分发器与备份 hook（12.8s，本文件最贵的一条）")
    def test_chain_actually_invokes_backed_up_hook(self):
        bash = _msys_bash()
        if not bash:
            self.skipTest("无可用 bash")
        work = tempfile.mkdtemp(prefix="b1-")
        try:
            repo = os.path.join(work, "end_repo")
            os.makedirs(os.path.join(repo, ".git", "agentguard-original-hooks"))
            subprocess.run(["git", "init", "-q", "."], cwd=repo, capture_output=True)
            bak = os.path.join(repo, ".git", "agentguard-original-hooks", "pre-commit")
            with io.open(bak, "w", encoding="utf-8", newline="") as f:
                f.write('#!/bin/sh\necho "[original-hook] 端仓自己的 pre-commit"\nexit 0\n')
            os.chmod(bak, 0o755)
            # 直接执行共享分发器（模拟 git 在端仓里调用它）
            r = subprocess.run([bash, os.path.join(ROOT, "harness", "hooks-dir", "pre-commit")],
                               cwd=repo, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            out = (r.stdout or "") + (r.stderr or "")
            self.assertIn("original-hook", out,
                          "分发器必须在跑门禁前链式调用端仓原有 hook；实际输出=%s" % out[:300])
        finally:
            _rmtree_force(work)

    @_slow("真起端仓 + 真跑分发器（2.1s）")
    def test_chain_failure_blocks(self):
        """端仓原 hook 失败必须阻断（接管不能把别人的门禁降级成"参考意见"）。"""
        bash = _msys_bash()
        if not bash:
            self.skipTest("无可用 bash")
        work = tempfile.mkdtemp(prefix="b1b-")
        try:
            repo = os.path.join(work, "end_repo2")
            os.makedirs(os.path.join(repo, ".git", "agentguard-original-hooks"))
            subprocess.run(["git", "init", "-q", "."], cwd=repo, capture_output=True)
            bak = os.path.join(repo, ".git", "agentguard-original-hooks", "pre-commit")
            with io.open(bak, "w", encoding="utf-8", newline="") as f:
                f.write('#!/bin/sh\necho "[original-hook] 失败"\nexit 7\n')
            os.chmod(bak, 0o755)
            r = subprocess.run([bash, os.path.join(ROOT, "harness", "hooks-dir", "pre-commit")],
                               cwd=repo, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
            self.assertNotEqual(r.returncode, 0,
                                "原 hook 非 0 退出必须阻断本次提交（rc=%s）" % r.returncode)
            self.assertIn("[original-hook]", (r.stdout or "") + (r.stderr or ""))
        finally:
            _rmtree_force(work)

    @_slow("真起端仓 + 真跑 install_hooks.sh（6.0s）")
    def test_installer_enumerates_and_backs_up(self):
        bash = _msys_bash()
        if not bash:
            self.skipTest("无可用 bash")
        work = tempfile.mkdtemp(prefix="b1c-")
        try:
            repo = os.path.join(work, "end_repo3")
            os.makedirs(os.path.join(repo, ".git", "hooks"))
            subprocess.run(["git", "init", "-q", "."], cwd=repo, capture_output=True)
            own = os.path.join(repo, ".git", "hooks", "pre-commit")
            with io.open(own, "w", encoding="utf-8", newline="") as f:
                f.write('#!/bin/sh\necho "团队自己的检查"\nexit 1\n')
            os.chmod(own, 0o755)
            inst = os.path.join(ROOT, "sandbox", "install_hooks.sh").replace("\\", "/")
            r = subprocess.run([bash, inst, "example"], cwd=repo, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
            backup = os.path.join(repo, ".git", "agentguard-original-hooks", "pre-commit")
            self.assertTrue(os.path.isfile(backup),
                            "安装时端仓原有 pre-commit 必须被备份（输出=%s）"
                            % ((r.stdout or "") + (r.stderr or ""))[:300])
        finally:
            _rmtree_force(work)

    def test_doctor_detects_swallowed_hooks(self):
        src = _read("sandbox/doctor.sh")
        self.assertIn("agentguard-original-hooks", src, "doctor 必须能发现「被接管但没备份」的原 hook")

    def test_doctor_scans_the_dirs_git_and_installer_actually_use(self):
        """2026-09-23 评审 M3：医生要扫**git 真会执行**、**安装脚本真会备份**的那两个目录。

        原判据按 `--absolute-git-dir` 拼 `<gitdir>/hooks`。对 worktree 那是错的：git 的 hook 查找
        走 **common-dir**（实测 `git -C <worktree> rev-parse --git-path hooks` → 主仓 `.git/hooks`，
        而往 `<worktree-gitdir>/hooks/` 放可执行 pre-commit 提交时**根本没跑**）。后果是本机那一个
        假 ❌：旧拷贝模式的残留被报成"被我们吞掉的原 hook"，还附一句"重跑 install_hooks.sh 会备份"
        —— 安装脚本按 COMMON 解析，永远枚举不到那个目录。
        判据与产品同源（install_hooks.sh L112-118 的 REAL_HOOKS / BACKUP_BASE）。
        """
        import hook_dirs
        # ⚠ 夹具必须踩在**当前平台上的真实绝对路径**上（2026-09-23 夜，WSL/CI 实测查出）。
        # 原先写死 `D:/repo/...`：在 Linux 上 `D:/repo/.git` **不是绝对路径**（没有前导 `/`），
        # 于是 `resolve()` 里那句「common 是相对路径就按 repo 绝对化」被触发，拼出
        # `D:/repo/D:/repo/.git/hooks` —— 本机（Windows）绿、CI（Linux）红。
        # ★ 这不是产品缺陷（产品拿到的路径永远来自本机 git），是夹具把**开发机的路径形状**
        #   当成了普适形状 —— 与 `c2b6e1b`「夹具写死了开发机状态」同族。
        # ★ 用 `abspath` 而不是 tempdir：本用例全程不碰文件系统（只算字符串），
        #   造目录再删是白付一次 IO 和一次清理义务。
        repo = os.path.abspath("repo_under_test")
        common = os.path.join(repo, ".git")
        wd = os.path.join(common, "worktrees", "wt")
        real, backup = hook_dirs.resolve(wd, common, repo)
        self.assertEqual(real, os.path.join(common, "hooks"),
                         "worktree 下必须扫 common-dir 的 hooks（git 与安装脚本都在那儿）")
        self.assertEqual(backup, os.path.join(common, "agentguard-original-hooks"),
                         "备份目录也必须按 common-dir 解析（安装脚本写在那儿）")
        # 非 worktree：common == gitdir，规则退化后与旧行为一致（不许把普通仓扫漏）
        real2, backup2 = hook_dirs.resolve(common, common, repo)
        self.assertEqual(real2, os.path.join(common, "hooks"))
        # 相对形态的 --git-common-dir（git 在仓根跑就返回 ".git"）要按 repo 绝对化
        real3, _ = hook_dirs.resolve(common, ".git", repo)
        self.assertEqual(real3, os.path.join(common, "hooks"))
        # 接线：doctor.sh 必须用这份判据，不许自己再拼一遍 gitdir
        src = _read("sandbox/doctor.sh")
        self.assertIn("import hook_dirs", src, "doctor.sh 必须用同源解析（harness/hook_dirs.py）")
        self.assertIn('hook_dirs.resolve(', src, "doctor.sh 的 hooks/备份目录必须走 resolve()")
        self.assertNotIn('os.path.join(gitdir, "hooks")', src,
                         "doctor 不许退回 `<absolute-git-dir>/hooks` —— 那对 worktree 是 git 从不执行的目录")


class TestB2PolicyOverrides(unittest.TestCase):
    """B2：local/env 只能覆盖路径映射，策略字段只认 base。"""

    def _load_with_local(self, local_text):
        import _config as C
        d = tempfile.mkdtemp(prefix="b2-")
        lp = os.path.join(d, "local.yaml")
        with io.open(lp, "w", encoding="utf-8", newline="") as f:
            f.write(local_text)
        old = os.environ.get("HARNESS_CONFIG")
        os.environ["HARNESS_CONFIG"] = lp
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                cfg = C.load()
        finally:
            if old is None:
                os.environ.pop("HARNESS_CONFIG", None)
            else:
                os.environ["HARNESS_CONFIG"] = old
            _rmtree_force(d)
        return cfg, buf.getvalue()

    def _base_value(self, gate, key):
        """共享层 config/harness.yaml 里某 gate 的某策略字段值（**不含**任何本机覆盖层）。

        做法：把 HARNESS_CONFIG 指到一个**空的**本地层 —— `load()` 读到空覆盖层时
        `if override:` 为假，直接返回 base（见 _config.load）。刻意**不**用"把这个环境变量
        删掉"那条路：那会落到本机的 config/harness.local.yaml，结果随开发机而变，
        正是 c2b6e1b 那类病的形状。
        """
        import _config as C
        d = tempfile.mkdtemp(prefix="b2base-")
        empty = os.path.join(d, "empty.yaml")
        with io.open(empty, "w", encoding="utf-8", newline="") as f:
            f.write("")
        old = os.environ.get("HARNESS_CONFIG")
        os.environ["HARNESS_CONFIG"] = empty
        try:
            return ((C.load() or {}).get("gates", {}).get(gate) or {}).get(key)
        finally:
            if old is None:
                os.environ.pop("HARNESS_CONFIG", None)
            else:
                os.environ["HARNESS_CONFIG"] = old
            _rmtree_force(d)

    def _local_scalar_for(self, gate, key):
        """本地层里该字段该写什么值：共享层的**反面**（写成 YAML 标量）。

        为什么必须取反面：`_guard_policy` 的判据是 `bg[name].get(k) != v` ——
        本地值与共享层**相同**时，它压根不是一次覆盖，守卫既不报放行也不报退回（对的：
        什么都没改）。所以要证"这个字段受保护"，本地就必须写一个**确实不同**的值；
        写死一个值又会把用例钉在"共享层当前恰好是另一个值"上，共享层一改就假红。
        """
        return "false" if self._base_value(gate, key) else "true"

    def test_policy_fields_are_ignored(self):
        # 评审 A3（Claude Code C-4）：本地层**允许** enabled: false（裁掉自己没有的 gate），
        # 所以这里不再用 enabled 做「被忽略」的样例 —— 改用其余策略字段。
        #
        # 2026-09-21 改判据（起因：635237b 在**共享层**给 31 个 gate 打开 legacy_baseline 后
        # 本用例红，而红的原因与 local 一点关系没有）。原先那条断言问的是
        # 「legacy_baseline 合并后是假吗」—— 那是"它现在关着吗"，不是"local 有没有把它打开"，
        # 于是共享层一开就红。真正的不变量只有一条：**local 写的值不许出现在合并结果里**。
        # 故：本地写共享层的反面，再断言合并结果仍是共享层那个值。
        cfg, out = self._load_with_local("""gates:
  example:
    mode: loose
    script: /tmp/evil.py
    legacy_baseline: %s
""" % self._local_scalar_for("example", "legacy_baseline"))
        g = cfg["gates"]["example"]
        self.assertNotEqual(g.get("mode"), "loose", "本机不得通过 local 降级 mode")
        self.assertEqual(g.get("script"), "checks/example_code_check.py", "script 不许被指到别处")
        self.assertEqual(bool(g.get("legacy_baseline")),
                         bool(self._base_value("example", "legacy_baseline")),
                         "不得通过 local 打开 legacy 豁免（合并结果必须仍是共享层的值）")
        self.assertIn("config-policy", out, "忽略策略覆盖必须出声")

    def test_local_can_disable_missing_gate_with_notice(self):
        """评审 A3（Claude Code C-4）：本地层可以**关掉自己没有的 gate**（enabled: false）——
        这是「裁掉自己用不到的」，不是「给共享策略降级」；且必须**出声**（不静默）。
        无此能力的后果：同事机器上 --validate 永久红（一堆「repo 路径不存在」），
        而文档又禁止改共享 yaml → 只剩「改 core.hooksPath」这种绕过路径。"""
        cfg, out = self._load_with_local("""gates:
  example:
    enabled: false
""")
        self.assertFalse(cfg["gates"]["example"].get("enabled"),
                         "本地层应当允许关掉自己没有的 gate")
        self.assertIn("已在本地关闭", out, "本地关闭必须出声（防静默减护）")

    def test_repos_override_allowed_and_visible(self):
        """路径映射是本地覆盖层的正当用途（必须允许），但**必须出声** —— 否则把某个 gate
        指到一个空目录就等于静默关掉它。

        2026-09-18（跨平台批次 2）：共享 config/harness.yaml 的 repos 段已清空，于是
        「覆盖」退化成「**全部来自覆盖层**」。逐条 diff 在这种形态下退化为打一整屏
        「(base 无) → …」，引擎改报一条汇总（`[config-policy] 本机 repos 全部来自本地覆盖层
        （N 条）… 文件：<path>`）。两种形态都留痕，所以这里接受任一 —— 但**都不许没有**。
        """
        cfg, out = self._load_with_local("""repos:
  example_repo: D:/somewhere/else
""")
        self.assertEqual(cfg["repos"]["example_repo"], "D:/somewhere/else",
                         "路径映射是本地覆盖层的正当用途，必须允许")
        self.assertTrue("repos 路径覆盖" in out or "全部来自本地覆盖层" in out,
                        "允许的动作也要留痕（否则指向空目录=静默关闭）：base 的 repos 段非空时"
                        "逐条报「repos 路径覆盖」，base 为空（本仓现状）时报「本机 repos 全部"
                        "来自本地覆盖层」；两者都没有=静默。实得：%r" % out[:300])

    def test_new_gate_locally_is_allowed_boundary(self):
        """边界（刻意收窄的语义）：本地**新增** gate 允许 —— HARNESS_CONFIG 也被测试夹具与
        verify 工具用来指向完整配置；守卫只管"已注册 gate 的策略字段不许被本机改"。
        这条锁把边界写清楚，避免以后有人"顺手"把守卫扩大成"禁止新增"而打死工具链。"""
        cfg, out = self._load_with_local("""gates:
  my_sneaky_gate:
    enabled: true
    script: /tmp/anything.py
""")
        self.assertIn("my_sneaky_gate", cfg.get("gates", {}),
                      "新增 gate 属于本守卫范围之外（工具/夹具需要）")

    def test_shared_config_carries_no_machine_paths(self):
        """共享 config/harness.yaml **不得**含本机路径（2026-09-18，跨平台批次 2）。

        为什么值得单独立锁：这类硬编码**一个症状都没有**。维护者本机那 33 条
        `D:/MyProject/aiauto/agentguard_*` 原先写在共享文件里，同事换盘符/用户名、或在 WSL 上
        clone 之后，配置**看起来是配好的**，实际条条指空 —— 一台新机器开箱就是「零受管」，
        而门禁一声不吭（本批的起因正是这个）。已改成：共享文件只放团队策略（gates 段），
        路径全部下沉到 gitignored 的 `config/harness.local.yaml`（各人一份）。

        判据刻意**只看生效行**（拿掉 `#` 注释后的行）：注释里举例 `D:/...`、`/mnt/d/...`
        是正当的教学材料（本文件顶部那段"为什么空着"就在讲它），锁它们等于逼人删掉解释；
        真正会生效的路径才是隐患。顺带两条同源的断言，防的是同一个病的另外两个形态：
          · `config/harness.local.yaml` 必须在 `.gitignore` 里 —— 它是**机器专属**文件，
            一旦入库，下沉下去的路径就原样回到了共享面（换个地方复发）；
          · 模板 `harness.local.example.yaml` 的生效行同样不许有机器路径 ——
            模板是"给人抄的"，抄完却不改就成了新的硬编码源头。
        """
        import re
        # 机器形态的判据：盘符（D:/ D:\）/ WSL 挂载（/mnt/d/）。刻意**不**用「含有作者用户名」
        # 这种黑名单 —— 换个名字就漏，而盘符与挂载点是形态本身。
        machine = re.compile(r"(?:^|[^\w])(?:[A-Za-z]:[\\/]|/mnt/[a-z]/)")
        bad = []
        for rel in ("config/harness.yaml", "config/harness.local.example.yaml"):
            for i, line in enumerate(_read(rel).split("\n"), 1):
                if line.lstrip().startswith("#"):
                    continue                       # 注释：教学举例，正当
                m = machine.search(line)
                if m:
                    bad.append("%s:%d %s" % (rel, i, line.strip()[:80]))
        self.assertEqual([], bad,
                         "共享面里出现了机器路径 —— 那是**每机事实**，写到共享文件里对别人就是错的，"
                         "而且没有任何症状（配置看起来配好了，实际全指空）：\n  %s\n"
                         "路径请放进 gitignored 的 config/harness.local.yaml。" % "\n  ".join(bad))
        ignored = _read(".gitignore")
        self.assertIn("config/harness.local.yaml", ignored,
                      "config/harness.local.yaml 必须在 .gitignore 里 —— 不然下沉下去的机器路径"
                      "会随提交进仓库，等于换个地方复发同一个病。")

    def test_escape_hatch_reports_what_it_let_through(self):
        """放行开关必须说清**放行了哪几条**，且两个信号互斥、不许说反（2026-09-16）。

        剖析报告六节「豁免出口缺少审计」：HARNESS_ALLOW_POLICY_OVERRIDE=1 原先只有一句
        泛泛的「已放行」，看不出动了哪几个字段 —— 排查「这台机器上某 gate 怎么突然变松了」
        时等于没线索。这里锁三件事：
          ① 放行时逐条报出字段名（不是只报"已放行"）；
          ② last_policy_override() 拿到同一批字段，而 last_policy_rejection() **必须为空**
             —— 放行模式下什么都没被忽略，若这里非空，--validate 就会打出「已被忽略」，
             与事实正好相反（这正是最坏的一种：报平安报到了反面）；
          ③ 关掉开关时反过来：rejection 有名有姓、override 为空（两向探针，防"恒空"的假绿）。

        2026-09-21 补一处前提（起因：635237b 在**共享层**打开 legacy_baseline 后本用例红）：
        样例字段的值必须取共享层的**反面**（见 _local_scalar_for）—— 本地值与共享层相等时，
        它压根不是一次覆盖，`_guard_policy` 正确地既不报放行也不报退回，① 的「两个字段都要报」
        就无从谈起。原先写死 `legacy_baseline: true`，共享层还是 false 时恰好是一次真覆盖，
        共享层一打开就退化成"没改" → 假红。（mode 那条不受影响：共享层是 strict，永远不同。）
        """
        import _config as C
        d = tempfile.mkdtemp(prefix="b2c-")
        lp = os.path.join(d, "local.yaml")
        with io.open(lp, "w", encoding="utf-8", newline="") as f:
            f.write("gates:\n  example:\n    mode: loose\n    legacy_baseline: %s\n"
                    % self._local_scalar_for("example", "legacy_baseline"))
        old_cfg = os.environ.get("HARNESS_CONFIG")
        old_ovr = os.environ.get("HARNESS_ALLOW_POLICY_OVERRIDE")
        os.environ["HARNESS_CONFIG"] = lp
        buf = io.StringIO()
        try:
            # ① 放行方向
            os.environ["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
            with contextlib.redirect_stderr(buf):
                cfg = C.load()
            out = buf.getvalue()
            self.assertEqual(cfg["gates"]["example"].get("mode"), "loose", "显式开关下应放行")
            self.assertIn("策略字段覆盖", out)
            self.assertIn("mode", out, "必须逐条报出放行了哪个字段，不能只说「已放行」")
            self.assertIn("legacy_baseline", out, "两个字段都要报，不能只报第一个")
            ovr = C.last_policy_override()
            self.assertIn("gates.example.mode", ovr)
            self.assertIn("gates.example.legacy_baseline", ovr)
            self.assertEqual([], C.last_policy_rejection(),
                             "放行模式下没有任何字段被忽略 —— rejection 非空会让 --validate "
                             "打出「已被忽略」，与事实正好相反")

            # ② 关掉开关：同一份 local 改回被拦，两边信号必须对调
            buf.truncate(0)
            buf.seek(0)
            os.environ.pop("HARNESS_ALLOW_POLICY_OVERRIDE", None)
            with contextlib.redirect_stderr(buf):
                cfg2 = C.load()
            self.assertNotEqual(cfg2["gates"]["example"].get("mode"), "loose", "无开关时必须拦回")
            self.assertEqual([], C.last_policy_override(),
                             "没走放行开关 → override 必须为空（否则上面那条是恒真的假绿）")
            self.assertIn("gates.example.mode", C.last_policy_rejection())
        finally:
            if old_cfg is None:
                os.environ.pop("HARNESS_CONFIG", None)
            else:
                os.environ["HARNESS_CONFIG"] = old_cfg
            if old_ovr is None:
                os.environ.pop("HARNESS_ALLOW_POLICY_OVERRIDE", None)
            else:
                os.environ["HARNESS_ALLOW_POLICY_OVERRIDE"] = old_ovr
            _rmtree_force(d)

    def test_escape_hatch_is_explicit(self):
        """HARNESS_ALLOW_POLICY_OVERRIDE=1 时才放行（并在 stderr 明示这是调试用）。"""
        import _config as C
        d = tempfile.mkdtemp(prefix="b2b-")
        lp = os.path.join(d, "local.yaml")
        with io.open(lp, "w", encoding="utf-8", newline="") as f:
            f.write("gates:\n  example:\n    enabled: false\n")
        os.environ["HARNESS_CONFIG"] = lp
        os.environ["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        buf = io.StringIO()
        try:
            with contextlib.redirect_stderr(buf):
                cfg = C.load()
            self.assertFalse(cfg["gates"]["example"].get("enabled"),
                             "显式开关下应放行（用于调试）")
            self.assertIn("策略字段覆盖", buf.getvalue())
        finally:
            os.environ.pop("HARNESS_ALLOW_POLICY_OVERRIDE", None)
            os.environ.pop("HARNESS_CONFIG", None)
            _rmtree_force(d)


if __name__ == "__main__":
    unittest.main()
