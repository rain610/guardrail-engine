# -*- coding: utf-8 -*-
"""出货件契约锁（评审 v13 高 6 / 低 25）

这条锁要钉住的东西，和别的锁不一样
================================================================================
既有的打包锁（`test_packaging_contract.py` / `test_agentguard.py::_wheel_form()`）
验的是**源码**：把当前工作树的 .py 拷进临时目录、再扮成一个轮子。它证明"源码是对的"，
证不了"出货的那个轮子是对的" —— 而 `dist/install-package/` 下三个版本的 wheel 里
**都没有 `_shell.py`**、元数据还写着 `Requires-Python: >=3.8`，这套自我印证全绿。

所以这里反过来做：**给校验器本身设锁**。校验器的价值全在"它会不会红"，
而"会不会红"只有拿**故意做坏的产物**喂它才验得出来。做法是构造假轮子，逐个把
已知的漂移形状塞进去，断言校验器报出来；同时断言**没坏的产物它必须放行**
（假警报比漏报更磨损可信度：下一次真问题就没人看了）。

锁的层次：
  ① 校验器有牙 —— 缺模块 / Requires-Python 不符 / 入口点不符，逐条必须报；
  ② 校验器不乱咬 —— 正确的产物必须零问题（防"恒红"式假锁）；
  ③ 清单对账有牙 —— SHA1SUMS 的漏列 / 多列 / 哈希不符，三种都要报；
  ④ 校验器**真的被接线** —— `pack_release.py` 与 `release.yml` 确实调用它；
     没接线的校验器等于没有（这正是高 6 的形状：东西写了，但没人跑）；
  ⑤ 真产物锁 —— 出货目录**存在**时，跑真校验必须 rc=0。
     不存在时 skip（CI 容器里没有 dist/）—— skip 而非假绿。

依赖：纯标准库（zipfile / importlib），不起引擎、不建工程副本，毫秒级。
"""
from __future__ import unicode_literals

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402
import zipfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VERIFIER = os.path.join(ROOT, "sandbox", "verify_release.py")
PACKER = os.path.join(ROOT, "sandbox", "pack_release.py")
RELEASE_YML = os.path.join(ROOT, ".github", "workflows", "release.yml")
CI_YML = os.path.join(ROOT, ".github", "workflows", "ci.yml")
FAST = os.environ.get("HARNESS_FAST") == "1"


