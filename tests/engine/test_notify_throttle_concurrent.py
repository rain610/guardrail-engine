"""tests/engine/test_notify_throttle_concurrent.py
并发抢锁回归锁（2026-09-21）：notify_throttled 在多 worktree / 多 agent 并发抢同一 gate 拦截通知时，read-modify-write 阶段必须有文件锁。
八条锁：
· 锁基本语义：同 gate 60 秒内节流，跨 gate 互不干扰
· 降级路径：JSON 损坏 / 锁被占 都返 False（未节流，宁可重发不漏拦截信息）
· 目录缺失：状态文件所在目录不存在时要建出来并写成功（2026-09-21 修的回归）
· 真互斥（本文件的重点）：锁被**同进程另一线程** / **另一进程**持有时，
  状态文件必须**一次都写不进去** —— 这是唯一能在"有锁 / 无锁"两态间取不同值的观察量。

⚠ 判据设计备忘（2026-09-21 重写）：本文件最初那两条"并发"用例是**空洞判据**——
它们断言的是"返回值全是 False"，而 `_notify_throttled` 把抢锁失败降级成返 False，
所以有锁没锁**取值相同**，把 `_file_lock_ctx` 换成 `nullcontext` 也照样绿。
现在一律改为断言"状态文件有没有被写"，并补"放锁后必须写得进去"作两向对照。
"""
import io
import json
import os
import shutil
import subprocess

import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import mkdtemp_cleanup as _mkdtemp    # noqa: E402
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402


# 测试隔离（第四轮 N-2）：run_gate 的 LOG_FILE 是在**导入期**从环境变量绑定的
# （harness/run_gate.py:37 `LOG_FILE = os.environ.get("HARNESS_LOG_FILE") or ...`），
# 所以必须在下面那行 import 之前钉死；测试里的子进程 env=os.environ.copy() 会继承。
#
# 这个文件实测**零污染**（只碰 NOTIFY_STATE，从不跑 gate）—— 也就是说
# tests/test_coverage.py::test_tests_set_harness_log_file 那条源码级锁对它是**假阳性**
# （判据只认子串 "run_gate"，分不出「导入」和「真跑一次」）。这里不去收窄判据——那是往
# 「报平安」的方向走——而是把文件弄成真密闭：代价一行的真副作用是，将来有人在
# 本文件里加了真跑 gate 的用例，隔离已经在了，不会静默写进真台账。
# 2026-09-21：退出时那次 `shutil.rmtree` **删不掉**这个目录（里面有 run_gate 真写出来的
# 台账，`ignore_errors=True` 把 PermissionError 静默吞掉 —— 症状与"清理成功"一模一样）。
# 实测 `harness-testlog-` 存量 1193 份 / 441 MB，只能靠数目录才发现。走强删助手。
_LOG_TMPDIR = _mkdtemp("harness-testlog-")
os.environ["HARNESS_LOG_FILE"] = os.path.join(_LOG_TMPDIR, "gate_log.jsonl")

# 工程根 = tests/engine/ 往上三层。分组重组后这里曾是 `".."`（只到 tests/），
# 标准 CPython 靠 cwd 侥幸 import 成功，嵌入式 Python 的 `-m` 不含 cwd 就 ModuleNotFoundError
# （2026-10-26 第四轮评审 §4.1）。显式插工程根，不依赖解释器的 cwd 语义。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import harness.run_gate as rg

# UTF-8 兜底（本仓硬规则：输出型入口必须自保，否则 Windows GBK 控制台会整体崩）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class _PatchStatePath:
    """临时把模块级 NOTIFY_STATE / LOG_DIR 指向 tempdir；退出时还原。

    LOG_DIR 一起挪，是为了让本文件对**真台账目录**保持零写入面：修好之后
    `_notify_throttled` 只按 `os.path.dirname(NOTIFY_STATE)` 建目录，理论上不再碰 LOG_DIR，
    但将来有人往里塞一条 LOG_DIR 写入路径时，那笔写也落在 tempdir 里而不是真 reports/。
    """
    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="harness-notify-")
        self.state = os.path.join(self.tmp, "notify_last.json")
        self.lock = self.state + ".lock"
        self._orig_state, self._orig_logdir = rg.NOTIFY_STATE, rg.LOG_DIR
        rg.NOTIFY_STATE = self.state
        rg.LOG_DIR = self.tmp
        return self

    def __exit__(self, *exc):
        rg.NOTIFY_STATE, rg.LOG_DIR = self._orig_state, self._orig_logdir


