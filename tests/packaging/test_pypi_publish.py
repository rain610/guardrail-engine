# -*- coding: utf-8 -*-
"""PyPI 发布链的契约锁（2026-10-25）。

三个会「看起来配好了、发布时才失败」的点，各锁一条：

  ① **sdist 必须带全引擎**：`setup.py` 的 `build_py` 在构建时从仓库根复制引擎；
     sdist 少一棵子树，`pip install <sdist>` 就会产出没有引擎的 wheel —— 正是评审
     §5.2 的形状（装完看起来成功，跑起来才知道没有引擎）。
  ② **发布走 OIDC 可信发布**：`publish` job 必须有 `id-token: write` 与 `environment: pypi`；
     出现 `password:` / `PYPI_API_TOKEN` 说明有人退回了长期令牌。
  ③ **元数据不能再漂回旧名**：发行名 / 两个导入包 / 两条命令都要在 `pyproject.toml` 里。
"""
import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETUP = os.path.join(ROOT, "setup.py")
MANIFEST = os.path.join(ROOT, "MANIFEST.in")
RELEASE = os.path.join(ROOT, ".github", "workflows", "release.yml")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _consts(path, names):
    """取模块顶层 `NAME = (...)` 的字面量（AST 不执行 setup.py）。"""
    tree = ast.parse(_read(path), filename=path)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id in names:
                out[tgt.id] = ast.literal_eval(node.value)
    return out


def _manifest_rules():
    rules = []
    for raw in _read(MANIFEST).splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            rules.append(line)
    return rules


def _job(workflow, name):
    """取出 `  <name>:` 这一 job 的正文（到下一个同级 job 或文件尾）。"""
    m = re.search(r"(?ms)^  %s:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)" % re.escape(name), workflow)
    return m.group(1) if m else ""


class TestSdistCarriesTheWholeEngine(unittest.TestCase):
    def test_manifest_covers_every_engine_dir_and_file(self):
        setup = _consts(SETUP, {"ENGINE_DIRS", "ENGINE_FILES"})
        self.assertTrue(setup.get("ENGINE_DIRS") and setup.get("ENGINE_FILES"),
                        "setup.py 的引擎清单被改名/删掉了，本锁会空转")
        rules = _manifest_rules()
        for d in setup["ENGINE_DIRS"]:
            self.assertTrue(any(r.startswith("recursive-include %s " % d) for r in rules),
                            "MANIFEST.in 漏了 `recursive-include %s`：从 sdist 打出的 wheel "
                            "会缺这棵子树，装完没有引擎（评审 §5.2）" % d)
        for f in setup["ENGINE_FILES"]:
            self.assertIn("include %s" % f, rules,
                          "MANIFEST.in 漏了 `include %s`：sdist 里没有它，从 sdist 构建会直接失败" % f)

    def test_local_config_is_not_shipped(self):
        rules = _manifest_rules()
        for name in ("config/harness.local.toml", "config/harness.local.yaml"):
            self.assertIn("exclude %s" % name, rules,
                          "MANIFEST.in 没排除 %s：本机仓库路径会被打进 sdist" % name)


