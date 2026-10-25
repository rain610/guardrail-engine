# -*- coding: utf-8 -*-
"""guardrail-engine 配置读取（主格式 TOML，标准库 tomllib；旧版 YAML 只作兼容层）。

主格式是 `config/harness.toml`（本机覆盖层 `config/harness.local.toml`）。两者都用
Python 3.11+ 标准库 `tomllib` 解析，零第三方依赖，也不会再出现"自研子集解析器静默
读错"的问题。

旧版 `config/harness.yaml` / `harness.local.yaml` 仍可读（`_load_yaml`，兼容层）：
  · 找不到 TOML 时自动回退到 YAML；
  · 两种格式同时存在时以 TOML 为准，并在 stderr 说明被忽略的是哪一份（不静默）；
  · `HARNESS_CONFIG` 指向的文件按扩展名分派（.toml / .yaml / .yml）。
"""
import hashlib
import os
import sys
import tomllib

# UTF-8 兜底（2026-09-13 评审 v3 H1 系统性修法）：宿主管道可能是 GBK/ASCII，
# 打印中文/✗ 会抛 UnicodeEncodeError；errors=replace 最坏只是个别字符变问号。
# 清单由 sandbox/list_entries.py 机器推导（tests/test_entry_level.py 有对应锁）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HARNESS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # = 引擎根

# 同目录模块（2026-10-25 拆包）：_config 以模块形态被 import 时，调用方通常已把 harness/
# 放进 sys.path；这里补一次兜底，避免按路径加载（importlib）时找不到兄弟模块。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from _errors import ConfigError
from _yaml_compat import parse as _yaml_parse


_warned = set()  # 同一进程内同一条告警只打一次（load 被 gate/all_gates 反复调用）


def _warn(msg):
    """配置诊断统一出口（stderr + 同进程去重）。

    走 stderr 是硬要求（评审 A1 2026-09-15）：走 stdout 会被 shell 的 `$( )` 捕获污染，
    Windows 上每行带 \\r 会让 `[ "$STATUS" = "MISSING" ]` 恒假。
    """
    if msg not in _warned:
        _warned.add(msg)
        sys.stderr.write(f"[config-warn] {msg}" + "\n")


def _note_unsupported(line, why, lineno=None):
    loc = f"第 {lineno} 行" if lineno else "配置中"
    _warn(f"{loc}用了本解析器不支持的写法（{why}）：{line.strip()[:80]}")


def _config_dir():
    """配置目录。**每次调用都从当前 `_HARNESS_ROOT` 算** —— 测试会 monkey-patch 它指到
    临时工程根；若在导入时固化成常量，patch 就失效，用例会去读真仓配置（静默跑错对象）。
    """
    return os.path.join(_HARNESS_ROOT, "config")


def _base_config_path():
    """共享配置：TOML 优先；两者并存时**出声**说明 YAML 那份被忽略。

    与 `_local_config_path` 同一纪律：改了 YAML 却不生效、屏幕上一切正常，
    正是本仓反复栽过的「看起来配好了」。
    """
    d = _config_dir()
    toml = os.path.join(d, "harness.toml")
    yaml = os.path.join(d, "harness.yaml")
    if os.path.isfile(toml):
        if os.path.isfile(yaml):
            _warn("同时存在 %s 与 %s：以 TOML 为准，YAML 那份已被忽略（可删掉或改名）"
                  % (toml, yaml))
        return toml
    if os.path.isfile(yaml):
        return yaml
    raise ConfigError("找不到共享配置：%s（旧版兼容路径 %s 也不存在）" % (toml, yaml))


def _local_config_path():
    """本机覆盖层：HARNESS_CONFIG > harness.local.toml > harness.local.yaml。

    两种格式同时存在时以 TOML 为准，并说清被忽略的是哪一份 —— 否则改了 YAML 不生效，
    而屏幕上一切正常。
    """
    env = os.environ.get("HARNESS_CONFIG")
    if env:
        return env
    d = _config_dir()
    toml = os.path.join(d, "harness.local.toml")
    yaml = os.path.join(d, "harness.local.yaml")
    if os.path.isfile(toml):
        if os.path.isfile(yaml):
            _warn("同时存在 %s 与 %s：以 TOML 为准，YAML 那份已被忽略（可删掉或改名）"
                  % (toml, yaml))
        return toml
    if os.path.isfile(yaml):
        return yaml
    return None


def _load_toml(path):
    """读 TOML。解析失败一律转 ConfigError（语义同旧版 `_load_yaml`，由 run_gate 报 rc=2）。"""
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError("配置文件读取失败：%s（%s）" % (path, e))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError("配置解析失败（TOML）：%s（%s）" % (path, e))
    return data or {}


