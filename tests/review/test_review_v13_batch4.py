# -*- coding: utf-8 -*-
r"""外部评审 v13（2026-09-16）第四批落地回归锁 —— 语义类。

这批修的都是「判定本身算错了」的形态：跑是跑了、也没报，但算出来的结论不对。
覆盖：中 C / 中 D / 中 E / 低 19 / 低 21 / 低 F（含一处只测量、待裁定的发现）。

  中 C   compute_changed_lines 把"没有 diff 信息"和"本次没有新增行"都写成空集
         → 纯删除型改动退回整文件扫描，存量违规被升成 ERROR 拦提交。
  中 D   SFC 结构校验用整份 findall 计数：注释里的 <script> 也算数。
  中 E   配置里声明了、但**没有任何消费方读**的键（encoding/mode/endpoint）无人管 ——
         写了等于没写，且没有任何症状。附 disabled_rules 的 id 拼错同样静默。
  低 19  PC 的 SFC 校验 `except Exception: pass` → 校验没跑成与校验通过不可区分。
  低 21  非 UTF-8 一律贴 `gbk` 标签，而下游文案照着标签劝人"另存为 GBK" → 二次损坏。
  低 F   正则自身的绕过面：写在注释里的边界等于没人知道，本文件把边界变成可执行断言。
"""
import io
import os
import shutil
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

# 各用例按需 import 门禁模块；不依赖"别的测试文件先污染过 sys.path"（单跑本文件时
# TestLow21 曾经全是 ModuleNotFoundError，全量套件里却绿 —— 顺序依赖的假绿/假红）。
for _p in (CHECKS, HARNESS_LIB):
    if _p not in sys.path:
        sys.path.insert(0, _p)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 快跑（HARNESS_FAST=1）：本文件的慢在**端到端跑真 gate**（起子进程）。44 例里只有 2 例
# ≥1s（2.01 / 1.47s，合计 3.5s；全文单跑 12.28s）—— 真摊薄型。批 6 只装饰其中一条：
#   · test_end_to_end_delete_only_...（中C 的**可观测后果**，1.47s）→ 装饰。它的底层语义
#     （compute_changed_lines 三态）本文件另有 3 例亚秒单元锁留跑，且另有 4 份**已接**文件
#     在快跑档覆盖同一函数（test_entry_level / v13_batch1 / v9_batch1 / test_staged_content）
#     —— 不是唯一判据。
#   · test_every_declared_rule_id_is_actually_disableable（2.01s）→ **刻意留跑**：全仓锁
#     「loader 级伪规则 `if ... not in off` 守卫」的**只有它一条**（`git grep "not in off"
#     tests/` 仅命中它自己；test_checks.py 的 TestDisabledRules 验的是 ruleset 规则
#     H5-UPLOAD，走的是另一条代码路径）。低19/中E 是**静默失效**型缺陷（写了
#     `disabled_rules` 照样拦、没有任何症状），唯一牙齿不进快跑档跳过名单。
# 选择依据与墙钟实测：reports/_slow_wiring_batch6.txt。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


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
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def _read(path):
    import pathlib
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


