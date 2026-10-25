# -*- coding: utf-8 -*-
"""setuptools 钩子：把引擎打进 wheel（`guardrail_engine/_engine/`）。

为什么需要这个文件（评审 2026-10-21 §5.2）
==========================================
`pyproject.toml` 只声明了壳包，打出来的 wheel 里只有四个壳模块
（`__init__ / _shell / cli / mcp`）。于是 `pip install guardrail-engine` 之后仍然要 clone 一份引擎、
再设 `GUARDRAIL_ENGINE_DIR` —— "装完就能跑"是断的。这里把引擎（checks / harness / config /
sandbox / golden-patterns / integrations / docs + VERSION 等）复制进
`build_lib/guardrail_engine/_engine/`，wheel 因此自带引擎；运行期由 `guardrail-engine init`
把它复制到可写目录（`~/.guardrail-engine/engine`）并写指针。台账不应该落在 site-packages 里 —— 升级即丢，
系统级安装还可能只读。

清单与 `guardrail_engine/_shell.py` 里的 `ENGINE_DIRS / ENGINE_FILES / ENGINE_EXCLUDE` **逐字相同**
（构建脚本不能 import 包：构建阶段包还没进 sys.path）。两处漂移由
`tests/packaging/test_init_bundle.py` 用 AST 对拍。

引擎源缺失时**直接失败**，不产出"没有引擎的 wheel"：那正是 §5.2 的形状
（装完看起来成功，跑起来才知道没有引擎）。
"""
import os
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

HERE = os.path.dirname(os.path.abspath(__file__))

ENGINE_DIRS = ("checks", "harness", "config", "sandbox", "golden-patterns",
               "integrations", "docs")
ENGINE_FILES = ("VERSION", "LICENSE", "README.md", "AGENTS.md")
ENGINE_EXCLUDE = ("config/harness.local.toml", "config/harness.local.yaml")

_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
_SKIP_SUFFIX = (".pyc", ".pyo")


class build_py(_build_py):
    """先按常规复制包模块，再把引擎铺进 `guardrail_engine/_engine/`。"""

    def run(self):
        super().run()
        self._copy_engine(os.path.join(self.build_lib, "guardrail_engine", "_engine"))

    def _copy_engine(self, dst_root):
        missing = [d for d in ENGINE_DIRS if not os.path.isdir(os.path.join(HERE, d))]
        missing += [f for f in ENGINE_FILES if not os.path.isfile(os.path.join(HERE, f))]
        if missing:
            raise RuntimeError(
                "引擎源缺失，拒绝产出没有引擎的 wheel：%s（工程根 %s）"
                % ("、".join(missing), HERE))

        if os.path.isdir(dst_root):
            shutil.rmtree(dst_root)
        excluded = {rel.replace("\\", "/") for rel in ENGINE_EXCLUDE}

        for name in ENGINE_DIRS:
            src_root = os.path.join(HERE, name)
            for dp, dns, fns in os.walk(src_root):
                dns[:] = sorted(d for d in dns if d not in _SKIP_DIRS)
                for fn in sorted(fns):
                    if fn.endswith(_SKIP_SUFFIX):
                        continue
                    src = os.path.join(dp, fn)
                    rel = os.path.relpath(src, HERE).replace("\\", "/")
                    if rel in excluded:
                        continue
                    dst = os.path.join(dst_root, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

        for name in ENGINE_FILES:
            dst = os.path.join(dst_root, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(HERE, name), dst)


setup(cmdclass={"build_py": build_py})
