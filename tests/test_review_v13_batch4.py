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
from _tmpclean import rmtree_force as _rmtree_force      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = os.path.join(ROOT, "checks")
HARNESS_LIB = os.path.join(ROOT, "harness")
PY = sys.executable

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

    临时配置里 gate 名**刻意不用** gxgk_*/hy_* 这些真名：真名会撞上 _config 的策略
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
        `_deep_merge` 到 `config/harness.yaml` **之上**，于是真配置里那 33 个客户 gate
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

    @_slow("端到端跑真 gate（子进程 1.47s）；三态语义在本类另有 3 例亚秒单元锁、"
           "且另有 4 份已接文件覆盖 compute_changed_lines，故它可跳")
    def test_end_to_end_delete_only_does_not_block_on_legacy_violation(self):
        """端到端：只删一行 → 同文件里没碰过的存量 console.log **不拦**。

        这是中 C 的可观测后果（撤掉修复即 rc 从 0 变 1）。
        """
        self.repo.commit("src/a.vue",
                         '<template>\r\n  <div>ok</div>\r\n</template>\r\n<script setup>\r\n'
                         'console.log("存量残留")\r\nlet keep = 1\r\nlet drop = 2\r\n</script>\r\n')
        self.repo.loose("src/a.vue",
                        '<template>\r\n  <div>ok</div>\r\n</template>\r\n<script setup>\r\n'
                        'console.log("存量残留")\r\nlet keep = 1\r\n</script>\r\n')   # 只删了 drop
        cfg = self._cfg_path("    enabled: true\n    script: checks/pc_code_check.py\n"
                             "    mode: strict\n    line_ending: CRLF\n")
        r = self._run_gate([self.repo.file("src/a.vue")], env=self._env(cfg))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0,
                         "纯删除型改动不该为存量 console.log 负责（中 C）；输出=%s" % out[-400:])
        self.assertNotIn("console.log", out)


# ============================================================ 中 D：SFC 结构计数
class TestMidDSfcStructure(_Fixture):
    """中 D：整份 findall 计数会把注释里的 `<script>` 也算进去。

    真实场景：模板里写一句注释说明"本文件不要写 script 标签"，注释里出现 `<script>`
    → 开标签数比闭标签多 1 → 假阻断。左到右走一遍块结构（并跳过 HTML 注释）才是对的。
    """

    def setUp(self):
        super().setUp()
        import pc_code_check
        self.mod = pc_code_check

    def test_commented_script_tag_is_not_counted(self):
        content = ('<template>\r\n  <!-- 注意：本文件不写 <script> 标签 -->\r\n'
                   '  <div>ok</div>\r\n</template>\r\n<style scoped>\r\n.a{color:red}\r\n</style>\r\n')
        self.assertIsNone(self.mod.check_sfc_structure(content),
                          "注释里的 <script> 不该被当成开标签（会导致「未闭合」假阻断）")

    def test_commented_broken_quote_is_not_counted(self):
        content = ('<template>\r\n  <!-- 反例：<script setup lang="ts> 少了引号 -->\r\n'
                   '  <div>ok</div>\r\n</template>\r\n')
        self.assertIsNone(self.mod.check_sfc_structure(content),
                          "注释里的坏引号不该报（它不在代码里）")

    def test_real_unclosed_script_still_reported(self):
        self.assertIn("未闭合", self.mod.check_sfc_structure(
            '<template><div/></template>\n<script setup>\nlet a = 1\n') or "")

    def test_nested_template_closes_correctly(self):
        """v-for 的 <template> 是合法的嵌套：2 开 2 闭必须放行（按深度收口）。"""
        content = ('<template>\r\n  <template v-for="i in list" :key="i">\r\n    <div>{{ i }}</div>\r\n'
                   '  </template>\r\n</template>\r\n<script setup>\r\nlet x = 1\r\n</script>\r\n')
        self.assertIsNone(self.mod.check_sfc_structure(content))

    def test_nested_template_unclosed_still_reported(self):
        content = ('<template>\r\n  <template v-for="i in list" :key="i">\r\n    <div>{{ i }}</div>\r\n'
                   '</template>\r\n<script setup>\r\nlet x = 1\r\n</script>\r\n')
        self.assertIn("不匹配", self.mod.check_sfc_structure(content) or "")

    def test_broken_lang_quote_still_reported(self):
        self.assertIn("引号未闭合", self.mod.check_sfc_structure(
            '<template><div/></template>\n<script setup lang="ts>\nlet a = 1\n</script>\n') or "")

    def test_custom_component_named_like_block_not_counted(self):
        """词边界：`<template-choose-model>` 不是 `<template>` 块（ZhongXin 实测误报）。"""
        content = '<template>\n  <template-choose-model />\n</template>\n<script setup>\nlet a = 1\n</script>\n'
        self.assertIsNone(self.mod.check_sfc_structure(content))

    def test_commented_placeholder_inside_template_block(self):
        """**块内**的注释同样不算数 —— 这是全量实测反查出来的，不是推想。

        gxgk PC `src/views/modules/danger/dangerTpl/tabs/basic.vue` 第 5 行就是
        `<!-- <template> -->`（写正文里的一行说明）。上一版只在"块外"跳注释，
        块内照样把它算成嵌套开标签 → 该文件恒定报"3开 2闭"，全量扫描实测到的唯一一条 SFC 命中。
        """
        content = ('<template>\r\n  <!-- <template> -->\r\n  <div>ok</div>\r\n</template>\r\n'
                   '<script setup>\r\nlet a = 1\r\n</script>\r\n')
        self.assertIsNone(self.mod.check_sfc_structure(content))

    def test_commented_closer_inside_template_block(self):
        """反方向也一样：块内注释里的 `</template>` 不能提前把块关掉。"""
        content = ('<template>\n  <!-- </template> -->\n  <div>ok</div>\n</template>\n'
                   '<script setup>\nlet a = 1\n</script>\n')
        self.assertIsNone(self.mod.check_sfc_structure(content))

    def test_slot_nested_template_closes_correctly(self):
        """`<template #slot>` 是另一种常见嵌套（组件插槽），同样要放行。"""
        content = ('<template>\n  <el-x>\n    <template #a><div/></template>\n  </el-x>\n</template>\n'
                   '<script setup>\nlet a = 1\n</script>\n')
        self.assertIsNone(self.mod.check_sfc_structure(content))


