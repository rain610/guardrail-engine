# -*- coding: utf-8 -*-
r"""
agentguard hook 链路集成测试（2026-09-06，真实子进程端到端）
====================================================================
背景：2026-09-06 install_hooks.sh 的 MSYS 路径 bug（/d/... 传给 Windows python 解析成
D:\d\...）导致 hook「装好但永远打不开脚本」——现有 47 例单测全绿，没有任何测试抓住它。
原因：单测只测 checks 逻辑，不测「hook 脚本 → run_gate → checks → 退出码」这条链。

本文件用**真实子进程 + 临时 git 仓库**测链路（慢，实测 45-60s，hooksPath 迁移后链路变长；但测的是真东西）：
  1. install_hooks.sh  生成的 hook 文件里 HARNESS 路径 python 必须能打开（防假安装回归）
  2. setup_machine.sh  dry-run/首跑生成 local/二跑枚举 gate（sh 层语法与流程）
  3. pre-commit.py     干净仓放行 / staged 违规拦截 / guard 拦截链路
  4. post-edit.py      空改动放行（护栏）/ 未跟踪违规文件拦截
  5. run_gate.py       未知参数告警 / --list / 退出码约定（0/1/2/3；未登记 gate→rc=2 fail-closed）

运行：python -m unittest discover -s tests  （与单测一起跑；全部用临时目录，不碰真实端仓）
快跑（跳过链路集成，只跑单测，约 6s（全量 ~60s））：HARNESS_FAST=1 python -m unittest discover -s tests
"""
import os
import sys
import json
import shutil
import subprocess

import tempfile
import unittest

# GBK 兼容（`sandbox/list_entries.py` 的判据：会输出 + 含非 ASCII 就必须有兜底）：
# 本模块的 _restore_probe 会往 stderr 写中文告警，而它跑在 tearDown 里 —— GBK 控制台下
# 那句中文若抛 UnicodeEncodeError，异常会**直接漏出 tearDown**，还原被中途打断。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tmpclean import mkdtemp_cleanup as _mkdtemp    # noqa: E402
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402


# 测试隔离（第四轮 N-2）：把 run_gate 的运行日志钉到临时文件。
# 原先只有 test_coverage.py 设了 HARNESS_LOG_FILE，其余用例调 run_gate 时会把探针记录
# （gate=t_pc 等）写进真实的 reports/gate_log.jsonl —— 实测污染过 124 条，覆盖度自评的
# 窗口数据（运行次数、gate 概览）被混入测试噪声。模块导入时就设好，子进程
# env=dict(os.environ, ...) 会继承；tests/test_coverage.py 里有源码级锁防止将来漏设。
# 2026-09-21：退出时那次 `shutil.rmtree` **删不掉**这个目录 —— 里面有 run_gate 真写出来的
# 台账，而 `ignore_errors=True` 会把 PermissionError 静默吞掉。症状与"清理成功"一模一样：
# 实测存量 1193 份 / 441 MB，只能靠数目录才发现。走强删助手。
_LOG_TMPDIR = _mkdtemp("harness-testlog-")
os.environ["HARNESS_LOG_FILE"] = os.path.join(_LOG_TMPDIR, "gate_log.jsonl")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "harness", "hooks")
SANDBOX = os.path.join(ROOT, "sandbox")
PY = sys.executable


# 快跑（HARNESS_FAST=1，2026-09-17 批 3 接线）：本文件是**端到端链路**文件（真建临时 git 仓、
# 真装 hook、真跑分发器）。接线前它是"半接"的：开关用行内 `@unittest.skipIf(...)` 写死在
# 4 个类上，而 TestHookChain 整整 11 例**一例没接** —— 快跑档单跑仍要 16.4s。
# 本批统一成仓里标准写法（`FAST` + `_slow`，见 test_review_v13_batch6.py），落点按实测：
#   · TestHookChain 里 ≥1s 的 7 例逐个**方法级**装饰（5.51 / 2.88 / 1.78 / 1.30 / 1.04s，
#     外加两条跑真 run_gate 的 1.17 / 1.03s）。留跑的是 6 例真正便宜的：两条纯源码级契约锁
#     （exit_code_contract_documented_matches / cfg_env_declares_policy_override，0.25~0.34s）、
#     pre_edit_always_passes / run_gate_disabled_gate_still_skips_rc0（0.52~0.60s），
#     以及 2026-09-18 跨平台批次 2 补的 `--list` 汇总两向锁（0.50 / 0.32s，各起一次
#     run_gate 子进程但**不建仓**，判据是 stdout 文本，故便宜）—— 后两条判的正是
#     "换了机器、配置的路径一条都不在"这个形态，**恰恰只在换机器那次运行里才有价值**，
#     挂 _slow 等于把它从唯一需要它的场景里拿掉。
#     类级会把它们一起清掉，而便宜锁的全部价值就是"改坏了编辑期立刻知道"。
#     ★ **两种量法的坑（本批实测）**：同一例**单跑**与**在同一类里跑**能差一倍 ——
#     run_gate_unregistered_gate_rc2 单跑 0.568s / 类内 1.17s（enabled_but_bad_repo 同款）。
#     原因是同类的临时 git 仓会累积。**判据一律以"类内、快跑档单跑整文件"的读数为准**，
#     因为那才是快跑档真正面对的场景；单例单跑的数只用来分辨"便宜锁还是链路"。
#   · 另 4 个慢类沿用**类级**（原本就已接，本次只是把行内写法换成标准写法，语义不变：
#     两者都是 import 期读同一个环境变量）。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


def _rmtree(roguetrack_dir):
    """rmtree，Windows 上文件可能带只读位（hook / git 对象），先清。

    2026-09-21：本文件原先自己实现了一份 `onerror` 版本 —— 与 `tests/_tmpclean.py`
    的 `rmtree_force` 是同一件事的两份拷贝。两份实现会各自漂移，收敛到助手，调用点不动。
    """
    _rmtree_force(roguetrack_dir)