class _Repo:
    """临时 git 仓（与 batch3 同款）。"""

    def __init__(self, tmp):
        self.path = os.path.join(tmp, "repo")
        os.makedirs(self.path)
        _git(self.path, "init", "-q")
        _git(self.path, "config", "user.email", "v13b4@t")
        _git(self.path, "config", "user.name", "v13b4")
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
    """公共底座：临时仓 + 一份只含本测试用得到的临时配置 + 台账隔离。

    临时配置里 gate 名**刻意不用**真实端 gate 名：真名会撞上 _config 的策略
    覆盖保护（本地/环境覆盖层不许改已注册 gate 的策略），夹具就构造不出被测场景了。
    """

    _ENV_KEYS = ("HARNESS_CONFIG", "HARNESS_LOG_FILE", "HARNESS_STATS_FILE",
                 "HARNESS_STAGED_REPO", "HARNESS_STAGED_OVERLAY")

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v13b4_")
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
        for p in (CHECKS, HARNESS_LIB):
            if p not in sys.path:
                sys.path.insert(0, p)

    def _cfg_path(self, body, name="cfg.yaml"):
        """body = gates 段里那一个 gate 的后续行（缩进 4 空格）。"""
        cfg = os.path.join(self.tmp, name)
        _write(cfg, "repos:\n  chain: %s\ngates:\n  probe_gate:\n"
                    "    repo: chain\n%s\n" % (self.repo.path.replace("\\", "/"), body))
        return cfg

    def _env(self, cfg, extra=None):
        e = {"HARNESS_CONFIG": cfg, "HARNESS_LOG_FILE": self.ledger,
             "HARNESS_STATS_FILE": os.path.join(self.tmp, "stats.json")}
        if extra:
            e.update(extra)
        return e

    def _validate(self, body, extra_env=None):
        """带 `--structure-only`（2026-09-18 加，与 ci.yml:77 同源）。

        `HARNESS_CONFIG` 是**覆盖层不是替换**：`_config.load()` 把这份临时配置
        `_deep_merge` 到 `config/harness.toml` **之上**，于是真配置里那 33 个业务 gate
        一个不少地留在场上（CI 那行 `=== 配置体检：35 个 gate ===` = 34 真 + probe_gate）。
        而不带 `--structure-only` 时「端仓路径不存在」是**阻断项**，该模式正是
        `run_gate.py._validate()` 为 CI 加的（干净容器里 clone 不到内网端仓）。

        后果不对称，所以这里必须显式降级：本机端仓都在 → 绿；CI 全不在 → rc=2，
        下面两条断言 rc==0 的反向锁会**只在 CI 上**失败（2026-09-18 实测）。
        本类要验的是「键有没有消费方」，与端仓在不在无关，降级掉的恰好是无关项。
        同文件 `test_real_config_has_no_dead_keys_and_passes` 早就是这么做的。
        """
        cfg = self._cfg_path(body)
        return _sh([PY, os.path.join(HARNESS_LIB, "run_gate.py"), "--validate", "--structure-only"],
                   env=self._env(cfg, extra_env))

    def _run_gate(self, args=(), env=None):
        return _sh([PY, os.path.join(HARNESS_LIB, "run_gate.py"), "probe_gate"] + list(args), env=env)


# ============================================================ 中 C：changed_lines 三态
class TestMidCThreeStateChangedLines(_Fixture):
    """中 C：空集**同时**表示"没有 diff 信息"和"diff 里没有新增行"，两者后果相反。

    纯删除型改动（只删一行）若也退回整文件口径，文件里没碰过的存量违规会被升成 ERROR
    拦提交 —— 改一行不为整文件历史遗留负责这条口径当场失效。
    反过来，"文件压根没动"必须仍是整文件口径：显式 `cli.py check <某文件>` 的存量体检、
    legacy 观察项都靠它。
    """

    def _changed(self, rel):
        import rule_base
        return rule_base.compute_changed_lines(self.repo.path, rel)

    def test_pure_deletion_returns_empty_set_not_none(self):
        self.repo.commit("src/a.vue", "line1\nline2\nline3\n")
        self.repo.loose("src/a.vue", "line1\nline3\n")     # 只删 line2，新增侧 0 行
        got = self._changed("src/a.vue")
        self.assertIsNotNone(got, "本次确实动过这个文件，不能回 None（None = 整文件口径）")
        self.assertEqual(set(got), set(), "纯删除型改动的新增行集合应为空集")

    def test_untouched_file_returns_none(self):
        self.repo.commit("src/a.vue", "line1\nline2\n")
        self.assertIsNone(self._changed("src/a.vue"),
                          "文件压根没动 → 没有「本次新增行」可言，必须回 None（整文件口径）")

    def test_added_lines_returned(self):
        self.repo.commit("src/a.vue", "line1\n")
        self.repo.loose("src/a.vue", "line1\nline2\n")
        self.assertEqual(set(self._changed("src/a.vue")), {2})

