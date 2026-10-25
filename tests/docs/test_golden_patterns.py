# -*- coding: utf-8 -*-
"""黄金范式库的闭环锁：规则 → `see` → 命中输出 `📖 See:` → 可照抄骨架。

锁五组事（全部只读）：

  甲、指针链：每条带 `see` 的示例规则，指向的仓库相对文档真实存在，且文档里点了规则 ID；
  乙、索引：`patterns/` 与 `endtoend/` 下的每份范式都在 `index.md` 登记，索引也不许指空；
  丙、内容：每份范式都有「适用 / 骨架 / 照抄 / 坑」四类章节，防止退化成一句口号；
  丁、样板自证：`reference/` 下的正面样板必须真的能过示例 gate（不是文档里说说）；
  戊、边界：范式里不出现机器相关路径，也不教人怎么写豁免。

为什么要有甲/丁两组：`see` 盖章与 `format_finding()` 的通用锁在 `tests/test_checks.py`
（M133/M134），它们只看机制；这里从真实入口跑示例 gate，确认指针**真的**送到了使用者眼前，
而且顺着指针读到的样板自己不会再次被门禁拦下。
"""
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "checks", "example_code_check.py")
INDEX = os.path.join(ROOT, "golden-patterns", "index.md")
PATTERNS_ROOT = os.path.join(ROOT, "golden-patterns")
REFERENCE_DIR = os.path.join(PATTERNS_ROOT, "reference")
CHECK_DIR = os.path.join(ROOT, "checks")

#: 机器相关路径：盘符（D:/ C:\）、常见家目录（/home/ /Users/ /mnt/）、UNC。
_MACHINE_PATH = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
    r"|(?:^|[\s(\"'`])/(?:home|Users|mnt)/"
    r"|\\\\[A-Za-z0-9._-]+\\"
)

#: 索引里登记的范式路径（只认 patterns/ 与 endtoend/ 下的 .md）。
_INDEX_PATH = re.compile(r"`((?:patterns|endtoend)/[^`]+\.md)`")

TEXT_SUFFIXES = (".md", ".js", ".vue", ".cs")


def _read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _pattern_docs():
    """patterns/ 与 endtoend/ 下的正文；templates/ 是元模板，不参与索引登记。"""
    docs = []
    for base in ("patterns", "endtoend"):
        for dirpath, _, filenames in os.walk(os.path.join(PATTERNS_ROOT, base)):
            for name in filenames:
                if name.endswith(".md"):
                    docs.append(os.path.join(dirpath, name))
    return sorted(docs)


def _rel(path):
    return os.path.relpath(path, PATTERNS_ROOT).replace(os.sep, "/")


def _all_pattern_files():
    for dirpath, _, filenames in os.walk(PATTERNS_ROOT):
        for name in filenames:
            if name.endswith(TEXT_SUFFIXES):
                yield os.path.join(dirpath, name)


def _reference_files():
    files = []
    for dirpath, _, filenames in os.walk(REFERENCE_DIR):
        for name in filenames:
            if name.endswith((".js", ".vue", ".cs")):
                files.append(os.path.relpath(os.path.join(dirpath, name), ROOT)
                             .replace(os.sep, "/"))
    return sorted(files)


def _example_rules():
    sys.path.insert(0, CHECK_DIR)
    import example_ruleset as E
    return E.EXAMPLE_RULES


class TestGoldenPatternPointers(unittest.TestCase):
    def test_every_rule_pointer_resolves_and_names_its_rule(self):
        """`see` 必须是存在的仓库相对文档，且文档里点了规则 ID（防指错 / 复制粘贴）。"""
        pointers = []
        for rule in _example_rules():
            see = getattr(rule, "see", "")
            if not see:
                continue
            pointers.append(rule.id)
            self.assertFalse(os.path.isabs(see), "%s 的 see 是绝对路径：%r" % (rule.id, see))
            self.assertNotIn("\\", see, "%s 的 see 用了反斜杠：%r" % (rule.id, see))
            self.assertTrue(see.startswith("golden-patterns/"),
                            "%s 的 see 不在 golden-patterns/ 下：%r" % (rule.id, see))
            target = os.path.join(ROOT, *see.split("/"))
            self.assertTrue(os.path.isfile(target),
                            "%s 的 see 指向不存在的文件：%s" % (rule.id, see))
            self.assertIn(rule.id, _read(target),
                          "%s 指向的范式没提这条规则：%s" % (rule.id, see))
        self.assertGreaterEqual(len(pointers), 2,
                                "带 see 的示例规则少于 2 条，闭环演示会退化成单点：%s" % pointers)

    def test_golden_pattern_files_carry_no_machine_specific_paths(self):
        # 先证明判据自己认得出坏路径，否则下面的循环在正则写坏时恒绿。
        self.assertIsNotNone(_MACHINE_PATH.search("见 D:/team-vault/x.md"), "判据没认出盘符路径")
        self.assertIsNotNone(_MACHINE_PATH.search("见 /home/rain/x.md"), "判据没认出家目录路径")
        files = sorted(_all_pattern_files())
        self.assertGreaterEqual(len(files), 15, "范式目录里的文件少得可疑：%s" % files)
        bad = []
        for path in files:
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                m = _MACHINE_PATH.search(line)
                if m:
                    bad.append("%s:%d %r" % (_rel(path), lineno, m.group(0)))
        self.assertEqual(bad, [], "范式里出现机器相关路径（换台机器就断）：\n%s" % "\n".join(bad))

    def test_index_is_the_entry_point(self):
        text = _read(INDEX)
        self.assertIn("📖 See:", text, "索引没解释命中的指针长什么样")
        self.assertIn("patterns/cross-cutting/logging.md", text, "索引没链到日志范式")
        self.assertIn("endtoend/task-management.md", text, "索引没链到端到端范本")
        self.assertIn("NO-CONSOLE", text, "索引没标出示例对接的规则")
        self.assertIn("NO-HARDIP", text, "索引没标出配置范式对接的规则")


