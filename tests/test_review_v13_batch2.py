# -*- coding: utf-8 -*-
r"""外部评审 v13（2026-09-16）第二批落地回归锁 —— 接线类修复。

本模块锁的是一条主线：**机制写好了，接线漏了一半**。
每条修复都必须有"真实入口 + 真实退出码"的行为锁，不 mock 规则内部。
覆盖：严重 2 / 高 A / 高 B / 中 9·10·11·12·13。

  严重 2  .js/.ts 这类**纯脚本文件**的 ctx.script 恒空 → PC/H5 整个正则规则族对它们
          一次都没跑过。锁：真仓库跑真脚本，.js 上的违规必须拦、.ts/.tsx 不得被
          语法规则误拦、.css 刻意不进代码段（防"修漏检"变成"造假阳性"）。
  高 A    R5/R6/R9 直接在 raw splitlines 上扫 → 注释掉的旧代码算命中，且不认 /* */。
  高 B    文件级结论（v-for 缺 key / CRLF / 组件）在 legacy 仓里因"改了别的行"拿不到
          降级 → 改一行就被存量问题拦死。锁：存量仓 INFO 放行、新文件照拦、非 legacy 仓照拦。
  中 10   `disabled_rules: [PC-SYNTAX]` 被静默忽略（开关算在语法规则之后）。
  中 11   后端不消费 disabled_rules、无行内豁免、豁免用掉也不打印。
  中 12   harness_guard 里极性相反的分支：端仓库提交消息带 [harness] 即放行。
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


# 快跑（HARNESS_FAST=1，2026-09-17 接线）：本文件的慢在**每例现建 git 仓 + 真起子进程跑
# run_gate / cli.py / 钩子**（实测墙钟 29.6s，18 例里 15 例 ≥1s，合计 28.0s）。
# 装饰判据落到类上时同样用「该类有单例 ≥1s」：TestZhong12GuardNotBypassedByTag（3 例共
# 1.6s，最慢 0.60s）**不接**。
FAST = os.environ.get("HARNESS_FAST") == "1"


def _slow(reason):
    return unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过%s" % reason)


# PC 期望 CRLF：样本一律显式 \r\n，免得行尾规则抢戏（EOL 问题会让 rc=1 变得说不出原因）
def _vue(body_script="let x = 1;", vfor='  <div v-for="i in list" :key="i">{{ i }}</div>'):
    return ('<template>\r\n' + vfor + '\r\n</template>\r\n<script setup>\r\n'
            + body_script + '\r\n</script>\r\n')


class _Repo:
    """临时 git 仓：commit(基线) / stage(本次改动) / 不 add 的裸文件（新建未跟踪）。"""

    def __init__(self, tmp):
        self.path = os.path.join(tmp, "repo")
        os.makedirs(self.path)
        _git(self.path, "init", "-q")
        _git(self.path, "config", "user.email", "v13@t")
        _git(self.path, "config", "user.name", "v13")
        self._n = 0

    def commit(self, rel, text):
        self._n += 1
        _write(os.path.join(self.path, rel), text, newline="")
        _git(self.path, "add", "-A")
        _git(self.path, "commit", "-qm", "base%d" % self._n)

    def stage(self, rel, text):
        _write(os.path.join(self.path, rel), text, newline="")
        _git(self.path, "add", "-A")

    def loose(self, rel, text):
        """只写文件，不进 index（未跟踪 = 新建文件口径）。"""
        _write(os.path.join(self.path, rel), text, newline="")

    def file(self, rel):
        return os.path.join(self.path, rel)


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v13b2_")
        self.addCleanup(_rmtree_force, self.tmp)
        self.repo = _Repo(self.tmp)
        if CHECKS not in sys.path:
            sys.path.insert(0, CHECKS)

    def _cfg(self, name, script, extra="", mode="strict"):
        """写一份只有本测试用得到的临时 harness 配置，返回环境变量。"""
        cfg = os.path.join(self.tmp, "cfg.yaml")
        _write(cfg,
                "repos:\n  chain: %s\ngates:\n  %s:\n    enabled: true\n"
                "    repo: chain\n    script: %s\n    mode: %s\n%s"
                % (self.repo.path.replace("\\", "/"), name, script, mode, extra))
        return {"HARNESS_CONFIG": cfg,
                "HARNESS_LOG_FILE": os.path.join(self.tmp, "gate_log.jsonl"),
                "HARNESS_STATS_FILE": os.path.join(self.tmp, "stats.json")}

    def _env_pc(self, extra=""):
        # 显式钉住行尾（2026-09-18 跨平台批次 3 的连带）：`line_ending` 现在默认是 `auto`
        # —— 按**本机本仓的检出事实**判定。而本文件的夹具是"手工写字节"的临时仓，
        # `core.autocrlf` 随本机全局配置走：在 autocrlf=true 的机器上，git 恒称检出为 CRLF
        # （不管盘上字节），于是 H5 那侧的 LF 样本被判成 H5-CRLF，rc 从 0 变 1 ——
        # 首轮全量实测正是如此，报的却是"disabled_rules 没生效"（与它毫无关系）。
        # 本文件各例的被测对象是**接线**（disabled_rules / 脚本文件 / 存量降级），
        # 行尾不该抢戏 → 钉死成与样本一致的显式值，让这些用例与平台彻底解耦。
        # 后端不在此列：`line_ending` 对后端不适用（见 checks/backend_layer_check.py:51）。
        return self._cfg("v13pc", "checks/pc_code_check.py",
                         "    line_ending: CRLF\n" + extra)

    def _env_h5(self, extra=""):
        return self._cfg("v13h5", "checks/h5_code_check.py",
                         "    line_ending: LF\n" + extra)

    def _env_be(self, extra=""):
        # 后端 gate 不配 encoding 时默认 utf-8-bom —— 会被 ENC 规则拦下（与本次修复无关），
        # 因此显式声明 utf-8，让 rc 只反映被测规则的接线。
        return self._cfg("v13be", "checks/backend_layer_check.py",
                         "    encoding: utf-8\n" + extra, mode="loose")

    def _pc(self, env, path, name="v13pc"):
        return _sh([PY, os.path.join(CHECKS, "pc_code_check.py"),
                    "--gate", name, "--repo", self.repo.path, path], env=env)

    def _h5(self, env, path, name="v13h5"):
        return _sh([PY, os.path.join(CHECKS, "h5_code_check.py"),
                    "--gate", name, "--repo", self.repo.path, path], env=env)

    def _be(self, env, path, name="v13be"):
        return _sh([PY, os.path.join(CHECKS, "backend_layer_check.py"),
                    "--gate", name, "--repo", self.repo.path, path], env=env)


# ============================================================ 严重 2：纯脚本文件
@_slow("每例现建 git 仓 + 真跑 run_gate 子进程（4 例 7.7s，最慢 2.23s）")
class TestSevere2ScriptFilesAreChecked(_Fixture):
    """严重 2 的行为锁：.js/.ts/.tsx 也必须真的过一遍正则规则族。

    修复前 `ctx.script` 只对 SFC 有值 → 这些文件的 script 恒空 → applicable 恒 False
    → PC-CONSOLE/IP/TODO「整族」静默不跑（不是漏一条，是漏一类文件）。
    """

    def test_js_file_violations_block(self):
        """新写的 .js 里三种违规都要被拦下（旧实现 rc=0 全放）。"""
        self.repo.commit("src/a.js", "let a = 1;\r\nlet b = 2;\r\n")
        self.repo.stage("src/a.js",
                        'let a = 1;\r\nconsole.log("leak");\r\n'
                        '// TODO 清理\r\nlet ip = "http://10.0.0.1/api";\r\n')
        r = self._pc(self._env_pc(), self.repo.file("src/a.js"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1, "纯 .js 文件的违规必须拦下（严重 2 正是它静默通过）\n" + out[-800:])
        self.assertIn("残留 console.log", out)
        self.assertIn("TODO 残留", out)
        self.assertIn("硬编码 IP", out)

    def test_ts_file_gets_regex_rules_without_false_syntax_error(self):
        """TS 也要过正则规则 —— 但**不得**被 node 语法检查误拦。

        修严重 2 时 .ts 首次进代码段；node --check 解析不了 TS，不做扩展名收窄就会
        把整类 .ts 报成「语法错误」= 用假阳性换掉漏检。此锁同时钉住两件事：
        ① 正则规则真的跑到了 .ts（PC-TODO 命中）；② 没有语法错误结论。
        """
        self.repo.commit("src/t.ts", "let a = 1;\r\n")
        self.repo.stage("src/t.ts",
                        "interface Foo {\r\n  a: number;\r\n}\r\n"
                        "const x: number = 1;\r\n// TODO 收口类型\r\n")
        r = self._pc(self._env_pc(), self.repo.file("src/t.ts"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertIn("TODO 残留", out, ".ts 上的正则规则没跑到（严重 2 未真正接通）\n" + out[-800:])
        self.assertNotIn("语法错误", out, "TS 被 node 语法检查误判 —— 假阳性硬拦（比漏检更糟）\n" + out[-800:])
        self.assertEqual(r.returncode, 1, out[-800:])

    def test_tsx_file_not_flagged_as_syntax_error(self):
        """JSX 同理：.tsx 必须免于 node 语法检查。"""
        self.repo.commit("src/c.tsx", "let a = 1;\r\n")
        self.repo.stage("src/c.tsx", "let a = 1;\r\nconst A = () => <div>hi</div>;\r\n")
        r = self._pc(self._env_pc(), self.repo.file("src/c.tsx"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertNotIn("语法错误", out, "JSX 被当成语法错误\n" + out[-800:])
        self.assertEqual(r.returncode, 0, out[-800:])

    def test_css_is_deliberately_not_a_code_region(self):
        """样式文件**刻意**不进代码段：`.todo-list` 不是 TODO、注释里的示例 IP 不是硬编码。

        这是修复严重 2 时划的边界（评审只说 .js/.ts 被绕过，样式文件不在其列）。
        没有这条锁，后来者"顺手"把 .css 塞进 code_region 就能制造一整类假阳性。
        """
        self.repo.commit("src/s.css", ".a {\r\n  color: red;\r\n}\r\n")
        self.repo.stage("src/s.css", ".todo-list {\r\n  color: red;\r\n}\r\n")
        r = self._pc(self._env_pc(), self.repo.file("src/s.css"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertNotIn("TODO 残留", out, "`.todo-list` 被当成 TODO —— 样式文件不该进代码段\n" + out[-800:])
        self.assertEqual(r.returncode, 0, out[-800:])


# ============================================================ 高 A：注释不算命中
@_slow("每例现建 git 仓 + 真跑 run_gate 子进程（3 例 4.5s，最慢 1.97s）")
class TestGaoACommentsAreNotHits(_Fixture):
    """高 A：R5/R6/R9 与 R10/R11 口径统一到 _code_lines（剥 // 与 /* */）。"""

    CS = ("using System;\nnamespace Eagle.RMC.Controller\n{\n"
          "    public class TestController\n    {\n"
          "        public void M()\n        {\n"
          "            %s\n        }\n    }\n}\n")

    def test_line_comment_is_not_a_hit(self):
        self.repo.commit("RMC/C.cs", self.CS % "int a = 1;")
        self.repo.stage("RMC/C.cs", self.CS % "// var a = GetData(1);")
        r = self._be(self._env_be(), self.repo.file("RMC/C.cs"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "行注释里的 GetData( 被当成真命中\n" + out[-800:])
        # 只看"违规行"（`❌ R5 L…`），不看表头 —— 表头本来就写着 R5/R6
        self.assertNotIn("❌ R5", out)

    def test_block_comment_is_not_a_hit(self):
        """`/* */` 是修复前连识别都不识别的形态（注释掉的旧分支）。"""
        self.repo.commit("RMC/C.cs", self.CS % "int a = 1;")
        self.repo.stage("RMC/C.cs", self.CS % "/* if (Status == 0) { x(); } */")
        r = self._be(self._env_be(), self.repo.file("RMC/C.cs"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "块注释里的 Status == 0 被当成魔法数字\n" + out[-800:])
        self.assertNotIn("❌ R9", out)

    def test_real_hits_still_fire(self):
        """反向对照：剥注释不能把真命中一起剥掉（没有这条，上面两条可能只是"规则死了"）。"""
        self.repo.commit("RMC/C.cs", self.CS % "int a = 1;")
        self.repo.stage("RMC/C.cs", self.CS % "var a = GetData(1);\n            if (Status == 0) { x(); }")
        r = self._be(self._env_be(), self.repo.file("RMC/C.cs"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1, "真命中必须照旧拦下\n" + out[-800:])
        self.assertIn("❌ R5", out)
        self.assertIn("❌ R9", out)


# ============================================================ 高 B：文件级结论的存量降级
@_slow("每例现建 git 仓 + 真跑 run_gate 子进程（3 例 5.5s，最慢 2.25s）")
class TestGaoBWholeFileLegacyDowngrade(_Fixture):
    """高 B：legacy 仓里，改一行不得因**存量**的文件级结论把提交拦死。

    PC-VFOR 判的是"这个文件里有 v-for 缺 :key"——没有行号可归因。基类默认降级要求
    `changed_lines 为空`，而"改一行"的提交恰好不满足 → 存量问题照出 ❌。
    """

    LEGACY = "    legacy_baseline: true\n"
    VFOR = '  <div v-for="i in list">{{ i }}</div>'

    def test_legacy_gate_keeps_preexisting_vfor_as_observation(self):
        self.repo.commit("src/a.vue", _vue("let x = 1;", self.VFOR))
        self.repo.stage("src/a.vue", _vue("let x = 2;", self.VFOR))   # 只改了第 5 行
        r = self._pc(self._env_pc(self.LEGACY), self.repo.file("src/a.vue"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0,
                         "存量 v-for 缺 :key 把「改一行」的提交拦死了（legacy 承诺的反面）\n" + out[-800:])
        self.assertIn("v-for 缺 :key", out, "降级 ≠ 消失：仍须以观察项报出来\n" + out[-800:])

    def test_new_file_still_blocks_in_legacy_gate(self):
        """新建文件整份都是新代码，与存量无关 —— legacy 不豁免它。"""
        self.repo.commit("src/a.vue", _vue("let x = 1;"))
        self.repo.loose("src/new.vue", _vue("let y = 1;", self.VFOR))
        r = self._pc(self._env_pc(self.LEGACY), self.repo.file("src/new.vue"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1,
                         "legacy 仓里的**新建**文件带 v-for 缺 :key，必须照拦\n" + out[-800:])

    def test_non_legacy_gate_still_blocks_same_edit(self):
        """反向对照：非 legacy 仓同样的改动照拦（证明上面 rc=0 来自降级，不是规则失效）。"""
        self.repo.commit("src/a.vue", _vue("let x = 1;", self.VFOR))
        self.repo.stage("src/a.vue", _vue("let x = 2;", self.VFOR))
        r = self._pc(self._env_pc(""), self.repo.file("src/a.vue"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1, "非 legacy 仓不该降级\n" + out[-800:])


# ============================================================ 中 10：disabled_rules 吃语法规则
@_slow("每例现建 git 仓 + 真跑 run_gate 子进程（2 例 4.8s，最慢 2.48s）")
class TestZhong10DisabledRulesReachSyntax(_Fixture):
    """中 10：`disabled_rules` 原先算在语法规则**之后** → 写 `[PC-SYNTAX]` 被静默忽略。"""

    BAD = "let x = ;"          # 无 import/export → 走 new Function 分支，必报语法错误

    def test_pc_syntax_flag_is_honored(self):
        self.repo.commit("src/a.vue", _vue("let x = 1;"))
        self.repo.stage("src/a.vue", _vue(self.BAD))
        on = self._pc(self._env_pc(""), self.repo.file("src/a.vue"))
        self.assertEqual(on.returncode, 1, "坏语法本该被拦（前提不成立，后面比不出东西）\n"
                         + ((on.stdout or "") + (on.stderr or ""))[-800:])

        off = self._pc(self._env_pc("    disabled_rules: [PC-SYNTAX]\n"),
                       self.repo.file("src/a.vue"))
        out = (off.stdout or "") + (off.stderr or "")
        self.assertEqual(off.returncode, 0,
                         "配置里写了 disabled_rules: [PC-SYNTAX] 却没生效（开关算晚了）\n" + out[-800:])

    def test_h5_syntax_flag_is_honored(self):
        # H5 仓要求 LF，样本用 \n（CRLF 会引入与本次修复无关的 H5-CRLF 拦截）
        self.repo.commit("src/a.vue", _vue("let x = 1;").replace("\r\n", "\n"))
        self.repo.stage("src/a.vue", _vue(self.BAD).replace("\r\n", "\n"))
        on = self._h5(self._env_h5(""), self.repo.file("src/a.vue"))
        self.assertEqual(on.returncode, 1, ((on.stdout or "") + (on.stderr or ""))[-800:])

        off = self._h5(self._env_h5("    disabled_rules: [H5-SYNTAX]\n"),
                       self.repo.file("src/a.vue"))
        out = (off.stdout or "") + (off.stderr or "")
        self.assertEqual(off.returncode, 0,
                         "H5 侧 disabled_rules: [H5-SYNTAX] 没生效\n" + out[-800:])


# ============================================================ 中 11：后端三件事接线
@_slow("每例现建 git 仓 + 真跑 run_gate 子进程（3 例 5.5s，最慢 2.05s）")
class TestZhong11BackendWiring(_Fixture):
    """中 11：后端原先既不消费 disabled_rules、也没有行内豁免、用了豁免也不打印。

    三端共用同一套配置 schema 与同一份豁免文档 —— 后端少任何一件，
    "配置说了算"和"豁免是唯一合法出口"在后端就是空话。
    """

    CS = ("using System;\nnamespace Eagle.RMC.Controller\n{\n"
          "    public class TestController\n    {\n"
          "        public void M()\n        {\n"
          "            %s\n        }\n    }\n}\n")

    def test_disabled_rule_is_honored(self):
        self.repo.commit("RMC/C.cs", self.CS % "int a = 1;")
        self.repo.stage("RMC/C.cs", self.CS % "var a = GetData(1);")
        on = self._be(self._env_be(""), self.repo.file("RMC/C.cs"))
        self.assertEqual(on.returncode, 1, "R5 本该拦（前提不成立）\n"
                         + ((on.stdout or "") + (on.stderr or ""))[-800:])

        off = self._be(self._env_be("    disabled_rules: [R5]\n"), self.repo.file("RMC/C.cs"))
        out = (off.stdout or "") + (off.stderr or "")
        self.assertEqual(off.returncode, 0, "后端不消费 disabled_rules（写了等于没写）\n" + out[-800:])

    def test_inline_waiver_works_and_is_printed(self):
        """`harness-ok: R5 <理由>` 在后端也要生效，且**必须打印**用掉了哪条豁免。"""
        self.repo.commit("RMC/C.cs", self.CS % "int a = 1;")
        self.repo.stage("RMC/C.cs", self.CS % (
            "// harness-ok: R5 gxgk 存量继承基类命名，本次不改动契约\n            var a = GetData(1);"))
        r = self._be(self._env_be(""), self.repo.file("RMC/C.cs"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 0, "后端行内豁免没生效\n" + out[-800:])
        self.assertIn("harness-ok", out, "用掉的豁免必须在输出里可见（唯一合法绕过出口）\n" + out[-800:])

    def test_inline_waiver_without_reason_does_not_waive(self):
        """只写规则 ID、不写理由 → **不豁免**（2026-09-23 评审报告 M2 顺带补的口径例）。

        三端共用同一句判据 `harness-ok:<ID>\\s+(\\S.*)`：尾部必须有非空理由。
        PC 侧早有对照例（test_entry_level.py 的 reasonless 一例），后端侧原先只有
        "写了理由能豁免"的正例 —— 正例过不了"没理由也放行"这种退化（把正则尾部的
        `\\s+(\\S.*)` 删掉，正例照样绿）。这一例就是给那半边钉钉子：
        一句话静默规则被设计上禁止，理由必须留在 diff 里给人工复核看。
        """
        self.repo.commit("RMC/C.cs", self.CS % "int a = 1;")
        self.repo.stage("RMC/C.cs", self.CS % (
            "// harness-ok: R5\n            var a = GetData(1);"))
        r = self._be(self._env_be(""), self.repo.file("RMC/C.cs"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1,
                         "没写理由的 harness-ok 也放行了（豁免退化成一句话静默规则）\n" + out[-800:])

    def test_inline_waiver_cannot_silence_file_level_rule(self):
        """文件级规则（R7：前端文件混入后端仓）**不可**被一行注释豁免。

        那两条判的是"这个文件该不该是这样"，不存在"这行写法有争议"——
        允许注释豁免＝给硬约定开后门。
        """
        self.repo.commit("RMC/keep.cs", self.CS % "int a = 1;")
        self.repo.loose("RMC/probe.vue",
                        "<!-- harness-ok: R7 探针，看完就删 -->\r\n<template>\r\n"
                        "  <div>probe</div>\r\n</template>\r\n")
        r = self._be(self._env_be(""), self.repo.file("RMC/probe.vue"))
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 1,
                         "前端文件混入后端仓被一行注释豁免了（硬约定开天窗）\n" + out[-800:])


# ============================================================ 中 12：guard 的极性分支
class TestZhong12GuardNotBypassedByTag(_Fixture):
    """中 12：`harness_guard` 里存在一份极性相反的分支 ——
    "非 self 模式 + 提交消息含 [harness] → 放行"，与紧随其后的注释（不放行）正好相反。

    这里把工程仓的"未提交受保护文件"用桩固定住（真读的话结果取决于跑测试时的工作区状态，
    那不是锁，是碰运气），只把**判据**（模式 + 标记）暴露出来测。
    """

    PROTECTED = "checks/pc_code_check.py"

    def _run_guard(self, argv, dirty=True):
        if HARNESS_LIB not in sys.path:
            sys.path.insert(0, HARNESS_LIB)
        import _config
        import harness_guard
        orig = _config.changed_files_ex
        # 桩点随哨兵换用 `changed_files_ex` 一起搬（2026-09-16）：哨兵改用带失败信号的版本后，
        # 还桩着旧名字等于**这条锁悄悄失去作用** —— 真去读工程仓，结果就取决于跑测试时工作区
        # 脏不脏，那不是锁，是碰运气（本类 docstring 里已经写明这个顾虑）。
        _config.changed_files_ex = (
            (lambda repo, *a, **k: ([os.path.join(repo, self.PROTECTED.replace("/", os.sep))], ""))
            if dirty else (lambda repo, *a, **k: ([], "")))
        old_argv, old_cwd = sys.argv, os.getcwd()
        try:
            sys.argv = ["harness_guard.py"] + list(argv)
            os.chdir(self.tmp)      # 非工程根 → 不触发"本仓提交"的 self 模式识别
            return harness_guard.main()
        finally:
            sys.argv = old_argv
            os.chdir(old_cwd)
            _config.changed_files_ex = orig

    def test_tag_in_message_does_not_pass_in_end_repo(self):
        """端仓库提交消息里带 [harness] ≠ harness 维护 → 门禁文件脏时仍须拦截。"""
        rc = self._run_guard(["gxgk_pc", "[harness] 顺手带个标记"])
        self.assertEqual(rc, 1,
                         "端仓库提交只要消息里带 [harness] 就放行 —— 现成的绕过口子")

    def test_self_mode_with_tag_still_passes(self):
        """反向对照：工程仓自身的 [harness] 维护提交必须照旧放行（别把合法出口一起堵了）。"""
        rc = self._run_guard(["--self", "[harness] 维护"])
        self.assertEqual(rc, 0, "self 模式的维护标记被误伤 —— 会自指死结")

    def test_clean_engine_passes_without_tag(self):
        rc = self._run_guard(["gxgk_pc"], dirty=False)
        self.assertEqual(rc, 0, "门禁文件干净时不该拦任何提交")


if __name__ == "__main__":
    unittest.main(verbosity=2)