class TestNotifyThrottleBasic(unittest.TestCase):
    """单线程路径：节流语义 + 降级语义。"""
    def setUp(self):
        self.ctx = _PatchStatePath()
        self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        _rmtree_force(self.ctx.tmp)

    def test_first_call_returns_false(self):
        self.assertFalse(rg._notify_throttled("foo"), "首次拦截应未被节流")

    def test_second_call_within_60s_returns_true(self):
        rg._notify_throttled("foo")
        self.assertTrue(rg._notify_throttled("foo"),
                        "60 秒内同 gate 再拦截必须节流（防 AI 循环刷屏）")

    def test_different_gates_are_independent(self):
        rg._notify_throttled("A")
        self.assertFalse(rg._notify_throttled("B"),
                         "不同 gate 应互不干扰：B 不该被 A 的节流波及")
        self.assertTrue(rg._notify_throttled("A"),
                        "A 第二次仍应被自己之前的拦截节流")

    def test_corrupted_state_file_does_not_raise(self):
        """notify_last.json 被外部破坏（如断电、人为编辑）→ 应降级返 False，不能抛。"""
        with io.open(self.ctx.state, "w", encoding="utf-8") as f:
            f.write("{this is not json")
        # 不抛异常即过；返 False 是设计意图（视为未节流，宁可重发不漏）
        self.assertFalse(rg._notify_throttled("foo"))

    def test_missing_state_dir_is_created(self):
        """状态文件所在目录**不存在**时必须被建出来并写成功 —— 这条锁的是真回归（2026-09-21 修）。

        成因：建目录原先写在 `with _file_lock_ctx(...)` **里面**，而哨兵锁文件
        `os.open(<state>.lock)` 就在同一个目录。目录不存在时 ENOENT 先抛在取锁那一步，
        被降级分支吞掉返 False，于是建目录那行**永远走不到**，节流状态一个键也写不进去
        —— 症状是重复发飞书通知，不是报错。

        所以判据只能是「目录本来不存在 → 调用后文件真被写出来」。
        旧版把 NOTIFY_STATE 指到 setUp 已经建好的 mkdtemp 里，这条**恒真**，等于没锁；
        这里显式指到三层深的未建目录，修前必红、修后必绿。
        """
        deep = os.path.join(self.ctx.tmp, "never", "created", "dir", "notify_last.json")
        self.assertFalse(os.path.exists(os.path.dirname(deep)),
                         "前置条件：这个目录必须本来不存在，否则本条锁不到东西")
        saved = rg.NOTIFY_STATE
        rg.NOTIFY_STATE = deep
        try:
            self.assertFalse(rg._notify_throttled("foo"),
                             "首次调用的返回值应为未节流（这里主要看写盘，返回值顺带钉住）")
            self.assertTrue(os.path.exists(deep),
                            "目录不存在时也必须建目录并把 notify_last.json 写出来 —— "
                            "否则节流静默失效（重复发通知）")
            with io.open(deep, encoding="utf-8") as f:
                self.assertIn("foo", json.load(f), "写出来的状态应含本次 gate")
        finally:
            rg.NOTIFY_STATE = saved


