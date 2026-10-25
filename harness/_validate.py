# -*- coding: utf-8 -*-
"""配置体检（`--validate`）与配置「空承诺」检查。

从 `run_gate.py` 拆出：这一组只读配置与检查脚本声明，不写台账、不发通知、不跑子进程，
与调度器的模块级状态（LOG_DIR / LOG_FILE / NOTIFY_STATE）无关。
"""
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HARNESS = os.path.dirname(os.path.abspath(__file__))  # = guardrail-engine/harness/
ROOT = os.path.dirname(HARNESS)
CHECKS = os.path.join(ROOT, "checks")

def _config_gates():
    """统一取 gate 配置。返回 `(gates, err)`：err 非空 = **配置读不出来**。

    2026-09-16（剖析报告 N3）：此前 base 配置解析崩溃（同级列表/映射混排等）会裸
    AttributeError/TypeError 逃出本文件，Python 以 **rc=1** 退出 —— 而 rc=1 的语义是
    「代码不合规」。钩子/CI 会把"配置写坏了"读成"门禁拦截"，人去改代码，真问题浮不出来。
    比 fail-open 更坏：它给的是一个**方向相反的确定结论**。
    现在统一转 rc=2（配置错误），与 `--validate` 既有语义、与未登记 gate 名同一条路。
    """
    sys.path.insert(0, HARNESS)
    from _config import all_gates, ConfigError
    try:
        return all_gates(), None
    except ConfigError as e:
        return None, str(e)