def _load_file(path):
    """按扩展名读配置：.toml 主格式；.yaml/.yml 走旧版兼容解析器。

    不按内容猜格式：猜错时最坏会"读出一份看起来正常的数据"（本仓最反对的失效形态），
    而扩展名是调用方明确给出的意图。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".toml":
        return _load_toml(path)
    if ext in (".yaml", ".yml"):
        return _load_yaml(path)
    raise ConfigError("不认识的配置文件类型：%s（支持 .toml；旧版 .yaml/.yml 仍可读）" % path)


def load(path=None):
    """读取 harness 配置（2026-09-06 加本地覆盖层，解决多人/多机器路径差异）：
      - 显式传 path（单测/工具直接指文件）→ 只读该文件，按扩展名分派解析器；
      - path=None（生产路径）→ base(config/harness.toml) 上深合并本地覆盖层：
        优先级 环境变量 HARNESS_CONFIG > config/harness.local.toml >
        config/harness.local.yaml（旧版兼容）> 仅 base。
        local 不入库（.gitignore），各人只写自己机器的 repos 路径映射；
        gates 段继承 base，repo 键引用自动解析到本地路径。"""
    if path is not None:
        _reset_policy_signals()
        return _load_file(path)
    # 2026-09-16（评审 v13 复查遗留项②）：三个信号都是「**最近一次** load() 的结果」，
    # 必须在每次调用开头清空。旧实现只在"本地覆盖层文件存在"那条分支里赋值 —— 于是
    # `load(path=显式文件)` 与「HARNESS_CONFIG 指的文件不存在」这两条早退路径会
    # **留着上一次的答案**：谁按「本机现在放行了哪几条」去读它，读到的可能是几分钟前
    # 另一次加载的残值，而它看起来与真话一模一样。
    _reset_policy_signals()
    base = _load_file(_base_config_path())
    local = _local_config_path()
    if local and os.path.isfile(local):
        try:
            override = _load_file(local)
        except Exception as e:
            # local 坏了不能静默跳过（否则 paths 错了又回到"静默放行"假保护）：
            # stderr 先逐条报，同时记进程序化信号 —— `--validate` 会据此 rc=2，
            # 不再在最后一行说"全部健康"。单次 gate 运行仍用 base 继续（一份可选的
            # 本机覆盖层不该阻断所有检查），但"这次降级了"必须能被体检读到。
            _LOCAL_PARSE_ERROR.append((local, str(e)))
            sys.stderr.write(f"[config-warn] 本地覆盖层解析失败已忽略: {local} ({e})" + "\n")
            return base
        if override:
            merged = _deep_merge(base, override)
            # 2026-09-16（剖析报告六节「豁免出口缺少审计」）：放行模式原先是**一句泛泛的提示**，
            # 看不出这次到底动了哪几条策略字段 —— 排查「这台机器上某个 gate 怎么突然变松了」
            # 时等于没有线索。本文件对 repos 路径覆盖早有「允许的动作也要留痕」的约定
            # （见下方 _diff，逐条列出改了哪几个键），而策略覆盖比路径覆盖危险得多，却连
            # 数量都没报。故：先算出「守卫本来会拦下哪几条」（report=False：此时没有任何东西
            # 被忽略，若照常打「已忽略（…）」就是假话），再逐条报出来。
            _allow = (os.environ.get("HARNESS_ALLOW_POLICY_OVERRIDE") or "").strip() in ("1", "true", "yes", "on")
            merged, bad = _guard_policy(base, merged, local, report=not _allow)
            if _allow:
                _policy_notice_once(
                    ("override", local, tuple(sorted(bad))),
                    "[config-policy] ⚠ 已按 HARNESS_ALLOW_POLICY_OVERRIDE=1 放行策略字段覆盖"
                    "（仅调试用，别进共享配置）；本次放行 %d 条：%s\n"
                    % (len(bad), ", ".join(sorted(bad)[:12]) or "无（本次未改动任何策略字段）"))
                # 放行模式下**没有任何字段被退回** —— 不能把 bad 记进「被退回」那个信号，
                # 否则 --validate 会打出一句「…已被忽略」，与事实正好相反。
                _POLICY_REJECTED[:] = []
                _POLICY_OVERRIDDEN[:] = list(bad)
                return merged
            # 2026-09-16（剖析报告 N5）：返回值此前赋了从不用（写死 `_bad`）—— 策略字段被退回
            # 只有 stderr 一行、没有程序化信号，`--validate` 想"汇总报一次"无从下手。
            # 记进模块级供 last_policy_rejection() 读。
            _POLICY_REJECTED[:] = list(bad)
            _POLICY_OVERRIDDEN[:] = []
            # 路径覆盖是允许的（每台机器路径不同），但"改了哪条"必须可见 —— 否则本机把某个
            # gate 指到空目录就等于静默关掉它（评审 v8 的思路：允许的动作也要留痕）。
            _br = base.get("repos") if isinstance(base.get("repos"), dict) else {}
            _mr = merged.get("repos") if isinstance(merged.get("repos"), dict) else {}
            _diff = ["%s: %s → %s" % (k, _br.get(k, "(base 无)"), v) for k, v in _mr.items() if _br.get(k) != v]
            # 2026-09-18（跨平台批次 2，批次 7 收口判据）：共享 harness.yaml 的 repos 段只有
            # **键名**（值一律留空），所以「本机填的路径」必然一条都不来自 base。此时逐条 diff
            # 会打出一整屏「(base 无) → …」，而真正要说的话只有一句：本机这些路径**全部来自**
            # 覆盖层。判据因此落在「本机填了路径、且没有一条来自 base」上（不是「base 为空」——
            # 批次 7 之后 base 不再是空的，那个写法会让这条汇总永远不触发）。
            _filled = [k for k in _mr if _mr[k]]
            if _filled and not any(_br.get(k) for k in _filled):
                _ex = "、".join(sorted(_filled)[:6])
                _policy_notice_once(
                    ("repos-all-local", local, tuple(sorted(_filled))),
                    "[config-policy] 本机 repos 全部来自本地覆盖层（%d 条）：%s%s\n"
                    "                设计如此 —— 共享 config/harness.toml 只声明键名、不含任何机器路径；\n"
                    "                换机器 / 换目录只改这一处，不影响别人。文件：%s\n"
                    % (len(_filled), _ex, "…" if len(_filled) > 6 else "", local))
            elif _diff:
                # 同一台机器上这条内容是不变的（路径映射静态），重复 3–4 遍只是噪音；
                # 但换了配置文件或改了映射关系仍会重报（key 里带了这两者）。
                _policy_notice_once(
                    ("repos", local, tuple(_diff)),
                    "[config-policy] 本地 repos 路径覆盖（允许，仅提示）：%s\n" % "; ".join(_diff[:8]))
            return merged
    elif os.environ.get("HARNESS_CONFIG"):
        sys.stderr.write(f"[config-warn] HARNESS_CONFIG 指向的文件不存在: {local}" + "\n")
    return base


def _load_yaml(path):
    """旧版 YAML 兼容层（主格式已是 TOML，见 `_load_toml`）。

    保留原因：已有本机 `harness.local.yaml` 与外部 `HARNESS_CONFIG` 夹具不因升级当天失效。
    新配置不要再写 YAML —— 这个子集解析器对超纲语法只能"出声"，不能真正解析。

    读 + 解析。**解析崩溃一律转 ConfigError**（2026-09-16 N3）：
    旧实现裸抛 AttributeError/TypeError 逃出 run_gate，Python 以 rc=1 退出 —— 钩子把它
    当"门禁拦截"，人去改代码，而真问题是配置文件本身写坏了。转 ConfigError 后
    run_gate 统一报 rc=2（配置错误），语义归位。
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError as e:
        raise ConfigError("配置文件读取失败：%s（%s）" % (path, e))
    try:
        data = _yaml_parse(lines, _note_unsupported)
    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError("配置解析崩溃：%s（%s: %s）" % (path, type(e).__name__, e))
    return data or {}


