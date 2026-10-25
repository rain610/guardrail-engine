"""零第三方依赖守卫（2026-09-12 新增，为 CI／同事机器接入而加）。

保护工程核心承诺：**纯 Python 标准库、零第三方依赖**。
一旦有人在 checks/ 或 harness/ 里 `import requests` / `import yaml`，任何干净机器、
CI 容器、同事新装的 Python 都会当场跑不起来——而这类破坏在开发机上往往"我这儿能跑"，
直到别人接入才暴露。本守卫把这条承诺变成可执行断言。

扫描范围：checks/*.py、harness/*.py、harness/hooks/*.py、harness/agents/*.py、
          guardrail_engine/*.py（打包壳——与引擎同为「同事机器上必须开箱能跑」）
          agentguard/*.py（迁移期兼容壳，v0.3 移除）
判定规则：模块名 ∈ 标准库（sys.stdlib_module_names，3.10+）或 ∈ 本工程模块（同目录 .py 文件名）
          → 放行；名单外的再看模块来源，落在 site-packages/dist-packages 或定位不到 → 判为第三方。
          第二步不依赖名单完整性，因而在 3.9 及更早解释器上同样可信。
"""
import ast
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCAN_DIRS = [
    os.path.join(ROOT, "checks"),
    os.path.join(ROOT, "harness"),
    os.path.join(ROOT, "harness", "hooks"),
    os.path.join(ROOT, "harness", "agents"),
    os.path.join(ROOT, "guardrail_engine"),  # 打包壳（ADR-0001：壳也零第三方依赖）
    os.path.join(ROOT, "agentguard"),        # 迁移期兼容壳（v0.3 移除）
]

# sys.stdlib_module_names 是 3.10+ 才有的权威清单；旧解释器上用本回落表
# （= 本引擎实际用到的全部标准库模块，见 tests 里的零依赖断言）。
# 回落表保守：只列引擎在用的，宁可漏判"该模块是标准库"也不误报第三方。
_FALLBACK_STDLIB = {
    "dataclasses", "enum", "glob", "importlib", "inspect", "json", "os",
    "pathlib", "re", "subprocess", "sys", "typing", "urllib", "io",
    "shutil", "tempfile", "time", "datetime", "collections", "functools",
    "unittest", "ast", "textwrap", "argparse", "hashlib", "traceback",
}


def _stdlib_set():
    return set(getattr(sys, "stdlib_module_names", ())) or _FALLBACK_STDLIB


def _local_modules():
    """本工程内可直接 import 的模块名（.py 文件名 + 包目录名）。

    包目录（带 `__init__.py`）本身也是合法导入名：兼容壳 `agentguard/` 里写的是
    `from guardrail_engine import ...`，只收文件名会把它误判成第三方（CI 的同类
    扫描器 2026-10-25 正是这样红过一次）。
    """
    names = set()
    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".py"):
                names.add(fn[:-3])
        if os.path.isfile(os.path.join(d, "__init__.py")):
            names.add(os.path.basename(d))
    return names


def _imported_modules(source):
    """返回源码里出现的顶层模块名集合（相对 import 不算）。"""
    out = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:      # level>0 是包内相对导入
                out.add(node.module.split(".")[0])
    return out


def _third_party(source, local):
    """第三方 import 判定：标准库名单快速排除 + 来源路径权威复核。

    只靠名单会漏（3.9 无 sys.stdlib_module_names，回落表再全也会漏 getpass/socket 这类），
    所以对名单外的模块再问一句「它从哪来的」：解析得到 spec 且来源落在 site-packages/dist-packages
    → 第三方（必拦）；解析不到 spec → 也无法定位，同样报（很可能是没装的第三方）。
    来源不在 site-packages 的（标准库/内建/本工程）放行——版本无关，不依赖名单完整性。
    """
    std = _stdlib_set()
    out = []
    for m in sorted(_imported_modules(source)):
        if m in local or m in std:
            continue
        spec = None
        try:
            spec = importlib.util.find_spec(m)
        except Exception:
            spec = None
        origin = getattr(spec, "origin", None) or ""
        # 已知边界（2026-09-12 第二轮评审 N8）：以 editable/开发模式安装的包，源码位于项目
        # 目录内、来源路径不含 site-packages → 本判定会把它当"自研/标准库"放行。取舍：
        # 宁可漏判也不误报自有模块（误报会让门禁被绕过）；真要严格，可改用
        # importlib.metadata.distribution(m).files 或维护显式黑名单。
        if "site-packages" in origin or "dist-packages" in origin:
            out.append(f"{m}（来自 site-packages）")
        elif spec is None:
            out.append(f"{m}（无法定位，疑似未安装的第三方）")
        # 能找到、且不在 site-packages → 标准库/内建/本环境自带，放行
    return sorted(out)


class TestStdlibOnly(unittest.TestCase):
    def test_no_third_party_imports_in_engine(self):
        """引擎层（checks/ + harness/）与打包壳（agentguard/）不得引入任何第三方依赖。"""
        # 两向探针：目录不存在时下面的循环会 `continue` —— 少扫一个目录就是少一道保险，
        # 而锁还是绿的。SCAN_DIRS 是手写的，所以这里先证明它指的目录真的都在。
        missing = [d for d in SCAN_DIRS if not os.path.isdir(d)]
        self.assertEqual(missing, [], "SCAN_DIRS 里有不存在的目录（本锁会静默少扫）：%s" % missing)
        local = _local_modules()
        offenders = []
        for d in SCAN_DIRS:
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".py"):
                    continue
                p = os.path.join(d, fn)
                with open(p, encoding="utf-8", errors="replace") as f:
                    src = f.read()
                bad = _third_party(src, local)
                if bad:
                    offenders.append(f"{os.path.relpath(p, ROOT)}: {', '.join(bad)}")
        self.assertEqual(offenders, [],
                         "引擎层出现第三方依赖（破坏'纯标准库'承诺，干净机器会跑不起来）:\n  "
                         + "\n  ".join(offenders))

    def test_package_dirs_are_local_import_names(self):
        """包目录本身必须算「本工程模块」——兼容壳 import 主包不能报第三方。"""
        local = _local_modules()
        for pkg in ("guardrail_engine", "agentguard"):
            self.assertIn(pkg, local, "包目录 %s 没进本工程模块名单" % pkg)
        self.assertEqual(_third_party("from guardrail_engine import cli\n", local), [])

    def test_guard_actually_detects_violation(self):
        """守卫自检：合成源码里的第三方 import 必须被抓到（防守卫自身失效）；
        标准库/本工程模块一个都不许报（防误报把套件变噪声）。"""
        local = _local_modules()
        r = _third_party("import definitely_not_installed_pkg_xyz\n", local)
        self.assertEqual(len(r), 1, r)
        self.assertIn("definitely_not_installed_pkg_xyz", r[0])
        self.assertEqual(_third_party("import os, json\nimport subprocess\n", local), [])
        # 名单外的标准库（3.9 无 sys.stdlib_module_names 时靠来源复核兜住）
        self.assertEqual(_third_party("import getpass, socket, threading\n", local), [])
        self.assertEqual(_third_party("from rule_base import Severity\n", local), [])
        self.assertEqual(_third_party("from . import sibling\n", local), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