# ============================================================ 中 E：配置「空承诺」
class TestMidEConfigPromise(_Fixture):
    """中 E：配置里声明了、但没有任何消费方读的键。

    这类键没有任何症状：写的人以为已经管住了，门禁照常跑、照常绿。实测三处
    （12 个 PC/H5 gate 的 encoding、11 个 backend gate 的 mode、harness_self 的 endpoint）
    已在本轮清掉；本类锁的是"以后再加回来会被当场拦下"。
    """

    PC = "    script: checks/pc_code_check.py\n"
    BACKEND = "    script: checks/backend_layer_check.py\n"

    def test_declared_but_unread_key_is_blocking(self):
        r = self._validate(self.PC + "    enabled: true\n    encoding: utf-8-bom\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2, "PC 端的 encoding 没有消费方，必须 rc=2；输出=%s" % out[-300:])
        self.assertIn("没有任何消费方读", out)

    def test_misspelled_key_is_blocking(self):
        """拼错键名（encodig）与"键本身没人读"是同一个后果：写了等于没写。"""
        r = self._validate(self.PC + "    enabled: true\n    encodig: utf-8-bom\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2)
        self.assertIn("encodig", out)

    def test_backend_mode_is_blocking(self):
        """后端不读 mode（ctx.mode 按定向/全量自己推）—— 声明 mode 与事实相反。"""
        r = self._validate(self.BACKEND + "    enabled: true\n    mode: loose\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2)
        self.assertIn("没有任何消费方读", out)

    def test_engine_level_keys_are_not_flagged(self):
        """反向锁：引擎/入口真的读的那些键不能被误报（否则体检天天红 = 告警疲劳）。"""
        r = self._validate(self.PC + "    enabled: true\n    run_on: [pre_commit]\n"
                                     "    mode: strict\n    src: src\n    line_ending: CRLF\n"
                                     "    legacy_baseline: true\n    disabled_rules: [PC-TODO]\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "这些都是真有消费方的键，不该被报；输出=%s" % out[-400:])

    def test_backend_encoding_is_not_flagged(self):
        """反向锁：后端 encoding **是**被读的（encoding_ruleset 接线），不能跟着一起报。"""
        r = self._validate(self.BACKEND + "    enabled: true\n    encoding: utf-8-bom\n")
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))

    def test_disabled_rules_unknown_id_is_blocking(self):
        r = self._validate(self.PC + "    enabled: true\n    disabled_rules: [PC-NOSUCH]\n")
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2, "拼错的规则 id 等于开关没拨；输出=%s" % out[-300:])
        self.assertIn("PC-NOSUCH", out)

    def test_disabled_rules_other_end_id_is_blocking(self):
        """H5 的规则 id 写在 PC gate 上 —— 同样什么也关不掉。"""
        r = self._validate(self.PC + "    enabled: true\n    disabled_rules: [H5-UPLOAD]\n")
        self.assertEqual(r.returncode, 2)

    def test_every_declared_rule_id_is_actually_disableable(self):
        """低 19/中 E 的交汇：`disabled_rules` 里的 id **存不存在**与**关不关得掉**是两回事。

        PC-EOL / PC-SFC 是 loader 直接执行的伪规则（不在 *_RULES 列表里），它们的
        `if ... not in off` 原先**根本没写** —— 写了 `disabled_rules: [PC-EOL]` 照样拦。
        所以这里不看源码、直接端到端验：真写进配置，真跑一次，必须不再报。
        """
        # 文件末尾少一个 </script> —— PC-SFC 必报（先确认"不关就报"，再确认"关了不报"）。
        # 行尾给 CRLF，免得 PC-EOL 混进来干扰归因（本测试只谈 PC-SFC 的开关）。
        self.repo.commit("src/a.vue", "<template>\r\n  <div>ok</div>\r\n</template>\r\n"
                                      "<script setup>\r\nlet a = 1\r\n")
        base = "    enabled: true\n    mode: strict\n    line_ending: CRLF\n"
        cfg_on = self._cfg_path(self.PC + base)
        r_on = self._run_gate([self.repo.file("src/a.vue")], env=self._env(cfg_on))
        self.assertEqual(r_on.returncode, 1, "这个文件是坏的 → 不关必须拦")
        cfg_off = self._cfg_path(self.PC + base + "    disabled_rules: [PC-SFC]\n")
        r_off = self._run_gate([self.repo.file("src/a.vue")], env=self._env(cfg_off))
        out = (r_off.stdout or "") + (r_off.stderr or "")
        self.assertEqual(r_off.returncode, 0,
                         "PC-SFC 写进 disabled_rules 就必须真的关掉（原先静默不生效）；输出=%s" % out[-300:])

    def test_real_config_has_no_dead_keys_and_passes(self):
        """真配置必须干净通过 —— 否则这道检查第二天就变成常驻噪音（告警疲劳）。

        外加一条**按端**的锁：本轮删掉的 24 行键不许被"顺手加回来"。不能只数字符串——
        `encoding` 在后端是**合法**的（encoding_ruleset 真读它），数错了会把后端 11 行
        也算成违规，测试就变成误导下一个人的东西。按 script 判端才对。
        """
        r = _sh([PY, os.path.join(HARNESS_LIB, "run_gate.py"), "--validate", "--structure-only"],
                cwd=ROOT, env={"HARNESS_CONFIG": ""})
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "真配置体检必须通过；输出=%s" % out[-600:])

        import _config
        cfg = _config.load(os.path.join(ROOT, "config", "harness.yaml"))
        seen = {"pc": 0, "h5": 0, "backend": 0}
        for name, g in (cfg.get("gates") or {}).items():
            script = os.path.basename(g.get("script") or "")
            if script == "pc_code_check.py":
                seen["pc"] += 1
                self.assertNotIn("encoding", g, "%s：PC 端不读 encoding，别加回来" % name)
            elif script == "h5_code_check.py":
                seen["h5"] += 1
                self.assertNotIn("encoding", g, "%s：H5 端不读 encoding，别加回来" % name)
            elif script == "backend_layer_check.py":
                seen["backend"] += 1
                self.assertNotIn("mode", g, "%s：后端不读 mode（定向实际按 strict 拦）" % name)
            elif script == "harness_self_check.py":
                self.assertNotIn("endpoint", g, "%s：endpoint 全仓无消费方" % name)
        self.assertTrue(all(v > 0 for v in seen.values()),
                        "按 script 认端的逻辑本身失效了（没数到任何一端）：%r" % seen)

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