def _load_verifier():
    """把 `sandbox/verify_release.py` 当模块加载（它不在包路径里，只能这样取）。

    直接 import 它的**函数**而不是抄一遍逻辑：抄一遍就变成了"给副本设锁"，
    校验器本体将来被改坏时，本文件照样绿。
    """
    spec = importlib.util.spec_from_file_location("_verify_release_under_test", VERIFIER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vr = _load_verifier()


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


def _write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _step(src, name):
    """取 YAML 里 `- name: <name>` 那个步骤的 (行号, 正文行列表)。

    为什么不直接 `src.find("构建 wheel")`：文件头那段注释里也写着「构建 wheel + 离线安装包 →
    创建 GitHub Release」，`find` 会命中注释 —— 首版就是这么写的，于是"校验排在构建之前"
    这条断言比的是注释位置（1296 vs 65），**测的根本不是步骤顺序**。
    按 `- name:` 锚定才落在真步骤上。
    """
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "- name: " + name:
            start = i
            break
    if start is None:
        return None, None
    body = []
    for ln in lines[start + 1:]:
        s = ln.lstrip()
        if s.startswith("- name: ") or s.startswith("- uses: "):
            break
        body.append(ln)
    return start, body


def _code_lines(body):
    """去掉注释行：注释里会**引用**被禁的旧写法（说明为什么改），那不是代码。"""
    return [ln for ln in (body or []) if ln.strip() and not ln.strip().startswith("#")]


#: 轮子自带的引擎探针（与 sandbox/verify_release.py 的 engine_required 同一组）
ENGINE_PROBE = ("guardrail_engine/_engine/harness/run_gate.py",
                "guardrail_engine/_engine/checks/example_code_check.py",
                "guardrail_engine/_engine/config/harness.toml")


def _make_wheel(path, modules, requires_python, name, version,
                entry_points=("[console_scripts]\n"
                              "guardrail-engine = guardrail_engine.cli:main\n"
                              "agentguard = agentguard.cli:main\n"),
                metadata=True, bundled_engine=True, extra=None):
    """现场造一个"形状对、内容可控"的 wheel —— 用来当喂给校验器的病变样本。"""
    dist = "guardrail_engine-%s.dist-info" % version
    with zipfile.ZipFile(path, "w") as z:
        for m in modules:
            z.writestr("guardrail_engine/" + m, "# %s\n" % m)
        # 迁移期兼容壳（老命令 / 老 MCP 注册在 v0.3 前必须继续可用）
        for m in ("__init__.py", "cli.py", "mcp.py", "_shell.py"):
            z.writestr("agentguard/" + m, "# shim\n")
        if bundled_engine:
            for n in ENGINE_PROBE:
                z.writestr(n, "# engine\n")
        for n, body in (extra or {}).items():
            z.writestr(n, body)
        if metadata:
            z.writestr(dist + "/METADATA",
                       "Metadata-Version: 2.1\nName: %s\nVersion: %s\nRequires-Python: %s\n"
                       % (name, version, requires_python))
        if entry_points is not None:
            z.writestr(dist + "/entry_points.txt", entry_points)


def _restamp_wheel(path, stamp=(2020, 1, 1, 0, 0, 0), edit=None):
    """把 `path` 那个轮子**原地重写**：成员内容照搬，条目时间戳统一换成 `stamp`。

    为什么要这么个夹具（2026-09-17，真扩指纹的现场）：wheel 是 zip，条目时间戳由构建时刻
    决定 —— 同一份源码连打两次，**文件字节不同、内容一模一样**（实测 f7b78f70… / 6ddd8029…，
    内容指纹同为 91763035fc…）。这就是"整包指纹若按字节算就会逢包必响"的成因，
    锁得能造出这个形状才验得出来。
    `edit(name, data)` 给了就顺带改某个成员的内容 —— 用来造"内容真变了"的反向样本。
    """
    with zipfile.ZipFile(path) as z:
        items = [(i.filename, z.read(i.filename))
                 for i in z.infolist() if not i.filename.endswith("/")]
    tmp = path + ".restamped"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in items:
            if edit:
                name, data = edit(name, data)
            zi = zipfile.ZipInfo(name, date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    os.replace(tmp, path)


def _restage_identically(src, dst):
    """把 `src` 这份包原样铺进 `dst`（`dst` 里已有 `_old_pack` 铺好的市场与 upgrade.py）。

    刻意**walk 整个产物**，而不是手写文件名单。这条锁要造的是"内容与本次**逐字节相同**的
    上一次那份包"——名单一旦是手写的，就必然在**加一支随包工具**的那天漏项：漏掉的那支会被
    打包器如实报成「+ xxx（本次新增）」，于是这条"内容相同就不许响"的锁，会在一次完全正常的
    重打包上**假红**。2026-09-18 加 `bootstrap.py` 时正是如此，锁当场红了 —— 报的是打包器有问题，
    实际是这份名单过期，两者在输出里长得一模一样。
    （同类rot本仓库已有多处：`verify_release.SHIPPED_TOOLS` / `pack_release` 的指纹口径注释，
    那两处的处置也是"名单以常量/产物为准"。）

    唯一必须排除的是两个自指文件：`PACK-INFO.json`（`built_at` 每次都不同，且本就不进指纹）
    与 `SHA1SUMS.txt`（派生清单）。轮子要**重新打时间戳** —— 上一份包的轮子是上次构建的产物，
    与本次那个必然字节不同，这正是 `test_identical_repack_stays_quiet` 要造的形状。
    """
    for dp, dns, fns in os.walk(src):
        dns.sort()
        for fn in sorted(fns):
            if fn in ("PACK-INFO.json", "SHA1SUMS.txt"):
                continue
            fp = os.path.join(dp, fn)
            out = os.path.join(dst, os.path.relpath(fp, src))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copyfile(fp, out)
            if fn.endswith(".whl"):
                _restamp_wheel(out)             # 上一次构建留下的时间戳（见上方 docstring）


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rel-lock-")
        self.addCleanup(_rmtree_force, self.tmp)
        self.proj, self.version, self.modules = vr._facts()
        self.assertTrue(self.modules, "guardrail_engine/ 下没枚举到 .py —— 本锁会因此空转")

    def wheel(self, fname="x.whl", **kw):
        """默认参数 = 一个**合格**产物；只覆盖要制造的那一处病变。"""
        kw.setdefault("modules", self.modules)
        kw.setdefault("requires_python", str(self.proj.get("requires-python")))
        kw.setdefault("name", self.proj.get("name"))
        kw.setdefault("version", self.version)
        p = os.path.join(self.tmp, fname)
        _make_wheel(p, **kw)
        return p

    def check(self, wheel):
        pr = vr.Problems()
        vr.check_wheel(wheel, pr, self.proj, self.version, self.modules)
        return pr.items


class TestVerifierHasTeeth(_Base):
    """① 已知的漂移形状，一条都不许放过。"""

    def test_correct_wheel_passes(self):
        """② 先立"不咬"的基线：合格产物必须零问题。

        没有这条，下面三条"报了错"可能是恒真断言（校验器见谁咬谁也一样能通过）。
        """
        self.assertEqual(self.check(self.wheel()), [])

    def test_missing_module_is_reported(self):
        """高 6 的原形：源码加了 `_shell.py`，轮子里没有。"""
        victim = self.modules[-1]
        items = self.check(self.wheel(modules=[m for m in self.modules if m != victim]))
        self.assertTrue(items, "轮子缺了 %s，校验器却说没问题 —— 这正是高 6 能进包的原因"
                        % victim)
        self.assertTrue(any("缺模块" in x and victim in x for x in items),
                        "报了问题但没说清缺哪个模块：%s" % items)

    def test_wrong_requires_python_is_reported(self):
        items = self.check(self.wheel(requires_python=">=3.8"))
        self.assertTrue(any("Requires-Python" in x for x in items),
                        "元数据压着旧的 Python 门槛，校验器没报：%s" % items)

    def test_wrong_entry_point_is_reported(self):
        items = self.check(self.wheel(entry_points="[console_scripts]\nother = x:main\n"))
        self.assertTrue(any("入口点" in x for x in items),
                        "装完没有命令，校验器没报：%s" % items)

    def test_no_metadata_is_reported(self):
        items = self.check(self.wheel(metadata=False))
        self.assertTrue(any("METADATA" in x for x in items), items)

    def test_missing_bundled_engine_is_reported(self):
        """§5.2：wheel 只有壳、没有引擎 → `pip install` 之后仍要 clone，装完就能跑是断的。"""
        items = self.check(self.wheel(bundled_engine=False))
        self.assertTrue(any("没有自带引擎" in x and "_engine" in x for x in items),
                        "shell-only 的轮子被放行了：%s" % items)

    def test_machine_config_in_wheel_is_reported(self):
        """打包机的本机配置进包 = 把别人的目录结构发出去。

        第三种形态是**实测**：迁移 TOML 时把旧 local.yaml 改名归档成
        `local.yaml.disabled-20261021`，精确匹配的两个旧文件名都没命中，
        归档件于是被原样打进 wheel。判据改成前缀，这条锁住它不许退回去。
        """
        for name in ("config/harness.local.toml",
                     "config/harness.local.yaml",
                     "config/harness.local.yaml.disabled-20261021"):
            items = self.check(self.wheel(
                extra={"agentguard/_engine/" + name: "harness: D:/someone\n"}))
            self.assertTrue(any(name.split("/")[-1] in x for x in items),
                            "%s 混进轮子却没报：%s" % (name, items))
        # 阴性对照：example 模板是给所有人抄的，必须放行（否则新机器没法生成 local 配置）。
        items = self.check(self.wheel(
            extra={"agentguard/_engine/config/harness.local.example.toml": ""}))
        self.assertEqual([x for x in items if "本机配置" in x], [],
                         "example 模板被误判成本机配置：%s" % items)

    def test_unreadable_wheel_is_reported(self):
        bogus = os.path.join(self.tmp, "half-downloaded.whl")
        with io.open(bogus, "w", encoding="utf-8") as f:
            f.write("这不是 zip（下载到一半 / 被挪用）")
        self.assertTrue(any("打不开" in x for x in self.check(bogus)),
                        "半个文件当成合法轮子放行了")


class TestPep440Normalization(unittest.TestCase):
    """版本折算：`VERSION` 写 `0.1.2-dev`，wheel 元数据必然是 `0.1.2.dev0`。

    setuptools 的 PEP 440 归一化是**正确行为**，不是缺陷。首版校验器直接字面比较，
    于是对合格产物报了"版本不符" —— 这条锁保证那种假警报不会回来；
    同时保证"折算出错"的那些形状仍然报错（不能为了不假警报而一律放行）。
    """

    def test_variants_that_must_match(self):
        for a, b in (("0.1.2-dev", "0.1.2.dev0"),
                     ("v1.0.0", "1.0.0"),
                     ("2.0.0-rc1", "2.0.0.rc1"),
                     ("1.0.0-beta", "1.0.0b0")):
            self.assertEqual(vr._pep440(a), vr._pep440(b), "%s 与 %s 应当等价" % (a, b))

    def test_variants_that_must_not_match(self):
        self.assertNotEqual(vr._pep440("0.1.2-dev"), vr._pep440("0.1.2.dev1"))
        self.assertNotEqual(vr._pep440("0.1.2"), vr._pep440("0.1.3"))

    def test_unknown_shape_compares_literally(self):
        """认不出的形状原样返回 —— 那时按字面比：宁可报错，也不猜。"""
        self.assertEqual(vr._pep440("二十三点一"), "二十三点一")
        self.assertNotEqual(vr._pep440("二十三点一"), vr._pep440("二十三点二"))


class TestSumsReconciliation(_Base):
    """③ 清单对账：漏列 / 多列 / 哈希不符，三种都要报。"""

    def _stage(self, wheel_bytes_name="pkg-1.0-py3-none-any.whl"):
        stage = os.path.join(self.tmp, "pkg")
        os.makedirs(stage, exist_ok=True)
        shutil.copyfile(self.wheel(), os.path.join(stage, wheel_bytes_name))
        with io.open(os.path.join(stage, "安装说明.md"), "w", encoding="utf-8") as f:
            f.write("先 pip install %s\n" % wheel_bytes_name)
        self._write_sums(stage)
        return stage

    def _write_sums(self, stage, mutate=None):
        lines = []
        for dp, dns, fns in os.walk(stage):
            dns.sort()
            for fn in sorted(fns):
                fp = os.path.join(dp, fn)
                rel = os.path.relpath(fp, stage).replace("\\", "/")
                if rel == "SHA1SUMS.txt":
                    continue
                # 用校验器自己的 _sha1（它带 with 块）：本文件在 CI 里跑在
                # `-W error::ResourceWarning` + 句柄文本判据之下，裸 open 会被逮住。
                lines.append((vr._sha1(fp), rel))
        lines.sort(key=lambda x: x[1])
        if mutate:
            lines = mutate(list(lines))
        with io.open(os.path.join(stage, "SHA1SUMS.txt"), "w", encoding="utf-8") as f:
            f.write("".join("%s  %s\n" % (h, r) for h, r in lines))

    def _sums_problems(self, stage):
        pr = vr.Problems()
        vr.check_package(stage, pr, self.proj, self.version, self.modules)
        sums = os.path.join(stage, "SHA1SUMS.txt")
        return [x for x in pr.items if x.startswith(sums)]

    def test_clean_stage_has_no_sums_problems(self):
        """基线：合格的清单不许被咬（否则下面三条不可信）。"""
        stage = self._stage()
        self.assertEqual(self._sums_problems(stage), [])

    def test_missing_line_is_reported(self):
        stage = self._stage()
        self._write_sums(stage, mutate=lambda ls: ls[1:])       # 少列一条
        self.assertTrue(any("漏列" in x for x in self._sums_problems(stage)),
                        "清单漏了一条却没报 —— 用户照着核对会以为文件少")

    def test_extra_line_is_reported(self):
        stage = self._stage()
        self._write_sums(stage, mutate=lambda ls: ls + [("0" * 40, "幽灵文件.txt")])
        self.assertTrue(any("列了但包里没有" in x for x in self._sums_problems(stage)),
                        "清单列了不存在的文件却没报 —— 说明清单不是从产物生成的")

    def test_hash_mismatch_is_reported(self):
        """最关键的一条：文件被换过、清单没跟着变（或反之）。"""
        stage = self._stage()
        self._write_sums(stage, mutate=lambda ls: [(("0" * 40), ls[0][1])] + ls[1:])
        self.assertTrue(any("哈希不符" in x for x in self._sums_problems(stage)),
                        "内容与清单对不上却没报 —— 那 SHA1SUMS 就是装饰")


class TestVerifierWiring(unittest.TestCase):
    """④ 校验器必须**真的被调用**。没接线的校验器 = 没有校验器（高 6 的形状）。"""

    def test_packer_runs_verifier_and_fails_on_it(self):
        src = _read(PACKER)
        self.assertIn("verify_release.py", src,
                      "pack_release.py 没有调用出货件校验 —— 打包流程又会一路 ✅ 到底")
        self.assertIn("不要分发这个包", src,
                      "校验失败必须拦住「完成」的结论，而不是打印一句警告继续")

    def test_release_workflow_runs_verifier(self):
        src = _read(RELEASE_YML)
        self.assertIn("sandbox/verify_release.py", src,
                      "release.yml 没有跑出货件校验：CI 会发布内容没人看过的包。"
                      "（pack_release 内部那一步在本地跑得到，但发布链路上要有独立步骤、"
                      "独立步骤名，失败时日志第一眼能定位）")

    def test_release_workflow_matches_ci_resource_warning_criterion(self):
        """步骤名自称「与 CI 同源」，就得**真的是**同源（低 25 的第一条）。

        原先 release.yml 只有 `-W error::ResourceWarning`，缺 ci.yml 的文本判据 ——
        而泄漏句柄通常表现为 "Exception ignored in: ..." 后套件照样 OK。
        """
        crit = 'grep -qE "ResourceWarning|Exception ignored in"'
        for path, name in ((CI_YML, "ci.yml"), (RELEASE_YML, "release.yml")):
            self.assertIn(crit, _read(path),
                          "%s 缺 ci.yml 的句柄文本判据（低 25）" % name)

    def test_tag_check_runs_before_build(self):
        """tag↔VERSION 校验必须在构建**之前**（低 25）。

        原先它长在最后一步「创建 Release」里：tag 打错时全套自检 + 构建先白跑几分钟，
        且 dist/ 里已躺着一个对不上的包。用文本位置判定，不引入 yaml 依赖
        （CI 只装标准库）。
        """
        src = _read(RELEASE_YML)
        i_tag, _ = _step(src, "校验 tag ↔ VERSION 一致")
        i_build, _ = _step(src, "构建 sdist + wheel（唯一构建点）")
        self.assertIsNotNone(i_tag, "release.yml 里找不到 tag↔VERSION 校验步骤")
        self.assertIsNotNone(i_build, "release.yml 里找不到构建步骤")
        self.assertLess(i_tag, i_build, "tag↔VERSION 校验排在构建之后 —— 白跑一次构建才拒绝")
        # 前移之后，原来的位置不许再留一份重复检查（锚在**比对表达式**上，
        # 而不是某句文案：文案会改，比对表达式是这件事的本体）
        self.assertEqual(src.count('"${TAG#v}" != "$VERSION"'), 1,
                         "tag↔VERSION 的比对出现了多份：改一处漏一处，两边迟早不一致")

    def test_wheel_pick_is_exact_not_lucky(self):
        """低 25：`ls … | head -1` 在多个 wheel 时**任取一个**。

        扫**整份 release.yml 的代码行**（不是只扫某一个步骤）：构建、组装、安装冒烟
        都可能“挑一个轮子”，任何一处留 `head -1` 都是同一个口子。注释不参与判定。
        """
        code = "\n".join(_code_lines(_read(RELEASE_YML).splitlines()))
        self.assertNotIn("head -1", code,
                         "发布链又在用 head -1 挑 wheel：多一个轮子就任取一个")
        self.assertIn("-ne 1", code, "缺「恰好一个 wheel」的断言")

    def test_module_list_is_derived_not_hardcoded(self):
        """模块清单必须**从源码目录推导**。

        写死清单正是高 6 的成因：`_shell.py` 加进 `agentguard/` 之后，
        任何"按旧清单验货"的检查都看不见它。这条锁保住"自动纳入新模块"这个性质。
        """
        src = _read(VERIFIER)
        self.assertIn("os.listdir(SRC_PKG)", src,
                      "校验器的模块清单不是从 agentguard/ 推导的 —— 新增模块会漏验")


def _dist_pack_hint():
    """`dist/install-package/` 下到底躺着哪几版 + 当前 VERSION —— 专门给 skip 文案用。

    为什么值得为一句提示单写一个函数（2026-09-17，U-5）：这个类是全套里**唯一**看真产物的
    断言，而它跳过时**一个字都不说**。于是最常见的现场 ——「dist/ 里躺着一个几天前的旧包，
    当前 VERSION 的包根本没打」—— 观感与"没写这条锁"完全一样：套件全绿，谁也不知道它没跑。
    skip 的文案是唯一能把这层说破的地方：它不是日志噪音，是这条锁**没跑**的唯一痕迹。

    只加提示、**不改成失败**：`dist/` 是 gitignore 的本地暂存，CI（以及任何刚 clone 的机器）
    里本来就没有它 —— 把它变成红，等于让干净检出必红，那是拿"本地卫生"冒充"代码有问题"。
    """
    d = os.path.join(ROOT, "dist", "install-package")
    try:
        names = sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n)))
    except OSError:
        names = []
    if not names:
        return "dist/install-package 是空的（本机从来没打过包）"
    try:
        cur = str(vr._facts()[1])
    except Exception:                                   # 取不到当前版本号不算这条锁的事
        cur = "?"
    return ("本机 dist/install-package 下现有：%s；当前 VERSION 是 %s —— 旧产物正是这条锁"
            "跳过的典型原因，跑一次 `python sandbox/pack_release.py` 才算真验过"
            % ("、".join(names), cur))