# ---------------------------------------------------------------- 策略字段守卫（评审 v8 / Codex P0-1）
# 背景：local / HARNESS_CONFIG 走的是**任意键深合并**，于是本机就能把 gates.<x>.enabled 改成 false、
# 把 mode 降级、把 legacy_baseline 打开、把 script 换成别的路径 —— 这跟本工程 guard 哨兵
# "防自我放行"的目标直接矛盾（文档却只说 local 用来写 repos 路径映射）。
# 处置：策略字段**只认 base**；本地/环境想改就出声并忽略（除非显式 HARNESS_ALLOW_POLICY_OVERRIDE=1）。
# 说明：这不是"防恶意用户"的安全边界（--no-verify 永远存在），而是防"顺手放行"与配置漂移。
_POLICY_KEYS = {
    "enabled", "mode", "script", "repo", "legacy_baseline", "disabled_rules",
    "run_on", "encoding", "skip_node_in_full", "endpoint", "dirs", "gate",
}
# 2026-09-16（剖析报告 N6）：原先这里还有一份 `_LOCAL_ALLOWED_TOP_KEYS`（白名单）**全仓无人引用**，
# 是 _guard_policy 收窄范围时留下的弃用设计 —— 白名单方案已被"只挡已注册 gate 的策略字段"取代
# （理由见该函数 docstring：禁止本地新增顶层键会把 HARNESS_CONFIG 夹具/工具链一起打死）。
# 死配置会让人以为"写了就生效"，按仓库纪律删掉而不是留着。
_POLICY_REJECTED = []   # 最近一次 load() 被退回的策略覆盖项（N5：让 --validate 能汇总报）
# 2026-09-16（剖析报告六节「豁免出口缺少审计」）：与上一行成对 —— 放行模式
# （HARNESS_ALLOW_POLICY_OVERRIDE=1）下**被放行**的策略覆盖项。两者互斥：
# 一次 load() 要么退回了它们、要么放行了它们，不可能同时。分开记是为了让
# 消费方（--validate）能说对话：退回要说「已忽略」，放行要说「已放行，仅调试用」，
# 混用一个列表就会出现「已忽略」配着一串其实生效了的字段。
_POLICY_OVERRIDDEN = []
# 2026-09-16（评审 v13 复查遗留项②，实测 reports/_localyaml_probe2.txt）：本机把 gate 关掉
# （local 的 `enabled: false`，设计上**允许**）时，台账里只有 skipped=true —— 看不出这个 gate
# 是"配置里本来就关着"还是"本机偷偷关的"。这个列表就是那个缺失的事实，供 run_gate 落进台账。
_LOCAL_DISABLED = []
# 2026-10-22（复审报告 §3.1）：本地覆盖层解析失败原先只往 stderr 写一句、然后拿 base 继续，
# 而 `--validate` 最后仍说「全部启用 gate 配置健康」并 rc=0 —— 覆盖层里的路径/策略**一条都
# 没生效**，体检却给绿色结论。这里把失败记成程序化信号，由 --validate 转成 rc=2。
_LOCAL_PARSE_ERROR = []   # [(路径, 原因)]，最近一次 load()