# ============================================================ 低 19：门禁自身故障不许静默
class TestLow19SfcInternalFailure(_Fixture):
    """低 19：`except Exception: pass` 让整项检查消失 —— 校验没跑成与校验通过不可区分。

    "越坏越安静"是门禁最不能有的性质：它把基础设施故障伪装成"代码没问题"。
    """

    def test_sfc_check_crash_is_reported_as_blocking(self):
        import pc_code_check
        self.repo.commit("src/a.vue", '<template><div/></template>\n<script setup>\nlet a = 1\n</script>\n')
        orig = pc_code_check.check_sfc_structure

        def _boom(_content):
            raise RuntimeError("模拟正则回溯炸掉")
        pc_code_check.check_sfc_structure = _boom
        try:
            problems, _warns = pc_code_check.check_file(self.repo.file("src/a.vue"), skip_node=True)
        finally:
            pc_code_check.check_sfc_structure = orig
        joined = "\n".join(problems)
        self.assertIn("SFC结构校验未能执行", joined,
                      "校验没跑成必须出声（否则等于静默放行）；实际=%r" % problems)
        self.assertIn("门禁自身的故障", joined, "文案要点明是门禁自己的问题，不是本文件的")
        self.assertIn("RuntimeError", joined, "要带上异常类型，排障才有抓手")


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


