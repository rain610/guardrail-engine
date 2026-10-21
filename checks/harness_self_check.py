# -*- coding: utf-8 -*-
"""agentguard 自检 gate（`harness_self`）—— 工程仓自我体检。

为什么需要：工程仓自己也需要门禁。原先 harness 仓不在 `config/harness.toml` 的受管清单里，
提交时走的是"未注册 → rc=2"分支（fail-closed 没问题，但等于**本仓没有自检**）；
2026-09-14（Codex 评审 v8 后）把它注册成一个正常 gate，顺带把工程自己的护栏变成提交期强制项。

检查项（**全部是仓库级、秒级**，不做全量套件——套件交给 CI）：
  ① 输出型入口必须带 UTF-8 兜底（机器推导清单，见 sandbox/list_entries.py）
  ② 引擎层与打包壳零第三方依赖（AST 扫 harness/ checks/ sandbox/ agentguard/，
     用 sys.stdlib_module_names 判定）
  ③ 分发器完整性：hooks-dir 分发器 + hooks/pre-commit.py 都在位（G2 的周期自检）
  ④ 规则能力边界：单行正则规则必须自报 heuristic，并在自检输出里可见
  ⑤ 配置结构：每个 gate 的 script 必须存在（repo 路径缺失只算 ⚠ —— 同事机器上多数端仓本来就不在）

输出约定与其它 check 脚本一致：❌ 阻断级（rc=1）/ ⚠ 提示（不阻断）/ 通过打印 ✅ 摘要。
"""
import argparse
import ast
import os
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # = agentguard 工程根
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "sandbox"))
sys.path.insert(0, os.path.join(ROOT, "harness"))

# 评审 v13 中 E：run_gate --validate 用它判定「配置里声明了、但没有任何消费方读」的键。
# 本脚本读**整份** config（⑤ 检查 script/repo 引用完整性），但不消费任何单 gate 的策略键，
# 所以是空集：harness_self 若声明 src/mode/line_ending… 一律算空承诺（原先的 `endpoint: pc`
# 就是这么来的 —— 全仓无消费方，coverage 的端是从 script 反推的）。空集是**结论**，不是漏填。
CONFIG_KEYS = frozenset()

problems, warns = [], []

# ② 零第三方依赖的扫描范围：引擎层 + 打包壳（2026-09-16 补 agentguard，
# 与 tests/test_stdlib_only.py 的 SCAN_DIRS 保持一致——两处都用手写清单，别单边改）。
_SCAN_SUBS = ("harness", "checks", "sandbox", "agentguard")


def _load_by_path(rel, name):
    """按**文件路径**加载工程内模块（不写 import 语句）。

    为什么不用 `import list_entries`：引擎层（checks/ + harness/）有一条守卫
    ——tests/test_stdlib_only.py「不得引入任何第三方依赖」——它会把 `import list_entries`
    当成越界依赖（list_entries 在 sandbox/，不属于引擎层目录）。那条守卫是对的：
    引擎不应依赖 sandbox。所以这里显式按路径加载，两步都不越界。
    """
    import importlib.util
    path = os.path.join(ROOT, rel)
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_utf8_fallback():
    """① 机器推导的输出型入口是否都带 UTF-8 兜底（同一坑踩过三次，故用推导而非手抄）。"""
    LE = _load_by_path(os.path.join("sandbox", "list_entries.py"), "_harness_list_entries")
    if LE is None or not hasattr(LE, "entries"):
        # 工程**子集布局**（如安装包只带 checks/harness，没有 sandbox/）属正常：
        # 这项检查依赖完整工程仓 → 记 ⚠ 并说清，不当作工程坏了（CI/完整仓里才会跑）。
        warns.append("① 本布局没有 sandbox/list_entries.py → UTF-8 兜底清单无法机器推导（完整工程仓才有意义）")
        return
    try:
        missing = [rel for rel in LE.entries()
                   if LE.needs_fallback(os.path.join(ROOT, rel))
                   and "reconfigure" not in pathlib.Path(os.path.join(ROOT, rel)).read_text(
                       encoding="utf-8", errors="replace")]
    except Exception as e:
        problems.append("① UTF-8 兜底检查本身失败：%r" % (e,))
        return
    if missing:
        problems.append("① 以下输出型入口缺 UTF-8 兜底（Windows GBK 控制台会整体崩）：%s" % ", ".join(missing))


