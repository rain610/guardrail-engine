# -*- coding: utf-8 -*-
r"""前置检查回归锁（2026-09-15 用户要求补）

背景：包元数据原先写 `Requires-Python: >=3.8`，而《安装说明》写 3.11+，引擎代码还用到 3.9+ 的
`pathlib.is_relative_to` —— 三处口径不一致，且**安装流程没有任何版本预检**：同事若用旧 Python，
会在解压安装后撞 AttributeError（被当成"包坏了"）。

本锁钉住三件事：① 每个用户入口都有 `_require_python()` 且低版本时报人话 + 退出码 2；
② 元数据与文档口径一致（>=3.11）；③ 安装说明里确实写了 git / node 的必需性。
（①原先写死"四个用户入口"—— 2026-09-18 随包新增 `bootstrap.py` 时那句就过期了。
名单以 `ENTRIES` 为准，注释不再写数。）
"""
import contextlib
import importlib.util
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENTRIES = [("bootstrap", "sandbox/bootstrap.py"),
           ("fix_paths", "sandbox/fix_paths.py"),
           ("uninstall", "sandbox/uninstall.py"),
           ("cli", "agentguard/cli.py"),
           ("mcp", "agentguard/mcp.py")]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location("preflight_" + name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPythonPreflight(unittest.TestCase):
    def test_every_entry_rejects_old_python(self):
        for name, rel in ENTRIES:
            mod = _load(name, rel)
            self.assertTrue(hasattr(mod, "_require_python"),
                            "%s 缺少 _require_python 前置检查（旧 Python 会撞 AttributeError）" % rel)
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                with self.assertRaises(SystemExit) as cm:
                    mod._require_python((3, 9))
            self.assertEqual(cm.exception.code, 2, "%s：版本不足应以退出码 2 结束" % rel)
            self.assertIn("3.11", buf.getvalue(), "%s：报错必须说清需要什么版本" % rel)
            mod._require_python((3, 11))      # 达标不应抛
            mod._require_python((3, 13))

    def test_metadata_matches_docs(self):
        """只认**赋值行**，别看全文：pyproject 的注释里会提到旧值（"别再写 >=3.8"），
        全文匹配会把自己的说明文字当成违规（本锁第一版就这么假红过）。"""
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
            py = fh.read()
        keys = [l.strip() for l in py.splitlines() if l.strip().startswith("requires-python")]
        self.assertEqual(keys, ['requires-python = ">=3.11"'],
                         "requires-python 必须与《安装说明》/入口守卫一致（>=3.11）")

    def test_install_doc_states_all_prerequisites(self):
        """安装说明必须同时写清 python / git / node 三样（node 缺失会拦提交，必须说）。"""
        with open(os.path.join(ROOT, "docs", "packaging", "INSTALL.md"), encoding="utf-8") as fh:
            txt = fh.read()
        for kw in ("3.11", "git", "node", "HARNESS_NODE_REQUIRED"):
            self.assertIn(kw, txt, "安装说明缺 %s（同事会踩坑）" % kw)


if __name__ == "__main__":
    unittest.main()