class _TmpGitRepo:
    """临时 git 仓（模拟端仓）：可 staged/unstage 文件，可装 hook。"""

    def __init__(self, prefix="hchain_"):
        self.path = tempfile.mkdtemp(prefix=prefix)
        self.git("init", "-q")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")

    def git(self, *args):
        return subprocess.run(["git", "-C", self.path] + list(args),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")

    def write(self, rel, text):
        p = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def stage(self, rel):
        self.git("add", rel)

    def cleanup(self):
        _rmtree(self.path)


def _find_sh():
    """解析 sh 解释器（2026-09-12 P1 收尾，评审实测暴露）：
    Git for Windows 默认只把 cmd\\ 加进 PATH，bin\\（sh/bash 所在）不加——
    "装了 Git 但没加 bin 到 PATH" 的机器/CI 上写死 ["sh", ...] 的 5 个用例
    FileNotFoundError 全红。优先 PATH，再探 Git for Windows 固定安装位。
    找不到返回 None（调用方 skipTest，套件在任何机器都确定绿）。"""
    p = shutil.which("sh")
    if p:
        return p
    for cand in (r"C:\Program Files\Git\bin\sh.exe",
                 r"C:\Program Files\Git\usr\bin\sh.exe",
                 r"C:\Program Files (x86)\Git\bin\sh.exe"):
        if os.path.exists(cand):
            return cand
    return None

_SH = _find_sh()


def _run(cmd, cwd=None, stdin_text=None, env=None):
    """子进程统一入口。stdin 一律 DEVNULL（2026-09-11 P1 密闭化）：
    旧版 input=None 时不重定向 stdin，子进程继承父进程打开的管道 →
    链路里任何一处 stdin 读取都会挂死（外部评审复现 discover 超时的根因）。"""
    # sh 依赖解析：["sh", ...] 形态的命令替换为解析到的绝对路径
    if cmd and cmd[0] == "sh":
        if not _SH:
            raise FileNotFoundError("sh 不可用（Git Bash 未安装或 bin 未在 PATH）")
        cmd = [_SH] + cmd[1:]
    # 2026-09-12 P2-2（外部评审）：encoding 显式 utf-8——原生 cp936 环境父进程按 GBK
    # 解码子进程 UTF-8 输出会 UnicodeDecodeError（reader 线程炸 → stdout=None）。
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd or ROOT,
                          input=stdin_text, stdin=subprocess.DEVNULL,
                          env=env, timeout=120)


_CLIENT_REPO_KEYS = []


def _client_repo_keys():
    """客户端 gate 引用的 repo 键（不含 harness_self）—— 从配置反查，不写死清单。

    注意反查的是 **gate.repo 指向的键**，不是 gate 名 —— 共享配置里两者可以不同名
    （`example` → `example_repo`），按 gate 名写覆盖层会写到一个没人引用的键上，
    用例于是"还在绿、但已经没在测那件事"。

    为什么走子进程而不是本进程 `import _config`：那是**模块级可变状态**
    （`_config._HARNESS_ROOT` 会被别的用例改指到自己的夹具目录），谁先跑谁说了算 ——
    用别人的夹具配置来构造本用例的前提，正是本仓反复记过的「测试之间互相投毒」。
    结果进程内缓存一次（只读）。
    """
    if not _CLIENT_REPO_KEYS:
        code = (
            "import json,sys;sys.path.insert(0, sys.argv[1]);"
            "from _config import load;"
            "cfg=load();"
            "keys={(v.get('repo') or '') for k,v in (cfg.get('gates') or {}).items()"
            " if k != 'harness_self'};"
            "print(json.dumps(sorted(keys - {''})))"
        )
        r = _run([PY, "-c", code, os.path.join(ROOT, "harness")])
        out = (r.stdout or "").strip().splitlines()
        _CLIENT_REPO_KEYS.extend(json.loads(out[-1]) if out else [])
    return _CLIENT_REPO_KEYS


@_slow("慢速链路集成（本类全部真起临时 git 仓并装 hook）")
class TestInstallHooksScript(unittest.TestCase):
    """install_hooks.sh：生成的 hook 里 HARNESS 路径必须 python 可打开（防 MSYS 路径假安装回归）。"""

    def setUp(self):
        self.repo = _TmpGitRepo()

    def tearDown(self):
        self.repo.cleanup()

    def _hook_path(self):
        r = self.repo.git("rev-parse", "--git-path", "hooks")
        return os.path.join(self.repo.path, r.stdout.strip(), "pre-commit")

    def test_hook_harness_path_opens_with_python(self):
        if not _SH:
            self.skipTest("Git Bash sh 不可用（PATH 无 sh 且未找到 Git for Windows 固定安装位）")
        """核心回归锁（2026-09-12 hooksPath 模式）：装完后 core.hooksPath 指向共享
        hooks-dir，且该目录下的分发器与 pre-commit.py 都能被 python 实际打开——
        防 MSYS 路径形态差异导致的「配好了但永远执行不到」假安装。"""
        r = _run(["sh", os.path.join(SANDBOX, "install_hooks.sh"), "example"],
                 cwd=self.repo.path)
        self.assertEqual(r.returncode, 0, r.stderr)
        # 1) hooksPath 已写入仓库 config，且指向 harness 共享 hooks-dir
        hp = self.repo.git("config", "--local", "core.hooksPath").stdout.strip()
        self.assertTrue(hp, "core.hooksPath 未配置")
        self.assertTrue(hp.replace("\\", "/").endswith("harness/hooks-dir"),
                        f"hooksPath 未指向共享 hooks-dir: {hp}")
        # 2) 分发器与目标脚本路径形态 python 可打开（MSYS /d/... 形态会在这里爆）
        chk = _run([PY, "-c",
                    f"import os;assert os.path.isfile(r'{hp}/pre-commit'), '{hp}/pre-commit';"
                    f"import subprocess;"
                    f"harness=os.path.dirname(os.path.dirname(os.path.abspath(r'{hp}')));"
                    f"assert os.path.isfile(os.path.join(harness,'harness','hooks','pre-commit.py'))"])
        self.assertEqual(chk.returncode, 0,
                         f"hooksPath 路径 python 打不开（假安装）: {hp}")
        # 3) 旧拷贝模式遗留 hook 应已被清理（防新旧双份执行）。
        #    注意：hooksPath 模式下 rev-parse --git-path hooks 返回共享 hooks-dir 本身，
        #    所以旧 hook 的判定路径是【仓库自己的 .git/hooks/pre-commit】，不是 --git-path 结果。
        old_hook = os.path.join(self.repo.path, ".git", "hooks", "pre-commit")
        if os.path.isdir(os.path.join(self.repo.path, ".git", "hooks")):
            self.assertFalse(os.path.exists(old_hook),
                             "旧 .git/hooks/pre-commit 未被清理，会双份执行")

    def test_hook_calls_gate_and_blocks_dirty_staged(self):
        if not _SH:
            self.skipTest("Git Bash sh 不可用（PATH 无 sh 且未找到 Git for Windows 固定安装位）")
        """hook 整链：staged 一个违规 .vue → sh 执行 hook → 退出码 1。"""
        self.assertEqual(_run(["sh", os.path.join(SANDBOX, "install_hooks.sh"), "example"],
                              cwd=self.repo.path).returncode, 0)
        # 这里直接验证「hook 脚本本身可执行且退出码语义正确」：
        hook = self._hook_path()
        r = _run(["sh", hook], cwd=self.repo.path)
        # 共享分发器按 cwd 识别 gate；本夹具用的是**未纳管的临时仓**，
        # 所以"查不了"是常态 → fail-closed 返回 2。
        # 评审 v9（Codex P2-1）后分发器**透传**原退出码（2=CONFIG_ERROR / 1=BLOCKED），
        # 而旧实现在这里把 2 压成 1 —— 断言跟着更新为接受 2，并强制要求**有解释输出**，
        # 防止"静默非 0"（那才是真问题）。
        self.assertIn(r.returncode, (0, 1, 2), f"hook 退出码异常: {r.returncode}")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertTrue(out, "hook 无任何输出（疑似脚本没跑起来）")
        if r.returncode != 0:
            self.assertTrue("✗" in out or "阻断" in out or "不放行" in out,
                            "非 0 退出必须带明确原因，不能静默：%s" % out[:200])