def check_stdlib_only():
    """② 引擎层与打包壳零第三方依赖（承诺：同事机器上不该因为缺包而跑不起来）。

    2026-09-16（剖析报告 N2）：原先只扫 harness/ checks/ sandbox/ —— `agentguard/`
    （wheel 与 CLI/MCP 的实际分发物）**不在扫描范围**，而 ADR 与 README 都把「零依赖」
    当承诺写。承诺与守卫之间的缺口，补齐即可（与 tests/test_stdlib_only.py 同一范围）。
    """
    import sys as _sys
    local = set()
    for sub in _SCAN_SUBS:
        for dirpath, _, files in os.walk(os.path.join(ROOT, sub)):
            for f in files:
                if f.endswith(".py"):
                    local.add(os.path.splitext(f)[0])
    bad = []
    for sub in _SCAN_SUBS:
        for dirpath, _, files in os.walk(os.path.join(ROOT, sub)):
            if "__pycache__" in dirpath:
                continue
            for f in sorted(files):
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    tree = ast.parse(pathlib.Path(p).read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [a.name.split(".")[0] for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        names = [node.module.split(".")[0]]
                    for n in names:
                        if n in _sys.stdlib_module_names or n in local or n in ("__future__",):
                            continue
                        bad.append("%s: import %s" % (os.path.relpath(p, ROOT).replace("\\", "/"), n))
    if bad:
        problems.append("② 引擎层出现第三方依赖（零依赖承诺被破坏）：%s" % "; ".join(sorted(set(bad))[:8]))


def check_dispatcher():
    """③ 分发器完整性：缺实现文件时提交期门禁会整体 fail-closed 阻断（G2）。"""
    # 评审 P0-1：哨兵 checks/harness_guard.py 被删 = 保护永久关闭，而这里原来没查它（鸡生蛋）。
    for rel in ("harness/hooks-dir/pre-commit", "harness/hooks-dir/commit-msg",
                "harness/hooks/pre-commit.py", "checks/harness_guard.py"):
        if not os.path.isfile(os.path.join(ROOT, rel)):
            problems.append("③ 缺关键门禁文件：%s（提交期门禁会 fail-closed 阻断）" % rel)


def check_guard_call_site():
    """⑥ 哨兵调用方完整性（P0-2）：pre-commit.py 必须真的调用 harness_guard。

    评审发现：③ 只验「哨兵文件存在」，不验「它被调用」——移除调用点 = 保护永久关闭，
    所有现有测试仍绿。调用点存在性 = 保护闭环的最后一条缝。
    """
    pc_path = os.path.join(ROOT, "harness", "hooks", "pre-commit.py")
    if not os.path.isfile(pc_path):
        return  # ③ 已报缺文件，不重复
    try:
        with open(pc_path, encoding="utf-8", errors="replace") as f:
            src = f.read()
    except Exception:
        problems.append("⑥ 无法读取 pre-commit.py 检查哨兵调用点")
        return
    if "harness_guard" not in src:
        problems.append("⑥ pre-commit.py 未调用 harness_guard（保护循环形同虚设）")


def heuristic_rules():
    """标记为启发式的规则（单行正则类）：能挡已知事故，但不保证覆盖多行/别名/生成代码。

    评审 v8 / Codex P2：把"能力边界"从口头承诺变成报告里的一行事实。
    规则既可能是类（`class XxxRule(Rule)`），也可能是 Rule 实例（示例规则集），两种都收。
    规则集来源取 registry.RULESETS（与覆盖度自评同一份登记，不另写清单）。
    """
    import importlib
    from registry import RULESETS
    ids = []
    for mod_name in sorted(set(RULESETS.values())):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        for n in dir(mod):
            o = getattr(mod, n)
            if isinstance(o, type):
                if getattr(o, "heuristic", False) and getattr(o, "id", ""):
                    ids.append(o.id)
            elif isinstance(o, (list, tuple)):
                for it in o:
                    if getattr(it, "heuristic", False):
                        rid = getattr(it, "id", "") or getattr(it, "rule_id", "")
                        if rid:
                            ids.append(rid)
    return sorted(set(ids))


def check_config():
    """⑤ 配置结构：script 必须存在、gate 引用的 repo 键必须在 repos 段里声明（❌）；
    「键在、本机没填路径 / 路径不存在」只 ⚠（同事机器上多数端仓不在本机）。

    2026-09-18（跨平台批次 2）补两条 —— 都是「repos 段下沉到 gitignored 的本机配置」逼出来的，
    而且**两条都是实测踩到的**，不是推演：

      · `harness_self` **不是** repos 段里的条目：它的「仓」就是工程仓自身，由
        `_config._sanity_check` 在 `gate()` / `all_gates()` 里**无条件注入**。本函数读的是
        `load()` 的**原始合并结果**（没有那一步），所以必须自己认它。
        实测：把共享 config 里那行死值（`harness_self: D:/…`）删掉之后，`run_gate.py harness_self`
        当场 rc=1 报「引用未定义的 repo」，而它跑在**每次提交链的最前面** → 全员提交被拦。
        （原先以为删它"零风险"，理由是 `_config` 会覆盖它 —— 覆盖只发生在 gate()/all_gates()，
        而这里读的是 load()。那个前提只对了一半。）

      · `repos` 段整段为空 = 本机**压根没配**（新 clone / CI 干净容器），这时「gate 引用未定义的
        repo」不是配置错，只是还没配。逐个报 ❌ 会让每台新机器开箱就把自己的提交拦死，
        而且报错指向的是共享文件（那里**故意**不写路径）—— 看的人会去改错地方。

    2026-09-18（跨平台批次 7）**判据收口**：批次 2 那条「整段为空才降级」的写法是拿
    「本机有没有机器路径」当判据，而它区分不了「一台机器一个仓都没配」与「同事只配了自己
    那一个仓」—— 后者是最常见的形态（INSTALL.md 明写「只写你机器上确实存在的那几个仓」），
    后果是另外 32 个 gate 全被判 ❌ → rc=1 → 提交链 fail-closed rc=2，**而他自己的代码一个
    字节都没被检查过**（实测报告：reports/_batch6_partial_local_finding.txt）。
    现在改成按**键**判，两根判据各归各位：
      · 键**不在** repos 段 → ❌（共享配置从未声明过这个名字：拼错 / 新 gate 漏登记），
        措辞随之改为「⑤ gate <X> 引用了 repos 段没有声明的键：<键名>」（逐条报、不汇总 ——
        理由见下面那段代码的注释：它指向的是一个具体的键名，逐条才看得出该改哪一行）；
      · 键在、值空或路径不存在 → ⚠（「本机没这个仓」，与「本机一条都没配」同一支）。
    这一支不再需要「本机配了几条」这个中间判据，`_machine` 随之删除。
    """
    try:
        import _config as C
        cfg = C.load()
    except Exception as e:
        problems.append("⑤ 读取 config/harness.toml 失败：%r" % (e,))
        return
    gates = cfg.get("gates") or {}
    repos = cfg.get("repos") or {}
    if not gates:
        problems.append("⑤ 配置里没有任何 gate（config/harness.toml 结构异常）")
    _undeclared = []
    for name, g in sorted(gates.items()):
        script = (g or {}).get("script") or ""
        if script and not os.path.isfile(os.path.join(ROOT, script)):
            problems.append("⑤ gate %s 的 script 不存在：%s" % (name, script))
        repo_key = (g or {}).get("repo")
        if not repo_key or repo_key == "harness_self":
            continue                       # 见 docstring 第一条：它不来自 repos 段
        if repo_key not in repos:
            _undeclared.append(name)
        elif not repos[repo_key] or not os.path.isdir(repos[repo_key]):
            warns.append("gate %s 的 repo 路径本机未配或不存在（%s）——不阻断，注册表在别的机器用"
                         % (name, repos[repo_key] or "(留空)"))
    if _undeclared:
        # 这条**不汇总**：它指向的是共享文件里一个具体的键名，逐条报才看得出改哪一行。
        # 数量上它天然很小（拼错一个 / 新 gate 漏登记一个），不存在批次 2 担心的刷屏。
        for name in _undeclared:
            problems.append("⑤ gate %s 引用了 repos 段没有声明的键：%s"
                            % (name, (gates[name] or {}).get("repo")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None)
    ap.add_argument("files", nargs="*")
    ap.parse_args()

    check_utf8_fallback()
    check_stdlib_only()
    check_dispatcher()
    check_guard_call_site()
    check_config()

    print("harness 自检 [strict]：UTF-8 兜底 / 零依赖 / 分发器 / 能力边界 / 配置结构")
    _h = heuristic_rules()
    if _h:
        print("  ℹ 启发式（单行正则）规则 %d 条：%s —— 命中即提示，多行/别名/生成代码等边界建议人工判定"
              % (len(_h), ", ".join(_h[:6])))
    for p in problems:
        print("  ❌ %s" % p)
    for w in warns[:10]:
        print("  ⚠ %s" % w)
    if len(warns) > 10:
        print("  ⚠ ...另有 %d 条同类提示" % (len(warns) - 10))
    if problems:
        print("✗ harness 自检发现 %d 个阻断级问题（修复后再提交）" % len(problems))
        return 1
    print("✅ harness 自检通过（%d 条提示）" % len(warns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