class TestNotifyThrottleConcurrent(unittest.TestCase):
    """并发路径：锁基本正确性 + 锁失败降级。"""
    def setUp(self):
        self.ctx = _PatchStatePath()
        self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        _rmtree_force(self.ctx.tmp)

    def test_contending_threads_cannot_write_while_lock_is_held(self):
        """主线程持锁期间，8 个线程一起抢 —— 必须**一次都写不进去**。

        判据为什么长这样（2026-09-21 重写）：旧版用 barrier 齐发 8 线程，断言
        "返回值全是 False" + "状态文件有 [1,N] 个键" —— 这是**空洞判据**。
        `_notify_throttled` 把抢锁失败**降级**成返 False，所以"全 False"在有锁 / 无锁
        两态下取值相同；键数区间同理。把 `_file_lock_ctx` 换成 `nullcontext` 它照绿。
        两态下真正取值不同的观察量只有一个：**状态文件有没有被写**。
        · 有锁 → 8 个线程全被挡在临界区外 → 文件根本不存在
        · 无锁 → 依次进临界区各写一个键 → 文件存在
        再补两向，防止假绿：① 放锁后同一条路径必须写得进去（否则"写盘整个坏了"
        也会让上面那条通过）；② 被挡的线程返 False 而不是抛异常（降级契约本身仍要守）。
        """
        N = 8
        results = [None] * N
        errors = []
        go = threading.Event()

        def worker(i):
            go.wait(3.0)
            try:
                results[i] = rg._notify_throttled("gate_%d" % i)
            except BaseException as e:      # 降级契约：异常不许漏出去
                errors.append(repr(e))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        with rg._file_lock_ctx(self.ctx.lock):      # 全程持锁 → 线程们必然全抢不到
            for t in threads:
                t.start()
            go.set()
            for t in threads:
                t.join(timeout=10.0)
            self.assertEqual(errors, [], "锁失败必须降级返 False，不应抛异常：%s" % errors)
            self.assertEqual(results, [False] * N,
                             "锁失败 → 未节流（宁可重发不漏）：%r" % results)
            self.assertFalse(os.path.exists(self.ctx.state),
                             "锁被持有时一次都不许写状态文件 —— 文件存在即锁没在互斥")

        # 两向：放锁之后同一条路径必须写得进去（防"写盘坏了"被误读成"锁生效了"）
        self.assertFalse(rg._notify_throttled("after_release"), "放锁后首次仍应未节流")
        self.assertTrue(os.path.exists(self.ctx.state),
                        "放锁后必须能写盘 —— 否则上面那条『没写进去』是与锁无关的假绿")

    def test_lock_is_exclusive_across_processes(self):
        """跨进程：子进程持锁不放时父进程同样写不进去；子进程退出后才写得进去。

        旧版（4 个子进程各调一次 `_notify_throttled`、断言"都返 False 且没崩"）与上一条同病：
        降级路径保证返回值恒为 False，无锁时也一样 —— 证伪不了任何东西。
        改成一个**没有竞速窗口**的两段式：
          ① 子进程取到锁 → 打印 READY → 读 stdin 阻塞（＝持锁不放）
          ② 父进程调 `_notify_throttled` → 必须返 False 且**状态文件不存在**
          ③ 关掉子进程 stdin → 它退出释放锁 → 父进程再调一次 → 这次必须写成
        Windows `msvcrt.locking` / POSIX `fcntl.flock` 都是跨进程排他，但这层语义此前
        从没被真断言过（只断言了"不抛异常"，而那是本文件自己的降级分支保证的）。
        """
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        child = (
            "import sys, os\n"
            "sys.path.insert(0, %r)\n"
            "import harness.run_gate as rg\n"
            "rg.NOTIFY_STATE = %r\n"
            "d = os.path.dirname(rg.NOTIFY_STATE)\n"
            "if d: os.makedirs(d, exist_ok=True)\n"
            "with rg._file_lock_ctx(rg.NOTIFY_STATE + '.lock'):\n"
            "    sys.stdout.write('READY\\n')\n"
            "    sys.stdout.flush()\n"
            "    sys.stdin.read()\n"
        ) % (repo_root, self.ctx.state)
        # with 块收管三个管道（否则 -W error::ResourceWarning 下会漏句柄 → CI 红）
        with subprocess.Popen([sys.executable, "-c", child], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=os.environ.copy(),
                              text=True, encoding="utf-8", errors="replace") as p:
            try:
                # ① 等它真拿到锁（拿不到会自己退出，readline 得空串 → 下面断言出声）
                self.assertEqual(p.stdout.readline().strip(), "READY",
                                 "持锁子进程没起来或没拿到锁")
                # ② 此刻锁被**另一个进程**独占 → 父进程必须写不进去
                self.assertFalse(rg._notify_throttled("parent"),
                                 "跨进程锁被持有时应降级返 False（未节流）")
                self.assertFalse(os.path.exists(self.ctx.state),
                                 "别的进程持锁时，本进程一次都不许写状态文件 —— "
                                 "文件存在即跨进程锁没生效")
            finally:
                try:
                    p.stdin.close()      # → 子进程 read() 返回 → with 退出 → 释放锁
                except Exception:
                    pass
                try:
                    p.wait(timeout=15)
                except Exception:
                    p.kill()
            rc, err = p.returncode, p.stderr.read()

        self.assertEqual(rc, 0, "持锁子进程非正常退出 rc=%s stderr=%s" % (rc, err))
        # ③ 锁已释放 → 同一条路径必须写得进去
        self.assertFalse(rg._notify_throttled("after_release"), "放锁后首次仍应未节流")
        self.assertTrue(os.path.exists(self.ctx.state),
                        "子进程退出释放锁后必须能写盘 —— 否则上面那条『没写进去』证明不了锁")

    def test_lock_held_by_another_thread_degrades_to_false(self):
        """锁被另一线程持有时，主线程调用必须降级返 False，不能抛异常；且**不许写盘**。"""
        holder_started = threading.Event()
        release_holder = threading.Event()

        def holder():
            with rg._file_lock_ctx(self.ctx.lock):
                holder_started.set()
                release_holder.wait(timeout=3.0)

        t_holder = threading.Thread(target=holder)
        t_holder.start()
        try:
            self.assertTrue(holder_started.wait(timeout=2.0),
                            "持锁线程没起来，无法验证降级路径")
            # 此刻锁被 holder 线程独占；主线程抢不到 → 必须降级
            try:
                rv = rg._notify_throttled("foo")
            except (BlockingIOError, PermissionError, OSError) as e:
                self.fail("锁失败必须降级返 False，不应抛异常：%r" % e)
            self.assertFalse(rv, "锁失败 → 未节流（宁可重发不漏）")
            # 降级 ≠ 可以绕过锁写盘：抢不到锁就必须什么都不写
            # （写进去了说明临界区根本没被锁住，那降级返 False 就成了双重错误）
            self.assertFalse(os.path.exists(self.ctx.state),
                             "抢不到锁时不许写状态文件（写了 = 锁没拦住 RMW，"
                             "节流状态会被并发覆盖——正是加锁要治的那个 bug）")
        finally:
            release_holder.set()
            t_holder.join(timeout=3.0)


if __name__ == "__main__":
    unittest.main()