# ============================================================ 低 F：正则绕过面
class TestLowFFirstFourBypasses(unittest.TestCase):
    """低 F：四处**零判断成本**的绕过（严格超集改写 —— 旧写法能命中的一个不少）。

    这些不是"收紧策略"，而是"这条规则本来就是这个意思，只是写法漏了"。
    """

    def _rules(self):
        import pc_ruleset as P
        import h5_ruleset as H
        return {"PC": P, "H5": H}

    def _pat(self, mod, rid):
        for n in dir(mod):
            o = getattr(mod, n)
            if isinstance(o, type) and getattr(o, "id", "") == rid:
                return o.pattern
        raise AssertionError("找不到规则 %s" % rid)

    def test_console_log_with_space(self):
        import re
        for end, mod in self._rules().items():
            pat = self._pat(mod, end + "-CONSOLE")
            self.assertTrue(re.search(pat, 'console.log ("x")'),
                            "%s-CONSOLE 漏了 log 与括号之间的空格" % end)
            self.assertTrue(re.search(pat, 'console.log("x")'), "%s-CONSOLE 不能丢原写法" % end)

    def test_https_ip(self):
        import re
        for end, mod in self._rules().items():
            pat = self._pat(mod, end + "-IP")
            self.assertTrue(re.search(pat, 'https://192.168.1.1/api'),
                            "%s-IP 漏了 https（内网地址同样该走环境变量）" % end)
            self.assertTrue(re.search(pat, 'http://192.168.1.1/api'), "不能丢原写法")

    def test_todo_any_case(self):
        import re
        for end, mod in self._rules().items():
            pat = self._pat(mod, end + "-TODO")
            for s in ("Todo: 修", "ToDo 修", "TODO 修", "todo 修"):
                self.assertTrue(re.search(pat, s), "%s-TODO 漏了 %r" % (end, s))

    def test_todo_knowledge_base_exception_kept(self):
        """有意保留的例外不能被这轮改写弄丢（约定允许引用知识库的 TODO）。"""
        import re
        for end, mod in self._rules().items():
            self.assertFalse(re.search(self._pat(mod, end + "-TODO"), "TODO: 详见知识库"),
                             "%s-TODO 的「知识库」例外被改写弄丢了" % end)

    def test_axios_default_plus_named_import(self):
        import re
        pat = self._pat(self._rules()["PC"], "PC-AXIOS")
        self.assertTrue(re.search(pat, 'import axios, { AxiosInstance } from "axios"'),
                        "默认导入 + 具名导入是同一个违规，仍绕过了 @/utils/request")
        self.assertTrue(re.search(pat, 'import axios from "axios"'), "不能丢原写法")
        self.assertFalse(re.search(pat, 'import axiosConfig from "@/utils/request"'),
                         "axiosConfig 是合规封装，不能被误伤")


