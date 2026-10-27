# -*- coding: utf-8 -*-
"""锁数量看守（评审 v10 §7-2 丢锁回归的固化）。

背景：我同8名覆盖了 tests/test_review_v9_batch2.py，静默吃掉上一轮的 4 条锁，
而当时**所有测试依然全绿**（那条分支没人看守了）。外部评审用「repo-wide grep 两版对照」才抓到。
本看守把那个手法固化成自动断言：模块数、每模块方法数、总数 **只增不减**。
有意减少 → 必须同一次提交里改 lock-inventory.json（等于显式声明，不再静默）。
"""
import io
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TESTS = os.path.join(ROOT, "tests")
MANIFEST = os.path.join(TESTS, "lock-inventory.json")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _count(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return len(re.findall(r"^\s+def test_", f.read(), re.M))


def _anchor_problem(text, mut):
    """这条变异在这份文本里**还施加得上**吗？`None` = 没问题，否则返回原因。

    判据与 `sandbox/mutation_smoke.py::apply_mutation` **同源**（三种 kind 各照抄它那一行），
    刻意**不**另立一套：两处判据一旦漂移，这条锁就会开始说假话（本仓对"判据分叉"的一贯态度）。
    """
    kind = mut["kind"]
    if kind == "exact":
        n = text.count(mut["old"])
        return None if n == 1 else "old 命中 %d 次（期望 1）" % n
    if kind == "regex":
        n = len(re.findall(mut["pattern"], text))
        return None if n >= 1 else "正则 0 命中"
    if kind == "after_def":
        return None if text.find(mut["anchor"]) >= 0 else "锚点未找到"
    return "未知 kind=%r" % kind


class TestLockInventory(unittest.TestCase):
    def setUp(self):
        with io.open(MANIFEST, encoding="utf-8") as f:
            self.inv = json.load(f)

    def test_no_module_lost_tests(self):
        missing, shrunk = [], []
        for mod, n in self.inv["modules"].items():
            p = os.path.join(TESTS, mod)
            if not os.path.isfile(p):
                missing.append(mod)                     # 文件被删/被改名（同名覆盖的典型症状）
                continue
            now = _count(p)
            if now < n:
                shrunk.append("%s：%d → %d" % (mod, n, now))
        self.assertEqual(missing, [], "有测试模块消失了（丢锁回归）：%s" % missing)
        self.assertEqual(shrunk, [], "有模块的 test 方法变少（丢锁回归）：%s" % shrunk)

    def test_total_not_shrunk(self):
        files = []
        for dirpath, dirnames, filenames in os.walk(TESTS):
            dirnames[:] = sorted(d for d in dirnames
                                 if os.path.isfile(os.path.join(dirpath, d, "__init__.py")))
            files.extend(os.path.join(dirpath, f) for f in filenames
                         if f.startswith("test_") and f.endswith(".py"))
        now = sum(_count(p) for p in files)
        self.assertGreaterEqual(now, self.inv["total"],
                                "test 方法总数变少（丢锁回归）：%d → %d" % (self.inv["total"], now))

    def test_key_locks_exist(self):
        """关键锁点名看守 —— 覆盖「文件在、但某条关键锁被删/改名」的情况。"""
        key = {
            "review/test_review_v9_batch1.py": [
                "test_rel_and_changed_lines_use_worktree_basis",
                "test_syntax_probe_is_false_without_node",       # V10-3 精确判据（M7）
                "test_dispatcher_passes_hook_rc_through",
                "test_commitmsg_chain_finds_backup_in_common_dir",
                "test_dispatcher_head_selfcheck_blocks",
                "test_harness_self_runs_on_staged_py",
            ],
            "review/test_review_v9_install_locks.py": [
                "test_custom_hookspath_hooks_are_backed_up",
                "test_worktree_install_backs_up_into_common_dir",
                "test_dispatcher_finds_backup_in_worktree",
                "test_archives_previous_value",                 # 原 test_review_v9_batch2.py，随模块并入
            ],
            # 自保（评审 v11 §3-1）：看守自己也要被看守 —— 否则删掉它不会有任何检查发现
            "tooling/test_lock_inventory.py": ["test_no_module_lost_tests", "test_total_not_shrunk",
                                       "test_key_locks_exist", "test_mutations_list_only_grows",
                                       "test_every_mutation_anchor_still_exists"],
        }
        lost = []
        for mod, names in key.items():
            p = os.path.join(TESTS, mod)
            src = ""
            if os.path.isfile(p):
                # 本仓硬规则：文件读取必须用 with 块（否则触发严格句柄门槛 -W error::ResourceWarning）
                with io.open(p, encoding="utf-8", errors="replace") as f:
                    src = f.read()
            for nm in names:
                if ("def %s(" % nm) not in src:
                    lost.append("%s::%s" % (mod, nm))
        self.assertEqual(lost, [], "关键锁消失（这是守卫被削弱的信号）：%s" % lost)


    def test_dispatcher_fingerprint_line_width(self):
        """评审 v11 §4-V11-4（宽度锁）：链式指纹用的是 `head -2` —— 它依赖「标记在第 2 行」这一事实。
        若未来某次编辑把标记挪走/删掉，指纹检测会**静默失效**；最坏情形是备份里恰有分发器拷贝时
        **链式自递归（无限循环）**。这条锁把那句隐含假设钉死（删/挪标记 → 立刻红）。"""
        for rel in ("harness/hooks-dir/pre-commit", "harness/hooks-dir/commit-msg"):
            with io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            self.assertGreaterEqual(len(lines), 2, "%s 行数过少" % rel)
            self.assertIn("agentguard dispatcher", lines[1],
                          "%s 第 2 行必须是分发器指纹标记（head -2 依赖它）" % rel)


    def test_hook_dispatchers_are_executable_in_git(self):
        """hooks-dir 的两个分发器必须以 100755 入库。

        git 在 POSIX 上**直接 exec** hook：mode 100644 时它被静默忽略（不报错、不跑门禁），
        表现为「装了 hook 但什么都没拦」。Windows 检出看不出差别（core.fileMode=false），
        所以锁在 git 索引的 mode 上，不在工作区权限上。
        """
        if not os.path.exists(os.path.join(ROOT, ".git")):
            self.skipTest("不是 git 检出（源码压缩包）")
        for rel in ("harness/hooks-dir/pre-commit", "harness/hooks-dir/commit-msg"):
            r = subprocess.run(["git", "-C", ROOT, "ls-files", "-s", rel],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            fields = (r.stdout or "").split()
            self.assertEqual(fields[0] if fields else "", "100755",
                             "%s 在 git 索引里的 mode 不是 100755 —— POSIX 上 git 会静默跳过它"
                             % rel)


    def test_mutations_list_only_grows(self):
        """评审 v12 §V12-1（P2，收敛拦路项）：`sandbox/mutation_smoke.py` 的 MUTATIONS 是护盾的"牙齿名单"，
        删/换一条变异 → 该块护盾**静默消失**而冒烟与 CI 全绿（与 v10 丢枪回归同构）。
        这里把名单也纳入"只增不减"看守（动态加载该脚本，比 grep 源码更贴近真实对象）。"""
        import importlib.util
        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        self.assertTrue(os.path.isfile(path), "冒烟脚本不见了：%s" % path)
        spec = importlib.util.spec_from_file_location("_mutation_smoke_probe", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        have = [m["id"] for m in getattr(mod, "MUTATIONS", [])]
        want = self.inv.get("mutations", [])
        self.assertEqual([i for i in want if i not in have], [],
                         "变异被删/改名（护盾静默减护）：清单 %s，实测 %s" % (want, have))
        self.assertGreaterEqual(len(have), len(want),
                                "变异数量变少：清单 %d，实测 %d" % (len(want), len(have)))

    def test_every_mutation_anchor_still_exists(self):
        """护盾的**锚点**看守（2026-09-23 夜；同族第二次现场：M44 → M88）。

        上面 `test_mutations_list_only_grows` 挡的是「变异被删/改名」，**挡不住**
        「变异还在、但它瞄的那句话已经从产品文件里消失了」。锚点命中 0 次 ⇒ `apply_mutation`
        返回错误 ⇒ 冒烟把它**按失败计**（判据本身是对的）。危害在于**没有任何测试会红**：
        产品全绿、名单照旧、台账照旧，唯一没发生的事是**注入本身** —— 属「已登记」冒充「已验证」，
        而且只有真跑冒烟（本机十几分钟）才发现。

        两次现场都是「动了被变异瞄着的那一行」：
          · M44：H1 把 `harness/run_gate.py` 的 `_log_run` 调用点拆成多行并加了 `mode_override=`；
          · M88：清单第⑦条**删掉的正是它自己的锚点句**（删完锚点命中 0 次）。

        这条锁把「锚点还在不在」提前到每次跑套件都问一遍（秒级，不依赖冒烟）。
        ⚠ 它证明的是「锚点还在」，**不是**「施加后锁一定会红」—— 后者只有真跑冒烟才算数。
        """
        import importlib.util
        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        spec = importlib.util.spec_from_file_location("_mutation_anchor_probe", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        dead = []
        for mut in getattr(mod, "MUTATIONS", []):
            p = os.path.join(ROOT, mut["file"])
            if not os.path.isfile(p):
                dead.append("%s：目标文件不存在（%s）" % (mut["id"], mut["file"]))
                continue
            # 本仓硬规则：文件读取必须用 with 块（否则触发严格句柄门槛 -W error::ResourceWarning）
            with io.open(p, encoding="utf-8", errors="replace") as f:
                text = f.read()
            why = _anchor_problem(text, mut)
            if why:
                dead.append("%s：%s（%s）" % (mut["id"], why, mut["file"]))
        self.assertEqual(dead, [],
                         "以下变异的锚点在产品文件里已经命中不到 —— 它们**根本没被注入过**"
                         "（冒烟按失败计，但那是十几分钟之后的事，而且只看得到「等于没测」）：\n  "
                         + "\n  ".join(dead)
                         + "\n改法：现读产品文件重接锚点（或按当前状态翻转证伪方向），再跑全量冒烟复验。")

    def test_the_anchor_check_is_not_vacuous(self):
        """判据本体两向都验：拿**合成文本**喂 `_anchor_problem`，不靠"现在恰好是哪种"。

        少了这一条，「锚点全都在」可能只是因为判据永远返回 None —— 那就是又一条空锁。
        """
        exact = {"kind": "exact", "old": "锚点句", "new": "x"}
        self.assertIsNone(_anchor_problem("前\n锚点句\n后", exact))
        self.assertIsNotNone(_anchor_problem("这句不在里面", exact),
                             "锚点不存在却没被判出来 —— 判据在空转")
        self.assertIsNotNone(_anchor_problem("锚点句\n锚点句", exact),
                             "命中两次也必须报：`apply_mutation` 的 exact 只接受**恰好一次**，"
                             "取第一次替换会静默改错地方")
        after = {"kind": "after_def", "anchor": "def f():", "inject": "    pass\n"}
        self.assertIsNone(_anchor_problem("def f():\n    return 1\n", after))
        self.assertIsNotNone(_anchor_problem("def g():\n    return 1\n", after),
                             "after_def 的锚点不存在却没被判出来")
        rx = {"kind": "regex", "pattern": r"AAA+", "repl": "B"}
        self.assertIsNone(_anchor_problem("xAAAz", rx))
        self.assertIsNotNone(_anchor_problem("xyz", rx), "正则 0 命中却没被判出来")


    def test_copy_tree_yields_a_clean_work_tree(self):
        """变异冒烟的**地基**锁：副本必须是一份能用的、干净的 git 工作区（2026-09-17 实测缺失）。

        为什么这条属于本看守：冒烟是护盾的牙齿，而"副本怎么造"是这个机制的地基。
        地基塌掉的表现**不是报错**，而是每一步都倒在第一步的对照上
        ——「✗ 对照失败：副本本身跑不绿 → 结论不可信」——观感是"冒烟在跑"，实际一条变异都没执行。
        成因是跨模块的隐性耦合：`checks/harness_guard.py` 按 fail-closed 设计，读不出工程仓的
        未提交改动就直接 rc=2（"查不了 ≠ 没改动"）；副本里原先没有 .git，于是任何走真实
        pre-commit 入口的锁都拿到 rc=2 而不是预期的 1。M1–M19 就这样空转，直到 2026-09-17
        给 M20–M25 登记名单时才发现。

        这条锁只钉**地基那一半**（副本是工作区 + status 干净）；另一半（整条冒烟端到端跑得通）
        由 CI 的 mutation-smoke job 兜 —— 本地跑一次十分钟出头，不适合塞进套件。
        """
        import importlib.util
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("git"):
            self.skipTest("无 git：本仓全部机制都建立在 git 上，此环境下无从判定")

        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        spec = importlib.util.spec_from_file_location("_mutation_smoke_copytree", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        def git(*args):
            r = subprocess.run(["git", "-C", tmp] + list(args), capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            return r.returncode, (r.stdout or ""), (r.stderr or "")

        tmp = tempfile.mkdtemp(prefix="lockinv_copytree_")
        try:
            mod.copy_tree(tmp)
            rc, out, err = git("rev-parse", "--is-inside-work-tree")
            self.assertEqual((rc, out.strip()), (0, "true"),
                             "变异冒烟的副本不是 git 工作区 → 对照必然倒在 harness_guard 的 "
                             "fail-closed 上，冒烟变成空转（rc=%d, out=%r, err=%r）" % (rc, out, err))
            rc, out, err = git("status", "--porcelain")
            self.assertEqual(rc, 0, "副本里 git status 跑不动：%s" % err)
            self.assertEqual([l for l in out.splitlines() if l.strip()], [],
                             "副本必须是**干净**工作区（HEAD 与工作区逐字节一致）："
                             "否则冒烟第一步就会把已有的未提交改动读成变异结果")
        finally:
            # git 的对象文件是**只读**的 → 直接 rmtree 会 PermissionError，而 `ignore_errors=True`
            # 把这个失败**静默吞掉**，于是每跑一次套件就在 %TEMP% 留一份完整仓库副本
            # （2026-09-21 实测：157 份 / 402 MB —— 对照本条自己的用途，它只是个只读地基锁）。
            # 2026-09-21：本文件原先内联了这份"摘只读位再删"的循环，现收敛到
            # `tests/_tmpclean.rmtree_force`（同一根因在本仓出现过多处，各写一份就会各自漂移）。
            _rmtree_force(tmp)


    def test_stale_bytecode_cannot_survive_a_mutation(self):
        """冒烟的**判据真伪**锁：变异换掉的字节，跑的时候真的被执行到了吗。

        为什么属于本看守：冒烟是护盾的牙齿，而「施加了变异、跑的是谁的字节码」是这个机制里最容易
        空转的一环。2026-09-23 当场证出来的坑 —— CPython 时间戳式 pyc 的失效判据是
        **(源码 mtime 的整秒, 源码字节数)** 这一对：同一文件的连续两条变异只要改出来字节数相同
        （本批 M49/M50/M54/M55 恰好都是 +18）、且上一轮 unittest 没跨过整秒边界（探针表 0.02s，
        一个来回常在同一秒内），第二条就会**跑上一条的字节码**。
        危害不对称，最坏的那半是真骗人：连红时只是失败名字不对劲（实测 M50 那轮报的是
        `['test_r1_cases']`，即 M49 的代码在跑），但**同名期望的连号变异会变成假绿** ——
        名单里 M51/M52/M53 都期望 `test_r2_cases`、都改同一个文件，谁的字节码被复用都照样"红"，
        而被复用的那条其实一次都没被测过。

        判据两向，缺一不可：
          · 绕过 `apply_mutation` 直接写（= 没有清理）→ 必须读到**旧**内容。少了这一向，
            这条锁可能在为一个不存在的危险上锁（本仓反复记过的"判据空转"）；
          · 经 `apply_mutation` 改 → 必须读到**新**内容。这一向才是接线锁：清理一旦从入口上
            被摘掉，它立刻红（同批的 M56 就是这么做的阴性对照）。

        ★ 第二向的坑（2026-09-23 当场踩到，写在这里免得后人再写出一条**看着有两向、其实只有一向**
        的锁）：`apply_mutation` 写入时拿的是**当前** mtime，所以「源被钉在 t、pyc 头记着 t」这组
        碰撞条件在变异之后**自动失效** —— 那一步无论有没有清理都会重编译、都会读到新内容，
        于是断言恒绿。实测：M56（摘掉清理）一度报「变异后仍然绿」，锁其实是空转的。
        修法是**把碰撞条件直接构造出来**：变异之后再把源的 mtime 钉回 t（= pyc 头里记的那个秒，
        `os.utime` 是测试侧的探针动作，不是被验的机制）—— 这正是真实情形里「两条变异落在同一秒」
        的那一步，只是不靠等运气。★ 反过来若改成"等它自然落在同一秒"，这条锁就会在跨秒时假绿，
        变成一条看时钟脸色的用例。

        mtime 用**钉死的整秒**（不是 `time.time()`），否则这条锁自己会变成一条看时钟脸色的用例。
        """
        import importlib.util
        import subprocess
        import tempfile
        import time

        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        spec = importlib.util.spec_from_file_location("_mutation_smoke_pyc", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        body_a = 'V = "A"\n# ' + "x" * 200 + "\n"
        body_b = 'V = "B"\n# ' + "x" * 200 + "\n"          # 与 A **逐字节同长**
        self.assertEqual(len(body_a.encode("utf-8")), len(body_b.encode("utf-8")))
        t = int(time.time()) - 600                            # 钉死的整秒，不跟时钟走

        tmp = tempfile.mkdtemp(prefix="lockinv_pyc_")
        try:
            p = os.path.join(tmp, "pyc_probe_mod.py")

            def read_v():
                env = dict(os.environ)
                # `python -c` 不保证 cwd 在 sys.path（PYTHONSAFEPATH=1 / -P 会摘掉它）：
                # 显式注入 PYTHONPATH，探针才只测「陈旧 pyc 会不会被复用」，不会把
                # 「import 不到模块」误读成同样空白的 stdout（2026-10-22 复审报告现场：
                # 某环境下 read_v() 恒返回 ''，红的却是锁本身）。
                env["PYTHONPATH"] = tmp + (os.pathsep + env["PYTHONPATH"]
                                           if env.get("PYTHONPATH") else "")
                # 2026-10-25 评审 §5.1：某些嵌入式 Python 里 PYTHONPATH 也不足以让
                # `-c` 找到 cwd 的模块（现场仍是 ModuleNotFoundError）。把路径用 argv
                # 显式塞进 sys.path —— 不依赖 cwd、-P、PYTHONPATH 任一环境语义。
                r = subprocess.run([sys.executable, "-c",
                                    "import sys;sys.path.insert(0, sys.argv[1]);"
                                    "import pyc_probe_mod as m;print(m.V)", tmp],
                                   cwd=tmp, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", env=env)
                self.assertEqual(r.returncode, 0,
                                 "探针子进程没跑起来（不是 pyc 判定问题）：%s"
                                 % (r.stderr or "")[-500:])
                return (r.stdout or "").strip()

            def write(body):
                with io.open(p, "w", encoding="utf-8", newline="") as f:
                    f.write(body)
                os.utime(p, (t, t))

            write(body_a)
            self.assertEqual((int(os.stat(p).st_mtime), os.path.getsize(p)),
                             (t, len(body_a.encode("utf-8"))),
                             "本文件系统不支持把 mtime 钉到指定整秒 → 这条锁失去判据，"
                             "不要放它过去（请在此环境下改用别的失效手段）")
            self.assertEqual(read_v(), "A", "第一次读不到 A：夹具本身没跑起来")
            # ⓪ 平台无关的 M56 判据：不依赖解释器是否写/复用 pyc，只问
            # `apply_mutation` 有没有清掉文件旁的 __pycache__。手工造一个目录即可，
            # 否则在「不写 pyc」的环境里这条判据会空转（M56 假绿）。
            cache_dir = os.path.join(tmp, "__pycache__")
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, "pyc_probe_mod.cpython-313.pyc"), "wb") as f:
                f.write(b"")
            err = mod.apply_mutation(p, {"kind": "exact", "old": body_a, "new": body_b})
            self.assertIsNone(err, "apply_mutation 没施加成功：%s" % err)
            self.assertFalse(os.path.isdir(cache_dir),
                             "apply_mutation 没清 __pycache__：M56 会假绿（下一条变异会复用"
                             "上一条的字节码，而汇总仍是全绿）")
            write(body_a)                         # 回到「源 A + pyc 头匹配」的前提
            self.assertEqual(read_v(), "A", "清理后重读 A 失败：夹具没恢复")
            # ① 直接改写（= 没有清理）：必须读到**旧**的 A —— 这一向证明坑真实存在
            write(body_b)
            got = read_v()
            if got == "B":
                # 2026-10-26 第四轮评审 §4.4：有的解释器/环境不按 (mtime 整秒, 字节数)
                # 复用陈旧 pyc（例如不写 pyc、或失效判据不同）。此时「陈旧字节码顶替」的
                # 前提根本不成立，断言成红只是在惩罚平台差异 —— 明确 skip，不改成 expect B。
                cache = os.path.join(tmp, "__pycache__")
                pycs = ([n for n in os.listdir(cache) if n.startswith("pyc_probe_mod.")]
                        if os.path.isdir(cache) else [])
                self.skipTest(
                    "本平台不复用 (mtime, 字节数) 匹配的陈旧 pyc（%s），"
                    "「陈旧字节码顶替」的前提不成立 —— 这条锁在本平台不适用（不是通过）。"
                    % ("没有生成 .pyc" if not pycs else "生成了 .pyc 但解释器未复用"))
            self.assertEqual(got, "A",
                             "同 mtime+同字节数时读到的是新内容，但也没走 skip 分支 —— "
                             "先核实判据（读的是字节码还是磁盘源码？），再决定这条锁该怎么写。")
            # ② 走 apply_mutation（真入口）：pyc 必须**顶替不了**变异后的内容
            write(body_a)                                     # 源回到 A，pyc 头 (t, 同长) 与它匹配
            err = mod.apply_mutation(p, {"kind": "exact", "old": body_a, "new": body_b})
            self.assertIsNone(err, "apply_mutation 没施加成功：%s" % err)
            os.utime(p, (t, t))                               # ★ 构造「两条变异落在同一秒」这一步
            self.assertEqual(read_v(), "B",
                             "变异后读到的还是上一条的字节码 → 冒烟的变异可能空转（假绿），"
                             "`apply_mutation` 末尾必须清掉被测文件旁边的 __pycache__"
                             "（注意：本断言依赖上面那次 os.utime —— 少了它，"
                             "pyc 头因为 mtime 变了而自动失效，这一步会恒绿）")
        finally:
            _rmtree_force(tmp)


    def test_smoke_run_is_pinned_to_full_mode(self):
        """冒烟的模式锁（2026-09-17 接线时补）：`run()` 必须**显式剔除 HARNESS_FAST**。

        为什么属于本看守：冒烟是护盾的牙齿，"它按哪个档位跑"是这个机制的前提。形状与上面那条
        地基锁同构 —— 出问题的表现**不是报错，而是结论被悄悄改了**：
        M1/M3/M5/M7 四条变异的 lock 就是 `tests/test_review_v9_batch1.py`，该文件接上快跑开关
        之后，`run()` 若继续继承调用方环境，谁 shell 里带着 `HARNESS_FAST=1`（README 把
        `--fast` 列为日常档）跑冒烟，这四条就会报「变异后仍然绿」—— 锁明明还在，却被读成
        "锁被弱化"。假红与假绿同害：都会把这份名单训成噪音，而名单一旦没人信就废了。

        两向断言（防"把环境整个清空"也算过）：`HARNESS_FAST` 必须**传不进**子进程，
        而其它变量必须**照样传得进**。
        """
        import importlib.util
        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        spec = importlib.util.spec_from_file_location("_mutation_smoke_fullmode", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        probe = [sys.executable, "-c",
                 "import os;print(os.environ.get('HARNESS_FAST', '-'),"
                 " os.environ.get('HARNESS_FAST_PROBE', '-'))"]
        keys = ("HARNESS_FAST", "HARNESS_FAST_PROBE")
        saved = {k: os.environ.get(k) for k in keys}
        os.environ["HARNESS_FAST"] = "1"            # 模拟"shell 里 export 了日常快跑档"
        os.environ["HARNESS_FAST_PROBE"] = "kept"
        try:
            r = mod.run(probe, ROOT, timeout=90)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual((r.stdout or "").split(), ["-", "kept"],
                         "冒烟的 run() 没把 HARNESS_FAST 剔干净（快跑档会污染变异结论），"
                         "或把环境整个清空了（其余变量必须照常继承）：rc=%d out=%r err=%r"
                         % (r.returncode, r.stdout, (r.stderr or "")[:200]))


    # 合成夹具（同 ENTRY 的纪律：写成类属性，不污染模块命名空间）。
    # 它比 ENTRY 多要一点：锁模块里有**两条**用例 —— 一条是 `expect` 点名的（被变异咬得住），
    # 一条是**兄弟用例**（写标记文件）。整跑模块时兄弟必然执行，"点名跑"时必然不执行。
    ENTRY_NAMED = {"id": "SYNTH-lock-run-targets-named-case", "file": "checks/probe_rule.py",
                   "kind": "exact", "old": 'RULE = "before"\n', "new": 'RULE = "after"\n',
                   "lock": "test_probe_lock.py", "expect": "test_wanted",
                   "why": "合成夹具：验冒烟只跑 `expect` 点名的那条用例，不牵连同模块的兄弟用例"}

    def test_lock_run_targets_only_the_named_case(self):
        """冒烟的**耗时**锁（2026-09-30）：每条变异只跑 `expect` 点名的那条，不再整跑锁模块。

        为什么属于本看守：冒烟是护盾的牙齿，**跑多慢**决定了牙齿多久被磨一次 ——
        原先每条变异都 `-p <锁模块>` 整跑整个模块，而判据本来就只认一条用例
        （`failed_names(out)` 与 `expect` 的比对），于是 105 条变异里 51 条挤在同一个
        `test_review_2026_09_23.py` 上，等于把那个模块整跑了 51 遍：实测该模块整跑 23.5s、
        `-k` 点名跑 0.445s（53×），整趟冒烟 ~40 分钟 → 几分钟。跑得慢的看守**实际不被跑**，
        那和没有看守是一回事（本仓对"名单没人信就废了"的一贯态度）。

        ⚠ 这不是性能优化那么简单 —— 它**改了跑什么**，所以必须两向锁住：
          · **只跑点名那条**：同名模块里的兄弟用例**不许**被执行（否则"优化"没生效，
            悄悄退回整跑，而这从输出上看不出来）；
          · **`--full-module-lock` 真的能退回整跑**：这是排查用的退路，若它是个摆设，
            将来某条变异依赖同模块残留状态时，人以为自己在 A/B，其实两次跑的是同一件事。
        两向都靠同一个**可观测的副作用**（兄弟用例写标记文件）来判，不是读源码字面。

        外加三条**形状**断言（秒级、不落盘）钉住 `_lock_cmd` 的三种入参：空/None 的
        `expect` 一律**不许**退化成 `-k ""`（那会匹配到全部用例——看起来"也跑了"，
        实际把「这条变异没登记期望锁」这件事藏起来），而三种形状都必须仍带着
        `discover -s tests -p <锁模块>` 的骨架。
        """
        import contextlib
        import importlib.util
        import subprocess
        import tempfile
        import types

        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        spec = importlib.util.spec_from_file_location("_mutation_smoke_lockcmd", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # ── 形状断言：三种入参 ───────────────────────────────────────────────
        for lock, exp, want_k in (("test_x.py", "test_a", "test_a"),
                                  ("test_x.py", "", None),
                                  ("test_x.py", None, None)):
            cmd = mod._lock_cmd(lock, exp)
            self.assertIn("-p", cmd, "命令丢了 `-p <锁模块>` 骨架：%r" % (cmd,))
            self.assertEqual(cmd[cmd.index("-p") + 1], lock, "`-p` 之后不是锁模块名：%r" % (cmd,))
            self.assertIn("discover", cmd, "命令丢了 `discover` 骨架：%r" % (cmd,))
            self.assertEqual(cmd[cmd.index("-s") + 1], "tests", "`-s` 之后不是 tests：%r" % (cmd,))
            if want_k is None:
                self.assertNotIn("-k", cmd,
                                 "`expect=%r` 时不该出现 `-k` —— 空值退化成 `-k ''` 会静默跑起全部用例，"
                                 "把「这条变异没登记期望锁」藏起来：%r" % (exp, cmd))
            else:
                self.assertIn("-k", cmd, "`expect` 在场却没带 `-k`：%r" % (cmd,))
                self.assertEqual(cmd[cmd.index("-k") + 1], want_k, "`-k` 后面不是期望的那条：%r" % (cmd,))

        # ── 行为断言：真 main() 跑合成仓，看兄弟用例有没有被执行 ──────────────
        marker = os.path.join(tempfile.mkdtemp(prefix="lockinv_named_"), "SIBLING_RAN")
        tmp = os.path.dirname(marker)
        src = os.path.join(tmp, "synth")

        def w(rel, text):
            p = os.path.join(src, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with io.open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)

        def commit():
            for argv in (["add", "-A", "-f"], ["commit", "-qm", "synth base"]):
                r = subprocess.run(["git", "-C", src, "-c", "user.email=lock-inventory@local",
                                    "-c", "user.name=lock-inventory", "-c", "commit.gpgsign=false",
                                    "-c", "core.hooksPath="] + argv,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(r.returncode, 0,
                                 "合成仓 %s 失败：%s" % (argv[0], (r.stderr or "")[:300]))

        def run_smoke(tag, argv):
            """跑**真的 main()**；返回 (rc, 输出, 兄弟用例有没有被执行)。"""
            copy_dir = os.path.join(tmp, "copy_" + tag)
            fake = types.SimpleNamespace(mkdtemp=lambda prefix="": copy_dir)
            saved = (mod.ROOT, mod.MUTATIONS, mod.tempfile, sys.argv)
            mod.ROOT, mod.MUTATIONS, mod.tempfile = src, [self.ENTRY_NAMED], fake
            sys.argv = ["mutation_smoke.py"] + list(argv)
            if os.path.exists(marker):
                os.remove(marker)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod.main()
            finally:
                mod.ROOT, mod.MUTATIONS, mod.tempfile, sys.argv = saved
                _rmtree_force(copy_dir)
            return rc, buf.getvalue(), os.path.exists(marker)

        try:
            os.makedirs(src, exist_ok=True)
            r = subprocess.run(["git", "init", "-q"], cwd=src, capture_output=True)
            self.assertEqual(r.returncode, 0, "合成仓 git init 失败")
            # 对照组（`test_review_v9_batch*.py`）必须在场且能跑，否则 main() 先倒在对照那一关。
            w("tests/test_review_v9_batch1.py",
              "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
            w("checks/probe_rule.py", 'RULE = "before"\n')
            # ★ 标记路径是**绝对**的、写在副本之外：`run_smoke` 的 finally 会删掉副本目录，
            #   写在副本里就来不及看；写在源仓里则会污染下一次 commit。
            w("tests/test_probe_lock.py",
              "import os\nimport sys\nimport unittest\n\n"
              "sys.path.insert(0, os.path.join("
              "os.path.dirname(os.path.dirname(os.path.abspath(__file__))), \"checks\"))\n"
              "import probe_rule\n\n\n"
              "MARKER = r%r\n\n\n"
              "class T(unittest.TestCase):\n"
              "    def test_wanted(self):\n"
              "        self.assertEqual(probe_rule.RULE, \"before\",\n"
              "                         \"被变异了：RULE=%%r\" %% (probe_rule.RULE,))\n\n"
              "    def test_sibling_never_named(self):\n"
              "        with open(MARKER, \"w\", encoding=\"utf-8\") as f:\n"
              "            f.write(\"ran\")\n" % (marker,))
            commit()

            # ① 默认档：只跑 test_wanted → 兄弟用例**不许**被执行
            rc, out, sibling_ran = run_smoke("named", [])
            self.assertEqual(rc, 0, "默认档没把这条变异判成命中（夹具前提不成立）：\n%s" % out)
            # `✓` 只出现在 `else` 那一支，而进那一支的前提正是
            # 「`test_wanted` 出现在 `failed_names(out)` 里」—— 即"点名跑"仍然咬得住变异。
            self.assertIn(self.ENTRY_NAMED["id"], out, "输出里没有这条变异：\n%s" % out)
            self.assertIn("✓ FAILED", out,
                          "没有走到 RED ✓ 那一支（唯一会检查「期望的那条真的红了」的分支）：\n%s" % out)
            self.assertFalse(sibling_ran,
                             "同模块的兄弟用例被执行了 —— 「只跑点名那条」没有生效（悄悄退回整跑）。"
                             "整趟冒烟会因此慢一个数量级，而输出上看不出来：\n%s" % out)

            # ② 退路档 `--full-module-lock`：必须**真的**退回整跑（否则排查时人以为在 A/B，
            #    其实两次跑的是同一件事）
            rc2, out2, sibling_ran2 = run_smoke("full", ["--full-module-lock"])
            self.assertEqual(rc2, 0, "退路档把这条变异判成失败了：\n%s" % out2)
            self.assertIn("✓ FAILED", out2, "退路档没走到 RED ✓：\n%s" % out2)
            self.assertTrue(sibling_ran2,
                            "`--full-module-lock` 没能退回整跑模块 —— 它是个摆设，"
                            "将来排查「变异依赖同模块残留状态」时会被它骗：\n%s" % out2)
        finally:
            _rmtree_force(tmp)


    # 平台盲区的**逐个列举**名单（只有这四条信息，别再往里加别的语义）。
    # 写成"名单 + 全等比对"而不是"只增不减"：盲区是个**逃生门**，多一条都得有人点头，
    # 所以增/删/改名**都要动这里**，而且一眼看得出来——这与 MUTATIONS 那条"只增不减"的
    # 纪律**故意不同**（那条防的是"护盾被悄悄删"，这条防的是"盲区被悄悄加"）。
    KNOWN_BLIND = {
        "M114-in-tree-drops-normcase": ("posix", "M115-in-tree-hardcoded-lower"),
        "M115-in-tree-hardcoded-lower": ("nt", "M114-in-tree-drops-normcase"),
        # 2026-10-25（Nightly L2 实测：M143 在 Linux 上「变异后仍然绿」）：
        # 杀整树的两份实现各绑一个平台 —— taskkill /T 只对 Windows 可见，
        # killpg 只对 POSIX 可见。两条合起来才是「两个平台都有人守」。
        "M143-taskkill-loses-slash-T": ("posix", "M145-killpg-becomes-proc-kill"),
        "M145-killpg-becomes-proc-kill": ("nt", "M143-taskkill-loses-slash-T"),
        # 2026-10-28（第六轮评审 P1-1）：调度器 run_gate 的 `_kill_tree` 是**另一份**同形实现
        # （引擎层不能 import 壳包），所以杀树的两个平台半边各要一套盲区声明 ——
        # M143/M145 守的是 cli.py，这两条守的是 run_gate.py，互相不可替代。
        "M157-taskkill-loses-slash-T": ("posix", "M158-killpg-becomes-proc-kill"),
        "M158-killpg-becomes-proc-kill": ("nt", "M157-taskkill-loses-slash-T"),
    }

    def test_platform_scoped_shields_are_paired_and_inventoried(self):
        """平台盲区（`blind_on`）的看守（2026-09-30；现场：`90b4b58` 的 CI 红）。

        病根不是锁坏了，是**冒烟没有平台概念**：`M114` 撤掉 `in_tree` 的 `os.path.normcase`，
        而 `normcase` 在 POSIX 上是恒等变换 ⇒ 变异体与原体在 Linux 上语义相同，**变不出红**。
        CI 的 `变异冒烟` 跑在 Linux，于是报「变异后仍然绿 → 锁被弱化」—— 诊断方向指反了
        （该去查"这条护盾在这个平台上是否可见"，却被指去查锁）。
        与 `4fa05de` 那次「只在 Linux 上红」的**锁**是同一个病的镜像：判据依赖了"哪台机器在跑"。

        ⚠ 放宽成"看不见就算了"就是个**逃生门** —— 任何一条护盾只要声明盲区就能自动闭嘴，
        而冒烟从此再也不会为它变红。所以这里钉死三件事：

          ① **名单要列举**：`blind_on` 的集合必须与 `KNOWN_BLIND` **逐一相等**（多一条、
             少一条、改个平台都红）—— 加盲区是一次要人点头的动作，不是顺手写的字段。
          ② **理由要写**：`blind_why` 非空（不许写"看不见"三个字了事）。
          ③ **互补性（关键）**：`covered_by` 指的那条必须**存在**、必须在**同一个锁模块**里、
             必须**在相反的平台上可见**，且两条的盲区并集**恰好覆盖** {nt, posix}。
             这条把"盲"限制在"单眼"：**没有任何一条护盾可以两个平台都瞎**。
             它也是这条机制唯一的**实质**保证 —— 没有它，`blind_on` 就只是一句自我声明。

        判据取的是数据（动态加载冒烟模块的 `MUTATIONS`），不是读源码字面 ——
        与 `test_mutations_list_only_grows` 同一口径。
        """
        import importlib.util
        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        spec = importlib.util.spec_from_file_location("_mutation_blind_probe", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        have = {m["id"]: list(m.get("blind_on") or []) for m in mod.MUTATIONS}
        declared = {k: v for k, v in have.items() if v}
        why = {m["id"]: (m.get("blind_why") or "").strip() for m in mod.MUTATIONS}
        cover = {m["id"]: m.get("covered_by") for m in mod.MUTATIONS}
        lockof = {m["id"]: m["lock"] for m in mod.MUTATIONS}

        # ① 名单列举：多/少/改平台都红
        self.assertEqual(sorted(declared), sorted(self.KNOWN_BLIND),
                         "平台盲区名单与 KNOWN_BLIND 不一致（多/少/改名都算 —— 盲区是逃生门，"
                         "要经人点头；实测 %s，清单 %s）" % (sorted(declared), sorted(self.KNOWN_BLIND)))
        for mid, plats in sorted(declared.items()):
            self.assertEqual(plats, [self.KNOWN_BLIND[mid][0]],
                             "%s 的 blind_on 与清单不符：实测 %s" % (mid, plats))

        for mid, (plat, buddy) in sorted(self.KNOWN_BLIND.items()):
            # ② 词表 + 理由
            self.assertIn(plat, ("nt", "posix"),
                          "%s 的盲区平台 %r 不在词表里（只认 nt / posix —— "
                          "写别的名字 = 永远不会命中，等于把这条护盾悄悄关掉）" % (mid, plat))
            self.assertTrue(why.get(mid),
                            "%s 声明了平台盲区却没写 blind_why —— 没有理由的盲区不允许" % mid)

            # ③ 互补性：对面那条必须存在、同模块、且在**另一个**平台上可见
            self.assertTrue(buddy and buddy in have,
                            "%s 的 covered_by=%r 指向一条不存在的变异 —— 盲区没有兜底" % (mid, buddy))
            self.assertIn(buddy, declared,
                            "%s 的 covered_by=%s 自己没声明盲区：要么对面也瞎（那本平台就没人管了），"
                            "要么它其实两个平台都看得见（那本条根本不必盲）" % (mid, buddy))
            self.assertEqual(lockof.get(buddy), lockof.get(mid),
                             "%s 与兜底 %s 不在同一个锁模块（%s vs %s）—— "
                             "换一条模块的护盾顶上不算互补，那是另一件事的判据"
                             % (mid, buddy, lockof.get(mid), lockof.get(buddy)))
            self.assertEqual(cover.get(buddy), mid,
                             "%s 与 %s 的 covered_by 没对上（互补要**互相**指，单向指的是一半）"
                             % (mid, buddy))
            self.assertEqual(set(declared[mid]) | set(declared[buddy]), {"nt", "posix"},
                             "%s 与 %s 的盲区并集不是 {nt, posix} —— "
                             "**没有任何一条护盾可以两个平台都瞎**" % (mid, buddy))


    def test_only_filter_is_never_silently_ignored_or_widened(self):
        """`--only` 的**四向**行为锁（2026-09-24：同一个「过滤器不可信」的病，一次咬了我两下）。

        现场①：`--only=M92`（完整 id 只写了第一段）一条都没匹配上，冒烟空转一圈打印
               「0 个变异：命中 0」并**返回 0** —— 与「跑过了、全绿」在观感与退出码上都没有区别。
        现场②：模块头的用法行写的是 `--only <id>`（空格），而解析器**只认等号** ⇒ 照用法行敲
               会被**静默忽略**，本来只想跑一条、结果跑了全量 95 条（约四十分钟）。
        两处方向相反（一个假绿、一个跑过头），根因同一个：**选择不可见**。

        判据是**行为**，不是字面（本仓对「读源码 grep 出结论」的一贯态度）：真喂 argv 给真
        `main()`，看它选了几条、退哪个码。四向 + 一条对照：

          ① `--only <完整 id>`（空格写法）→ rc=0，且**恰好**选中那一条
          ② `--only=<完整 id>`（等号写法）→ 与①**逐字相同**（本次故障正是二者不一致）
          ③ `--only=<短 id>`（= 完整 id 的第一段，现场①）→ rc=1、报「匹配不上」，
             且**不许**出现「选中 0 个变异」—— 空结论不得报绿
          ④ `--only` 摆在末尾（无值，现场②的静默退回全量）→ rc=1、报「（空值）」，
             且**不许**出现任何「选中…」—— 出现了就是又跑成全量了
          ⑤ 对照：不带 `--only` → rc=0 且选中**全量**。少了这一向，①～④ 可能被一个
             「永远报错」的实现蒙混过关（同 `test_missing_lock_module…` 的两向纪律）

        末段再把**用法行本身**拉进判据：模块 docstring 必须示范至少一种 `--only` 写法，且示范的
        **每一种都必须真能跑通**。2026-09-24 那次故障的本质就是「文档教的写法解析器不认」，而用法行
        是散文，没有任何测试看得住它。⚠ 扫描**只认 `--only=` 与 `--only `（空格）两种分隔符**：
        散文里像 `--only（…）`、`--only，例如` 这类跟随字符刻意**不扫** —— 把散文的任意跟随字符都
        当写法会造成误报，而误报的代价是下一次真漂移没人信（同 `test_docs_consistency.py` 的纪律）。

        夹具走 `--dry-run`（同批新增）：它在**守卫之后、建副本之前**返回，于是整条锁不起子进程、
        不落盘 —— 真跑四向要小十分钟（一条变异的对照段就是 ~84s）。`mkdtemp` 被换成会炸的假件：
        干跑若"不干"，这条锁自己先红，而不是悄悄跑趟全量再报绿。
        """
        import contextlib
        import importlib.util
        import types

        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        spec = importlib.util.spec_from_file_location("_mutation_smoke_only_forms", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        full = mod.MUTATIONS[0]["id"]
        short = full.split("-")[0]          # 现场①：完整 id 只写第一段
        self.assertNotEqual(short, full, "夹具前提不成立：短 id 与完整 id 相同（%r）" % full)

        def dry(argv):
            """喂真 argv 给真 `main()`，返回 (rc, 输出)。干跑不许落盘（落盘就直接炸）。"""
            def _boom(prefix=""):
                raise AssertionError("`--dry-run` 走到了建副本那一步（干跑不干跑）：argv=%s" % (argv,))
            fake = types.SimpleNamespace(mkdtemp=_boom)
            saved = (mod.tempfile, sys.argv)
            mod.tempfile, sys.argv = fake, ["mutation_smoke.py"] + list(argv)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod.main()
            finally:
                mod.tempfile, sys.argv = saved
            return rc, buf.getvalue()

        # ① 空格写法（用法行教的就是这一种）：恰好选中那一条
        rc1, out1 = dry(["--only", full, "--dry-run"])
        self.assertEqual(rc1, 0, "空格写法被判失败（用法行教的就是它）：\n%s" % out1)
        self.assertIn("选中 1 个变异 %s" % full, out1,
                      "空格写法没有恰好选中那一条 —— 现场②的静默忽略就是这么发生的：\n%s" % out1)

        # ② 等号写法：必须与①**逐字相同**（本次故障正是两种写法给出不同结论）
        rc2, out2 = dry(["--only=" + full, "--dry-run"])
        self.assertEqual(rc2, 0, "等号写法被判失败：\n%s" % out2)
        self.assertEqual(out2, out1, "两种写法给出的结论必须逐字相同（本次故障正是二者不一致）")

        # ③ 短 id：必须被拒，且不许把「选了 0 条」报成绿
        rc3, out3 = dry(["--only=" + short, "--dry-run"])
        self.assertEqual(rc3, 1, "短 id 没被拒 —— 过滤器疑似退化成前缀/模糊匹配：\n%s" % out3)
        self.assertIn("一条变异都没匹配上", out3, "短 id 的报法丢了（现场①的误诊会复发）：\n%s" % out3)
        self.assertNotIn("选中 0 个变异", out3, "选了 0 条却照常打印选中结果 —— 空结论报绿：\n%s" % out3)

        # ④ 末尾无值：必须被拒，且不许静默退回全量（现场②）
        rc4, out4 = dry(["--dry-run", "--only"])
        self.assertEqual(rc4, 1, "裸 `--only` 没被拒 —— 它会静默退回全量（现场②）：\n%s" % out4)
        self.assertIn("（空值）", out4, "裸 `--only` 的报法丢了（回显应是「空值」）：\n%s" % out4)
        self.assertNotIn("选中", out4, "裸 `--only` 竟然跑了起来（这就是现场②）：\n%s" % out4)

        # ⑤ 对照：不带 `--only` → 全量。少了这一向，①②可能被「永远报错」蒙混过关。
        rc5, out5 = dry(["--dry-run"])
        self.assertEqual(rc5, 0, "不带 `--only` 竟然被判失败：\n%s" % out5)
        self.assertIn("选中 %d 个变异" % len(mod.MUTATIONS), out5,
                      "不带 `--only` 时没有选中全量：\n%s" % out5)

        # 末段：用法行示范的写法，每一种都必须真能跑通（写法从 docstring 现扫，不写死）
        spelled = sorted(set(re.findall(r"--only(=| )", mod.__doc__ or "")))
        self.assertTrue(spelled,
                        "用法行里一个 `--only` 写法都没示范 —— 后来人无从照抄：%r"
                        % (mod.__doc__ or "")[:200])
        for sep in spelled:
            argv = ["--only=" + full] if sep == "=" else ["--only", full]
            rc, out = dry(argv + ["--dry-run"])
            self.assertEqual(rc, 0,
                             "用法行教的写法 %r 跑不通 —— 文档与解析器分叉，"
                             "2026-09-24 的故障就是这个（照用法行敲会被静默忽略）：\n%s"
                             % ("--only" + sep, out))


    # 合成变异：`file` 只存在于自建小仓里；`lock` 在"缺席"那一向里根本不存在。
    # ⚠ 写成**类属性**而不是模块级名字，是为了不污染本文件的模块命名空间 ——
    # 它长得像一条真变异，摆在模块级容易被后来人当成 MUTATIONS 名单的一部分。
    ENTRY = {"id": "SYNTH-lock-module-absent", "file": "checks/probe_rule.py", "kind": "exact",
             "old": 'RULE = "before"\n', "new": 'RULE = "after"\n',
             "lock": "test_probe_lock.py", "expect": "test_ok",
             "why": "合成夹具：只用来验冒烟自己的裁决，不针对任何真实规则"}

    def test_missing_lock_module_is_not_read_as_a_weak_lock(self):
        """冒烟的**假失败**锁（2026-09-23 查出并修）：锁模块不在副本里时，报的是「没被验证」。

        为什么属于本看守：上面几条都盯着"判据空转"，这一条盯着**判据读错**。两者同害 ——
        "变异后仍然绿"这个结论有两种完全相反的成因，而当时的提示只有后者：
          · 锁真的被写弱了 → 去改锁；
          · 锁模块**根本不在副本里**（`copy_tree` 只复制 `git ls-files`，未跟踪/未提交的文件
            进不去；`discover -p <不存在的模块>` 是 `Ran 0 tests … OK` + **rc=0**）→ 去提交/改名单。
        实测现场（本仓，2026-09-23）：`tests/test_review_2026_09_23.py` 当时是**未跟踪**文件，
        于是 M49…M55 七条 + 新登记的 M57/M58/M59，八条全报「锁被弱化/没打在判据上」——
        而前七条锁一条都没在副本里跑过。**排查方向被提示词指向了反面**。

        判据两向，同一次构造里跑两遍（`main()` 是真入口，不是复述它的分支）：
          · 锁模块缺席 → 必须报「一条用例都没跑」，且**不许**出现「锁被弱化」字样；
          · 锁模块在场 → 必须**走回**原来的裁决（照样报「锁被弱化」）—— 少了这一向，
            这条新分支可能是个吞掉所有 rc=0 的万能兜底，那会让真·弱锁再也没人发现。
        夹具是**合成的小仓**（自建 git 仓 + 一条能跑通的对照用例），所以这条锁不跑真套件、秒级完成。
        """
        import contextlib
        import importlib.util
        import shutil
        import subprocess
        import tempfile
        import types

        if not shutil.which("git"):
            self.skipTest("无 git：冒烟的地基就是 git，此环境下无从判定")

        path = os.path.join(ROOT, "sandbox", "mutation_smoke.py")
        spec = importlib.util.spec_from_file_location("_mutation_smoke_zerorun", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        tmp = tempfile.mkdtemp(prefix="lockinv_zerorun_")
        src = os.path.join(tmp, "synth")

        def w(rel, text):
            p = os.path.join(src, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with io.open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)

        def commit():
            r = subprocess.run(["git", "-C", src, "-c", "user.email=lock-inventory@local",
                                "-c", "user.name=lock-inventory", "-c", "commit.gpgsign=false",
                                "-c", "core.hooksPath=", "add", "-A", "-f"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, "合成仓 add 失败：%s" % (r.stderr or "")[:300])
            r = subprocess.run(["git", "-C", src, "-c", "user.email=lock-inventory@local",
                                "-c", "user.name=lock-inventory", "-c", "commit.gpgsign=false",
                                "-c", "core.hooksPath=", "commit", "-qm", "synth base"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(r.returncode, 0, "合成仓 commit 失败：%s" % (r.stderr or "")[:300])

        def run_smoke(tag):
            """跑**真的 main()**，副本落在钉死的目录里（免得再去 %TEMP% 里认它）。"""
            copy_dir = os.path.join(tmp, "copy_" + tag)
            fake = types.SimpleNamespace(mkdtemp=lambda prefix="": copy_dir)
            saved = (mod.ROOT, mod.MUTATIONS, mod.tempfile, sys.argv)
            mod.ROOT, mod.MUTATIONS, mod.tempfile = src, [self.ENTRY], fake
            sys.argv = ["mutation_smoke.py"]
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = mod.main()
            finally:
                mod.ROOT, mod.MUTATIONS, mod.tempfile, sys.argv = saved
                _rmtree_force(copy_dir)
            return rc, buf.getvalue()

        try:
            os.makedirs(src, exist_ok=True)
            r = subprocess.run(["git", "init", "-q"], cwd=src, capture_output=True)
            self.assertEqual(r.returncode, 0, "合成仓 git init 失败")
            # 对照组的锁模块（`test_review_v9_batch*.py`）必须在场且能跑 ——
            # 否则 `main()` 会先倒在对照那一关（那正是同一根因的另一半，见下面第 ③ 段）。
            w("tests/test_review_v9_batch1.py",
              "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
            w("checks/probe_rule.py", 'RULE = "before"\n')
            commit()

            # ① 锁模块缺席（既没建、也没提交）→ 必须说「没被验证」，且不许甩锅给锁
            rc, out = run_smoke("absent")
            self.assertEqual(rc, 1, "锁模块缺席时冒烟竟然判成功：\n%s" % out)
            self.assertIn("一条用例都没跑", out,
                          "锁模块缺席的报法丢了（`_ran_nothing` 分支没生效）：\n%s" % out)
            # 判据钉**旧裁决的原话**（"变异后仍然绿"），不钉"锁被弱化"：
            # 后者会出现在新提示自己的对照句里（"不是锁写弱了"），拿它当判据会自咬。
            self.assertNotIn("变异后仍然绿", out,
                             "锁模块缺席被报成了「变异后仍然绿」—— 这正是要修的那个误诊"
                             "（提示会把人送去改锁，而该做的是提交/改名单）：\n%s" % out)

            # ② 同一夹具把锁模块补成 tracked（trivial 用例，抓不住变异）→ 走回原裁决
            w("tests/test_probe_lock.py",
              "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
            commit()
            rc, out = run_smoke("present")
            self.assertEqual(rc, 1, "锁在场时冒烟竟然判成功：\n%s" % out)
            self.assertIn("变异后仍然绿", out,
                          "锁在场、却没咬住变异，竟然没有报「变异后仍然绿」—— 新分支成了万能兜底，"
                          "真·弱锁从此没人发现：\n%s" % out)
            self.assertNotIn("一条用例都没跑", out, "锁在场却报了「没跑过」：\n%s" % out)

            # ③ 对照组也缺席（同一根因的另一半）→ 必须说清"对照"没跑，不能报「✓ 绿」。
            #    改名而不是删目录：`discover -s tests` 在**目录不存在**时是 rc=1（走到另一条
            #    报错分支上去），那就不是在验"跑 0 条"这件事了。
            os.remove(os.path.join(src, "tests", "test_review_v9_batch1.py"))
            w("tests/util_not_a_lock.py", "HELPER = 1\n")
            commit()
            rc, out = run_smoke("noctrl")
            self.assertEqual(rc, 1, "对照组缺席时冒烟竟然判成功：\n%s" % out)
            self.assertIn("对照（未变异副本）一条用例都没跑", out,
                          "对照组空转没被识破 —— 「✓ 对照：绿」会变成一句没有判据的结论：\n%s" % out)
        finally:
            _rmtree_force(tmp)


if __name__ == "__main__":
    unittest.main()