def _reset_policy_signals():
    """清空四个「最近一次 load()」信号（load 的开头调用，见 load 里的说明）。"""
    _POLICY_REJECTED[:] = []
    _POLICY_OVERRIDDEN[:] = []
    _LOCAL_DISABLED[:] = []
    _LOCAL_PARSE_ERROR[:] = []


def last_local_disabled():
    """最近一次 load() 中**被本机覆盖层关掉**的 gate 名列表（未发生时为空）。

    与 last_policy_rejection 的区别：那条是"想改策略、被挡住了"，这条是"允许的关闭，
    但必须留下痕迹"——两者都要能被台账/体检读到，而不是只散落一行 stderr。
    """
    return list(_LOCAL_DISABLED)


def last_local_parse_error():
    """最近一次 load() 中**本地覆盖层解析失败**的 [(路径, 原因)]（未发生时为空）。

    给 `run_gate --validate` 用：覆盖层读不出来时本机路径/策略一条都没生效，
    那时再说"全部健康"就是假绿（复审报告 2026-10-22 §3.1）。
    """
    return list(_LOCAL_PARSE_ERROR)


# 去重记账（"这条已经说过了"）就是**进程环境变量本身**，不再另存一份进程内 set。
# 2026-09-17 施工中发现：两份等价的记账并存时，把 set 那一份整个停掉（突变验证）测试**照样全绿**
# —— 说明其中一份是摆设，而"到底按哪一份去重"会变成下一个问题（本仓有同类先例：
# _config 里那份全仓无人引用的 `_LOCAL_ALLOWED_TOP_KEYS`）。故只留这一份。
# 它同时解决两件事：① 同一进程内多次 load（实测 3~4 遍）② run_gate 起的**检查脚本子进程**
# 继承后不再重复同一句（实测进程内去重后仍有 2 条，第二条来自子进程）。
_POLICY_MSG_ENV = "HARNESS_POLICY_NOTICE_SHOWN"


def _notice_id(key):
    """把 key 压成短指纹（进环境变量用）：避免环境变量里塞非 ASCII 路径被编码环节改形。"""
    return hashlib.sha1(("|".join(str(x) for x in key)).encode("utf-8")).hexdigest()[:16]


def _policy_notice_once(key, text):
    """同一条策略告警只打印一次（本进程，以及 run_gate 起的检查子进程里）。

    2026-09-16 实测（reports/_localyaml_probe2.txt、_probe_polnote.txt）：一次 run_gate 会多次
    调用 load()（all_gates / gate / notify…），同一条 `[config-policy] ⚠ 已按 … 放行` 实测打印
    **3–4 遍**。刷屏的代价不是"多几行"，而是**人不读了** —— 而这行告警恰恰是"本机策略被改过"
    的唯一提示，把它稀释成噪音等于把它删掉。

    跨进程那一半（2026-09-17 补）：进程内去重后实测仍有 **2** 条 —— 第二条来自 run_gate 起的
    检查脚本子进程，它读的是同一份配置、打的是同一句话。同一条事实由同一次命令的父子两端各报
    一遍，读者看到的仍然是重复。故把指纹写进自己的 environ（子进程继承），子进程见到同一条即跳过。
    这只压**完全同一条**（配置来源 + 字段清单都进指纹）：换了配置文件、或改的字段不同，
    照样各报一次 —— 去重 ≠ 少报。指纹用哈希，免得非 ASCII 路径在环境变量里被编码改形后误判。

    只对**文本**去重：程序化信号（last_policy_*）每次 load 照常更新，不受影响。
    （测试若要断言"重复调用会重复打印"，pop 掉 `_POLICY_MSG_ENV` 指向的环境变量即可复位。）
    """
    kid = _notice_id(key)
    seen = (os.environ.get(_POLICY_MSG_ENV) or "").split(";")
    if kid in seen:
        return
    os.environ[_POLICY_MSG_ENV] = ";".join([x for x in seen if x] + [kid])
    sys.stderr.write(text)