def _norm_rel(p):
    """脚本路径归一成「相对工程根」的 posix 形式，供 yaml ↔ registry 两处比对。

    两侧写法可能不同（绝对/相对、`\\`/`/`、多余 `./`），但指向同一文件就不该报错 ——
    这里只归一形式，不做存在性判断（存在性由上面第 1) 项单独管）。
    """
    p = (p or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        try:
            p = os.path.relpath(p, ROOT)
        except ValueError:
            pass
    return os.path.normpath(p).replace(os.sep, "/")


# ==================================================== 配置「空承诺」检查（评审 v13 中 E）
# 引擎/入口层消费的 gate 级配置键（与端无关，对 34 个 gate 一体适用）：
#   repo → _repo_path  run_gate 从 repos 段解析出 _repo_path 注入给检查脚本
#   enabled            run_gate / _config 决定这个 gate 跑不跑
#   script             run_gate / registry_loader 决定跑哪个脚本
#   run_on             harness/hooks/pre-commit.py、harness/agents/auto-gate.py 决定何时跑
#   legacy_baseline / disabled_rules
#                      rule_base 里按 gate 名（其次按 repo 反查）取用，三端共用同一实现
# 端脚本**自己**读的键不在这里，写在各自文件顶部的 CONFIG_KEYS 里（键与读者相邻）。
ENGINE_CONFIG_KEYS = frozenset({
    "repo", "_repo_path", "enabled", "script", "run_on", "legacy_baseline", "disabled_rules",
})


def _script_config_keys(script_rel):
    """取该端检查脚本自己声明的「我读哪些 gate 级配置键」（脚本里的 CONFIG_KEYS 常量）。

    为什么必须有这道检查（评审 v13 中 E）：配置表里写一个键、却**没有任何消费方读它**，
    是"配置项白写"里最坏的一种 —— 它没有任何症状：写的人以为已经管住了，
    门禁也照常跑、照常绿。实测（2026-09-16）：
      · 12 个 PC/H5 gate 写着 `encoding: utf-8-bom`，而 encoding 只有 backend_layer_check 读；
      · 11 个 backend gate 写着 `mode: loose`，而后端根本不读 mode（它按定向/全量自己推，
        定向提交实际按 strict 拦）—— 声明与事实**相反**，且看不出来；
      · harness_self 写着 `endpoint: pc`，全仓无消费方（coverage 是从 script 反推端的）。
    靠人眼在 34 个 gate × 十几个键里维护"谁读谁不读"不可靠，所以声明放在**读它的那个文件里**，
    由本函数取回来跟 yaml 比对：键和读者相邻，改一边就会想起另一边。

    返回值：键集合；**取不到声明时返回 None = 本次不判**（脚本缺失/导入失败时宁可不报，
    不因为拿不到声明就把整份配置判成错的）。

    导入防护：检查脚本是**以脚本身份**写的，多数把动作放在 `if __name__ == "__main__"` 里，
    但这条约定没有任何东西强制。所以导入时屏蔽 stdout/stderr，并显式吞掉 SystemExit ——
    否则一个"模块级就 sys.exit()"的检查脚本会让 `--validate` 以**它**的退出码结束，
    把体检结果和被检脚本的结论混在一起（那种 bug 只有加脚本那天才会现身）。
    """
    base = os.path.basename(script_rel or "")
    if not base.endswith(".py"):
        return None
    import contextlib
    import importlib
    import io
    try:
        if CHECKS not in sys.path:
            sys.path.insert(0, CHECKS)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            mod = importlib.import_module(base[:-3])   # check 脚本只定义常量与函数，无 import 副作用
        keys = getattr(mod, "CONFIG_KEYS", None)
        return frozenset(keys) if keys is not None else None
    except SystemExit:
        return None
    except Exception:
        return None


def _rule_ids_for_script(script_rel):
    """该端**全部规则 id**（含 loader 直接执行的伪规则 id，如 EOL-UNIFIED）。

    数据源复用 harness/coverage.py 的 static_rules()（唯一一份规则清单，且已含
    「非 Rule 实例、由 loader 直接执行」那几条）——不在这里另维护第二份，
    否则清单会漂移，而漂移的后果是"合法 id 被误判成拼错"。
    端标签取自 checks/registry.py 的 ENDPOINT_BY_SCRIPT（不在这里硬编码脚本名）。

    返回值：id 集合；拿不到（脚本非端脚本/coverage 加载失败）时返回 None = 不判。
    """
    base = os.path.basename(script_rel or "")
    try:
        if CHECKS not in sys.path:
            sys.path.insert(0, CHECKS)
        from registry import ENDPOINT_BY_SCRIPT
    except Exception:
        return None
    ep = ENDPOINT_BY_SCRIPT.get(base)
    if not ep:
        return None
    try:
        import importlib.util as _ilu
        p = os.path.join(HARNESS, "coverage.py")
        spec = _ilu.spec_from_file_location("harness_coverage_validate", p)
        m = _ilu.module_from_spec(spec)
        # coverage.py 的 import 期会打印/读台账吗？——不会：它只在 main() 里读日志。
        spec.loader.exec_module(m)
        return {rid for rid, info in m.static_rules().items() if info.get("endpoint") == ep}
    except Exception:
        return None


def _validate(structure_only=False):
    """配置体检（2026-09-12）：遍历全部 gate，逐项验证 repo 路径/脚本/ruleset 可导入。
    改完 config/harness.toml 当下跑一次，把配置错当场揪出来，不等提交时 rc=2 才暴露。
    退出码：0=全部健康；2=存在阻断项（路径/脚本缺失等）。

    structure_only（--structure-only，2026-09-12 加，为 CI 用）：端仓不在本机是常态
    （CI 跑在干净容器里，clone 不到内网端仓），此时"repo 路径不存在"不是配置错。
    该模式只验与端仓无关的结构项：script 存在 / ruleset 可导入 / mode 合法 / run_on 合法，
    并把缺失的端仓路径降级为统计提示（不阻断）。

    2026-09-18（跨平台批次 7）：repo 相关的判据分三档，各归各位（见下方分支处的说明）——
    「键不在 repos 段」在任何形态下都阻断（它是键名拼错 / 新 gate 漏登记唯一的信号），
    「键在、本机没填值」⊘ 不阻断（同事机常态），「键在、路径不存在」按 structure_only 分。"""
    sys.path.insert(0, HARNESS)
    gates, cfg_err = _config_gates()
    if cfg_err:
        print(f"=== 配置体检：✗ 配置读不出来（rc=2）===\n  {cfg_err}")
        return 2
    # 2026-10-22（复审报告 §3.1）：覆盖层坏了不等于"全部健康"。原先 load() 只往 stderr
    # 写一句就退回 base，体检最后仍报绿并 rc=0 —— 覆盖层里的路径/策略一条都没生效，
    # 只看 rc 的脚本会把"配置坏了"读成"配置好了"。这里把降级转成阻断项。
    try:
        from _config import last_local_parse_error
        local_bad = last_local_parse_error()
    except Exception:
        local_bad = []
    if local_bad:
        path, why = local_bad[-1]
        print("=== 配置体检：✗ 本地覆盖层读不出来（rc=2）===")
        print("  文件：%s" % path)
        print("  原因：%s" % why)
        print("  ⚠ 这份文件里的 [repos] 路径与策略覆盖**一条都没生效**；")
        print("    不修好就继续，本机 gate 会按共享配置的空路径运行（等于没接入）。")
        print("    修好它，或删掉该文件 / 去掉 HARNESS_CONFIG 后重跑。")
        return 2
    problems = []
    missing_repos = []
    zero_conf = []                     # 本机没填路径的 gate（见下，不阻断）
    # 2026-09-18（跨平台批次 7）：判据按**键**分，不再按「本机配了几条路径」分。
    # `[repos]` 段的**键名**是共享配置里的团队策略（33 个键、值一律留空），**路径**在各人
    # gitignored 的 config/harness.local.toml。于是「repo 未配置」的三种含义各归各位：
    #   键不在 repos 段  → 真配置错（键名拼错 / 新 gate 忘了登记），阻断；
    #   键在、值留空     → 「本机没这个仓」，**常态**，⊘ 汇总、不阻断；
    #   键在、路径不存在 → structure_only 下不阻断（CI 干净容器正是这个形态），否则阻断。
    # 批次 2 用的是「本机有没有机器路径」当判据，它区分不了「一台机器一个仓都没配」与
    # 「同事只配了自己那一个仓」—— 后者被误判成配置错，提交链 fail-closed 全拦
    # （实测：reports/_batch6_partial_local_finding.txt）。键名表回到共享配置之后，
    # 这个中间判据不再需要：一个键**在不在**注册表里，与这台机器配了几条无关。
    try:
        from _config import load as _load_cfg
        _repos = (_load_cfg() or {}).get("repos")
        if not isinstance(_repos, dict):
            _repos = {}
    except Exception:
        _repos = {}
    print(f"=== 配置体检：{len(gates)} 个 gate{'（仅结构，端仓路径不阻断）' if structure_only else ''} ===")
    for name in sorted(gates):
        g = gates[name]
        issues = []
        norepo = False
        rp = g.get("_repo_path") or ""
        # 1) repo 路径
        # 「键不在 repos 段」= 共享配置从未声明过这个名字（真错，任何机器上都阻断）；
        # 「键在、本机没填值」= 同事机常态（⊘ 汇总，不阻断，见上方批次 7 的说明）；
        # 「键在、值也在、但目录不在这台机器上」= CI/换机常态，structure_only 下不阻断。
        repo_key = g.get("repo") or ""
        if repo_key and repo_key != "harness_self" and repo_key not in _repos:
            issues.append("repo 键未声明: %s（gates.%s.repo 引用的键不在 repos 段里 —— "
                          "键名是共享 config/harness.toml 的团队策略，"
                          "路径才是各人 config/harness.local.toml 的事）" % (repo_key, name))
        elif not rp:
            norepo = True
        elif not os.path.isdir(rp):
            if structure_only:
                missing_repos.append(name)
            else:
                issues.append(f"repo 路径不存在: {rp}")
        # 2) gate 基本字段
        script = g.get("script") or ""
        if not script:
            issues.append("script 未配置")
        elif g.get("enabled"):
            sp = os.path.join(ROOT, script) if not os.path.isabs(script) else script
            if not os.path.exists(sp):
                issues.append(f"脚本不存在: {script}")
        if "mode" in g and g.get("mode") not in ("strict", "loose"):
            issues.append(f"mode 非法: {g.get('mode')}")
        # 2026-09-18（跨平台批次 3）：`line_ending` 补**值域**校验。在此之前它**什么校验都没有**
        # —— 写 `auto` 会被静默接受（那时"auto"落到下面的 else 分支等于 CRLF，没人发现），
        # 拼错（`crlf `、`CRLF\n`、`AUTO`）同样静默。三个取值的语义差别很大
        # （auto=按本机检出判定；显式值=锁定），所以拼错不是"用了个奇怪的值"，
        # 而是**写的人以为锁住了、实际没有**。与上面 run_on/endpoint/encoding 同源。
        le = g.get("line_ending")
        if le is not None and (not isinstance(le, str)
                               or le.strip().lower() not in ("auto", "crlf", "lf")):
            issues.append(f"line_ending 非法: {le!r}（应为 auto/CRLF/LF）")
        # 2026-09-14（Codex 评审 v8 / P2）：补齐**值域**校验 —— 原先 run_on 只被读不被校验，
        # 写错（如 pre-commmit 拼错）会让该触发点静默不跑；endpoint/encoding/disabled_rules 同理。
        # 2026-09-16：白名单里原有 stop / auto，但全仓**没有任何消费方**（只有
        # harness/hooks/pre-commit.py 认 pre_commit、harness/agents/auto-gate.py 认 post_edit）。
        # 这条校验存在的意义正是防「触发点静默不跑」，而那两个值恰好**保证**静默不跑：写进去能过
        # 体检、gate 却永远不在任何时机触发 —— 是"配置项白写"里最坏的一种（看着像配了）。
        # 故从白名单移除：写它们现在会被 --validate 以 rc=2 拦下。要扩触发点请**先接线再进白名单**
        # （与 2026-09-12 P3-3 给 post_edit 接线的做法一致）。
        _RUN_ON_OK = {"pre_commit", "post_edit"}
        ro = g.get("run_on")
        if ro is not None:
            if not isinstance(ro, list) or not ro:
                issues.append(f"run_on 非法（应为非空列表）: {ro!r}")
            else:
                bad = [x for x in ro if x not in _RUN_ON_OK]
                if bad:
                    issues.append(f"run_on 含未知触发点 {bad}（合法值: {sorted(_RUN_ON_OK)}）")
        ep = g.get("endpoint")
        if ep is not None and ep not in ("pc", "h5", "backend"):
            issues.append(f"endpoint 非法: {ep}（应为 pc/h5/backend）")
        enc = g.get("encoding")
        if enc is not None and enc not in ("utf-8", "utf-8-bom", "utf-8-sig", "gbk"):
            issues.append(f"encoding 非法: {enc}（应为 utf-8/utf-8-bom/utf-8-sig/gbk）")
        dr = g.get("disabled_rules")
        if dr is not None and (not isinstance(dr, list) or any(not isinstance(x, str) for x in dr)):
            issues.append(f"disabled_rules 应为字符串列表: {dr!r}")
        # 4) 键级/值级「空承诺」检查（评审 v13 中 E）：配置里写了、但没有任何消费方读它。
        #    上面几条只校验**值域**（mode 写 strict/loose），管不住"这个键根本没人读"。
        _rs_script = os.path.basename(script or "")
        _declared = _script_config_keys(script) if g.get("enabled") else None
        if _declared is not None:
            _dead = sorted(set(k for k in g if not k.startswith("_")) - ENGINE_CONFIG_KEYS - _declared)
            if _dead:
                issues.append(
                    "声明了但**没有任何消费方读**的配置键 %s —— 写了等于没写（看着像配了）："
                    "要么接线（在 %s 里加读取代码），要么删掉这一行"
                    % (_dead, _rs_script or "对应检查脚本"))
        if dr and isinstance(dr, list):
            _valid = _rule_ids_for_script(script)
            if _valid is not None:
                _unknown = [x for x in dr if isinstance(x, str) and x.strip() not in _valid]
                if _unknown:
                    issues.append(
                        "disabled_rules 里 %s 不是本端的规则 id（拼错即静默不生效："
                        "写的人以为关掉了，规则照样拦；合法 id 见 checks/*_ruleset.py）" % _unknown)
        # 3) ruleset 可导入（按端映射 checks/<名字>ruleset.py）
        base = os.path.basename(script or "")
        rs = {"backend_layer_check.py": "backend_ruleset",
              "pc_code_check.py": "pc_ruleset",
              "h5_code_check.py": "h5_ruleset"}.get(base)
        if g.get("enabled") and rs:
            sys.path.insert(0, os.path.join(ROOT, "checks"))
            try:
                __import__(rs)
            except Exception as e:
                issues.append(f"ruleset 导入失败 {rs}: {e}")
        status = "✅" if not issues else ("⚠" if not g.get("enabled") else "✗")
        if issues:
            print(f"  {status} {name}: " + "; ".join(issues))
            if g.get("enabled"):
                problems.append(name)
        elif norepo:
            # 刻意**不**打 ✅：这个 gate 此刻一条检查也跑不了。也不打 ✗ —— 它没坏，
            # 只是本机还没配路径（见下面那条汇总）。
            print(f"  ⊘ {name}: 本机未配仓库路径（repos 段里这个键留着空值）")
            zero_conf.append(name)
        else:
            print(f"  {status} {name}")
    # 4) 全局项：registry/loader 一致性抽查
    try:
        sys.path.insert(0, os.path.join(ROOT, "checks"))
        import registry_loader
        # 2026-09-14（Codex P2）：原先只 `script_for(第一个 gate)` —— 只抽查了其中一个。
        # 改成全部 gate，顺带把"同名不同端"的映射错也抓出来（这条很便宜）。
        # （低 18 评审 v13：原注释写「抽查 1/33」，而 gate 总数早就是 34 —— 不写绝对数。）
        _rs_bad = []
        _rs_fallback = []
        _rs_mismatch = []
        # registry 的**登记值**（相对路径）——与 script_for() 不同：后者返回解析后的绝对路径，
        # 且"未登记"和"登记了但文件不在"都返回 None，没法拿来跟 yaml 比对。
        _reg_rel = registry_loader.load_gate_scripts()
        for _name in sorted(gates):
            try:
                _got = registry_loader.script_for(_name)
            except Exception as _e:
                _rs_bad.append("%s: %s" % (_name, _e))
                continue
            # 评审 v3 P2（2026-09-15）：`None` = **registry 未登记**（靠 run_gate 的 config.script
            # 兜底跑起来的），旧文案"全部解析"比实际覆盖面强，会误导迁移/审计。这里分开报：
            # 命中 / 兜底 / 失败 三态；兜底只提醒不判失败（config.script 是设计内的回退路径）。
            if not _got:
                _rs_fallback.append(_name)
                continue
            # 2026-09-16（剖析报告 N4）：此前 `_got` **只当布尔用**，从不和配置里的 `script`
            # 比对 —— 于是"改了 harness.toml 的 script 却不生效"这件事全链路无声：run_gate 取
            # 脚本时 registry 优先（见下方 `if not script: script = …g["script"]`），配置里
            # 那一行变成**写了等于没写的死配置**。改配置的人不会知道，只有哪天 registry 被删
            # 才突然生效（行为突变）。这里把"两处说法不一致"当配置错误报出来。
            _y = (gates[_name].get("script") or "").strip()
            _r = (_reg_rel.get(_name) or "").strip()
            if _y and _r and _norm_rel(_y) != _norm_rel(_r):
                _rs_mismatch.append("%s: config=%s ≠ registry=%s" % (_name, _y, _r))
        if _rs_bad:
            print("  ✗ registry_loader 对 %d 个 gate 解析失败: %s" % (len(_rs_bad), "; ".join(_rs_bad[:5])))
            problems.extend([x.split(":")[0] for x in _rs_bad])
        elif _rs_fallback:
            print("  ⚠ registry 未登记 %d/%d 个 gate（走 config.script 兜底，不算失败但无注册表保障）：%s"
                  % (len(_rs_fallback), len(gates), ", ".join(_rs_fallback[:6])))
            print("    → 建议补进 checks/registry.py（OVERRIDES）；改了脚本路径时 registry 不会跟着变。")
        else:
            print("  ✅ registry_loader 可用（%d 个 gate 全部由 registry 命中）" % len(gates))
        if _rs_mismatch:
            print("  ✗ harness.toml 的 script 与 registry 登记值不一致 %d 处（registry 优先，"
                  "配置那行实际不生效）:" % len(_rs_mismatch))
            for _m in _rs_mismatch[:6]:
                print("      " + _m)
            problems.extend([x.split(":")[0] for x in _rs_mismatch])
        # N5（2026-09-16）：本机覆盖层若试图改策略字段，_guard_policy 已退回并打了 stderr；
        # 这里**汇总报一次**，否则那行 stderr 散落在 load 期，体检时看不见。
        try:
            import _config as _C
            _rej = _C.last_policy_rejection()
            if _rej:
                print("  ⚠ 本地/环境覆盖层试图修改已注册 gate 的策略字段，已被忽略（%d 项）：%s"
                      % (len(_rej), ", ".join(_rej[:8])))
            # 2026-09-16（剖析报告六节「豁免出口缺少审计」）：另一支也要报 ——
            # HARNESS_ALLOW_POLICY_OVERRIDE=1 放行时那些字段**是生效了的**，与「已被忽略」
            # 正好相反。原先体检输出里一个字都没有（只有 load 期一行 stderr），
            # 于是"这台机器上策略被放行了"在体检报告上完全看不出来。
            _ovr = _C.last_policy_override()
            if _ovr:
                print("  ⚠⚠ HARNESS_ALLOW_POLICY_OVERRIDE=1 生效中：%d 条策略字段覆盖**已放行**"
                      "（仅调试用，别进共享配置）：%s" % (len(_ovr), ", ".join(_ovr[:8])))
        except Exception:
            pass
    except Exception as e:
        print(f"  ⚠ registry_loader 异常: {e}")
    if missing_repos and structure_only:
        print(f"  ℹ {len(missing_repos)} 个 gate 的端仓在本机不存在（干净容器/CI 常态，不阻断）: "
              f"{', '.join(missing_repos)}")
    if zero_conf:
        print(f"  ℹ 本机没填仓库路径 —— 上述 {len(zero_conf)} 个 gate 因此一条检查都跑不了。"
              f"这不是配置错：**键名**在共享 config/harness.toml 里（团队策略），"
              f"**路径**在各人 gitignored 的 config/harness.local.toml 里（每机事实）。")
        print(f"    只写你机器上确实存在的那几个仓即可，写几条生效几条。首次接入："
              f"sh sandbox/setup_machine.sh（首次会提示你编辑 local.toml 后重跑）；"
              f"或照 config/harness.local.example.toml 的「场景 A」抄一份 [repos] 段。")
    if problems:
        print(f"\n=== 体检不通过：{len(problems)} 个启用 gate 有阻断项: {', '.join(problems)} ===")
        return 2
    print("\n=== 体检通过：全部启用 gate 配置健康 ===")
    return 0
