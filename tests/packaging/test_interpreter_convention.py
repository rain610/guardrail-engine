# -*- coding: utf-8 -*-
"""解释器统一约定的回归锁（2026-09-18 跨平台批次 1）。

背景：WSL 上**每一次提交**都 fail-closed，报错还看不懂 —— `harness/hooks-dir/pre-commit`
调的是裸 `python`，而 Ubuntu 上只有 `python3`。反方向也有洞：Windows 上 `python3` 常常只是
Microsoft Store 的占位别名（`command -v` 能命中、一跑就失败）。所以约定**不是**「命令在不在」，
而是「**真的能跑起来**」：`--version` 退出码 0 且输出 **Python 3.11+**（评审 2026-11-04 P2-4：
判据从「任意 Python 3」收紧到 3.11+，因为 3.10 会让引擎 import 期失败）。

本文件锁四件事，每件都对应一种真实的复发形态：

1. **一次定义**：候选顺序只写在标记行 `# HARNESS-PY-CANDIDATES:` 里；各入口的探测段必须
   **逐字节相同**。否则"统一约定"名不副实 —— 改一处、其余各处不跟，正是本仓反复出现过的形状。
   （为什么必须内联、不抽成被 source 的 _python.sh：见 `harness/hooks-dir/pre-commit` 内注释
   与 `tests/test_review_v5.py` —— 那两条锁搭的是"只拷单支脚本"的最小布局。）
2. **标记即事实**：标记行里写的顺序，就是源码里真按顺序试的顺序。标了不试 = 假承诺。
3. **不留裸 `python` 调用**：将来只改一半（新加一处 `python xxx.py`）当场红。
4. **行为（阴阳两向）**：真跑一遍 —— 一个解释器都没有时必须 fail-closed 且出声；本机必须解析到
   一个**真能跑**的 Python 3；`HARNESS_PYTHON` 必须被尊重；一个自称 Python 2 的 `python3`
   必须被挡掉（判据落在输出上，不是名字上）。
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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _tmpclean import rmtree_force as _rmtree_force  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MARK = "# HARNESS-PY-CANDIDATES:"

# 标记行右侧的**唯一合法取值**。这是本约定的第二个落点（第一个是标记行本身、第三个是
# INSTALL.md 的人话说明）：改这一行 = 同时改全部入口 + 安装说明，想"悄悄只改一处"改不动。
EXPECTED_CANDIDATES = "python3 python py -3"

# 本批确立约定的入口清单。首个是**正本**（探测段就是从这里拷出去的）。
ENTRIES = [
    "harness/hooks-dir/pre-commit",     # 正本：全仓唯一 fail-closed 起点，31 个 gate 共用
    "sandbox/setup_machine.sh",         # 比任何 gate 枚举都先跑（它要靠 python 读生效配置）
    "sandbox/verify_integrations.sh",
    "sandbox/doctor.sh",                # 唯一保留 exit 1 语义的入口（见其内注释）
    "sandbox/demo_gate.sh",
    "sandbox/install_engine_hooks.sh",  # 注意：探测段写在它**生成**的那份钩子里
    "sandbox/install_hooks.sh",
    "sandbox/bootstrap_agent_env.sh",
    # 2026-09-18 结清：原先挂在 EXEMPT 里的**真实欠账**（不是"无关紧要"）—— 它的两个调用点
    # 都会真执行（逐个 gate --all 全量扫描 + 覆盖度自评），WSL 上跑它以 command not found 收场。
    # plan §不做 5 给的理由是"文档性片段，不是运行路径"，那个理由**只对 docs/** 成立。
    "sandbox/sweep_coverage.sh",
]

# 本批**刻意不接线**的 sh 入口。写在这里 = 留成明账，而不是"忘了"。
# （sweep_coverage.sh 曾挂在这里，2026-09-18 已接线上移到 ENTRIES —— 那次正是下面
#   test_sandbox_entry_inventory_is_explicit 最后那条断言主动变红逼出来的。）
EXEMPT = {
    "sandbox/where.sh": "只推导并打印工程路径，全篇不含任何 python 调用",
}

FAIL_MSG = "找不到可用的 Python 3 解释器"


def _read(rel):
    # 硬规则：文件读取必须用 with 块（严格句柄门槛 -W error::ResourceWarning）。
    # 统一折 CRLF：本仓 .gitattributes 钉了 eol=lf，但检出配置异常时不该让"解析"跟着崩。
    with io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def _sandbox_sh():
    d = os.path.join(ROOT, "sandbox")
    return sorted("sandbox/" + n for n in os.listdir(d) if n.endswith(".sh"))


def _marker_of(rel):
    """取该入口的标记行右侧（去空白）。没有则返回 None，多于一行为则抛断言。"""
    hits = [l[len(MARK):].strip() for l in _read(rel).splitlines() if l.startswith(MARK)]
    if len(hits) > 1:
        raise AssertionError("%s 有 %d 行 %s（单一事实来源必须只有一行）：%r"
                             % (rel, len(hits), MARK, hits))
    return hits[0] if hits else None


def _detection_block(rel):
    """抽出**探测段**：从 `HARNESS_PY=""` 到失败分支之前（含循环与 py -3 回退）。

    这段是"一次定义"的对象 —— 失败分支因入口而异（doctor 用 fail+exit 1、其余 exit 2），
    所以刻意切到它之前；失败分支另行断言（见 test_every_entry_is_fail_closed）。
    """
    src = _read(rel)
    i = src.index('HARNESS_PY=""')
    j = src.index('if [ -z "$HARNESS_PY" ]; then', i)
    return src[i:j]


def _failure_branch(rel):
    src = _read(rel)
    j = src.index('if [ -z "$HARNESS_PY" ]; then')
    k = src.index("\nfi\n", j)
    return src[j:k]


def _sh():
    """一个可用的 POSIX sh；找不到返回 None（不把环境问题伪装成通过）。

    注意 Windows 上 `shutil.which("bash")` 常拿到 System32 的 **WSL bash**，它看不懂 C:/ 路径
    —— 所以显式找 Git-for-Windows 的 MSYS sh（同 tests/test_review_v5.py 的取舍）。
    """
    if os.name == "nt":
        for cand in (r"C:/Program Files/Git/bin/sh.exe", r"C:/Program Files/Git/usr/bin/sh.exe"):
            if os.path.isfile(cand):
                return cand
        return shutil.which("sh")
    return shutil.which("sh") or ("/bin/sh" if os.path.exists("/bin/sh") else None)


def _sh_path(p):
    """Windows 盘符路径 → MSYS 形态（`D:/x` → `/d/x`）。MSYS 只认后者。"""
    if os.name != "nt":
        return p
    p = p.replace("\\", "/")
    return "/" + p[0].lower() + p[2:]


def _write(path, text, mode=None):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


_OPEN_HEREDOC = re.compile(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?\s*$")
_BARE = re.compile(r"(?<![A-Za-z0-9_./-])python\s+(\"[^\"\n]*\"|'[^'\n]*'|\S+)")


def _word_at(src, token, start=0):
    """整词查找（不是子串）—— `python3` 里含有 `python`，子串查找会把顺序判错。"""
    m = re.compile(r"(?<![A-Za-z0-9_.-])%s(?![A-Za-z0-9_.-])" % re.escape(token)).search(src, start)
    return m.start() if m else -1


def _is_prose_at(line, idx):
    """idx 处是否落在引号串里、或位于行内注释之后 —— 那都是**给人看的字**，不是命令。

    为什么需要（实测假红一次）：doctor.sh 的失败文案是
    `fail "找不到可用的 Python 3 解释器（已试：HARNESS_PYTHON / python3 / python / py -3）"`，
    按"后面那个 token 像不像路径"判会把 `/ python / py` 读成一次调用。
    """
    q, i, comment_at = "", 0, -1
    while i < idx:
        c = line[i]
        if q:
            if q == '"' and c == "\\":
                i += 2
                continue
            if c == q:
                q = ""
        elif c in "\"'":
            q = c
        elif c == "#":
            comment_at = i
            break
        i += 1
    return bool(q) or comment_at >= 0


def _strip_heredocs(src):
    """删掉 heredoc **正文**，保留起首行 —— 正文是"生成物内容"，不是本脚本会执行的命令。"""
    out, delim = [], None
    for line in src.splitlines():
        if delim is None:
            m = _OPEN_HEREDOC.search(line)
            if m:
                delim = m.group(1)
            out.append(line)
        elif line.strip() == delim:
            delim = None
    return "\n".join(out)


def _bare_python_lines(rel):
    """本脚本**会真的执行**的裸 python 调用（散文与"打印给人看的命令"不算）。

    判据：不在引号串里、不在行内注释之后、不在 heredoc 正文里（那三类都是给人看的字），
    且紧跟其后的 token 长得像命令（引号 / 选项 / 变量 / 绝对路径 / *.py）。
    """
    hits = []
    for n, line in enumerate(_strip_heredocs(_read(rel)).splitlines(), 1):
        m = _BARE.search(line)
        if not m or _is_prose_at(line, m.start()):
            continue
        tok = m.group(1)
        if not (tok[0] in "\"'-$/" or tok.endswith(".py")):
            continue                       # 散文：`python 可用` / `查 python 环境`
        hits.append("%s:%d: %s" % (rel, n, line.strip()))
    return hits


class TestCandidateMarkerIsSingleSource(unittest.TestCase):
    """标记行是**唯一**的事实来源：一处定义、各处一致（条数见 ENTRIES）。"""

    def test_every_entry_declares_it_exactly_once(self):
        for rel in ENTRIES:
            self.assertIsNotNone(_marker_of(rel),
                                 "%s 没有 %s 标记行（约定在这条入口上失效了）" % (rel, MARK))
            src = _read(rel)
            self.assertEqual(src.count('HARNESS_PY=""'), 1,
                             "%s 里 `HARNESS_PY=\"\"` 出现 %d 次 —— 粘贴两份会让后一份覆盖前一份"
                             % (rel, src.count('HARNESS_PY=""')))

    def test_all_entries_declare_identical_candidates(self):
        got = {rel: _marker_of(rel) for rel in ENTRIES}
        vals = sorted(set(got.values()))
        self.assertEqual(vals, [EXPECTED_CANDIDATES],
                         "各入口候选不一致（%r）—— 约定写在 %d 个文件里，任一处漂移即失效。"
                         "改顺序请**同时**改：这 %d 个标记行 + 本文件的 EXPECTED_CANDIDATES "
                         "+ docs/packaging/INSTALL.md 的说明。"
                         % (got, len(ENTRIES), len(ENTRIES)))

    def test_detection_block_is_byte_identical_everywhere(self):
        base = _detection_block(ENTRIES[0])
        for rel in ENTRIES[1:]:
            self.assertEqual(_detection_block(rel), base,
                             "%s 的探测段与正本（%s）不同 —— 「一次定义」名不副实，"
                             "说明有人只改了一半" % (rel, ENTRIES[0]))

    def test_every_entry_is_fail_closed(self):
        """失败分支必须**出声 + 非 0 退出**（沉默放行 = 门禁整体失效，且没人发现）。"""
        for rel in ENTRIES:
            br = _failure_branch(rel)
            self.assertIn(FAIL_MSG, br, "%s 的解释器缺失分支没讲清原因（不能只给退出码）" % rel)
            self.assertRegex(br, r"exit\s+[12]\b",
                             "%s 的解释器缺失分支必须非 0 退出（fail-closed）" % rel)

    def test_marker_order_is_the_order_actually_tried(self):
        """标记行不是注释，是**契约**：写的顺序 = 源码里真按顺序试的顺序。

        刻意**不**按"标记之后各 token 首次出现的先后"判 —— 实测那样在 install_hooks.sh 上假红：
        标记与循环之间的解释性注释里就写着"用裸 python 直接 command not found"，
        那个 `python` 是散文，不是尝试顺序。要解析的是**真在试的那两处**：
        `for _c in ...` 的候选列表 + `py -3` 回退分支。
        """
        for rel in ENTRIES:
            src = _read(rel)
            m = re.search(r"for _c in (.+?);\s*do", src)
            self.assertIsNotNone(m, "%s：找不到候选循环（改结构请同步更新本锁）" % rel)
            raw = [t.strip("\"'") for t in m.group(1).split()]   # `"${HARNESS_PYTHON:-}"` 带引号
            self.assertTrue(raw[0].startswith("${HARNESS_PYTHON"),
                            "%s：$HARNESS_PYTHON 必须排在候选第一位（安装说明承诺的优先级）" % rel)
            loop = [t for t in raw if not t.startswith("${")]
            fb = re.search(r"\bpy (-3) --version", src)
            self.assertIsNotNone(fb, "%s：找不到 `py -3` 回退分支" % rel)
            attempted = loop + fb.group(0).split()[:2]          # ["py", "-3"]
            self.assertEqual(attempted, _marker_of(rel).split(),
                             "%s：标记行声明 `%s`，源码里真试的是 `%s` —— 标记 ≠ 实现"
                             % (rel, _marker_of(rel), " ".join(attempted)))

    def test_sandbox_entry_inventory_is_explicit(self):
        """枚举面兜底：`sandbox/*.sh` 里每一个都必须在 ENTRIES 或 EXEMPT 里。

        没有这条，新增一支会用 python 的脚本就天然漏网 —— 那正是「清单靠手工列举」
        已经在本仓造成过三次事故的形状（见 sandbox/list_entries.py 的文件头）。
        """
        planned = set(ENTRIES) | set(EXEMPT)
        self.assertIn(ENTRIES[0], planned)
        actual = set(_sandbox_sh()) | {ENTRIES[0]}
        self.assertEqual(sorted(actual - planned), [],
                         "有 sh 入口既没接线也没登记：%s —— 接线（并把文件名加进本文件 "
                         "ENTRIES）、或写进 EXEMPT 说清为什么不接" % sorted(actual - planned))
        self.assertEqual(sorted(planned - actual), [],
                         "本文件登记了不存在的入口：%s" % sorted(planned - actual))
        # 反方向：已结清的欠账必须从 EXEMPT 搬走（防"修好了却还挂在豁免里"这个静默形态）
        for rel, why in sorted(EXEMPT.items()):
            self.assertIsNone(_marker_of(rel),
                              "%s 已经接上了探测段，但它还挂在 EXEMPT 里（%s）—— "
                              "这是好事：把它从 EXEMPT 移到 ENTRIES 即可" % (rel, why))


class TestConventionIsDocumented(unittest.TestCase):
    """约定必须对同事可见 —— 否则他只会在 WSL 上撞一次看不懂的 fail-closed。"""

    def test_install_doc_documents_the_env_var_and_order(self):
        txt = _read("docs/packaging/INSTALL.md")
        self.assertIn("HARNESS_PYTHON", txt,
                      "安装说明没写 HARNESS_PYTHON（解释器特殊的人无处可查）")
        at = txt.index("HARNESS_PYTHON")
        win = txt[at:at + 700]
        pos = -1
        for t in _marker_of(ENTRIES[0]).split():
            p = _word_at(win, t, pos + 1)
            self.assertGreater(p, pos,
                               "安装说明里的候选顺序与标记行（%s）对不上：`%s` 不在它该在的位置"
                               % (EXPECTED_CANDIDATES, t))
            pos = p


class TestNoBarePythonInvocation(unittest.TestCase):
    """结构反向锁：不许再出现本脚本真会执行的裸 `python`（防"只改一半"）。"""

    def test_no_bare_python_invocation_in_entries(self):
        hits = []
        for rel in ENTRIES:
            hits += _bare_python_lines(rel)
        self.assertEqual(hits, [],
                         "以下位置会真的执行裸 `python`（WSL/Ubuntu 上 command not found）：\n  "
                         + "\n  ".join(hits)
                         + "\n改法：换成 $HARNESS_PY（不加引号，\"py -3\" 要按空格拆词）。")


class TestResolverBehaviour(unittest.TestCase):
    """行为锁：真跑一遍，阴阳两向。文档锁不住行为，结构锁也锁不住。"""

    def setUp(self):
        self.sh = _sh()
        if not self.sh:
            self.skipTest("无可用的 POSIX sh（不把环境问题伪装成通过）")
        self.d = tempfile.mkdtemp(prefix="pyconv-")
        # 探针 = 从正本**原样抽出**的探测段 + 一句话回显解析结果。
        # 不复制粘贴：抽的是文件里的真字节，正本一改这里立刻跟着变。
        self.probe = os.path.join(self.d, "probe.sh")
        _write(self.probe, "#!/bin/sh\n" + _detection_block(ENTRIES[0])
               + '\nprintf "%s\\n" "$HARNESS_PY"\n')

    def tearDown(self):
        _rmtree_force(getattr(self, "d", ""))

    def _run(self, script, env, cwd=None):
        return subprocess.run([self.sh, _sh_path(script)], cwd=cwd or self.d, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)

    def test_no_interpreter_means_fail_closed_and_loud(self):
        """**阴**：PATH 里一个候选都没有 → 必须 rc=2 且讲清「找不到可用的 Python 3」。

        这正是 WSL 上的原始症状（原先报的是看不懂的 command not found / 127）。
        """
        empty = os.path.join(self.d, "empty-path")
        os.makedirs(empty, exist_ok=True)
        # HARNESS_PYTHON 必须清空：否则调用者 shell 里的导出会把这条探针静默放行
        env = dict(os.environ, PATH=empty, HARNESS_PYTHON="")
        r = self._run(os.path.join(ROOT, "harness", "hooks-dir", "pre-commit"), env)
        out = (r.stdout or "") + (r.stderr or "")
        self.assertEqual(r.returncode, 2,
                         "没有解释器时必须 fail-closed（rc=2），当前 rc=%s 输出=%r"
                         % (r.returncode, out[:300]))
        self.assertIn(FAIL_MSG, out, "阻断时必须说清原因，当前输出=%r" % out[:300])

    def test_resolves_a_working_python_and_honours_the_env_var(self):
        """**阳**：本机必须解析到**真能跑**的 Python 3；`HARNESS_PYTHON` 必须被尊重。"""
        env0 = {k: v for k, v in os.environ.items() if k != "HARNESS_PYTHON"}
        out0 = (self._run(self.probe, env0).stdout or "").strip()
        self.assertTrue(out0,
                        "本机解析不出任何解释器 —— 那全部入口此刻都在 fail-closed。"
                        "若这台机器确实没装 Python 3，请装；装的是特殊名字，请 export HARNESS_PYTHON")
        # 解析结果必须**真能跑**（这正是本约定的判据；顺便挡住"回显了一个不存在的名字"）
        rv = subprocess.run(out0.split() + ["--version"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(rv.returncode, 0, "解析到 `%s`，但它跑不起来：%r" % (out0, rv.stderr))
        self.assertIn("Python 3", (rv.stdout or "") + (rv.stderr or ""),
                      "解析到 `%s`，但它不是 Python 3" % out0)
        # 显式指定同一个 → 必须还是它（优先级最高；"py -3" 走回退分支也要等价）
        out1 = (self._run(self.probe, dict(env0, HARNESS_PYTHON=out0)).stdout or "").strip()
        self.assertEqual(out1, out0,
                         "HARNESS_PYTHON=%r 时解析成了 %r —— 显式指定必须被尊重" % (out0, out1))

    def test_criterion_is_the_output_not_the_name(self):
        """判据落在 `--version` 的输出上，不是名字上 —— 两向各一次。"""
        stubdir = os.path.join(self.d, "bin")
        os.makedirs(stubdir, exist_ok=True)
        stub = os.path.join(stubdir, "python3")
        env0 = {k: v for k, v in os.environ.items() if k != "HARNESS_PYTHON"}

        def run_with(version_line):
            _write(stub, "#!/bin/sh\necho \"%s\"\n" % version_line, mode=0o755)
            env = dict(env0, HARNESS_PYTHON="",
                       PATH=stubdir + os.pathsep + env0.get("PATH", ""))
            return (self._run(self.probe, env).stdout or "").strip()

        # 阳：自称 Python 3 → 必须被采用（证明它不是"看到 python3 就跳过"）
        self.assertEqual(run_with("Python 3.12.0"), "python3",
                         "一个**真能跑**的 python3 没被采用 —— 约定被改成了按名字判定？")
        # 阴：自称 Python 2 → 必须被挡掉（否则会用一个跑不动门禁的解释器）
        got = run_with("Python 2.7.18")
        self.assertNotEqual(got, "python3",
                            "自称 `Python 2.7.18` 的 python3 被采用了 —— 判据不在输出上。"
                            "（本机若无其它解释器，解析为空即 fail-closed，那是正确结果）")

    def test_requires_311_not_just_any_python3(self):
        """P2-4：判据是 **3.11+**，不是「任意 Python 3」。

        3.10 会被旧判据（`case` 只匹配 `Python 3*`）选中，引擎 `import ...` 当场失败，而失败提示
        只在「找不到任何 Python 3」时才出现 —— 用户看到的是看不懂的 import 错，不是「装 3.11+」。
        这里用**只含 stub 的隔离 PATH**（不依赖本机真解释器）：3.11/3.14 采用，3.10/3.9 拒绝
        （解析为空 ⇒ 上游 fail-closed 阻断，正是期望的安全方向）。
        """
        stubdir = os.path.join(self.d, "only-bin")
        os.makedirs(stubdir, exist_ok=True)
        stub = os.path.join(stubdir, "python3")
        env0 = {k: v for k, v in os.environ.items() if k != "HARNESS_PYTHON"}

        def resolve(version_line):
            _write(stub, "#!/bin/sh\necho \"%s\"\n" % version_line, mode=0o755)
            # PATH 里只有 stub 目录：拒绝即解析为空，不会被本机真解释器兜底掩盖。
            env = dict(env0, HARNESS_PYTHON="", PATH=stubdir)
            return (self._run(self.probe, env).stdout or "").strip()

        for ok in ("Python 3.11.0", "Python 3.11.9", "Python 3.14.0", "Python 3.12.3"):
            self.assertEqual(resolve(ok), "python3",
                             "%s 是 3.11+，却没被采用（探针过严）" % ok)
        for bad in ("Python 3.10.5", "Python 3.9.13", "Python 3.8.0", "Python 3.0.0"):
            self.assertEqual(resolve(bad), "",
                             "%s 低于 3.11，却被采用了（P2-4 未修：会让引擎 import 期炸）" % bad)



if __name__ == "__main__":
    unittest.main()