def last_policy_rejection():
    """最近一次 load() 中被 _guard_policy 退回的策略字段列表（无则空列表）。

    给 run_gate --validate 用：把"本机覆盖层试图改策略、已被忽略"这件事**汇总报一次**，
    而不是只散落在 load 期的 stderr 里（N5：返回值原先被丢弃，没有任何程序化信号）。
    """
    return list(_POLICY_REJECTED)


def last_policy_override():
    """最近一次 load() 中**被明确放行**的策略字段列表（未走放行开关时为空）。

    与 last_policy_rejection 的区别就是"生效了没有"：那个是没生效的，这个是生效了的。
    放行是调试后门，出了事要能回答「这台机器上到底放行了哪几条」，故让它和退回一样
    有程序化出口，而不是只留一行散落的 stderr。
    """
    return list(_POLICY_OVERRIDDEN)


def _guard_policy(base, merged, source, report=True):
    """把 local/env 对**已存在 gate**的策略字段覆盖退回 base，并出声（返回 (新 cfg, 违规列表)）。

    2026-09-14（评审 v8 / Codex P0-1）：范围刻意收窄 —— 只管"base 里已有的 gate 的策略字段"，
    不再禁止"本地新增 gate / 新增顶层键"：`HARNESS_CONFIG` 也被测试夹具与 verify 工具用来指向
    一份完整配置，把"新增"当违规会把工具链一起打死（实测：hook 契约测试整片报错）。
    真正的风险场景是"把共享 harness.yaml 里的某个 gate 本机降级/关掉"，这条被精确挡住。

    report=False（2026-09-16）：只算出"本来会被拦下哪几条"、不动数据也不出声。
    放行模式（HARNESS_ALLOW_POLICY_OVERRIDE=1）需要这个数字来**说清放行了什么**——
    那时没有任何东西被忽略，若照常打「已忽略（…）」就是一句假话。
    """
    bad = []
    if not isinstance(base, dict) or not isinstance(merged, dict):
        return merged, bad
    bg = base.get("gates") if isinstance(base.get("gates"), dict) else {}
    mg = merged.get("gates") if isinstance(merged.get("gates"), dict) else {}
    for name, val in mg.items():
        if name not in bg or not isinstance(val, dict) or not isinstance(bg.get(name), dict):
            continue                      # 本地新增的 gate / 结构异常：不在本守卫范围
        fixed = dict(val)
        for k, v in list(fixed.items()):
            # 评审 C-4：本地层允许**关闭自己没有的 gate**（enabled: false）——
            # 这是「裁掉自己用不到的」，不是「给共享策略降级」（后者仍然拦）；且**出声**不静默。
            if k == "enabled" and v is False and bg[name].get(k) is not False:
                if report:
                    _LOCAL_DISABLED.append(name)
                    _policy_notice_once(
                        ("local-off", source, name),
                        "[config-local] gate %s 已在本地关闭（只影响本机；"
                        "共享策略未变）\n" % name)
                continue
            if k in _POLICY_KEYS and bg[name].get(k) != v:
                bad.append("gates.%s.%s" % (name, k))
                if report:
                    if k in bg[name]:
                        fixed[k] = bg[name][k]
                    else:
                        fixed.pop(k, None)
        if report:
            mg[name] = fixed
    if bad and report:
        _policy_notice_once(
            ("reject", source, tuple(sorted(bad))),
            "[config-policy] 本地/环境覆盖层试图修改**已注册 gate 的策略字段**，已忽略（%s）：%s\n"
            "                本地覆盖只允许改 repos/paths 路径映射；确需临时改策略请设 "
            "HARNESS_ALLOW_POLICY_OVERRIDE=1（仅调试用，别进共享配置）。\n"
            % (source, ", ".join(sorted(bad)[:12])))
    return merged, bad

def _deep_merge(base, override):
    """dict 递归合并（local 覆盖 base），标量/列表整体替换。"""
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = _deep_merge(base[k], v) if k in base else v
        return out
    return override


# ---------- 完整性兜底（防 mini-yaml 静默解析错误，2026-09-03） ----------
# 只告警不阻断：解析结果结构性异常（**解析不出来**的走 _load_yaml → ConfigError，那是另一条路）
# 评审 A1（2026-09-15 交付前评审「分享前最小动作」#2）：诊断**必须走 stderr** —— 走 stdout 会被 shell 的
# `$( )` 捕获污染，Windows 上每行带 \r 会让 `[ "$STATUS" = "MISSING" ]` 恒假
# （setup_machine 的 ⊘ 死代码 / 抽样只验 1 个 / doctor 必假红 三条同源）。（空/类型错/引用悬空/仓库路径不存在）时打印 [config-warn]。
# 动机：mini-yaml 能力有限（不支持多行字符串/嵌套列表/引号内 #），yaml 一旦扩展超纲，
# 坏处不是报错而是静默解析错数据。
# 2026-09-16：`_warned` / `_warn` 已提到文件顶部（解析器也要用同一出口，见 _note_unsupported），
# 这里不再自带一份。


