#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""门禁覆盖度自评 —— 回答那个开放问题：「传感器不响 = 质量高，还是检测失效？」

背景：门禁跑得再多，只要不看「每条规则到底触发过没有」，就分不清两种情况——
某个仓真的很干净（规则是好的，只是没违规），还是规则写死了/压根不适用（传感器坏了）。
本脚本把台账（月度分片 reports/gate_log-YYYY-MM.jsonl 全集）里的**规则级**触发与豁免数据聚合成一张自评表。

用法：
    python harness/coverage.py                     # 全窗口
    python harness/coverage.py --days 30           # 只算最近 30 天
    python harness/coverage.py --last 200          # 只看最近 200 次运行
    python harness/coverage.py --json              # 机器可读（供 CI / 看板）
    python harness/run_gate.py --coverage --days 30   # 等价入口

数据来源：
    台账月度分片 reports/gate_log-YYYY-MM.jsonl 全集（旧单文件 gate_log.jsonl 若在，
    认作最早一片）—— 每次 run_gate 追加一行。2026-09-12 起带 `rules` 字段
    （规则级触发/豁免计数，由 checks/*_code_check 通过 HARNESS_STATS_FILE 回传）。
    更早的记录没有该字段，本脚本会照常统计运行次数并明确提示「规则级数据缺失」。
    `--quick`：不穿全量台账，直接读最新锚点（reports/gate_log_anchor.jsonl，入库那份）
    的 rollup 出盲区速览。

退出码：0（信息性报告，**不**用于拦截）
"""
import glob
import json
import os
import sys
import time

# UTF-8 兜底（2026-09-13 评审 v3 H1 系统性修法）：宿主管道可能是 GBK/ASCII，
# 打印中文/✗ 会抛 UnicodeEncodeError；errors=replace 最坏只是个别字符变问号。
# 清单由 sandbox/list_entries.py 机器推导（tests/test_entry_level.py 有对应锁）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HARNESS = os.path.dirname(os.path.abspath(__file__))        # <root>/harness
ROOT = os.path.dirname(HARNESS)                             # <root>
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.path.insert(0, HARNESS)

LOG_FILE = os.environ.get("HARNESS_LOG_FILE") or os.path.join(ROOT, "reports", "gate_log.jsonl")
CONFIG = os.path.join(ROOT, "config", "harness.yaml")


def ledger_files():
    """台账片全集（时间序）—— 与 harness/run_gate.py::_ledger_files 同判据（跨文件契约，对拍锁）。

    HARNESS_LOG_FILE 密闭 → 只有那一个文件（tests/test_coverage.py 的隔离锁靠它，
    行为与旧版逐字一致）；否则旧 gate_log.jsonl（若有，排最前）+ 月度片（字典序=月序）。
    HARNESS_LOG_DIR 与引擎/壳两侧同源（三份判据必须逐字一致，tests/test_gate_log_anchor.py）。
    """
    if os.environ.get("HARNESS_LOG_FILE"):
        return [LOG_FILE]
    base = os.environ.get("HARNESS_LOG_DIR") or os.path.join(ROOT, "reports")
    paths = []
    legacy = os.path.join(base, "gate_log.jsonl")
    if os.path.exists(legacy):
        paths.append(legacy)
    paths.extend(sorted(glob.glob(os.path.join(base, "gate_log-*.jsonl"))))
    return paths


def _anchor_probe():
    """锚点文件尾行 → `(记录 or None, 状态)`，状态三态 `ok` / `absent` / `corrupt`。

    ★ 2026-09-30 修：与壳侧 `eaglermc_gate/cli.py::_anchor_probe` 同一条判据（同一处
    缺陷的两个副本）。原先「没有锚点文件」与「有文件但尾行解析不了」都返回 `None`，
    于是一个坏字节就能让读侧退回"尚无锚点"。这里虽然只是信息性报告（不拦人），
    但把损坏说成"没有"同样是假话 —— 本报告存在的意义正是把"没响"和"没跑"分开。
    """
    path = (os.environ.get("HARNESS_ANCHOR_FILE")
            or os.path.join(ROOT, "reports", "gate_log_anchor.jsonl"))
    try:
        last = None
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    last = line
    except OSError:
        return None, "absent"
    if last is None:
        return None, "absent"
    try:
        rec = json.loads(last)
    except ValueError:
        return None, "corrupt"
    if not isinstance(rec, dict):
        return None, "corrupt"
    return rec, "ok"


def _quick_from_anchor():
    """读最新锚点的 rollup 出「盲区速览」—— 不穿全量台账（2026-09-29 方案 E 的消费口）。

    盲区 = static_rules() 全集 − rollup.rules_fired 出现过的规则。口径只覆盖
    「自上一锚点以来」的窗口，输出里把窗口点明（不冒充全窗口结论）——
    「从未触发」= 本窗口一次都没响：可能是覆盖盲区，也可能是真干净，与四分类第 4 类同口径。
    无锚点 → 提示先锚定，rc=0（信息性报告，绝不拦人）。锚点**损坏** → 同样 rc=0，
    但必须说"损坏"而不是"尚无"（否则本报告会把「读不到」冒充成「还没锚」）。
    """
    anchor, astate = _anchor_probe()
    if astate == "corrupt":
        print("(锚点文件尾行解析不了 —— 已失效；`gate log --verify` 会按此报错。"
              "先核对尾行，确认误伤就删掉那一行再重锚)")
        return 0
    if anchor is None:
        print("(尚无锚点 —— 先 `gate log --anchor` 一次，之后 --quick 直接读锚点 rollup)")
        return 0
    roll = anchor.get("rollup") or {}
    fired = set(str(k) for k in (roll.get("rules_fired") or {}).keys())
    rules = static_rules()
    blind = [rid for rid in rules if rid not in fired]
    print("锚点 %s（n=%d，窗口=自第 %s 条以来 %s 条）" % (
        anchor.get("ts", "?"), anchor.get("n", "?"), roll.get("from_n", 0), roll.get("n", 0)))
    print("rc 分布: %s | weakened %d | skipped %d" % (
        roll.get("rc", {}), roll.get("weakened", 0), roll.get("skipped", 0)))
    print("规则触发 %d / 全集 %d —— 从未触发（盲区候选）%d 条：" % (
        len(fired), len(rules), len(blind)))
    for rid in blind[:20]:
        info = rules.get(rid)
        name = info.get("name", "") if isinstance(info, dict) else str(info)
        print("  · %s  %s" % (rid, name))
    if len(blind) > 20:
        print("  …还有 %d 条" % (len(blind) - 20))
    return 0

ENDPOINT_BY_SCRIPT = {"pc_code_check.py": "pc", "h5_code_check.py": "h5",
                      "backend_layer_check.py": "backend"}
ENDPOINT_LABEL = {"pc": "PC(Vue)", "h5": "H5(UniApp)", "backend": "后端(.NET)"}


# ---------------------------------------------------------------- 读取
def load_records(path=None, days=None, last=None):
    """读运行记录（月度分片感知，2026-09-29）。path 显式给定 → 单文件（对账某一片 /
    env 密闭）；None → ledger_files() 全集按时间序拼读。坏行跳过（日志是观测，
    不能反过来炸掉自评）。"""
    paths = [path] if path else ledger_files()
    out = []
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("gate"):
                    out.append(rec)
    if days:
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
        out = [r for r in out if str(r.get("ts", "")) >= cutoff]
    if last:
        out = out[-int(last):]
    return out


def load_config():
    """gate → {enabled, endpoint, disabled_rules, repo}（直接用工程自己的 mini-yaml，零第三方）。"""
    import _config
    # 第四轮 N-4：原先的 `_config.load_config() if hasattr(...)` 是死分支（_config 没这个函数，
    # 恒取 {}）。唯一数据源就是 all_gates()。
    try:
        gates = _config.all_gates()
    except Exception:
        return {}
    out = {}
    for name, g in (gates or {}).items():
        script = os.path.basename(g.get("script", ""))
        out[name] = {"enabled": bool(g.get("enabled", True)),
                     "endpoint": ENDPOINT_BY_SCRIPT.get(script, "unknown"),
                     "disabled": list(g.get("disabled_rules") or []),
                     "repo": g.get("repo", "")}
    return out


# loader 级（非 Rule 实例、由 loader 直接执行）的检查清单。
# 第四轮 N-1：语法规则的探针早就埋好了，自评清单却漏了它——而它正是门禁史上第一次
# 静默失效的那条规则（P0）。自评漏掉它 = 最该盯的传感器在报告里隐身。
# ENC（编码卫生）同理：由 backend loader 经 encoding_ruleset 执行，也要在册。
# 完整性不靠人眼：tests/test_coverage.py::TestStaticCatalog 会扫 checks/*.py 里所有
# bump("<字面量>") 并对清单做集合包含断言，少一个就红。
LOADER_LEVEL_CHECKS = {
    "PC-SYNTAX":  ("PC 语法校验（node --check）", "pc"),
    "PC-EOL":     ("行尾污染(PC)", "pc"),
    "PC-SFC":     ("SFC 结构(SFC标签/属性)", "pc"),
    "(读取失败)": ("文件读取失败", "pc"),
    "H5-SYNTAX":  ("H5 语法校验（node --check）", "h5"),
    "ENC":        ("编码卫生（utf-8-bom / 行尾按仓约定）", "backend"),
}


def static_rules():
    """全部规则 ID → 名称/端（从 rulesets 现取，不维护第二份清单）。"""
    out = {}
    for mod_name, endpoint in (("pc_ruleset", "pc"), ("h5_ruleset", "h5"), ("backend_ruleset", "backend")):
        try:
            mod = __import__(mod_name)
        except Exception:
            continue
        for key, val in vars(mod).items():
            if key.endswith("_RULES") and isinstance(val, list):
                for r in val:
                    rid = getattr(r, "id", None)
                    if rid:
                        out[rid] = {"name": getattr(r, "name", ""), "endpoint": endpoint}
    # 非 Rule 实例、由 loader 直接执行的检查也计入（否则它们在自评里隐身）
    for rid, (name, endpoint) in LOADER_LEVEL_CHECKS.items():
        out.setdefault(rid, {"name": name, "endpoint": endpoint})
    return out


# 结构性「只可能被定向口径触发」的规则 —— 不打这个标，报告会把它们误读成「传感器坏了」。
# R7：后端 loader 的全量扫描只喂 .cs（backend_layer_check.iter_cs_files），只有定向口径才会把
#     非 .cs 文件交给它判定；实测两个后端仓 wwwroot 之外的非 .cs 代码文件 = 0 → 它「从未触发」
#     是**真干净**。准确性由 tests/test_coverage.py::test_targeted_only_table_matches_loader 锁住
#     （loader 的全量收集方式一改，锁就红，逼人回来改这张表）。
TARGETED_ONLY = {
    "R7": "只在定向口径可能触发：全量扫描只喂 .cs（backend_layer_check.iter_cs_files）",
}


# ---------------------------------------------------------------- 聚合
SCOPES = ("targeted", "all", "unknown")   # unknown = 早期没写 scope 字段的记录


def _blank_scope():
    return {"error": 0, "warn": 0, "info": 0, "waived_inline": 0, "waived_legacy": 0, "runs": 0}


# Q2（评审 v7）：口径变更标注 —— 2026-09-14 起按行口径修复（K1：script 段相对行号 → 文件绝对行号）。
# 此前窗口里被**误丢弃**的新行命中曾被记成 waived_legacy（存量豁免），因此跨该日期的对比请分段解读：
# 修复后 script 类规则的 waived_legacy 预期**下降**（勿把"豁免变少"误读成"变严"）。不做历史数据迁移——
# 旧记录是"当时口径下的正确记录"。
def aggregate(records):
    """→ (by_rule, by_gate, has_rule_data)

    **按运行口径分组**（第四轮外审 ③）：门禁有两种口径，混在一起会让数字失真——
      · targeted（定向：pre-commit / post-edit / auto-gate）= 真拦截，看"日常有没有抓东西"
      · all（全量扫描）= 摸底观察，存量违规全报，天然量大
    所以每条规则同时给「合计」和「分口径」两份数；豁免率只按 targeted 算（全量根本不做按行豁免）。
    """
    by_rule, by_gate, has_rule_data, n_no_rule_data = {}, {}, False, 0
    for rec in records:
        gate = rec.get("gate", "?")
        scope = rec.get("scope") or "unknown"
        g = by_gate.setdefault(gate, {"runs": 0, "blocked": 0, "ms_sum": 0, "last": "", "rules_runs": 0,
                                      "scopes": {s: {"runs": 0, "blocked": 0} for s in SCOPES}})
        g["runs"] += 1
        g["blocked"] += 1 if rec.get("rc") == 1 else 0
        g["ms_sum"] += int(rec.get("ms") or 0)
        g["last"] = max(g["last"], str(rec.get("ts", "")))
        g["scopes"][scope]["runs"] += 1
        g["scopes"][scope]["blocked"] += 1 if rec.get("rc") == 1 else 0
        rules = rec.get("rules")
        if not isinstance(rules, dict) or not rules:
            # K4（评审 v6）：**空 dict 也算"没有规则级数据"** —— 旧写法 isinstance 通过就
            # 记成 has_rule_data=True，但它对覆盖度分子分母零贡献（异常写入路径），
            # 比"缺字段"更容易骗过口径检查。
            # G8（评审 v5）：把静默缺口语义化 —— 这部分记录对"规则级覆盖度"没有贡献，
            # 以前默默跳过（"没数据不判定"是对的，但从不告诉你缺了多少条）。
            n_no_rule_data += 1
            continue
        has_rule_data = True
        g["rules_runs"] += 1
        for rid, kinds in rules.items():
            if not isinstance(kinds, dict):
                continue
            d = by_rule.setdefault(rid, {"error": 0, "warn": 0, "info": 0, "waived_inline": 0,
                                         "waived_legacy": 0, "gates": set(),
                                         "scopes": {s: _blank_scope() for s in SCOPES}})
            sc = d["scopes"][scope]
            sc["runs"] += 1
            for k, v in kinds.items():
                if k in d and isinstance(v, int):
                    d[k] += v
                    if k in sc:
                        sc[k] += v
            d["gates"].add(gate)
    return by_rule, by_gate, has_rule_data, n_no_rule_data


def classify(rid, info, agg_one, cfg, endpoint_runs):
    """一条规则的覆盖度状态。分类学是这份报告的核心：把"没响"拆成可行动的几种。"""
    enabled_gates = [n for n, g in cfg.items() if g["enabled"] and g["endpoint"] == info["endpoint"]]
    disabled_in = [n for n in enabled_gates if rid in cfg[n]["disabled"]]
    hits = (agg_one or {}).get("error", 0) + (agg_one or {}).get("warn", 0) + (agg_one or {}).get("info", 0)
    if enabled_gates and len(disabled_in) == len(enabled_gates):
        return "按客户禁用"                      # 所有启用该端的 gate 都关了它 = 有意关闭
    if endpoint_runs == 0:
        return "该端窗口内无运行"                # 这条端没跑过，说明不了规则好坏
    if hits > 0:
        return "活跃"
    return "从未触发（需确认：不适用 or 失效）"   # 该端跑过、规则没响 = 要么真干净，要么传感器坏了


def build_report(records, cfg, rules):
    by_rule, by_gate, has_rule_data, n_no_rule_data = aggregate(records)
    endpoint_runs = {}
    for rec in records:
        ep = cfg.get(rec.get("gate", ""), {}).get("endpoint", "unknown")
        endpoint_runs[ep] = endpoint_runs.get(ep, 0) + 1
    rows = []
    for rid, info in sorted(rules.items(), key=lambda kv: (kv[1]["endpoint"], kv[0])):
        agg_one = by_rule.get(rid) or {}
        scopes = agg_one.get("scopes") or {}
        tgt = dict(scopes.get("targeted") or _blank_scope())
        full_sc = dict(scopes.get("all") or _blank_scope())
        hits_t = tgt["error"] + tgt["warn"] + tgt["info"]
        wv_t = tgt["waived_inline"] + tgt["waived_legacy"]
        tot_t = hits_t + wv_t
        rows.append({
            "rule": rid, "name": info["name"], "endpoint": info["endpoint"],
            "status": classify(rid, info, agg_one, cfg, endpoint_runs.get(info["endpoint"], 0)),
            # 合计（兼容/占比用）
            "error": agg_one.get("error", 0), "warn": agg_one.get("warn", 0), "info": agg_one.get("info", 0),
            "waived_inline": agg_one.get("waived_inline", 0), "waived_legacy": agg_one.get("waived_legacy", 0),
            # 分口径（③）：定向 = 真拦截；all = 全量观察
            "targeted": tgt, "full": full_sc,
            "fired_targeted": hits_t > 0,
            # 豁免率只按定向口径算：全量扫描不做按行豁免，混进来会失真
            "waiver_rate": round(wv_t / tot_t, 3) if tot_t else None,
        })
    # 第四轮 N-4：清单外数据不许静默丢弃。日志里出现、但不在清单里的规则 ID 单独成表，
    # 让「清单漏了」或「埋点用了未登记 ID」当场浮出来（N-1 就是这么漏了整整一轮的）。
    extra = []
    for rid, agg_one in sorted(by_rule.items()):
        if rid in rules:
            continue
        extra.append({"rule": rid, "error": agg_one.get("error", 0), "warn": agg_one.get("warn", 0),
                      "info": agg_one.get("info", 0), "waived_inline": agg_one.get("waived_inline", 0),
                      "waived_legacy": agg_one.get("waived_legacy", 0)})
    gates = []
    for name, g in sorted(cfg.items()):
        st = by_gate.get(name)
        gates.append({"gate": name, "endpoint": g["endpoint"], "enabled": g["enabled"],
                      "runs": (st or {}).get("runs", 0), "blocked": (st or {}).get("blocked", 0),
                      "scopes": (st or {}).get("scopes", {}),
                      "avg_ms": int((st or {}).get("ms_sum", 0) / st["runs"]) if st and st["runs"] else None,
                      "last": (st or {}).get("last", "")})
    live = [r for r in rows if r["status"] in ("活跃", "从未触发（需确认：不适用 or 失效）")]
    fired = [r for r in rows if r["status"] == "活跃"]
    return {"window": {"records": len(records),
                       "from": records[0].get("ts", "") if records else "",
                       "to": records[-1].get("ts", "") if records else ""},
            "has_rule_data": has_rule_data,
            "n_no_rule_data": n_no_rule_data,   # G8：缺规则级数据的记录条数（把静默缺口语义化）
            "coverage": {"candidates": len(live), "fired": len(fired),
                         "pct": round(100.0 * len(fired) / len(live), 1) if live else None},
            "rules": rows, "gates": gates, "extra_rules": extra,
            "endpoint_runs": endpoint_runs}


# ---------------------------------------------------------------- 呈现
def render_text(rep, log_path=LOG_FILE):
    L = []
    w = rep["window"]
    L.append("=" * 78)
    L.append("门禁覆盖度自评（回答：传感器不响 = 质量高，还是检测失效？）")
    L.append("=" * 78)
    L.append(f"数据源: {log_path}")
    L.append(f"窗口:   {w['records']} 次运行" + (f"（{w['from']} → {w['to']}）" if w["records"] else ""))
    if not w["records"]:
        L.append("")
        L.append("⚠ 窗口内没有任何运行记录：先跑几次门禁（run_gate / auto-gate / 提交）再看。")
        return "\n".join(L)
    _nod = rep.get("n_no_rule_data") or 0
    if _nod:
        # G8（评审 v5）：把静默缺口语义化 ——"没数据不判定"是对的，但要让你知道缺了多少条。
        L.append("")
        L.append("（口径说明：窗口内另有 %d 条记录**缺规则级数据**（旧记录/异常写入），"
                 "按「没数据不判定」跳过，不参与覆盖度分子分母。）" % _nod)
    if not rep["has_rule_data"]:
        L.append("")
        L.append("⚠ 窗口内的记录都**没有规则级数据**（rules 字段）——覆盖度结论只能看运行次数，")
        L.append("  规则触发情况无法回答。规则级埋点 2026-09-12 才上线，跑一次新门禁即可产生数据。")
        L.append("  （下表中的规则一律**不判定**：没有数据 ≠ 从未触发。）")
        L.append("")
        L.append("【gate 运行概览】")
        ran0 = [g for g in rep["gates"] if g["runs"]]
        for g in sorted(ran0, key=lambda x: -x["runs"])[:8]:
            L.append(f"    {g['gate']:<16} 跑 {g['runs']:>4} 次  均 {g['avg_ms']}ms  最近 {g['last']}")
        return "\n".join(L)
    cov = rep["coverage"]
    L.append("")
    if cov["pct"] is not None:
        L.append(f"【覆盖度】可评判规则 {cov['candidates']} 条，窗口内触发过 {cov['fired']} 条 → **{cov['pct']}%**")
    else:
        L.append("【覆盖度】暂无可评判规则（该端窗口内无运行）")
    # 未触发的规则：最需要看的一段
    dead = [r for r in rep["rules"] if r["status"] == "从未触发（需确认：不适用 or 失效）"]
    if dead:
        L.append("")
        L.append(f"【从未触发 · {len(dead)} 条】该端在窗口内跑过，但这些规则一次都没响——")
        L.append("  要么对应场景真的干净，要么规则不适用/写法失效，**建议各挑一条人工核一遍**：")
        for r in dead:
            note = TARGETED_ONLY.get(r["rule"], "")
            L.append(f"    · {r['rule']:<12} {r['name']}" + (f"\n        ⟵ {note}" if note else ""))
    inactive = [r for r in rep["rules"] if r["status"] == "该端窗口内无运行"]
    if inactive:
        eps = sorted({r["endpoint"] for r in inactive})
        L.append("")
        L.append(f"【该端窗口内无运行 · {len(inactive)} 条】端={','.join(eps)}——先跑该端门禁再评估。")
    off = [r for r in rep["rules"] if r["status"] == "按客户禁用"]
    if off:
        L.append("")
        L.append(f"【按客户禁用 · {len(off)} 条】所有启用该端的 gate 都关了它（有意关闭，非故障）：")
        L.append("    · " + "、".join(r["rule"] for r in off))
    # 有触发的
    fired = [r for r in rep["rules"] if r["status"] == "活跃"]
    if fired:
        L.append("")
        L.append(f"【已触发 · {len(fired)} 条】")
        L.append("    —— 拦截/告警/观察 = **定向口径**（日常提交路径，真拦截）；全量观察 = 扫描摸底 ——")
        L.append(f"    {'规则':<12} {'拦截':>5} {'告警':>5} {'观察':>5} {'全量观察':>8} "
                 f"{'行内豁免':>8} {'按行豁免':>8} {'豁免率':>7}")
        for r in sorted(fired, key=lambda x: -(x["targeted"]["error"] * 100 + x["targeted"]["warn"] * 10
                                               + x["targeted"]["info"])):
            t, fl = r["targeted"], r["full"]
            full_obs = fl["error"] + fl["warn"] + fl["info"]
            wr = f"{int(r['waiver_rate'] * 100)}%" if r["waiver_rate"] is not None else "-"
            L.append(f"    {r['rule']:<12} {t['error']:>5} {t['warn']:>5} {t['info']:>5} {full_obs:>8} "
                     f"{r['waived_inline']:>8} {r['waived_legacy']:>8} {wr:>7}")
        # 只在全量口径响过 = 日常提交路径 0 拦截（值得单独看一眼）
        only_full = [r for r in fired if not r["fired_targeted"]]
        if only_full:
            L.append("")
            L.append(f"【只在全量口径响过 · {len(only_full)} 条】日常（定向）从没抓过东西——"
                     f"要么真没问题，要么定向口径喂不到它：")
            L.append("    · " + "、".join(r["rule"] for r in only_full))
        # 第四轮 N-5：两列豁免 + info 列的口径说明（原来只叫"存量豁免"，读者会把
        # legacy 仓的整文件降级误当成普通观察项 —— 它也是一种存量豁免，只是在 info 列里）。
        L.append("    注：「行内豁免」= 行尾 `harness-ok` 标记；「按行豁免」= legacy 存量**按行**过滤（定向口径）。")
        L.append("        legacy_baseline 仓的**整文件**降级计入 info 列，故 info 既含观察项、也含整文件降级。")
    # 高豁免率提示（N9 修复）——分子分母必须与 waiver_rate **同口径、同分母**。
    # 原先这里打印的是**合计**口径的「豁免/(error+warn+info)」，而 waiver_rate 取的是
    # **定向**口径的 wv/(命中+豁免)：读者拿打印出来的两个数复算，永远算不出旁边那个百分比
    # （口径不同是其一，分母语义不同是其二 —— 打印的是「命中」，而 rate 的分母是「命中+豁免」）。
    # 修法：两个数都取自 targeted，并把百分比直接印出来，使这一行**自洽可验**。
    hot = [r for r in fired if r["waiver_rate"] is not None and r["waiver_rate"] >= 0.5]
    if hot:
        L.append("")
        L.append("【豁免率 ≥50% 的规则（定向口径）】命中大多被豁免 → 规则口径可能过宽，或该场景属正当写法：")
        for r in hot:
            t = r["targeted"]
            wv = t["waived_inline"] + t["waived_legacy"]
            hits = t["error"] + t["warn"] + t["info"]
            L.append(f"    · {r['rule']}：豁免 {wv} / 总计 {wv + hits}"
                     f"（命中 {hits} + 豁免 {wv}）= {r['waiver_rate'] * 100:.1f}%")
    # gate 概览
    L.append("")
    L.append("【gate 运行概览】")
    never = [g["gate"] for g in rep["gates"] if g["enabled"] and g["runs"] == 0]
    ran = [g for g in rep["gates"] if g["runs"]]
    L.append(f"    启用 {sum(1 for g in rep['gates'] if g['enabled'])} 个，窗口内跑过 {len(ran)} 个，从未跑 {len(never)} 个")
    for g in sorted(ran, key=lambda x: -x["runs"])[:8]:
        sc = g.get("scopes") or {}
        t = sc.get("targeted") or {"runs": 0, "blocked": 0}
        rate = f"{int(100.0 * t['blocked'] / t['runs'])}%" if t["runs"] else "-"
        L.append(f"    {g['gate']:<16} 跑 {g['runs']:>4} 次（定向 {t['runs']} / 全量 {g['runs'] - t['runs']}）"
                 f"  定向拦下 {t['blocked']:>3} 次({rate})  均 {g['avg_ms']}ms  最近 {g['last']}")
    if never:
        L.append(f"    从未跑过（前 10）: {', '.join(never[:10])}" + (" …" if len(never) > 10 else ""))
    extra = rep.get("extra_rules") or []
    if extra:
        L.append("")
        L.append(f"【清单外规则级数据 · {len(extra)} 条】日志里出现但不在自评清单——清单漏了，或埋点用了未登记 ID：")
        for r in extra[:10]:
            L.append(f"    · {r['rule']}：error={r['error']} warn={r['warn']} info={r['info']} "
                     f"按行豁免={r['waived_legacy']} 行内豁免={r['waived_inline']}")
    L.append("")
    L.append("提示：本报告只描述「规则有没有响」，不评判代码质量；要回答『这条规则还该不该留』，")
    L.append("      把「从未触发」清单按端分给对应仓负责人人工确认一轮。")
    return "\n".join(L)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--quick" in argv:
        return _quick_from_anchor()
    days = last = None
    as_json = "--json" in argv
    log_path = LOG_FILE          # 显示用；数据源见下（显示与数据源解耦，2026-09-29 月度分片）
    for i, a in enumerate(argv):
        if a == "--days" and i + 1 < len(argv):
            days = int(argv[i + 1])
        elif a == "--last" and i + 1 < len(argv):
            last = int(argv[i + 1])
        elif a == "--log" and i + 1 < len(argv):
            log_path = argv[i + 1]
    # 数据源：显式 --log 或 env 密闭 → 单文件（行为与旧版逐字一致，tests/test_coverage.py
    # 的隔离锁靠它）；否则 None = ledger_files() 月度分片全集，显示行随之一并点名。
    log_src = log_path if (log_path != LOG_FILE or os.environ.get("HARNESS_LOG_FILE")) else None
    if log_src is None:
        log_path = "reports/gate_log-*.jsonl（月度分片 %d 片）" % len(ledger_files())
    records = load_records(log_src, days=days, last=last)
    rep = build_report(records, load_config(), static_rules())
    if as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render_text(rep, log_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