class TestPublishUsesTrustedPublishing(unittest.TestCase):
    def setUp(self):
        self.txt = _read(RELEASE)

    def test_publish_job_exists_and_runs_after_release(self):
        body = _job(self.txt, "publish")
        self.assertTrue(body, "release.yml 里没有 `publish` job：tag 发布不会上传 PyPI")
        self.assertIn("needs: release", body,
                      "publish 必须在 release 之后跑（tag 校验 / 测试 / 冒烟都过了才上传）")

    def test_publish_job_uses_oidc(self):
        body = _job(self.txt, "publish")
        self.assertIn("id-token: write", body,
                      "publish job 没开 id-token: write：PyPI 可信发布拿不到 OIDC 凭证")
        self.assertIn("environment:", body)
        self.assertIn("pypi", body,
                      "publish job 没挂 `environment: pypi`：PyPI 端登记的 environment 对不上")
        self.assertIn("pypa/gh-action-pypi-publish", body,
                      "publish job 没有用官方上传 action")

    def test_no_long_lived_pypi_token(self):
        body = _job(self.txt, "publish")
        self.assertNotIn("PYPI_API_TOKEN", body,
                         "publish job 引入了长期 PyPI token：本仓约定用 OIDC 可信发布")
        self.assertNotRegex(body, r"(?m)^\s*password:\s*\S",
                            "publish job 里出现了 `password:`：可信发布不需要任何 token")

    def test_one_build_feeds_github_and_pypi(self):
        """单一构建点：build 产出 dist/pypi；release / publish 只下载，不重建。"""
        build = _job(self.txt, "build")
        release = _job(self.txt, "release")
        publish = _job(self.txt, "publish")

        self.assertIn("python -m build", build,
                      "build job 必须用 `python -m build`（先打 sdist 再从 sdist 打 wheel）；"
                      "只打 `--wheel` 就测不出 MANIFEST 漏带引擎")
        self.assertIn("twine check --strict", build,
                      "build job 缺 `twine check`：元数据坏了会到上传那一步才失败")
        self.assertIn("pack_release.py --wheel", build,
                      "build job 必须用 `--wheel` 复用同一份 wheel 组装内部包，不能重新构建")
        self.assertIn("actions/upload-artifact", build,
                      "build job 没有上传 artifact：release/publish 只能各自重建")
        self.assertIn("!dist/install-package/_wheel*/", build,
                      "artifact 又把打包器的 _wheel 构建缓存收进去了：同一份 wheel 会在"
                      "artifact 里出现第三份副本，白占体积与上传时间")
        self.assertIn("include-hidden-files: true", build,
                      "artifact 默认不传隐藏文件：stage 里的 .mcp.json 与 .claude-plugin/ "
                      "会缺，release job 的 verify_release 会以“产物不完整”告终")
        self.assertIn("dist/pypi", build,
                      "build job 没把公开发布物收进 dist/pypi：publish 容易把内部 stage 里的轮子重复上传")

        for name, body in (("release", release), ("publish", publish)):
            self.assertIn("actions/download-artifact", body,
                          "%s job 没有下载 build 的 artifact" % name)
            self.assertNotIn("python -m build", body,
                             "%s job 又跑了一次 `python -m build`："
                             "GitHub Release 与 PyPI 会变成两份不同的字节" % name)

        self.assertIn("dist/pypi/*.whl", release,
                      "GitHub Release 没附上 PyPI 的同一个 wheel")
        self.assertIn("dist/pypi/*.tar.gz", release,
                      "GitHub Release 没附上 PyPI 的同一个 sdist")
        self.assertIn("packages-dir: dist/pypi", publish,
                      "publish 必须只上传 dist/pypi，避免把内部安装包里的轮子重复上传")


class TestPackageMetadata(unittest.TestCase):
    def test_distribution_and_import_names(self):
        import tomllib
        with open(PYPROJECT, "rb") as f:
            data = tomllib.load(f)
        project = data["project"]
        self.assertEqual(project["name"], "guardrail-engine")
        self.assertEqual(project["scripts"]["guardrail-engine"], "guardrail_engine.cli:main")
        self.assertEqual(project["scripts"]["agentguard"], "agentguard.cli:main",
                         "迁移期别名 `agentguard` 被删了：老脚本会在 v0.3 之前就断")
        self.assertEqual(sorted(data["tool"]["setuptools"]["packages"]),
                         ["agentguard", "guardrail_engine"],
                         "wheel 必须同时带主包与迁移期兼容壳")
        self.assertEqual(project["readme"]["file"], "README.en.md",
                         "PyPI 长描述应指向英文版（仓库 README 仍是中文主文档）")
        self.assertEqual(project["readme"]["content-type"], "text/markdown")
        self.assertEqual(project["requires-python"], ">=3.11")


if __name__ == "__main__":
    unittest.main()