def _sanity_check(cfg):
    warn = _warn    # 统一出口（同进程去重 + stderr）

    if not cfg:
        warn("config 解析结果为空 —— 请检查 config/harness.toml（旧版 harness.yaml 仍可读）")
        return
    repos = cfg.get("repos")
    if repos is None:
        # `repos:` 段留空 —— 2026-09-18（跨平台批次 2）之后这是**常态**：仓库路径是「每机事实」，
        # 全部下沉到 gitignored 的 config/harness.local.toml，共享文件里那一段刻意为空。
        # 旧版 mini-yaml 对「键下面只有注释」给的是 None（实测），不是 {}；TOML 未写 [repos]
        # 时同样可能缺失 —— 这支兜底对两种格式都成立。
        repos = {}
        cfg["repos"] = repos
    elif not isinstance(repos, dict):
        warn("repos 段解析结果不是字典，配置结构异常")
        repos = {}
        cfg["repos"] = repos
    # ⚠ 上面两处 `cfg["repos"] = repos` 不是多余的：gate() / all_gates() 是从
    #   `cfg.get("repos") or {}` 取路径表的 —— 不写回时，下一段 harness_self 的注入落在一个
    #   没人看的临时对象上，而 None 走 `or {}` 又退回空表：该 gate 的 _repo_path 变 ""。
    #   实测（repos 为空 + 不写回）：tests/test_hooks_chain 里「cwd=工程根 → 纠偏成自检 gate」
    #   那条用例当场红 —— 它正是本次下沉的**硬前提**，不是顺手重构。
    _machine_repos = _machine_keys(repos)
    # 评审报告 2026-09-15 / C-5：harness_self 的「仓」就是工程仓自身 —— 不再取配置里的
    # 绝对路径（那是作者私盘，同事换目录 clone 后指空，而它跑在每次提交链的最前面）。
    # ⚠ 赋值必须在上面那条类型守卫**之后**：repos 缺失/畸形时先赋会 TypeError，
    # 把"只告警不阻断"的兜底整个绕过去（实测 {'repos': None} / {'gates': {}} 都崩）。
    repos["harness_self"] = _HARNESS_ROOT
    gates = cfg.get("gates")
    if gates is None or gates == {}:
        warn("gates 段缺失或为空 —— 门禁将全部失效，请检查 config/harness.toml")
        return
    if not isinstance(gates, dict):
        warn("gates 段解析结果不是字典，配置结构异常")
        return
    missing = []                       # 「仓不在这台机器上」的 gate，汇总成一条（见函数尾）
    undeclared = []                    # 「键名共享配置里没有」的 gate，汇总成一条（真配置错）
    unconfigured = []                  # 「键在、本机没填路径」的已启用 gate，同样汇总（常态）
    for name, g in gates.items():
        if not isinstance(g, dict):
            warn(f"gate {name} 解析结果不是字典，配置结构异常")
            continue
        repo_key = g.get("repo", "")
        rp = (repos or {}).get(repo_key, "")
        if not repo_key:
            warn(f"gate {name} 未配置 repo 指向")
        elif repo_key not in repos:
            # 2026-09-18（跨平台批次 7）：键**不在** repos 段 = 共享配置从未声明过这个名字
            # —— 与「我这台机器没填这个键的路径」（下一支）是两件事，见 _machine_keys。
            undeclared.append((name, repo_key))
        elif not rp:
            if g.get("enabled"):
                unconfigured.append(name)
        elif not os.path.isdir(rp):
            missing.append((name, rp))
        mode = g.get("mode")
        if mode and mode not in ("strict", "loose"):
            warn(f"gate {name} 的 mode '{mode}' 非法（应为 strict/loose）")
        run_on = g.get("run_on")
        if run_on is not None and not isinstance(run_on, list):
            warn(f"gate {name} 的 run_on 解析结果不是列表，配置结构异常")

    if missing:
        # 收敛成**一条**（2026-09-15）：逐 gate 各发一条时，同事机器上 30+ 条诊断会把钩子的
        # 回灌窗口（_compact，40 行）整段占满，把"到底要改哪一行"挤出模型视野
        # —— CI 上 4 条钩子契约锁齐红就是这个形状（见 integrations/hooks/agentguard-gate.py）。
        # 汇总后仍然保留三样信息：数量 / gate 名 / 一条真实路径样例（要看全表用 `run_gate.py --list`）。
        names = "、".join(n for n, _ in missing[:8])
        more = "，等共 %d 个" % len(missing) if len(missing) > 8 else ""
        warn("本机有 %d 个 gate 的仓库路径不存在（多半是没 clone 这些端仓，属常态）："
             "%s%s；例：%s → %s"
             % (len(missing), names, more, missing[0][0], missing[0][1]))

    if undeclared:
        # 2026-09-18（跨平台批次 7）：这一支是**真配置错**，与下面 unconfigured 那支必须
        # 说清区别 —— 两者的现场长得很像（都表现为「gate 跑不了」），但改的地方完全不同：
        # 这里要改**共享** config/harness.toml 的 repos 段（拼错 / 新 gate 忘了登记键名），
        # 那里要改**自己**的 config/harness.local.toml（填路径）。报错指向错地方，
        # 是批次 7 那个事故的一半（同事被指向共享文件，而共享文件里故意不写路径）。
        items = "、".join("%s(repo=%s)" % (n, k) for n, k in undeclared[:8])
        more = "，等共 %d 个" % len(undeclared) if len(undeclared) > 8 else ""
        warn("有 %d 个 gate 引用了 repos 段里**没有声明**的键：%s%s\n"
             "        这是**配置错**：键名拼错，或新加的 gate 忘了在 config/harness.toml 的\n"
             "        [repos] 段登记一行。注意与「本机没配路径」的区别 —— 后者长这样：\n"
             "        键在、值留空（`my_gate = \"\"`），那是常态、不阻断。"
             % (len(undeclared), items, more))

    if unconfigured:
        # 「键在、本机没填路径」= 同事机的常态（批次 2 把路径下沉到 local.yaml 的必然结果）。
        # 刻意**不逐 gate 各报一条**：一台新机器上这就是三十几行，把钩子的回灌窗口
        # （_compact，40 行）整段占满，真正要改的那一行反而被挤出去 —— 与上面两条同源。
        items = "、".join(unconfigured[:8])
        more = "，等共 %d 个" % len(unconfigured) if len(unconfigured) > 8 else ""
        warn("本机有 %d 个已启用 gate 没配仓库路径（本机已填 %d 条）：%s%s\n"
             "        这不是配置错：**键名**在共享 config/harness.toml 里（团队策略），\n"
             "        **路径**在各人 gitignored 的 config/harness.local.toml 里（每机事实）。\n"
             "        首次接入先跑：sh sandbox/setup_machine.sh     # 首次会提示你编辑 local.toml 后重跑\n"
             "        或照 config/harness.local.example.toml 的「场景 A」抄一份 [repos] 段。"
             % (len(unconfigured), len(_machine_repos), items, more))