class TestGoldenPatternLibrary(unittest.TestCase):
    def test_index_lists_every_pattern_doc(self):
        """目录 ↔ 索引双向锁：漏登记、指空都报红（旧工程的同类锁漂过一次）。"""
        actual = {_rel(p) for p in _pattern_docs()}
        listed = set(_INDEX_PATH.findall(_read(INDEX)))
        self.assertGreaterEqual(len(actual), 8, "范式正文少得可疑：%s" % sorted(actual))
        self.assertEqual(sorted(actual - listed), [],
                         "这些范式没在 index.md 登记（agent 找不到）：%s" % sorted(actual - listed))
        self.assertEqual(sorted(listed - actual), [],
                         "index.md 指向不存在的范式（指路指到空处）：%s" % sorted(listed - actual))

    def test_every_pattern_doc_has_required_sections(self):
        """每份范式必须有：适用 / 可照抄代码 / 照抄要点 / 常见坑 —— 缺一段就是口号。"""
        problems = []
        for path in _pattern_docs():
            text = _read(path)
            name = _rel(path)
            if "## 适用" not in text:
                problems.append("%s: 缺「## 适用」" % name)
            if "```" not in text:
                problems.append("%s: 没有代码块" % name)
            if not re.search(r"(?m)^## .*照抄", text):
                problems.append("%s: 缺「照抄要点/顺序」章节" % name)
            if not re.search(r"(?m)^## .*坑", text):
                problems.append("%s: 缺「常见坑」章节" % name)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_patterns_do_not_teach_waivers(self):
        """范式是参照系，不是绕过门禁的许可；正文里不许出现豁免写法。"""
        bad = [_rel(p) for p in _pattern_docs() if "harness-ok" in _read(p).lower()]
        self.assertEqual(bad, [], "这些范式在教人写豁免（应与规则一起修，而不是绕过）：%s" % bad)


class TestReferenceSamples(unittest.TestCase):
    def test_reference_positive_samples_pass_the_example_gate(self):
        """范式里的正面样板自己也要过门禁 —— 否则它就是在教一种会被拦的写法。"""
        files = _reference_files()
        self.assertGreaterEqual(len(files), 10, "参考实现文件少得可疑：%s" % files)
        r = subprocess.run(
            [sys.executable, SCRIPT, "--repo", ROOT, "--gate", "example"] + files,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        self.assertEqual(r.returncode, 0,
                         "参考样板被示例 gate 拦下（正面样板必须自证）：\n%s%s"
                         % (r.stdout, r.stderr))


class TestExampleGateCarriesThePointer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _run(self, content, name="bad.js"):
        path = os.path.join(self.tmp.name, name)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return subprocess.run(
            [sys.executable, SCRIPT, "--repo", self.tmp.name, "--gate", "example", name],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def _run_bytes(self, payload, name="bad.js"):
        path = os.path.join(self.tmp.name, name)
        with io.open(path, "wb") as f:
            f.write(payload)
        return subprocess.run(
            [sys.executable, SCRIPT, "--repo", self.tmp.name, "--gate", "example", name],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)

    def test_blocked_output_carries_the_pointer(self):
        r = self._run('console.log("x")\n')
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        out = r.stdout
        self.assertIn("[NO-CONSOLE]", out, "拦截理由必须是规则命中：\n%s" % out)
        self.assertIn("     📖 See: golden-patterns/patterns/cross-cutting/logging.md", out,
                      "命中后没把范式路径送到眼前：\n%s" % out)
        self.assertLess(out.index("FIX:"), out.index("📖 See:"),
                        "指针应排在 FIX 之后：\n%s" % out)

    def test_rule_without_a_pointer_prints_no_see_line(self):
        # EOL-UNIFIED 没有声明 see，输出里不许凭空长出指针。
        r = self._run_bytes(b"var a = 1\r\nvar b = 2\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("[EOL-UNIFIED]", r.stdout, "反向锁的前提没成立：\n%s" % r.stdout)
        self.assertNotIn("📖 See:", r.stdout,
                         "EOL-UNIFIED 没声明 see，却凭空出现了指针：\n%s" % r.stdout)


if __name__ == "__main__":
    unittest.main()