class TestLowFKnownBlindSpots(unittest.TestCase):
    """低 F 的另一半：剩下的边界**不可能**靠收紧正则解决，所以把它钉成可执行事实。

    断言的是"这条现在确实不报"。这不是替漏检找理由 —— 一旦有人改好了正则，
    这些断言会红，逼人回来同步 README「规则能力边界」那张表。
    边界只写在代码注释里，等于没人知道。
    """

    def _pat(self, mod, rid):
        for n in dir(mod):
            o = getattr(mod, n)
            if isinstance(o, type) and getattr(o, "id", "") == rid:
                return o.pattern
        raise AssertionError("找不到规则 %s" % rid)

    def test_console_bracket_form_missed(self):
        import re
        import pc_ruleset as P
        self.assertFalse(re.search(self._pat(P, "PC-CONSOLE"), 'console["log"]("x")'))

    def test_protocol_relative_ip_missed(self):
        import re
        import pc_ruleset as P
        self.assertFalse(re.search(self._pat(P, "PC-IP"), '//192.168.1.1/api'))

    def test_require_axios_missed(self):
        import re
        import pc_ruleset as P
        self.assertFalse(re.search(self._pat(P, "PC-AXIOS"), 'const a = require("axios")'))

    def test_component_path_form_missed(self):
        """**这不是理论绕过**：`@/components/eagle-x.vue` 才是本仓组件引用的主要形态。

        本条锁的是**窄口径**：**单看这一行**，`_extract_components` 认不出路径写法。
        ⚠ 2026-09-18 复量更正了本条原来的**现实结论**：`registerGlobComp.ts` 里这类组件
        **并不会逃逸** —— 注册文件同时写着 `app.component("eagle-tabs-new", ...)`（裸形态），
        旧规则在那一行就抓到了，该文件实跑今天**就是红的**（rc=1）。真正看不见的是
        `views/layout/*.vue` 与 `views/modules/**` 那类**只用路径写法**的文件。
        接线与否是业务判断，README 已记复量结果，故这里锁的仍是"**单行**确实看不见"。
        """
        import pc_ruleset as P
        import h5_ruleset as H
        for mod in (P, H):
            self.assertEqual(mod._extract_components(
                'import EagleTabsNew from "@/components/eagle-tabs-new.vue";'), set(),
                "路径写法被认出来了 —— 说明有人接线了：请同步 README「待裁定」一节与 `_is_new_parallel` 的后果")
            self.assertEqual(mod._extract_components('<eagle-tabs-new />'), {"eagle-tabs-new"},
                             "标签写法必须仍然看得见")

    def test_vfor_window_sibling_key_satisfies(self):
        """v-for 之后 300 字符内**任一** `:key` 都算数 —— 兄弟元素的也算。"""
        import h5_ruleset as H
        from rule_base import CheckContext
        tpl = ('<template>\r\n  <div v-for="i in list">\r\n    <span>{{ i }}</span>\r\n  </div>\r\n'
               '  <div :key="other">兄弟元素的 key</div>\r\n</template>')
        ctx = CheckContext(path=__import__("pathlib").Path("x.vue"), content=tpl, template=tpl,
                           mode="strict")
        self.assertEqual([f.message for f in H.VForKeyRule().run(ctx)], [],
                         "窗口内的兄弟 :key 满足了规则 —— 这是已实测的漏检，写进 README 了")

    def test_readme_documents_every_locked_boundary(self):
        """文档锁：上表每条边界都必须在 README「规则能力边界」里写着。

        防的是"测试改了、文档没改"—— 对使用者来说，没写进文档的边界就是不存在。
        """
        readme = _read(os.path.join(ROOT, "README.md"))
        i = readme.find("### 已实测的具体边界")
        self.assertGreater(i, 0, "README 缺「已实测的具体边界」一节")
        sec = readme[i:i + 6000]
        for token in ('console["log"]("x")', "//192.168.1.1/x", 'require("axios")',
                      "TODO: 见知识库", "300 字符"):
            self.assertIn(token, sec, "边界 %r 没写进 README，等于用户不知道" % token)
        j = readme.find("### 待裁定：组件引用的路径写法")
        self.assertGreater(j, 0, "组件路径写法这条发现必须写进 README（它是本轮最重的一条）")
        self.assertIn("registerGlobComp.ts", readme[j:j + 3000])


if __name__ == "__main__":
    unittest.main()