def changed_files_ex(repo, exts=(), gate_name=None):
    """统一「改动文件」收集器（post-edit / auto-gate 共用）——**带失败信号**版本。

    未暂存 diff + 已暂存 diff + 未跟踪新文件（--untracked-files=all），去重保序，返回绝对路径。
    注意：pre-commit 不用本函数（它只看 staged，语义是只拦真正进历史的内容）。

    返回 `(files, err)`：**err 非空 = 查不了**（repo 无效 / git 命令非 0 / 清单加载失败 / 调用异常）
    → 调用方必须 fail-closed（rc=2 + 说明），**不得**当「无改动」放行。
    2026-09-15 外部评审 v3 P1：旧实现把一切异常吞成 []，于是 auto-gate/post-edit 打印
    「无改动文件，门禁通过」rc=0 —— 与 pre-commit 的 fail-closed 语义不一致的假绿。

    gate_name 非空且属**仓库级 gate**（file_types.is_repo_wide，如 harness_self）时不按扩展名过滤。
    """
    import subprocess
    files = []
    if not repo or not os.path.isdir(repo):
        return files, "目标仓不存在或不是目录：%r" % (repo,)
    repo_wide = False
    if gate_name:
        try:
            _checks = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checks")
            if _checks not in sys.path:
                sys.path.insert(0, _checks)
            import file_types
            repo_wide = file_types.is_repo_wide(gate_name)
        except Exception as e:
            return files, "无法加载检查面清单 checks/file_types.py：%r" % (e,)
    exts = tuple(e.lower() for e in exts)

    def _keep(p):
        # 中 20（评审 v13）：这里原先 `.strip()`。两个调用点的 git 输出都是 **-z**（NUL 分隔），
        # token 本身就是逐字路径 —— Windows 合法文件名可以首尾带空格（` git status` 报 " a.js"），
        # strip 后路径不再存在 → 调用方按「文件不存在」静默跳过（高 7 那条链），叠成完整漏检：
        # 改了东西、门禁一个字没说，台账里还是 PASS。只裁掉 universal-newlines 可能带进来的 \r\n。
        p = (p or "").rstrip("\r\n")
        if not p:
            return None
        if not repo_wide and exts and not p.lower().endswith(exts):
            return None
        q = os.path.normpath(os.path.join(repo, p))
        return None if q in files else q

    # 剥离调用方仓的 git 环境变量（hook 场景 GIT_DIR/GIT_INDEX_FILE/GIT_WORK_TREE
    # 指向端仓，会让本函数对 harness 仓的查询读到端仓的 index —— 2026-09-07 实测误拦）
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY")}
    try:
        # --diff-filter=d：排除**已删除**的文件（pre-commit.py:138 早就这么做了，这里没跟上）。
        # 删除的文件没有内容可查，但路径照样会出现在 diff --name-only 里；交给检查脚本
        # 只会落进"路径不存在"那一类 —— 高 7 修复把这类路径判成 rc=2（fail-closed）后，
        # 「删掉一个 .vue」就会变成一次**假的配置错误拦截**。收集端先把不可能检查的路径摘掉。
        for args in (["diff", "--name-only", "--diff-filter=d", "-z"],
                     ["diff", "--cached", "--name-only", "--diff-filter=d", "-z"]):
            r = subprocess.run(["git", "-C", repo] + args, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=30,
                               env=clean_env)
            if r.returncode != 0:
                return files, ("git %s 失败（rc=%d）：%s"
                               % (" ".join(args), r.returncode, (r.stderr or "").strip()[:160]))
            for p in (r.stdout or "").split("\x00"):
                q = _keep(p)
                if q:
                    files.append(q)
        # 未跟踪新文件：git status --porcelain -z（?? 记录；R/C rename 记录后跟一个原始路径 token，跳过）
        # --untracked-files=all（2026-09-07）：默认模式下新目录只报 "?? dir/"（目录级 token，
        # 不以代码扩展名结尾会被 _keep 过滤 → 新目录里的违规文件漏检）。all 模式逐文件列出。
        r = subprocess.run(["git", "-C", repo, "status", "--porcelain", "-z", "--untracked-files=all"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                           env=clean_env)
        if r.returncode != 0:
            return files, ("git status 失败（rc=%d）：%s"
                           % (r.returncode, (r.stderr or "").strip()[:160]))
        toks = (r.stdout or "").split("\x00")
        i = 0
        while i < len(toks):
            t = toks[i]
            i += 1
            if not t or len(t) < 3:
                continue
            xy = t[:2]
            path = t[3:] if t[2] == " " else t[2:].lstrip()
            if xy == "??":
                q = _keep(path)
                if q:
                    files.append(q)
            elif "R" in xy or "C" in xy:
                i += 1  # 下一个 token 是 rename 的原始路径，跳过
    except Exception as e:
        return files, "调用 git 失败：%r" % (e,)
    return files, ""


def changed_files(repo, exts=()):
    """兼容包装（旧调用方）：查不了时返回 []。

    需要 fail-closed 语义的调用方请用 `changed_files_ex()`。
    """
    return changed_files_ex(repo, exts)[0]

def gate(name, path=None):
    cfg = load(path)
    _sanity_check(cfg)
    repos = cfg.get("repos") or {}
    g = (cfg.get("gates") or {}).get(name, {})
    g["_repo_path"] = repos.get(g.get("repo", ""), "")
    return g


def all_gates(path=None):
    cfg = load(path)
    _sanity_check(cfg)
    repos = cfg.get("repos") or {}
    out = {}
    for k, g in (cfg.get("gates") or {}).items():
        gg = dict(g)
        gg["_repo_path"] = repos.get(g.get("repo", ""), "")
        out[k] = gg
    return out


def _machine_keys(repos):
    """repos 段里**值非空**的键（除引擎注入的 `harness_self`）—— 口径只此一处。

    2026-09-18（跨平台批次 7）：判据从「键在不在」改成「值是不是空的」。批次 7 把键名表
    还给了共享 harness.yaml（33 个键、值留空），于是「键在不在」恒为真、再也区分不出
    任何东西；真正要问的是「本机有没有把这个键填成一条路径」。
    差别是实打实的事故（reports/_batch6_partial_local_finding.txt）：按旧口径，一份只填了
    1 条的 local.yaml 会被判成「本机有机器路径」，另外 32 个未填的键被当成**拼错**而阻断，
    提交链 fail-closed —— 而同事只是照 INSTALL.md 配了自己真有那几个仓。
    """
    if not isinstance(repos, dict):
        return []
    return sorted(k for k in repos if k != "harness_self" and repos[k])


def machine_repos(path=None):
    """本机**真填了路径**的仓库键名（= `_machine_keys(合并后配置的 repos)`）。

    2026-09-18（跨平台批次 2）：机器路径从共享 harness.yaml 下沉到 gitignored 的
    local.yaml 之后，「本机配没配」本身成了一个要判的事实。
    2026-09-18（跨平台批次 7）：口径见 `_machine_keys` —— 定义搬进那个函数，本函数只负责
    「读哪份配置」（`load()`，即 local 覆盖 base 的合并结果；不跑 _sanity_check，无递归）。
    """
    return _machine_keys((load(path) or {}).get("repos"))


if __name__ == "__main__":
    import json
    print(json.dumps(all_gates(), ensure_ascii=False, indent=2))