@_slow("慢速链路集成（真跑 setup_machine.sh）")
class TestSetupMachineScript(unittest.TestCase):
    """setup_machine.sh：sh 语法 + 三步流程（dry-run / 首跑生成 local / gate 枚举）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hsetup_")

    def tearDown(self):
        _rmtree(self.tmp)

    def test_sh_syntax_ok(self):
        if not _SH:
            self.skipTest("Git Bash sh 不可用（PATH 无 sh 且未找到 Git for Windows 固定安装位）")
        r = _run(["sh", "-n", os.path.join(SANDBOX, "setup_machine.sh")])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_first_run_generates_local_template(self):
        if not _SH:
            self.skipTest("Git Bash sh 不可用（PATH 无 sh 且未找到 Git for Windows 固定安装位）")
        """无 local 时首跑：生成 local.yaml 并提示编辑，退出 0（不装 hook）。"""
        # 用临时 HARNESS？setup 从 $0 推导根，会写真实 config/。
        # 规避：mock 一个最小 harness 树（config + example + sandbox 副本）。
        h2 = os.path.join(self.tmp, "harness_copy")
        os.makedirs(os.path.join(h2, "config"))
        os.makedirs(os.path.join(h2, "sandbox"))
        shutil.copy(os.path.join(SANDBOX, "setup_machine.sh"), os.path.join(h2, "sandbox"))
        shutil.copy(os.path.join(ROOT, "config", "harness.local.example.yaml"),
                    os.path.join(h2, "config"))
        r = _run(["sh", os.path.join(h2, "sandbox", "setup_machine.sh")])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(h2, "config", "harness.local.yaml")),
                        "首跑未生成 local 模板")
        self.assertIn("harness.local.yaml", r.stdout)

    def test_dry_run_lists_enabled_gates(self):
        if not _SH:
            self.skipTest("Git Bash sh 不可用（PATH 无 sh 且未找到 Git for Windows 固定安装位）")
        """--dry-run：枚举启用 gate（只读不装）。

        2026-09-12 密闭化（CI 首次运行暴露）：原版直接跑**真实仓**的 setup_machine.sh，
        依赖"本机已存在 config/harness.local.yaml"——CI/同事全新克隆上该文件不存在，
        脚本走"首跑生成模板后 exit 0"分支，从不枚举 gate → 断言必红（GitHub Actions
        run#1 python3.11/3.12 双矩阵同点失败）。附带问题：缺文件时它还会往真实仓写
        harness.local.yaml（测试有副作用）。
        现改为最小 harness 树 + 自带空覆盖层，断言落在 fixture 自己的 gate 上。"""
        h2 = os.path.join(self.tmp, "harness_copy")
        for d in ("config", "sandbox", "harness"):
            os.makedirs(os.path.join(h2, d))
        shutil.copy(os.path.join(SANDBOX, "setup_machine.sh"), os.path.join(h2, "sandbox"))
        shutil.copy(os.path.join(ROOT, "harness", "_config.py"),
                    os.path.join(h2, "harness", "_config.py"))
        # 端仓目录真实存在 → dry-run 才会打印 "[dry-run] <gate> -> <path>"
        repo = os.path.join(self.tmp, "fx_repo")
        os.makedirs(repo)
        with open(os.path.join(h2, "config", "harness.yaml"), "w", encoding="utf-8") as f:
            f.write("repos:\n  fx_pc: " + repo.replace("\\", "/") + "\n"
                    "gates:\n  fx_pc:\n    enabled: true\n    repo: fx_pc\n")
        # 覆盖层已就位 = 非首跑状态（正是 CI 与本机的差异点，也是本用例的前提）
        with open(os.path.join(h2, "config", "harness.local.yaml"), "w", encoding="utf-8") as f:
            f.write("# 空覆盖层（本用例只验枚举，不改路径）\n")
        r = _run(["sh", os.path.join(h2, "sandbox", "setup_machine.sh"), "--dry-run"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[dry-run] fx_pc", r.stdout)


@_slow("慢速链路集成（含起子进程跑保护集自检的用例）")
class TestProtectedPathContract(unittest.TestCase):
    """「测试守卫」与「pre-commit 保护循环」必须共用同一份保护集（防漂移）。

    2026-09-13 实测踩坑：两者曾漂移——`_skip_if_engine_dirty` 只查 checks/config/harness，
    而 `checks/harness_guard.py::_in_protected()` 还保护 `integrations/hooks/*.py`；
    结果是"改了钩子还没提交就跑套件"时本用例不跳过、却被保护循环拦成 rc=1，表现成**偶发红**
    （CI 干净检出不受影响，极易被当成 flaky 放过）。修法是让测试直接引用 guard 的判定函数，
    这条锁则钉住"guard 的保护集确实覆盖这几类"，将来谁收窄它都会红。
    """

    def test_guard_protected_set(self):
        import sys
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from checks.harness_guard import _in_protected

        for rel in ("checks/example_code_check.py",            # 规则实现
                    "checks/example_ruleset.py",               # 规则集
                    "config/harness.yaml",                     # 策略配置
                    "integrations/hooks/agentguard-gate.py"):  # ★ 本次漂移的那一类
            self.assertTrue(_in_protected(rel), "%s 应属受保护文件（保护循环会拦未提交改动）" % rel)
        for rel in ("docs/x.md", "tests/test_x.py", "README.md", "sandbox/install_hooks.sh"):
            self.assertFalse(_in_protected(rel), "%s 不该算受保护（否则测试会大面积误跳）" % rel)

    def test_guard_call_site_exists_in_pre_commit(self):
        """P0-2 哨兵调用方完整性（锁）：pre-commit.py 源码必须引用 harness_guard。

        评审发现：checks/harness_guard.py 自身受保护，但「谁来调用它」没有守卫——
        移除 pre-commit.py 里的 _run_guard() 调用 = 保护永久关闭，所有现有测试仍绿。
        本锁直接读源码断言调用点存在；若有人删调用，本锁 RED + CI 红。
        """
        pc_src = os.path.join(ROOT, "harness", "hooks", "pre-commit.py")
        if not os.path.isfile(pc_src):
            self.skipTest("pre-commit.py 不存在（分发器完整性③已覆盖）")
        with open(pc_src, encoding="utf-8", errors="replace") as f:
            src = f.read()
        self.assertIn("harness_guard", src,
                      "pre-commit.py 未引用 harness_guard —— 保护循环形同虚设（P0-2）")

    def test_self_check_catches_missing_guard_call_site(self):
        """P0-2 哨兵调用方完整性（行为锁）：自检必须能发现调用点缺失。

        反向锁：构造一个不含 harness_guard 的 pre-commit.py → 自检必须 ❌。
        正向那半（**真实的** pre-commit.py 确实引用 guard）由同类的
        `test_guard_call_site_exists_in_pre_commit` 正面守住 —— 它读的就是真实文件。

        ★ 2026-09-30：原先**在真实工程仓里**把这个文件改写成去掉 guard 的版本、跑完再写回
        （两处 `open(..., "w")`）。三宗罪：
          · 它是**共享分发器的实现** —— 全部接入仓的 pre-commit 实体；改写期间进程被中断
            就留下一个残缺文件，等于**全仓提交期门禁失效**；
          · `open(..., "w")` 是**先截断后写**，并发读者会读到 0 字节（与 `_restore_probe`
            是同一种病，只是这个文件更致命）；
          · 改写内容与原文**长度不同**（`harness_guard` 13 字符 → `__guard_removed__` 18 字符），
            把窗口拉得更宽。
        改为把自检模块的 `ROOT` 指到临时目录：`check_guard_call_site()` 的路径就是从
        模块级 `ROOT` 拼出来的（`os.path.join(ROOT, "harness", "hooks", "pre-commit.py")`），
        指向哪里就查哪里 —— 测的仍是**同一份真实自检逻辑**，工程仓**一个字节都不动**。
        """
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import checks.harness_self_check as sc

        with tempfile.TemporaryDirectory(prefix="harness_selfcheck_") as td:
            hooks_dir = os.path.join(td, "harness", "hooks")
            os.makedirs(hooks_dir)
            with open(os.path.join(hooks_dir, "pre-commit.py"), "w",
                      encoding="utf-8", newline="") as f:
                # 反向探针：**故意**不含 harness_guard 调用
                f.write("# 反向探针（自动清理）：本文件故意不引用 guard\n"
                        "import sys\nsys.exit(0)\n")
            saved = (sc.ROOT, sc.problems, sc.warns)
            sc.ROOT, sc.problems, sc.warns = td, [], []
            try:
                sc.check_guard_call_site()
                found = list(sc.problems)
            finally:
                sc.ROOT, sc.problems, sc.warns = saved
        self.assertTrue(any("harness_guard" in p for p in found),
                        "自检未发现调用点缺失（P0-2 保护闭环失效）: %s" % found)


class TestHookChain(unittest.TestCase):
    """hooks/*.py 与 run_gate 的真实子进程链路（退出码契约）。"""

    def setUp(self):
        self.repo = _TmpGitRepo()
        self.tmpcfg = tempfile.mkdtemp(prefix="hchain_cfg_")

    def tearDown(self):
        self.repo.cleanup()
        _rmtree(self.tmpcfg)

    def _skip_if_engine_dirty(self):
        """引擎仓存在未提交的**受保护文件**改动时跳过本用例。

        pre-commit 链路里带**保护循环**（guard）：引擎仓的受保护文件有未提交改动 → 一律拦截
        rc=1。这是设计行为，与本用例要验的「门禁链路口径」无关；开发中（改完未提交就跑套件）
        会因此变红，属于状态依赖噪声。CI 上是干净检出，本用例照常真跑——所以只跳不删。

        ⚠️ 2026-09-13 修正（实测踩坑）：本函数原先只查 `checks/ config/ harness/` 三个目录，
        而 `checks/harness_guard.py::_in_protected()` 的保护集**还包含 `integrations/hooks/*.py`**
        ——两份清单漂移，于是"改了钩子还没提交就跑套件"时这里不跳过、却被保护循环拦成 rc=1，
        表现为**偶发红**（CI 干净检出不受影响，很容易被当成 flaky 放过）。
        现在直接引用 guard 自己的判定函数（**单一来源**），不再手写路径清单。
        """
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from checks.harness_guard import _in_protected

        r = subprocess.run(["git", "-C", ROOT, "status", "--porcelain"],
                           capture_output=True, encoding="utf-8", errors="replace")
        dirty = []
        for line in (r.stdout or "").splitlines():
            if not line.strip() or "__pycache__" in line:
                continue
            rest = line[3:].strip().strip('"')
            # rename/复制形态是 "old -> new"，两边都要判
            for one in [x.strip().strip('"') for x in rest.split("->")]:
                rel = one.replace("\\", "/")
                if _in_protected(rel):
                    dirty.append(rel)
        if dirty:
            self.skipTest("引擎仓有未提交的受保护文件改动（%d 项：%s…）——pre-commit 保护循环会按设计拦截；"
                          "提交后再跑本用例" % (len(dirty), ", ".join(dirty[:3])))

    @_slow("真起临时 git 仓 + 真跑 pre-commit 分发器（1.3s）")
    def test_pre_commit_clean_repo_passes(self):
        """干净临时仓（HARNESS_CONFIG 指向它）→ pre-commit.py 放行 rc=0。
        2026-09-11 密闭化：不再碰真实端仓（原版违反本文件头部注释）。"""
        self._skip_if_engine_dirty()
        r = _run([PY, os.path.join(HOOKS, "pre-commit.py"), "example",
                  "--repo", self.repo.path], env=self._cfg_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def _cfg_env(self):
        """生成临时 HARNESS_CONFIG：example gate 指向 self.repo.path（临时仓）。
        让链路测试在任何装了 Python+git 的机器确定性可跑，不绑真实仓路径。"""
        cfg = os.path.join(self.tmpcfg, "c.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(f"repos:\n  chain_repo: {self.repo.path}\n"
                    "gates:\n  example:\n    enabled: true\n    repo: chain_repo\n"
                    "    script: checks/example_code_check.py\n")
        env = dict(os.environ)
        env["HARNESS_CONFIG"] = cfg
        # 2026-09-15（CI 回归修复）：夹具配置会**改已注册 gate 的策略字段**（repo/script），
        # 而 2026-09-14 采纳外部评审 P0-1 后，本地/环境覆盖层改策略字段一律被**忽略**
        # （防"本机关掉门禁"）。夹具是刻意的替换，因此显式开逃生门——否则夹具被忽略，
        # run_gate 会拿 base 的真实仓路径（CI 上不存在）→ rc=2 假红。
        # 逃生门本身有独立锁（tests/test_review_v8_batch2.py）保证默认是关的。
        env["HARNESS_ALLOW_POLICY_OVERRIDE"] = "1"
        return env

    def test_cfg_env_declares_policy_override(self):
        """夹具配置会改**已注册 gate 的策略字段**（repo/script）——必须显式开逃生门。

        2026-09-15：采纳外部评审 P0-1 后，本地/环境覆盖层改策略字段一律被忽略（防"本机关门禁"）。
        夹具若不开逃生门，就被忽略 → run_gate 拿 base 的真实仓路径（CI 上不存在）→
        rc=2 假红。默认必须是关的（另有独立锁 test_review_v8_batch2.py 保证）。
        """
        env = self._cfg_env()
        self.assertEqual(env.get("HARNESS_ALLOW_POLICY_OVERRIDE"), "1",
                         "夹具未声明「我在改策略字段」的意图 → 换机器/CI 上会被忽略")

    @_slow("真起临时 git 仓 + 真跑 pre-commit 分发器（2.9s）")
    def test_pre_commit_blocked_reports_and_rc1(self):
        """staged 违规文件（临时仓探针，HARNESS_CONFIG 指向它）→ rc=1 且输出含文件与问题。
        2026-09-11 密闭化：探针写临时仓，不再 git add 真实仓 index。"""
        self._skip_if_engine_dirty()
        self.repo.write("src/__chain_probe__.vue",
                        "<template><div /></template>\n<script>console.log(1)</script>\n")
        g = self.repo.git("add", "src/__chain_probe__.vue")
        self.assertEqual(g.returncode, 0)
        r = _run([PY, os.path.join(HOOKS, "pre-commit.py"), "example",
                  "--repo", self.repo.path], env=self._cfg_env())
        self.assertEqual(r.returncode, 1, "违规 staged 未被拦截")
        self.assertIn("__chain_probe__.vue", r.stdout)
        # 2026-09-15 回归锁（CI 假绿/假红根因）：显式给了 --repo，cwd 纠偏**不得**劫持 gate。
        # 起因：工程仓注册成 harness_self 后，cwd=工程根的调用被纠偏成自检 gate →
        # 本机表现为 rc=0 假绿（staged 违规被拿去跑自检），CI 表现为 rc=2 假红。
        self.assertNotIn("worktree 纠偏: example ->", r.stdout,
                         "显式 --repo 的调用被 cwd 纠偏劫持（会把别的 gate 的规则套到本仓）")
        self.assertIn("跑门禁 example", r.stdout,
                      "实际跑的不是指定的 gate（--repo 权威性被破坏）")

    @_slow("真起临时 git 仓 + 真跑 post-edit 入口（1.0s）")
    def test_post_edit_empty_changes_passes(self):
        """空改动护栏：无改动仓 → post-edit.py rc=0（防误触发全量扫描）。
        2026-09-11 密闭化：用临时空仓 + HARNESS_CONFIG。"""
        r = _run([PY, os.path.join(HOOKS, "post-edit.py"), "example",
                  "--repo", self.repo.path], env=self._cfg_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @_slow("真起临时 git 仓 + 真跑 post-edit 入口（1.8s）")
    def test_post_edit_untracked_violation_blocks(self):
        """未跟踪违规新文件（AI 常见路径）→ post-edit.py rc=1。
        2026-09-11 密闭化：用临时仓 + HARNESS_CONFIG，不碰真实仓。"""
        self.repo.write("src/__chain_probe2__.vue", "<script>console.log(9)</script>\n")
        r = _run([PY, os.path.join(HOOKS, "post-edit.py"), "example",
                  "--repo", self.repo.path], env=self._cfg_env())
        self.assertEqual(r.returncode, 1, "未跟踪违规文件未被拦截")
        self.assertIn("__chain_probe2__.vue", r.stdout)

    def test_pre_edit_always_passes(self):
        r = _run([PY, os.path.join(HOOKS, "pre-edit.py"), "example"])
        self.assertEqual(r.returncode, 0)

    @_slow("真跑 run_gate（类内计时 1.2s；★ 见文件头关于两种量法的说明）")
    def test_run_gate_unregistered_gate_rc2(self):
        """未登记 gate 名 → rc=2 明确报错（2026-09-14 采纳方案 B / fail-closed）。
        背景：此前与「已禁用」同路径静默跳过 rc=0；gate 改名/拼错 = 调用方"假绿"。
        对照：已登记但 enabled=false 仍 rc=0 跳过（见下一条）。"""
        r = _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "no_such_gate"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("未登记", r.stdout)
        self.assertNotIn("未启用", r.stdout)

    def test_run_gate_disabled_gate_still_skips_rc0(self):
        """已登记但 enabled=false → rc=0「未启用，跳过」（与「未登记」严格区分）。"""
        env = dict(os.environ)
        tmpcfg = tempfile.mkdtemp(prefix="hrc0_")
        cfg = os.path.join(tmpcfg, "c.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("repos:\n  ghost_pc: D:/nonexistent_repo_xyz\n"
                    "gates:\n  ghost_pc:\n    enabled: false\n    repo: ghost_pc\n"
                    "    script: checks/example_code_check.py\n")
        env["HARNESS_CONFIG"] = cfg
        try:
            r = subprocess.run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "ghost_pc"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               cwd=ROOT, env=env, timeout=120)
            self.assertEqual(r.returncode, 0)
            self.assertIn("未启用", r.stdout)
            self.assertNotIn("未登记", r.stdout)
        finally:
            _rmtree(tmpcfg)

    @_slow("真跑 run_gate（类内计时 1.0s；★ 见文件头关于两种量法的说明）")
    def test_run_gate_enabled_but_bad_repo_rc2(self):
        """gate 启用但仓库路径无效 → rc=2（配置错误，需要人修）。"""
        env = dict(os.environ)
        tmpcfg = tempfile.mkdtemp(prefix="hrc2_")
        cfg = os.path.join(tmpcfg, "c.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("repos:\n  ghost_pc: D:/nonexistent_repo_xyz\n"
                    "gates:\n  ghost_pc:\n    enabled: true\n    repo: ghost_pc\n"
                    "    script: checks/example_code_check.py\n")
        env["HARNESS_CONFIG"] = cfg
        try:
            r = subprocess.run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "ghost_pc"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
            self.assertEqual(r.returncode, 2)
            self.assertIn("仓库路径无效", r.stdout)
        finally:
            _rmtree(tmpcfg)

    def _list_with_repos(self, tmpcfg, target):
        """写一份「客户端 gate 引用的 repo 键都指向 target」的覆盖层，跑 `run_gate.py --list`。

        `HARNESS_CONFIG` 是**覆盖层**（deep merge 到共享 config 之上），所以只需给 repos 段。
        """
        keys = _client_repo_keys()
        self.assertTrue(keys, "共享配置里没有可覆盖的 repo 键，本用例前提不成立")
        cfg = os.path.join(tmpcfg, "c.yaml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("repos:\n" + "".join(
                "  %s: %s\n" % (k, target.replace("\\", "/")) for k in keys))
        env = dict(os.environ)
        env["HARNESS_CONFIG"] = cfg
        return _run([PY, os.path.join(ROOT, "harness", "run_gate.py"), "--list"], env=env)

    def test_list_shouts_when_every_configured_repo_is_missing(self):
        """repos **都配了**、但本机一条都不存在 → `--list` 必须汇总出声，不许静默。

        2026-09-18（跨平台批次 2 补齐）：这是同事换盘符 / 换用户名 / 在 WSL 上 clone 之后
        最常见的形态 —— 配置看起来是配好的，而逐行打出来的 `@ D:/...` 里**没有一行
        是真的**。此前 `--list` 只逐行列路径、一句汇总都没有，正是本批要消灭的「静默失效」。
        判据看**磁盘**（不是「配置里写没写」），与 `--validate` 的 ⊘ 同源。
        """
        tmpcfg = tempfile.mkdtemp(prefix="hlist_")
        try:
            # ① 全部指向不存在的路径 → 必须出声
            r = self._list_with_repos(tmpcfg, os.path.join(tmpcfg, "no_such_dir_xyz"))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("指向本机不存在的路径", r.stdout,
                          "换机器形态必须汇总出声，实测输出：\n%s" % r.stdout)
        finally:
            _rmtree(tmpcfg)

    def test_list_stays_quiet_when_every_configured_repo_exists(self):
        """阴性对照：那些路径**全都在**时，上面那句汇总一个字都不许出现。

        没有这条，把汇总写成无条件的也会绿 —— 而那句提示天天出现的话，换来的不是「知道」
        而是**不再读**，与漏报是同一种失败（`_config._policy_notice_once` 的 docstring
        为同一件事写过一遍）。
        """
        tmpcfg = tempfile.mkdtemp(prefix="hlist_")
        try:
            r = self._list_with_repos(tmpcfg, tmpcfg)      # tmpcfg 确实存在
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("指向本机不存在的路径", r.stdout,
                             "路径都在时不该出现换机器提示，实测输出：\n%s" % r.stdout)
        finally:
            _rmtree(tmpcfg)

    @_slow("真起临时 git 仓 + 真跑 auto-gate 全流程（5.5s，本文件最贵的一条）")
    def test_auto_gate_unmanaged_repo_rc3(self):
        """auto-gate 在非受管目录 → rc=3（语义：跳过，不拦不报错）。"""
        r = _run([PY, os.path.join(ROOT, "harness", "agents", "auto-gate.py")], cwd=self.repo.path)
        self.assertEqual(r.returncode, 3)
        self.assertIn("不在 harness 受管仓库", r.stdout)

    def test_exit_code_contract_documented_matches(self):
        """退出码契约与 AGENTS.md 文档一致（防文档漂移）。

        只锁语义不锁排版：文档会重排（反引号/顿号/换行），而 0/1/2/3 的含义
        是跨三个入口（CLI / hook / auto-gate）的硬契约，漂了就是调用方误判。
        """
        with open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as fh:
            doc = fh.read()
        line = next((l for l in doc.splitlines() if "退出码" in l), "")
        self.assertTrue(line, "AGENTS.md 缺少退出码契约")
        for code, meaning in (("0", "通过"), ("1", "拦截"),
                              ("2", "配置错"), ("3", "非受管仓")):
            self.assertIn(f"`{code}` {meaning}", line,
                          "AGENTS.md 退出码契约漂移（缺 %s=%s）：%s" % (code, meaning, line))


# P0-3 探针追加的那一行（**字节**形态）。追加与还原**共用这一份常量**：
# 还原走「按原始长度截回」，截断前要拿它核对尾巴确实是本类写的那一行（见 _restore_probe）。
PROBE_LINE = b"\n# === P0-3 sentinel probe (auto-removed by tearDown) ===\n"


@_slow("慢速链路集成（真起端仓 + 真提交触发哨兵）")
class TestSentinelBlocksDirtyEngine(unittest.TestCase):
    """P0-3（外部评审最漂亮的发现）：`TestHookChain._skip_if_engine_dirty` 恰好跳过
    **唯一** 会触发哨兵的分支 → 绿灯不构成哨兵可用的证据。

    本类**故意弄脏工程仓**造出触发条件，正反两向验证哨兵真能拦 / 真能放，
    不再依赖「跳过」来取得绿色。

    触发条件：工程仓存在「未提交的受保护文件改动」。
      · 从 **端仓 cwd**（cwd ≠ 工程根）调 guard（带 gate 名、无 --self）
        → 非 self 模式 → 扫工程仓发现脏受保护文件 → **rc=1 拦截**。
      · self 模式带 `[harness]` 维护标记 → **rc=0 放行**（有意改动门禁，人看着）。
      · 工程仓干净 → 从端仓 cwd 调 guard → **rc=0 放行**（普通业务工单不受影响）。
    """

    # 受保护文件（_in_protected 返回 True）。只追加一行无害注释，tearDown 必还原。
    PROBE = "checks/example_code_check.py"

    def setUp(self):
        self.repo = _TmpGitRepo()           # 与工程仓无关的临时端仓，作为 guard 的 cwd
        self._probe = os.path.join(ROOT, self.PROBE)
        if not os.path.isfile(self._probe):
            self.skipTest("探针受保护文件不存在，跳过哨兵验证")
        # 先存原始字节，再弄脏。还原必须**只撤销本类自己加的那一行**——
        # 2026-09-16（评审 v13）：原先 _restore_probe 走 `git checkout -- <探针>`，撤销的是
        # 该文件的**全部未提交改动**，不只是本类的标记行。后果实测：跑一次套件就把开发者正在
        # 改的 checks/pc_code_check.py 静默回滚（本轮 严重 2 / 中 10 的三处修改就是这样被吃掉的，
        # 没有任何提示，直到复查文件才发现）。测试不该是破坏性操作。
        try:
            with open(self._probe, "rb") as f:
                self._orig = f.read()
        except OSError:
            self._orig = None
        # 立刻弄脏工程仓。★ 必须**二进制追加**：文本模式（`open(..., "a")` 不写 newline=）
        # 会把 "\n" 翻成 "\r\n"，而还原是按**原始字节长度**截回（见 _restore_probe）——
        # 两边不同源，截出来的就不再是原始字节。追加与还原共用同一个 PROBE_LINE。
        with open(self._probe, "ab") as f:
            f.write(PROBE_LINE)
        # 健全性：探针确实属受保护（否则本类前提不成立）
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from checks.harness_guard import _in_protected
        if not _in_protected(self.PROBE):
            self._restore_probe()
            self.skipTest("探针文件不在受保护集内，跳过哨兵验证")

    def tearDown(self):
        self._restore_probe()               # 无论如何还原工程仓（防遗留脏改动）
        self.repo.cleanup()

    def _restore_probe(self):
        """只撤销本类追加的探针行——保留工作区里原有的未提交改动（评审 v13）。

        ★ 2026-09-30 竞态修复：**不再「读回原字节 + open(..., "wb") 整写」**。
        那个写法是**先截断后写**，截断与实际写入之间有一个窗口，读者在窗口里读到
        **0 字节**。这不是纸面推理：探针文件正是本套件反复 import / 执行的脚本，
        两者并行时读者就落在窗口里。
        2026-09-30 的现场原文：`TestSyntaxSensorNotSilent::test_node_missing_must_announce`
        报 `AssertionError: 'warn_node_missing_once' not found in ''` —— 读到的就是**空串**
        （该模块那一跑 63 例 / 286.8s / failures=1）。这类假红的形状是**负载相关**：
        换负载就复现、再跑一次又绿，很容易被读成"抖动"而放过。

        改为**按原始字节长度截回**（`truncate(n)`）：它只改文件长度，从不把文件清空，
        读者只会看到两种合法形态 —— 原始内容，或「原始内容 + 本类那行注释」，都是合法源码。
        本机并发实测（4 读者线程 + 200 轮追加/还原，两种顺序各跑一遍）：

            open(..., "wb") 整写回   读到 0 字节  169/470  ≈ 36%
            本写法（截回）           读到 0 字节  0/1888   0%
        本写法下还额外满足：0 次读到「短于原始长度」、0 次读失败。

        ★ 为什么**不用**更常见的「同目录临时文件 + `os.replace()`」原子替换：
        在 Windows 上它修不了这个问题。同一次实测里它制造了 104 次 0 字节读 + **1965 次读失败**
        （读者拿 `FileNotFoundError`）—— 因为 ① 读者持有句柄时 `MoveFileEx` 直接
        `PermissionError`（单次重试最久 2.873s，26/120 轮 200 次重试仍失败）；
        ② 替换瞬间目标名解析不到。**「原子替换 ⇒ 读者要么见旧要么见新」只在 POSIX 上成立。**

        截断前**先核对尾巴确实是本类那行**：不一致 = 有人在我们跑的时候动过这个文件，
        此时**宁可留着那行注释并出声，也不动别人的改动**（先例仍是评审 v13 严重 2：
        旧版走 `git checkout`，把开发者未提交的改动整份回滚且没有任何提示）。

        读不回原始字节时（setUp 就读不到）才退回 `git checkout` —— 那一步会丢弃该文件的
        未提交改动，属最后手段；没有原始长度就无从"截回"。该分支与修复前逐字相同。
        """
        if getattr(self, "_orig", None) is None:
            subprocess.run(["git", "-C", ROOT, "checkout", "--", self.PROBE],
                           capture_output=True, encoding="utf-8", errors="replace")
            return
        n = len(self._orig)
        try:
            with open(self._probe, "rb") as f:
                cur = f.read()
            if cur[:n] != self._orig or cur[n:] not in (b"", PROBE_LINE):
                sys.stderr.write(
                    "[test_hooks_chain] ⚠ %s 在本类运行期间被改动 —— 本类**不还原**，"
                    "以免覆盖你自己的改动。那行 `P0-3 sentinel probe` 注释会留在文件末尾"
                    "（内容无害、git status 可见），请自行删除。\n" % self.PROBE)
                return
            if len(cur) != n:
                with open(self._probe, "r+b") as f:
                    f.truncate(n)           # ← 只改长度，不填空文件；读者见不到 0 字节
        except OSError as e:
            sys.stderr.write("[test_hooks_chain] ⚠ 探针还原失败（%s）：%r —— "
                             "工程仓可能残留一行探针注释。\n" % (self.PROBE, e))

    def _dirty_protected(self, include_probe=False):
        """工程仓里**除本类探针外**未提交的受保护文件（本类断言的隐含前提）。

        2026-09-16（评审 v13）：本类只还原自己那个探针，却断言"整个工程仓干净" ——
        于是任何人带着别的受保护文件改动跑套件，都会在**正确行为**下拿到红灯。

        `include_probe=True`：连探针文件自身也算进去。还原不再动工作区之后，开发者若在
        `checks/example_code_check.py` 上有未提交改动，它就是"脏"的 —— 此时"干净工程仓"这个前提
        同样不成立，调用方应据此 skip 而不是把红灯记到哨兵头上。
        """
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from checks.harness_guard import _in_protected
        r = subprocess.run(["git", "-C", ROOT, "status", "--porcelain"],
                           capture_output=True, encoding="utf-8", errors="replace")
        out = []
        for ln in (r.stdout or "").splitlines():
            p = ln[3:].strip().strip('"')
            if " -> " in p:                     # rename 形如 "old -> new"
                p = p.split(" -> ")[-1].strip()
            if not p or not _in_protected(p):
                continue
            if p != self.PROBE or include_probe:
                out.append(p)
        return out

    def test_sentinel_blocks_dirty_engine_from_end_repo(self):
        """工程仓门禁文件未提交 + 从端仓 cwd 调 guard（非 self）→ rc=1 拦截。"""
        r = _run([PY, os.path.join(ROOT, "checks", "harness_guard.py"), "example"],
                 cwd=self.repo.path)
        self.assertEqual(r.returncode, 1,
                         "哨兵未拦截脏工程仓（门禁可被静默弱化）: %s"
                         % (r.stdout + r.stderr))
        self.assertIn("example_code_check.py", (r.stdout or "") + (r.stderr or ""),
                      "拦截输出未指出被改动的受保护文件")

    def test_sentinel_passes_with_maintenance_tag(self):
        """脏工程仓 + self 模式带 [harness] 维护标记 → rc=0 放行（有意改动）。"""
        r = _run([PY, os.path.join(ROOT, "checks", "harness_guard.py"),
                  "--self", "[harness] P0-3 sentinel probe"],
                 cwd=self.repo.path)
        self.assertEqual(r.returncode, 0,
                         "带维护标记的合法改动被白拦: %s"
                         % (r.stdout + r.stderr))

    def test_sentinel_clean_engine_passes(self):
        """工程仓干净（还原探针）→ 从端仓 cwd 调 guard → rc=0 放行（普通工单不受影响）。

        2026-09-16（评审 v13）：此例原先只 `_restore_probe()`（还原自己那一个探针）就断言
        "整仓干净"。断言的前提它从不建立 → 开发者工作区里只要有**别的**受保护文件未提交，
        它就在正确行为下报"误报型灾难"（实测：改 `checks/h5_code_check.py` + `harness/run_gate.py`
        时必红，且与该改动是否正确无关）。测试不该依赖工作区的偶然状态：前提不成立就 skip。
        还原改为非破坏性之后，探针文件自身的未提交改动也要算进前提（include_probe=True）。
        """
        self._restore_probe()               # 模拟干净态（只撤本类的探针行）
        dirty = self._dirty_protected(include_probe=True)
        if dirty:
            self.skipTest("工程仓另有未提交的受保护文件，本断言的隐含前提（整仓干净）不成立："
                          + ", ".join(dirty))
        r = _run([PY, os.path.join(ROOT, "checks", "harness_guard.py"), "example"],
                 cwd=self.repo.path)
        self.assertEqual(r.returncode, 0,
                         "干净工程仓被哨兵误拦（误报型灾难）: %s"
                         % (r.stdout + r.stderr))


class TestGuardCallSiteReadsTheRootItIsGiven(unittest.TestCase):
    """锁（2026-09-30）：`check_guard_call_site()` 必须查**它被给的那个 ROOT**。

    这是「上面那条反向锁不再动真实文件」的**前提条件**，所以单独立锁钉住它：
    该函数的路径是从模块级 `ROOT` 拼出来的，只有"指到哪就查哪"成立，反向锁才能
    在临时目录上构造"缺 guard 的 pre-commit.py"；一旦有人把它改成写死路径或
    另接一个来源，那条反向锁就只能回去改写真实的 `harness/hooks/pre-commit.py`
    （共享分发器实体 + 先截断后写）—— 本锁就是拦这件事的。

    三向，防"恒报"与"恒不报"两种假锁：
      ① 目录里**没有** pre-commit.py      → 不报（③ 检查负责报缺文件，此处不重复）
      ② 有，但**不含** guard 引用          → 必须报
      ③ 有，且引用齐全                     → 必须**不**报
    """

    def _check(self, content):
        """把自检模块的 ROOT 指到一棵临时树上跑一次，返回它报出的 problems。"""
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import checks.harness_self_check as sc
        with tempfile.TemporaryDirectory(prefix="harness_selfcheck_") as td:
            if content is not None:
                hooks_dir = os.path.join(td, "harness", "hooks")
                os.makedirs(hooks_dir)
                with open(os.path.join(hooks_dir, "pre-commit.py"), "w",
                          encoding="utf-8", newline="") as f:
                    f.write(content)
            saved = (sc.ROOT, sc.problems, sc.warns)
            sc.ROOT, sc.problems, sc.warns = td, [], []
            try:
                sc.check_guard_call_site()
                return list(sc.problems)
            finally:
                sc.ROOT, sc.problems, sc.warns = saved

    def test_case1_missing_file_is_not_reported_here(self):
        """① 没有 pre-commit.py：本条**不报**（缺文件是 ③ 的职责，重复报会让同一问题出现两次）。"""
        self.assertEqual(self._check(None), [],
                         "③ 已经会报「缺关键门禁文件」，⑥ 不该重复报 —— 否则一个缺失两个 ❌")

    def test_case2_missing_guard_reference_is_reported(self):
        """② 有文件、无 guard 引用 → 必须报（这就是 P0-2 要拦的形态）。"""
        probs = self._check("# 只有别的调用\nimport subprocess\nsubprocess.run(['git', 'status'])\n")
        self.assertTrue(any("harness_guard" in p for p in probs),
                        "pre-commit.py 不引用 guard 却没被发现（保护循环可被静默拆除）: %s" % probs)

    def test_case3_guard_reference_present_is_not_reported(self):
        """③ 有文件、有 guard 引用 → 必须**不**报（否则本判据恒报，等于没有判据）。"""
        self.assertEqual(self._check("import harness_guard\nharness_guard.main()\n"), [],
                         "合规的 pre-commit.py 被误报 —— 判据变成恒报，锁就失效了")


class TestProbeRestoreNeverExposesEmptyFile(unittest.TestCase):
    """回归锁（2026-09-30）：探针还原**不许**让并发读者看到 0 字节 / 残缺内容。

    为什么必须单独立锁：这个缺陷的形状是**并发窗口** —— 单跑本文件看不见它，它只在
    `-j N` 并行、且恰好有别的测试在读同一个文件时才现形，而**下一次**跑又可能是绿的
    （2026-09-30 现场：`test_entry_level.py` 那条把文件读成了**空串**）。靠"记得跑全量、而且别把绿读成
    证据"是纪律，不是锁；本锁把那个窗口搬成一个可重复、亚秒级的判据。

    判据取**读者看见了什么**，不取"源码里写了什么"：假红的成因是读者读到的字节，
    不是某一行代码的存在。所以这里是"起一个读者线程盯着读、主线程反复追加+还原"，
    断言读者**从未**读到「0 字节」或「短于原始长度」。
    ★ 全是本文件自己造的临时文件，**不碰工程仓**（这正是本批要修的形态之一）。
    """

    def test_restore_is_never_seen_as_a_truncated_file(self):
        import threading
        body = b"# -*- coding: utf-8 -*-\n" + b"x = 1  # padding line\n" * 1800
        with tempfile.TemporaryDirectory(prefix="harness_probe_") as td:
            # 直接借用真实那套 setUp/还原逻辑（同一个对象、同两个属性），不另抄一份：
            # 抄一份就会各自漂移，而本锁要钉的恰恰是**那个**实现的并发行为。
            t = TestSentinelBlocksDirtyEngine("test_sentinel_clean_engine_passes")
            t._probe = os.path.join(td, "example_code_check.py")
            t._orig = body
            with open(t._probe, "wb") as f:
                f.write(body)

            stop = threading.Event()
            seen = {"empty": 0, "short": 0, "err": 0, "ok": 0}
            lk = threading.Lock()

            def reader():
                while not stop.is_set():
                    try:
                        with open(t._probe, "rb") as fh:
                            data = fh.read()
                    except OSError:
                        with lk:
                            seen["err"] += 1
                        continue
                    with lk:
                        if not data:
                            seen["empty"] += 1
                        elif len(data) < len(body):
                            seen["short"] += 1
                        else:
                            seen["ok"] += 1

            th = threading.Thread(target=reader, daemon=True)
            th.start()
            try:
                for _ in range(120):
                    with open(t._probe, "ab") as fh:
                        fh.write(PROBE_LINE)
                    t._restore_probe()
            finally:
                stop.set()
                th.join(timeout=5)
            # 末态：还原干净（本类自己也得能收回探针行）
            with open(t._probe, "rb") as fh:
                self.assertEqual(fh.read(), body, "还原后不是原始内容")

        self.assertGreater(seen["ok"], 0,
                           "读者一次都没读到 —— 本锁空转（判据不成立，先修锁再谈结论）")
        self.assertEqual(seen["empty"], 0,
                         "并发读者读到了 **0 字节**（%d 次）—— 还原又变成"
                         "「先截断后写」了。check_level 那类的假红就是这么来的。"
                         % seen["empty"])
        self.assertEqual(seen["short"], 0,
                         "并发读者读到了**短于原始长度**的内容（%d 次）—— 还原过程"
                         "把文件写残了，这比 0 字节更隐蔽（内容看着像真的）。" % seen["short"])
        self.assertEqual(seen["err"], 0,
                         "并发读者读文件失败（%d 次）—— 还原过程中文件一度不可读。" % seen["err"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