# ============================================================ 中 E：配置「空承诺」
class TestMidEConfigPromise(_Fixture):
    """中 E：配置里声明了、但没有任何消费方读的键。

    这类键没有任何症状：写的人以为已经管住了，门禁照常跑、照常绿。开源裁剪后
    只剩示例 gate（`checks/example_code_check.py` 不读任何 gate 级策略键），
    本类锁的是"以后再加键会被当场拦下"。
    """

    EXAMPLE = "    script: checks/example_code_check.py\n"

    def test_declared_but_unread_key_is_blocking(self):
        r = self._validate(self.EXAMPLE + "    enabled: true\n    encoding: utf-8-bom\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2, "示例端不读 encoding，必须 rc=2；输出=%s" % out[-300:])
        self.assertIn("没有任何消费方读", out)

    def test_misspelled_key_is_blocking(self):
        """拼错键名（encodig）与"键本身没人读"是同一个后果：写了等于没写。"""
        r = self._validate(self.EXAMPLE + "    enabled: true\n    encodig: utf-8-bom\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2)
        self.assertIn("encodig", out)

    def test_engine_level_keys_are_not_flagged(self):
        """反向锁：引擎/入口真的读的那些键不能被误报（否则体检天天红 = 告警疲劳）。"""
        r = self._validate(self.EXAMPLE + "    enabled: true\n    run_on: [pre_commit]\n"
                                         "    legacy_baseline: true\n    disabled_rules: [NO-CONSOLE]\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "这些都是真有消费方的键，不该被报；输出=%s" % out[-400:])

    def test_disabled_rules_unknown_id_is_blocking(self):
        r = self._validate(self.EXAMPLE + "    enabled: true\n    disabled_rules: [NO-SUCH]\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2, "拼错的规则 id 等于开关没拨；输出=%s" % out[-300:])
        self.assertIn("NO-SUCH", out)

    def test_disabled_rules_foreign_id_is_blocking(self):
        """别的产品线的规则 id 写在本 gate 上 —— 同样什么也关不掉。"""
        r = self._validate(self.EXAMPLE + "    enabled: true\n    disabled_rules: [H5-UPLOAD]\n")
        self.assertEqual(r.returncode, 2)

    def test_real_config_has_no_dead_keys_and_passes(self):
        """真配置必须干净通过 —— 否则这道检查第二天就变成常驻噪音（告警疲劳）。

        外加一条反向锁：示例端不消费的策略键不许被"顺手加回来"。
        """
        r = _sh([PY, os.path.join(HARNESS_LIB, "run_gate.py"), "--validate", "--structure-only"],
                cwd=ROOT, env={"HARNESS_CONFIG": ""})
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "真配置体检必须通过；输出=%s" % out[-600:])

        import _config
        cfg = _config.load(os.path.join(ROOT, "config", "harness.toml"))
        seen = 0
        for name, g in (cfg.get("gates") or {}).items():
            if os.path.basename(g.get("script") or "") != "example_code_check.py":
                continue
            seen += 1
            for dead in ("encoding", "endpoint", "line_ending", "src"):
                self.assertNotIn(dead, g, "%s：示例端不读 %s，别加回来" % (name, dead))
        self.assertGreater(seen, 0, "按 script 认端的逻辑失效了（没数到示例 gate）")

    def test_badly_behaved_check_script_does_not_hijack_validate(self):
        """体检要读检查脚本拿 CONFIG_KEYS，于是**导入**了它们。

        "检查脚本只在 `if __name__ == '__main__'` 里干活"是约定，不是机制。一个在模块级
        `sys.exit(1)` 的脚本会让 `--validate` 以**它**的退出码结束（SystemExit 不是
        Exception，`except Exception` 兜不住）—— 体检结论和被检脚本的结论混成一个数字。
        这里造一个模块级就打印 + sys.exit 的脚本，锁住防护。
        """
        import run_gate
        mod_dir = os.path.join(self.tmp, "fakemods")
        os.makedirs(mod_dir, exist_ok=True)
        _write(os.path.join(mod_dir, "evil_check.py"),
               "import sys\nprint('模块级噪音')\nsys.exit(7)\nCONFIG_KEYS = frozenset({'x'})\n")
        sys.path.insert(0, mod_dir)
        try:
            got = run_gate._script_config_keys("checks/evil_check.py")
        finally:
            sys.path.remove(mod_dir)
            sys.modules.pop("evil_check", None)
        self.assertIsNone(got, "拿不到声明就不判（None），而不是让体检被脚本的退出码劫持")


# ============================================================ 低 21：编码标签不许乱贴
class TestLow21EncodingLabel(unittest.TestCase):
    """低 21：原实现对一切非 UTF-8 都贴 `gbk`，而下游文案照着标签劝人"另存为 GBK"。

    拿 Big5 当 GBK 重存 = **二次损坏**，比不报更糟。现在：BOM 能定的先定，
    含 NUL 的报"疑似 UTF-16/32"，只有真能用 gbk 严格解出来才叫 gbk。
    """

    def setUp(self):
        import encoding_ruleset
        self.mod = encoding_ruleset

    def test_utf8_bom(self):
        self.assertEqual(self.mod._detect_encoding(b"\xef\xbb\xbfabc"), "utf-8-bom")

    def test_plain_utf8(self):
        self.assertEqual(self.mod._detect_encoding("中文".encode("utf-8")), "utf-8")

    def test_real_gbk_still_named_gbk(self):
        """反向锁：真 GBK 必须仍被认出来 —— 返回值直接参与 `actual == expect` 比较，
        含糊化会把正常提交拦下来。"""
        self.assertEqual(self.mod._detect_encoding("中文".encode("gbk")), "gbk")

    def test_utf16le_bom(self):
        self.assertEqual(self.mod._detect_encoding(b"\xff\xfe" + "abc".encode("utf-16-le")), "utf-16-le")

    def test_utf16be_bom(self):
        self.assertEqual(self.mod._detect_encoding(b"\xfe\xff" + "abc".encode("utf-16-be")), "utf-16-be")

    def test_utf32le_bom_not_mistaken_for_utf16le(self):
        """顺序锁：utf-32-le 的 BOM 以 utf-16-le 的 BOM 开头，先判 16 就会认错。"""
        self.assertEqual(self.mod._detect_encoding(b"\xff\xfe\x00\x00" + "abc".encode("utf-32-le")),
                         "utf-32-le")

    def test_nul_bytes_are_not_called_gbk(self):
        """无 BOM 的 UTF-16LE：GBK 能把它的 ASCII 段解得"看起来正常"，只有 NUL 是稳的信号。"""
        got = self.mod._detect_encoding("abc".encode("utf-16-le"))
        self.assertIn("NUL", got)
        self.assertNotEqual(got, "gbk", "把它叫成 gbk，文案就会劝人'另存为 GBK' → 二次损坏")

    def test_undecodable_big5_is_not_called_gbk(self):
        raw = "繁體字".encode("big5")
        try:
            raw.decode("gbk")
            self.skipTest("这串 Big5 恰好能被 GBK 解开（语言相关），换字面量才能测；跳过不算通过")
        except UnicodeDecodeError:
            pass
        got = self.mod._detect_encoding(raw)
        self.assertNotEqual(got, "gbk")
        self.assertIn("非 UTF-8 且非 GBK", got)
        self.assertIn("需人工确认", got, "给不出确定结论时要明说要人看，而不是猜一个编码名")


if __name__ == "__main__":
    unittest.main()