@unittest.skipUnless(os.path.isdir(os.path.join(ROOT, "dist", "install-package")),
                     "本机没有 dist/install-package（CI 容器即是如此）：真产物锁跳过")
class TestRealArtifactPasses(unittest.TestCase):
    """⑤ 真产物锁：出货目录存在时，跑真校验必须 rc=0。

    这是唯一一条"看真东西"的断言 —— 上面那些喂的都是假轮子。
    没有它，校验器可以正确得无懈可击，而 `dist/` 里躺着的仍然是 09-14 的旧包。
    """

    def test_shipped_package_verifies(self):
        r = subprocess.run([sys.executable, VERIFIER], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        if r.returncode == 2 and "还没有 v" in (r.stdout or ""):
            self.skipTest("本机没有当前 VERSION 对应的安装包产物（先跑 pack_release.py）：%s"
                          % _dist_pack_hint())
        self.assertEqual(r.returncode, 0,
                         "出货件与源码对不上（rc=%s）—— 重跑 python sandbox/pack_release.py\n%s"
                         % (r.returncode, (r.stdout or "") + (r.stderr or "")))


# ================================================================ 出货件的形状与升级链路
# 评审 v13 之后的第二轮（2026-09-17）。这一组的方向和前四组不同：前四组问"校验器会不会红"，
# 这一组问"**包本身长什么样**"。起因是一个很朴素的问题 —— 同事已经装了旧包，我给他新包时
# 他该怎么升级？顺着这个问题查下去，发现三件更要紧的事，共同形状都是**全流程没有一步报错**：
#
#   ① 包内 `.mcp.json` 带着**打包机**的绝对路径（同事漏跑适配就无声用着别人的机器）；
#   ② 插件清单里的 version 可以和 `VERSION` 脱钩（同事按版本号认版本，于是认错）；
#   ③ 同版本号、不同内容的重打包不留任何痕迹（`claude plugin update` 会绿着什么都不换）。
#
# 这一组**不手写假 stage**：假 stage 只能证明"校验器认我写的形状"，证不了 pack_release.py
# 真把 `--keep-placeholders` 传下去了 —— 而后者才是要钉的东西。所以真跑一次打包
# （`--wheel` 复用假轮子，省掉 setuptools），再在副本上逐个制造病变。


@unittest.skipIf(FAST, "快速模式（HARNESS_FAST=1）跳过真打包基线"
                       "（每个子类一次 pack_release + 每例整树拷贝；全量口径仍跑）")
class _Pack(_Base):
    """真产物基线：跑一次 `pack_release.py`，每个用例在它的**副本**上做病变。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="pack-lock-")
        proj, version, modules = vr._facts()
        cls.seed = os.path.join(cls._tmp, "seed.whl")
        _make_wheel(cls.seed, modules, str(proj.get("requires-python")),
                    proj.get("name"), version)
        cls.out = os.path.join(cls._tmp, "out")
        cls.pkg_name = "guardrail-engine-package-v%s" % version
        r = subprocess.run([sys.executable, PACKER, "--out", cls.out, "--wheel", cls.seed],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        cls.build_rc = r.returncode
        cls.build_log = (r.stdout or "") + (r.stderr or "")
        cls.stage_src = os.path.join(cls.out, cls.pkg_name)

    @classmethod
    def tearDownClass(cls):
        _rmtree_force(cls._tmp)

    def setUp(self):
        super().setUp()
        self.assertTrue(os.path.isdir(self.stage_src),
                        "打包器没产出 stage（rc=%s）：\n%s" % (self.build_rc, self.build_log))
        self.stage = os.path.join(self.tmp, "stage")
        shutil.copytree(self.stage_src, self.stage)
        self.mj = os.path.join(self.stage, "marketplace", "plugins", "agentguard",
                               ".mcp.json")   # 插件 ID 保持 agentguard（v0.3 前不改）

    # ---- 夹具 ----
    def problems(self):
        pr = vr.Problems()
        vr.check_package(self.stage, pr, self.proj, self.version, self.modules)
        return pr.items

    def problems_at(self, path):
        """按**标签精确取**某一份文件名下的问题。

        刻意不用 `startswith(path)`：`stage/SHA1SUMS.txt` 也以 `stage` 开头，于是
        "缺 PACK-INFO.json" 这类断言会被 SHA1SUMS 的"列了但包里没有 PACK-INFO.json"
        顶替 —— 检查项被删掉之后它照样绿。突变验证第一轮就是这么抓到这条假绿的
        （`reports/_falsify_u1_lock.txt` 突变 D）。
        """
        return [x for x in self.problems() if x.split("：", 1)[0] == path]

    def _rewrite_json(self, path, mutate):
        data = json.loads(_read(path))
        mutate(data)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


class TestShippedPackIsMachineIndependent(_Pack):
    """① 出货件必须与机器无关 —— 装机时由包内 `fix_paths.py` 填本机路径。

    缺陷形状：打包时把**打包机**的 python 与工程路径烧进 `.mcp.json`。同事漏跑适配
    （安装说明里原来那句"原地升级、机器没变可跳过"最容易让人跳过）时，那份文件就指向
    别人的机器：MCP 服务起不来、**编辑期门禁整体无声失效**，且没有任何一步报错。
    """

    def test_real_pack_verifies_and_has_no_problems(self):
        """基线与接线一起验：真跑打包必须 rc=0，且校验器对真产物**零问题**。

        pack_release 自己会在最后调校验器并据此决定成败，所以 rc=0 同时说明
        "打包器接线了校验器"和"校验器没对合格产物乱咬"（假警报比漏报更磨损可信度）。
        """
        self.assertEqual(self.build_rc, 0,
                         "打包器自己没通过自己的校验（rc=%s）：\n%s"
                         % (self.build_rc, self.build_log))
        self.assertEqual(self.problems(), [],
                         "校验器对一个刚出炉的合格包报了问题（假警报）：\n%s"
                         % "\n".join(self.problems()))

    def test_pack_ships_placeholders_not_packer_paths(self):
        """反面：包内写的是**打包机**的绝对路径时，三个字段一个都不许放过。"""
        srv = {"command": "C:/Users/someone/AppData/Local/Programs/Python/python.exe",
               "args": ["D:/someone/agentguard/agentguard/mcp.py"],
               "env": {"AGENTGUARD_DIR": "D:/someone/agentguard"}}
        self._rewrite_json(self.mj, lambda d: d["mcpServers"].update({"guardrail-engine": srv}))
        got = "\n".join(self.problems_at(self.mj))
        for field in ("command", "args[0]", "env.AGENTGUARD_DIR"):
            self.assertIn(field, got,
                          "出货件带着打包机的路径而 %s 没被报出来：\n%s" % (field, got))
        self.assertIn("fix_paths.py", got, "报错要说清怎么修：\n%s" % got)

    def test_packer_offers_no_way_back_to_machine_stamped_packs(self):
        """出货路径上不许留"重新打戳"的开关。

        `build_plugin.py` 默认仍是就地打戳（本机自用要它），但 `pack_release.py` 必须
        **无条件**加 `--keep-placeholders` —— 一旦这里变成可配置，缺陷就有路回来，
        而回来之后没有任何一条断言会红（那时包是"合法"的）。
        """
        src = _read(PACKER)
        self.assertIn('"--keep-placeholders"', src,
                      "pack_release.py 没把 --keep-placeholders 传给 build_marketplace.py")
        self.assertNotIn("add_argument(\"--keep-placeholders", src,
                         "打包器把「出货件与机器无关」做成了选项 —— 它该是包的定义，不是开关")


class TestShippedToolsLock(_Pack):
    """④ 包内那三个"动手用"的工具，必须是仓库里那一份（逐字节）。

    升级链路剖析报告 G2：升级这条路此前**没有任何工具**，也没有任何锁 —— 同事手上只有
    `uninstall.py`，于是唯一能一键跑完的路成了"先卸干净再装"。`upgrade.py` 补的就是这一格，
    而它一旦与仓库那份脱钩（包里的旧、仓库里的新），同事跑的就是**另一支脚本**，
    且没有任何一步会报错 —— 这类"工具自身的版本漂移"和插件版本漂移是同一个形状。

    两条锁成对，缺一条都不算覆盖：`test_shipped_tools_are_the_repo_copies` 盯**打包器**搬对了，
    `test_stale_tool_in_pack_is_reported` 盯**校验器**发现得了没搬对 —— 只留前者，校验器那条
    检查被删掉时无人在意；只留后者，打包器搬错源时同样无人在意。
    """

    #: 包内文件名 → 仓库里的单一来源
    TOOLS = {"bootstrap.py": os.path.join("sandbox", "bootstrap.py"),
             "fix_paths.py": os.path.join("sandbox", "fix_paths.py"),
             "uninstall.py": os.path.join("sandbox", "uninstall.py"),
             "upgrade.py": os.path.join("sandbox", "upgrade.py")}

    def test_shipped_tools_are_the_repo_copies(self):
        for name, rel in sorted(self.TOOLS.items()):
            shipped = os.path.join(self.stage, name)
            self.assertTrue(os.path.isfile(shipped),
                            "包里没有 %s（同事拿到手就没有这一格）" % name)
            with open(shipped, "rb") as f:
                got = f.read()
            with open(os.path.join(ROOT, rel), "rb") as f:
                want = f.read()
            self.assertEqual(got, want,
                             "包里的 %s 与仓库 %s 不是同一份 —— 同事跑的是另一支脚本" % (name, rel))

    def test_stale_tool_in_pack_is_reported(self):
        """阴性对照：把包内 `upgrade.py` 换成**上一版的内容**，校验器必须报出来。

        为什么要有这一条（2026-09-17，U-5 现场）：上面那条锁的是**打包器搬对了**，
        这条锁的是**校验器发现得了没搬对**。缺了它，包里的工具与仓库脱钩之后每一处都是绿的
        —— 存在性检查（包里有没有 upgrade.py）绿、包内自洽的 SHA1SUMS 绿、只覆盖
        `marketplace/` 的指纹也绿 —— 同事照文档跑的却是另一支脚本。
        这不是假设：改完 `sandbox/upgrade.py` 忘了重打包时，`dist/` 里那份就是旧的，
        而 `python sandbox/verify_release.py` 照样 rc=0（当场实测）。
        """
        stale = os.path.join(self.stage, "upgrade.py")
        with io.open(stale, "w", encoding="utf-8") as f:
            f.write("# 上一版的升级脚本（与仓库那份不同）\n")
        got = "\n".join(self.problems_at(stale))
        self.assertIn("不是同一份", got,
                      "包内工具与仓库脱钩却没人报（⑧ 那条检查被删/被短路了？）：\n%s" % got)

    def test_missing_upgrader_is_reported(self):
        """阴性对照：把包内 `upgrade.py` 拿掉，校验器必须报出来。

        防的是「校验器那行被删了」：`for req in (...)` 里少一个名字，打包照旧 ✅，
        而同事打开包发现没有升级工具 —— 又是一次"工具空缺"。所以这里删文件、看是否有人喊。
        """
        os.remove(os.path.join(self.stage, "upgrade.py"))
        got = "\n".join(self.problems_at(self.stage))
        self.assertIn("缺 upgrade.py", got,
                      "包内升级工具没了却没人报（校验器那行被削了？）：\n%s" % got)

    def test_every_shipped_tool_is_named_in_the_install_note(self):
        """包里的每支 .py 都必须在安装说明里被点名 —— 否则它是一支"没写进文档的工具"。

        阴性对照：往 stage 里放一支**谁都没听说过**的脚本，安装说明若不提它就必须报。
        （探针用的是真产物，所以这条同时保证"现有那几支工具都被写到了"。）
        """
        probe = os.path.join(self.stage, "zz-probe-tool.py")
        with io.open(probe, "w", encoding="utf-8") as f:
            f.write("# 探针\n")
        note = os.path.join(self.stage, "安装说明.md")
        got = "\n".join(self.problems_at(note))
        self.assertIn("zz-probe-tool.py", got,
                      "包里多了一支没写进文档的工具却没报（这条锁空转）：\n%s" % got)


class TestPluginVersionLock(_Pack):
    """② 插件清单的 version 必须与 `VERSION` 一致（按 PEP 440 折算）。"""

    def _man(self):
        return os.path.join(self.stage, "marketplace", "plugins", "agentguard",
                            ".claude-plugin", "plugin.json")

    def test_version_mismatch_is_reported(self):
        """插件缓存按 version 分键，同事也按它认版本 —— 脱钩时报出来比装错强。"""
        self._rewrite_json(self._man(), lambda d: d.update({"version": "0.0.1"}))
        got = "\n".join(self.problems_at(self._man()))
        self.assertIn("0.0.1", got, "插件清单版本与 VERSION 脱钩却没报：\n%s" % got)

    def test_pep440_equivalent_spelling_is_not_reported(self):
        """阴性对照：`0.1.2.dev0` 与 `0.1.2-dev` 是**同一个版本**的两种写法。

        这条防的是"锁写成字面比较"—— 那种锁会在合格产物上报警，而假警报的代价是
        下一次真问题没人看（本文件开头那句）。
        """
        want = vr._pep440(self.version)
        self._rewrite_json(self._man(), lambda d: d.update({"version": want}))
        self.assertEqual(self.problems_at(self._man()), [],
                         "PEP 440 折算后相等的两种写法被当成了版本不符（假警报）")


class TestPackInfoLock(_Pack):
    """③ `PACK-INFO.json` 记的**两个**指纹都必须真的等于包的当前内容。

    两个字段口径不同、都要核（2026-09-17 扩）：
      · `pack_sha1`  —— **整包**（除 PACK-INFO.json / SHA1SUMS.txt 两个自指文件）；
      · `marketplace_sha1` —— 只有市场子树（插件那条通道按版本号取内容，市场那一份单独有名字）。
    只核一个是不够的：整包指纹覆盖市场子树，于是"市场变了"两处都会响；反过来"只有工具变了"
    时，**只有整包指纹会响** —— 那正是 2026-09-17 之前不响的那一种。
    """

    def _info(self):
        return os.path.join(self.stage, "PACK-INFO.json")

    def test_missing_pack_info_is_reported(self):
        os.remove(self._info())
        got = "\n".join(self.problems_at(self.stage))
        self.assertIn("缺 PACK-INFO.json", got,
                      "缺出货件身份文件却没报：\n%s" % "\n".join(self.problems()))

    def test_tampered_market_fingerprint_is_reported(self):
        self._rewrite_json(self._info(), lambda d: d.update({"marketplace_sha1": "0" * 40}))
        self.assertIn("市场子树指纹与 marketplace/ 实际内容不符",
                      "\n".join(self.problems_at(self._info())),
                      "市场子树指纹被改过却对得上 —— 那这道对账是死的")

    def test_tampered_pack_fingerprint_is_reported(self):
        self._rewrite_json(self._info(), lambda d: d.update({"pack_sha1": "0" * 40}))
        self.assertIn("整包指纹与包实际内容不符",
                      "\n".join(self.problems_at(self._info())),
                      "整包指纹被改过却对得上 —— 那扩到整包等于没扩")

    def test_old_format_pack_info_is_not_silently_passed(self):
        """旧格式（只有 marketplace_sha1）→ **必须点名**，不许当成核过了。

        旧包是真实存在的（同事手里、dist/ 里都可能有）。这里要防的是"核不出来就当没事"：
        少核一半而一个字都不说，读的人会把绿当成"整包身份已核"。所以判失败（这份 PACK-INFO
        该重新生成），且理由里要给出重打包这条命令。
        """
        self._rewrite_json(self._info(), lambda d: d.pop("pack_sha1", None))
        got = "\n".join(self.problems_at(self._info()))
        self.assertIn("旧格式", got, "旧格式 PACK-INFO 被静默放过了：\n%s" % got)
        self.assertIn("pack_release.py", got, "说了旧格式却没给出路（重跑打包）：\n%s" % got)

    def test_any_marketplace_change_moves_the_fingerprint(self):
        """最关键的一条：动 marketplace 里**任何一个字节**，指纹就必须对不上。

        只验"字段能对上"是不够的 —— 一个覆盖范围漏掉半棵树的指纹，照样能自洽。
        这里往市场 README 末尾加一个换行（最不显眼的一种改动），断言它被逮住。
        """
        rd = os.path.join(self.stage, "marketplace", "README.md")
        before = self.problems_at(self._info())
        self.assertEqual(before, [], "前提：未改动时指纹应当是对得上的")
        with io.open(rd, "a", encoding="utf-8") as f:
            f.write("\n")
        self.assertIn("市场子树指纹与 marketplace/ 实际内容不符",
                      "\n".join(self.problems_at(self._info())),
                      "marketplace 内容变了而指纹照旧对得上 —— 这个指纹没覆盖全树")

    def test_a_tool_change_moves_the_pack_fingerprint(self):
        """动**市场子树之外**的任何一个字节（这里是一支工具），整包指纹就必须对不上。

        为什么必须单独有这条（2026-09-17 扩指纹之前它不存在）：旧口径只覆盖 marketplace/，
        于是"改了工具或文档、忘了重打包"这类改动**指纹纹丝不动** —— G3 那句同版本提醒在最该响
        的一次上没有响（现场见 reports/_falsify_v1_lock.txt §⑥）。
        ★ 改的必须是市场子树之外的文件：只改 marketplace 的话旧口径也会响，这条就证明不了扩没扩。
        ★ 先立前提（未改动时不许有问题）：没有它，"指纹不符"这句话可能来自**别的**原因
        （例如打包器与校验器各用一套口径 —— 那样一个没被动过的包也会报同一句话），
        这条锁就会为错误的理由变绿。变异 M34 第一版正是这样被放过去的。
        """
        self.assertEqual(self.problems_at(self._info()), [],
                         "前提：未改动时整包指纹应当是对得上的")
        _write(os.path.join(self.stage, "upgrade.py"),
               _read(os.path.join(ROOT, "sandbox", "upgrade.py")) + "\n# 就地改一笔\n")
        got = "\n".join(self.problems_at(self._info()))
        self.assertIn("整包指纹与包实际内容不符", got,
                      "包内工具变了而整包指纹照旧对得上 —— 指纹还是只覆盖 marketplace/：\n%s" % got)

    def test_tool_change_does_not_move_the_market_fingerprint(self):
        """反方向：工具变了，**市场子树**指纹不该跟着变（两个字段口径不同，各司其职）。

        没有这条的话，把 `marketplace_sha1` 也写成整包指纹（两个字段同一个值）照样全绿 ——
        那样"市场那一份单独有名字"就名存实亡：读包的人以为市场变了，其实只是换了支工具。
        """
        _write(os.path.join(self.stage, "upgrade.py"), "# 换一份内容\n")
        want = vr._packer().pack_fingerprint(os.path.abspath(self.stage))
        self._rewrite_json(self._info(), lambda d: d.update({"pack_sha1": want}))
        got = "\n".join(self.problems_at(self._info()))
        self.assertEqual(got, "",
                         "整包指纹已按实况更新，市场子树指纹却不该变：\n%s" % got)


class TestWheelEntersTheFingerprintByContent(_Pack):
    """轮子按**内容**入整包指纹，不按文件字节（2026-09-17 真扩时当场量出来的）。

    现场：两个轮子的文件字节不同（`f7b78f70…` / `6ddd8029…`），内容指纹却同为 `91763035fc…`
    —— zip 的条目时间戳由构建时刻决定，构建本身**不可复现**。若指纹按字节算，那条"同一个
    版本号下已经存在过一份内容不同的包"就会**每次重打包都响**；而逢包必响的提醒等于没有提醒
    （本文件开头那句：假警报比漏报更磨损可信度）。所以口径必须是内容，且两个方向都要钉：
      · 字节变了、内容没变 → 指纹**不许动**（否则是噪音）；
      · 内容真变了         → 指纹**必须动**（否则是漏报，比噪音更糟）。
    """

    def _wheel(self):
        for fn in sorted(os.listdir(self.stage)):
            if fn.endswith(".whl"):
                return os.path.join(self.stage, fn)
        self.fail("stage 里没找到轮子 —— 本锁会空转")

    def test_repacked_wheel_with_new_timestamps_keeps_the_fingerprint(self):
        packer = vr._packer()
        whl = self._wheel()
        before_fp = packer.pack_fingerprint(os.path.abspath(self.stage))
        before_bytes = vr._sha1(whl)
        _restamp_wheel(whl)
        self.assertNotEqual(vr._sha1(whl), before_bytes,
                            "夹具没起作用：重写之后轮子的文件字节居然没变，这条锁是空转的")
        self.assertEqual(packer.pack_fingerprint(os.path.abspath(self.stage)), before_fp,
                         "只换了时间戳、内容没变，整包指纹就动了 —— 那指纹是**按字节**算的，"
                         "每次重打包都会响一次「同版本号内容不同」，提醒会被训练成噪音")

    def test_real_change_inside_the_wheel_moves_the_fingerprint(self):
        packer = vr._packer()
        whl = self._wheel()
        with zipfile.ZipFile(whl) as z:
            victim = sorted(n for n in z.namelist() if not n.endswith("/"))[0]
        before = packer.pack_fingerprint(os.path.abspath(self.stage))
        _restamp_wheel(whl, edit=lambda n, d: (n, d + b"\n# tampered\n") if n == victim
                       else (n, d))
        self.assertNotEqual(packer.pack_fingerprint(os.path.abspath(self.stage)), before,
                            "轮子里 %s 的内容真变了，整包指纹却没动 —— 漏报比噪音更糟："
                            "同事装的轮子与我这次做的不是同一个，而没有任何一步会报错" % victim)


class TestSameVersionRepackWarns(_Pack):
    """③ 的打包侧：同版本号、**内容不同**的重打包必须吵醒人（而不是静默覆盖）。

    为什么不去真跑两次 pack_release 来造这个场景：marketplace 的内容全部来自本仓库
    单一来源，同一个版本号下两次构建必然逐字节相同 —— 想造出"同版本不同内容"，只能去改
    真实仓库的源文件，而测试不许改被测源码。所以这里按它的**真实调用形状**验：
    在输出目录里预置一份"上一次的包"（zip），再让 pack_release.py 面对它。
    指纹一律由模块自己的函数算，本文件不另抄一份口径。
    """

    def _old_pack(self, out, old_id, tool_body=None, market_delta=False):
        """在 `out` 里预置一份"上一次的包"（zip）。

        默认形态刻意**只让市场之外的文件不同**（工具 `upgrade.py` 内容不一致），市场子树
        与本次逐字节相同 —— 因为要证明的正是"指纹扩到了整包"：若把市场也改掉，那么按**旧的**
        （只覆盖 marketplace/）口径同样会响，这两条锁就都证明不了扩指纹这件事。
        `market_delta=True` 时反过来（只改市场），用来钉住"另一边也必须响"。
        """
        old = os.path.join(self.tmp, "oldpack")
        shutil.copytree(os.path.join(self.stage, "marketplace"), os.path.join(old, "marketplace"))
        if market_delta:
            _write(os.path.join(old, "marketplace", "README.md"), "# 上一次的市场（与本次不同）\n")
        _write(os.path.join(old, "upgrade.py"),
               "# 上一版的升级脚本（与本次那份不同）\n" if tool_body is None else tool_body)
        _write(os.path.join(old, "PACK-INFO.json"), json.dumps(
            {"version": self.version, "pack_id": old_id}, ensure_ascii=False, indent=2) + "\n")
        with zipfile.ZipFile(os.path.join(out, self.pkg_name + ".zip"), "w") as z:
            for dp, dns, fns in os.walk(old):
                dns.sort()
                for fn in sorted(fns):
                    fp = os.path.join(dp, fn)
                    z.write(fp, self.pkg_name + "/" + os.path.relpath(fp, old).replace("\\", "/"))

    def _repack(self, out):
        r = subprocess.run([sys.executable, PACKER, "--out", out, "--wheel", self.seed],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def test_same_version_different_content_warns_but_still_succeeds(self):
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        old_id = "deadbeef0000"
        self._old_pack(out, old_id)
        rc, log = self._repack(out)
        self.assertEqual(rc, 0,
                         "同版本重打包被**拦住**了 —— 重建是正当动作（补丁、重跑），"
                         "构建脚本没资格因此失败：\n%s" % log)
        self.assertIn("同一个版本号下已经存在过一份", log,
                      "同版本不同内容的重打包一声不吭 —— 同事会以为升级成功了：\n%s" % log)
        self.assertIn(old_id, log, "警告要点出上一次的 pack_id，否则人无从比对：\n%s" % log)
        self.assertIn("claude plugin update", log,
                      "警告要说清后果（update 会绿着什么都不换），否则像例行提示：\n%s" % log)
        self.assertIn("~ upgrade.py", log,
                      "警告没点出**是哪一份文件不同** —— 只给两个 pack_id，人还是不知道改了什么：\n%s"
                      % log)

    def test_market_only_change_also_warns(self):
        """反方向：市场子树不同、工具相同 → 同样必须响（整包指纹覆盖市场那一份）。"""
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        self._old_pack(out, "deadbeef0000", tool_body=None, market_delta=True)
        rc, log = self._repack(out)
        self.assertEqual(rc, 0, log)
        self.assertIn("同一个版本号下已经存在过一份", log,
                      "只改了市场 subtree 反而没响 —— 整包指纹漏掉了 marketplace/：\n%s" % log)

    def test_identical_repack_stays_quiet(self):
        """**内容逐字节相同**的同版本重打包 → 不许出声。

        为什么这条和上面两条一样重要：一个"每次重打包都响"的提醒等于没有提醒 ——
        人会被训练成无视它，于是真正该看见的那一次也看不见了。市场与文档都由仓库单一来源
        构建、轮子复用 `--wheel` 那份，所以除两个自指文件外**内容**确实能造出"同一份"。

        注意预置的那份旧包里的轮子会被**重新打时间戳**（`_restamp_wheel`）：这不是刁难，
        这就是真实场景 —— 上一次那份包里的轮子是我上次构建的产物，zip 条目时间戳按构建时刻
        写死，所以它和本次这个轮子**必然字节不同**（实测 f7b78f70… / 6ddd8029…）。若指纹按
        字节算，这条锁当场就红：一个真实存在的、完全正常的重打包会被报成"内容不同"。
        """
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        # 把上一次那份包的**内容**原样放进 zip（除两个自指文件外逐字节相同：
        # PACK-INFO.json 里的 built_at 每次都不同，SHA1SUMS.txt 是派生清单，两者都不进指纹）
        self._old_pack(out, "deadbeef0000",
                       tool_body=_read(os.path.join(self.stage, "upgrade.py")))
        # 名单从**产物本身**派生（`_restage_identically`），不在这里手写 —— 手写的那一版
        # 在加 `bootstrap.py` 那天漏了项，让本锁在一次正常重打包上假红。
        old = os.path.join(self.tmp, "oldpack")
        _restage_identically(self.stage_src, old)
        with zipfile.ZipFile(os.path.join(out, self.pkg_name + ".zip"), "w") as z:
            for dp, dns, fns in os.walk(old):
                dns.sort()
                for fn in sorted(fns):
                    fp = os.path.join(dp, fn)
                    z.write(fp, self.pkg_name + "/" + os.path.relpath(fp, old).replace("\\", "/"))
        rc, log = self._repack(out)
        self.assertEqual(rc, 0, log)
        self.assertNotIn("同一个版本号下已经存在过一份", log,
                         "内容相同的重打包也响了 —— 提示会被训练成噪音，"
                         "真正该看见的那次就看不见了：\n%s" % log)


if __name__ == "__main__":
    unittest.main()
