# -*- coding: utf-8 -*-
# GBK 兼容（2026-09-07 实测：GUI 触发 hook 时控制台是 GBK，print Unicode 符号崩溃 → 门禁误拦）
import sys, os, io
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
"""
agentguard 统一门禁调度器（对标 Codex hooks / exec 接口）
===============================================================
# 按 gate 名（<仓>_<端> 的约定）从 config 解析策略，调用对应 checks/ 脚本。
各端仓库的 pipeline / git hook 统一调本文件，不直接调 checks/ 下脚本。

用法：
  python harness/run_gate.py <gate名> [文件...]      # 针对性（文件相对仓库根或绝对）
  python harness/run_gate.py <gate名> --all          # 全量扫描
  python harness/run_gate.py <gate名> --mode loose   # 临时宽松
  python harness/run_gate.py --list                  # 列出所有 gate 及启用状态
  python harness/run_gate.py --summary [最近N条]     # 聚合 reports/gate_log.jsonl（默认500）
  python harness/run_gate.py --validate              # 配置体检:遍历全部gate验证路径/脚本/ruleset(改完yaml跑一次)

gate 名示例：example / harness_self（端仓库自定义的 gate 名同样适用）
退出码：0=通过，1=门禁拦截，2=配置错误

可观测性（2026-09-03；2026-09-29 起月度分片）：每次真实执行检查后追加一行 JSON 到
reports/gate_log-YYYY-MM.jsonl（git 忽略；旧单文件 gate_log.jsonl 若在仍被读侧认作最早一片），
记录时间/gate/mode/文件数/结果/耗时，供 --summary 聚合与趋势排查。每行带行内链哈希 h
（改/删任何一行，其后全部对不上），`gate log --verify` 与锚点（gate_log_anchor.jsonl，入库）
对账 —— 篡改可发现三件套：分片有界 / 链抓中间改动 / 锚点抓截断与重写。
它是 tamper-evident 而不是 tamper-proof：能写台账的人也能重算链；信任根在远端、
分支保护与 CI 用干净 checkout 重跑。
"""
import sys, os, json, time, glob, hashlib, subprocess, tempfile, threading, socket, getpass
from contextlib import contextmanager

HARNESS = os.path.dirname(os.path.abspath(__file__))  # = agentguard/harness/
ROOT = os.path.dirname(HARNESS)  # = agentguard 工程根
CHECKS = os.path.join(ROOT, "checks")
LOG_DIR = os.environ.get("HARNESS_LOG_DIR") or os.path.join(ROOT, "reports")
LOG_FILE = os.environ.get("HARNESS_LOG_FILE") or os.path.join(LOG_DIR, "gate_log.jsonl")   # env 覆盖：便于测试密闭
NOTIFY_STATE = os.path.join(LOG_DIR, "notify_last.json")
# 月度分片（2026-09-29）：设了 HARNESS_LOG_FILE（测试密闭）→ 写单文件，行为与旧版逐字一致；
# 不设 → 写「当月片」，台账按月有界 —— 单文件只涨的问题从写入侧就封顶，
# 读侧 _ledger_files() 把旧单文件 + 全部月度片按时间序拼全（旧文件自动成为最早的一片）。
_LEDGER_GLOB = "gate_log-*.jsonl"
NOTIFY_MIN_INTERVAL = 60  # 同 gate 拦截通知最小间隔秒（防 AI 循环触发刷屏）
_VER_CACHE = None        # 规则/引擎版本缓存（VERSION 单一来源；打包路线 2026-09-14 台账字段）


@contextmanager
def _file_lock_ctx(lock_path):
    """跨平台非阻塞文件锁（Windows msvcrt.locking / POSIX fcntl.flock）。

    背景：notify_last.json 的 read-modify-write 在多 worktree / 多 agent 并发抢同一 gate 拦截时
    可能竞争覆盖，导致节流状态丢失 -> 重复发飞书通知。
    哨兵文件（<path>.lock）与目标文件解耦，可对同一目标多次创建锁实例。
    失败抛 BlockingIOError（POSIX）/ PermissionError / OSError（Windows），由调用方降级。
    """
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    locked = False
    try:
        if sys.platform == "win32":
            import msvcrt
            # msvcrt.locking 要求锁区存在：空文件写 1 字节占位再锁
            try:
                if os.fstat(fd).st_size < 1:
                    os.write(fd, b"\x00")
                    os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_NB | fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass  # 解锁失败不影响关 fd（fd 关闭 OS 自动释放记录锁）
        try:
            os.close(fd)
        except Exception:
            pass  # 防 fd 已被外部关闭的双重 close


def _read_stats(path):
    """读子进程写回的规则级统计（覆盖度）。读不到/坏了都返回 {} —— 观测绝不阻断门禁。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _engine_version():
    """台账「规则版本」：读 VERSION（单一来源）；读不到返回空串（不落键）。"""
    global _VER_CACHE
    if _VER_CACHE is None:
        try:
            with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
                _VER_CACHE = f.read().strip()
        except OSError:
            _VER_CACHE = ""
    return _VER_CACHE


# 「读不出削弱信号」这句提示在同一个进程里只说一遍（与 _config 的策略告警同理：重复=噪音）。
_WEAKEN_WARNED = set()


def _weaken_marks(gate_name):
    """本次判定「被削弱过」的痕迹列表（没有则返回空 = 台账不写该字段）。

    2026-09-16（评审 v13 复查遗留项②，用户拍板；实测见 reports/_localyaml_probe2.txt）：
    两条**被允许的**削弱路径原先在台账里与「真检查过且通过」不可区分 ——
      · 本机把 gate 关掉（`config/harness.local.toml` 的 `enabled = false`，设计上允许）：
        台账只多一个 `skipped: true`，看不出是"本地关的"还是"配置里本来就关着"；
      · `HARNESS_ALLOW_POLICY_OVERRIDE=1` 放行策略覆盖：**一个字段都不多**，
        与真通过逐字相同（实测那一行的全部键就是 gate/rc）。
    项目自称"出问题先看台账"，而这两条恰是最该被看见的路径：实测整本 gate_log.jsonl 里
    `enabled` / `override` / `policy` / `local` 四个关键词一个都搜不到。
    有了这个字段，一条 `grep weakened reports/gate_log.jsonl` 就能捞出全部被削弱的判定。

    读不出信号时（混装/半残部署）：**出声**一次，然后按"没有削弱"返回 —— 两头都不能骗：
    既不能默认"有削弱"（那是另一个方向的假话），也不能**不出声**地按"没削弱"处理
    （第四问：新机制最容易在边界上悄悄报平安）。判定本身不受影响（台账绝不反过来挡门禁）。
    """
    marks = []
    try:
        sys.path.insert(0, HARNESS)
        import _config
        if gate_name in _config.last_local_disabled():
            marks.append("local_disabled")
        pref = "gates.%s." % gate_name
        # 只记**本 gate** 的放行项：拿别的 gate 的覆盖来标记这一行，等于说假话。
        marks += ["policy_override:" + e for e in _config.last_policy_override()
                  if e.startswith(pref)]
    except Exception as e:
        if "signals" not in _WEAKEN_WARNED:
            _WEAKEN_WARNED.add("signals")
            sys.stderr.write("[run_gate] ⚠ 读不出策略削弱信号（%r）→ 本次台账不标 weakened，"
                             "判定本身不受影响。\n" % (e,))
    return list(dict.fromkeys(marks))      # 去重保序：同一条事实只该出现一次（别让台账吓人）


def _mode_override(g, mode_val):
    """本次判定被 `--mode` 覆盖时的痕迹 → `(台账 override 值, weakened 标记列表)`。

    **为什么会需要这个**（2026-09-23 评审报告 H1 实测）：`--mode` 是 run_gate 的位置参数
    旗标，而三个入口把调用方给的"文件名"原样拼在 gate 名之后 —— 一个内容为 `--mode` 的
    "文件"就能把判定口径从 strict 翻成 loose（实测 PC-COMP 拦截 rc=1 → 放行 rc=0），
    **且台账那一行与"真检查过且通过"逐字不可区分**（没有 mode、没有 weakened）。
    与 2026-09-16 修掉的 `--gate` 冒充同类。收口在调用方（见 agentguard 的 flag_like）；
    引擎侧无法区分"人敲的 --mode"与"files 注入的 --mode"，所以这里退而求其次：
    **让它至少留痕**。

    返回两样东西而不是一样，是因为要记的是两件不同的事（与 eol/weakened 的分工同理）：
      · override：**当时按什么口径判的** —— 放宽、收紧都记（干净跑不出现，用了才出现）；
      · weakened：**谁把它放宽了** —— 只有 loose 方向记。把"收紧"也塞进 weakened 是说假话，
        而少记一次放宽等于台账骗人，两个方向都不能将就。

    与配置同值时**不记**（那不是覆盖；每行都挂一个标记 = 标记没有信息量，同 _log_run 的纪律）。
    """
    if mode_val not in ("strict", "loose"):
        return None, []                 # 非法值端脚本本就不采纳（pc_code_check.py:485），不算覆盖
    cfg = g.get("mode") if g.get("mode") in ("strict", "loose") else "strict"
    if mode_val == cfg:
        return None, []
    note = "%s(config=%s)" % (mode_val, cfg)
    return note, (["mode_override:" + note] if mode_val == "loose" else [])


def _log_file():
    """本次判定写进哪本台账（写入唯一入口，_log_run 与读侧判据都从这走）。

    设了 HARNESS_LOG_FILE（测试密闭）→ 逐字用它（旧单文件行为，现有 tests 靠它隔离）；
    没设 → 当月片 `gate_log-YYYY-MM.jsonl`。分片让台账按月有界：写 O(1) 不变，
    每片有天然边界（月），读侧按时间序拼全集 —— 旧单文件 gate_log.jsonl 若在，
    自动成为最早的一片（_ledger_files 认它）。
    """
    env = os.environ.get("HARNESS_LOG_FILE")
    if env:
        return env
    return os.path.join(LOG_DIR, "gate_log-%s.jsonl" % time.strftime("%Y-%m"))


def _ledger_files():
    """台账全集，时间序（读侧唯一判据）：旧单文件（若有，排最前）+ 月度片（字典序=月序）。

    env 密闭 → 只有 HARNESS_LOG_FILE 那一本（与旧版逐字一致）。
    三个读者共用本函数：--summary（本文件）/ coverage.py（跨文件契约）/ 壳侧
    agentguard/cli.py 的 _ledger_paths（壳不 import 引擎，两份实现靠
    tests/test_gate_log_anchor.py 的对拍锁咬住 —— 漂移即红，与 archive_name 三份同款纪律）。
    """
    env = os.environ.get("HARNESS_LOG_FILE")
    if env:
        return [env]
    files = []
    legacy = os.path.join(LOG_DIR, "gate_log.jsonl")
    if os.path.exists(legacy):
        files.append(legacy)
    files.extend(sorted(glob.glob(os.path.join(LOG_DIR, _LEDGER_GLOB))))
    return files


def _tail_h(path):
    """一片台账的链尾 h（最后一条带 h 的记录；旧格式行不参与链，跨过去找）。

    从文件尾回扫 64KB —— 分片有界，最坏也只回扫本片；解析不了/读不到返回 ""
    （空链头），由调用方按「链从这里起头」处理。台账是观测输出，读失败
    绝不反过来炸掉写入（与 _log_run 的失败纪律同向）。
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            chunk = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for line in reversed([l for l in chunk.splitlines() if l.strip()]):
        try:
            h = json.loads(line).get("h")
        except Exception:
            continue
        if h:
            return str(h)
    return ""


def _chain_head():
    """跨片链尾：按时间序从最新片往回找第一条带 h 的尾行。

    跨月第一行接上一片的链尾 —— 分片不断链，这是「月度分片」与「行内链哈希」
    能叠加的前提（_chain_h 的 prev 参数从这里来）。
    """
    for p in reversed(_ledger_files()):
        h = _tail_h(p)
        if h:
            return h
    return ""


def _chain_h(prev_h, rec):
    """行内链哈希：h = sha256(上一行h + 本行规范化JSON) 截 16 hex。

    规范化 = sort_keys + 紧凑分隔符 + ensure_ascii=False + **去掉 h 本身**。
    效果：改/删中间任何一行，其后所有 h 全部对不上 —— 「台账被人改过」从
    无声变成 `gate log --verify` 一条命令可点名。壳侧 cli.py 有一份同公式实现
    （壳不 import 引擎，跨文件契约，tests 对拍锁咬住）。
    """
    payload = dict(rec)
    payload.pop("h", None)
    canon = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(((prev_h or "") + canon).encode("utf-8")).hexdigest()[:16]


_ENGINE_SHA_CACHE = []


def _engine_files():
    """判定所用引擎的内容面：规则、调度、审计器与锁清单。"""
    files = []
    for sub in ("checks", "harness"):
        base = os.path.join(ROOT, sub)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for fn in sorted(filenames):
                if fn.endswith(".py") or (sub == "checks" and fn in
                                          ("components_allowlist.yaml", "components_allowlist_h5.yaml")):
                    files.append(os.path.relpath(os.path.join(dirpath, fn), ROOT))
    for rel in ("config/harness.toml", "config/harness.yaml", "sandbox/mutation_smoke.py",
                "tests/test_lock_inventory.py", "tests/lock-inventory.json"):
        if os.path.isfile(os.path.join(ROOT, rel)):
            files.append(rel)
    return sorted(set(f.replace("\\", "/") for f in files))


def _engine_sha(files=None):
    """引擎内容哈希（16 hex）：同一份判定代码必须得到同一个值。

    与 ver 的区别：VERSION 只说明「哪一版」，本值说明「这次实际跑的是哪份内容」——
    瞬时改弱规则再改回来，两条台账的 engine_sha 不同（2026-10-21 外部评审 5.7-A）。
    读不到的文件按 `<missing>` 计入，让缺失本身也改变哈希，不静默跳过。
    """
    if files is None and _ENGINE_SHA_CACHE:
        return _ENGINE_SHA_CACHE[0]
    h = hashlib.sha256()
    for rel in (files if files is not None else _engine_files()):
        p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        h.update(rel.replace("\\", "/").encode("utf-8") + b"\0")
        try:
            with open(p, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
        except OSError:
            h.update(b"<missing>")
    out = h.hexdigest()[:16]
    if files is None:
        _ENGINE_SHA_CACHE.append(out)
    return out


def _log_run(gate_name, scope, n_files, rc, ms, rules=None, skipped=False, weakened=None,
             eol=None, mode_override=None):
    """追加一条运行记录（失败**出声**但不阻断——日志不能反过来挡门禁）。

    skipped=True：本次**没有执行任何规则**（gate 未启用），只留一条可区分的痕迹 ——
    否则台账里「跳过」与「跑了且通过」都是 rc=0、没有任何字段差异（评审 v13 中 15）。

    weakened：本次判定被**允许的**手段削弱过（本地关 gate / 调试后门放行策略覆盖 /
    把 strict 门禁按 `--mode loose` 放宽）时的痕迹列表，见 _weaken_marks、_mode_override。
    空则**不写该字段** —— 让"干净跑"的行保持干净，
    标记才有信息量（每行都挂一个空标记，等于没标记）。

    eol：本 gate 期望行尾的**来源**（`{"value","source"}`，由检查子进程经
    `HARNESS_STATS_FILE` 回传；plan §3.5(c) 防护 3）。落成两个平铺键而不是嵌对象，
    为的是 `grep eol_source reports/gate_log.jsonl` 这一句能直接用 —— 台账的全部价值就在这。
    **与 weakened 的区别**：weakened 记"谁动了手脚"（干净跑不出现），eol 记"当时凭什么定的"
    （干净跑照样有）。同一条纪律：空则不写。

    mode_override：**调用方用 `--mode` 改了口径**且与 gate 配置不同时的 `值(config=值)`
    （2026-09-23 H1 接线；与 eol 同属"当时按什么判的"，故收紧方向也记）。
    `grep mode_override reports/gate_log.jsonl` 一句话捞出全部被覆盖过的判定。

    h / sv（2026-09-29）：行内链哈希 + 分片 schema 版本。h = sha256(上一行h + 本行规范化
    JSON) 截 16 hex，链尾跨月接上一片（分片不断链）；sv=1 只写片首行。改/删中间任何
    一行，其后所有 h 全部对不上 —— `gate log --verify` 一条命令点名，静默改账从此不可能。
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "gate": gate_name,
               "scope": scope, "files": n_files, "rc": rc, "ms": ms}
        # 批次 3（2026-10-15）：**直调兜底** —— entry 原先只在环境变量存在时落字段，于是
        # doctor / demo_gate / acceptance 等直接调 run_gate 的路径与手敲命令在台账里全是
        # 空 entry（10 月实测 79 条），「谁跑的」永久无法归因（CodeBuddy 漏跑排查现场正卡在这）。
        # 无标者记 "run_gate"；各直调工具另行显式命名（doctor / demo / acceptance）。
        entry = (os.environ.get("HARNESS_ENTRY") or "").strip() or "run_gate"
        rec["entry"] = entry
        # 中 16（评审 v13）：四个入口原先一律 setdefault（外层已有的值永远胜出）或干脆不设 ——
        # 于是「MCP 起 auto-gate」这类链路会把**真实执行者**记成上游、post-edit/pre-edit 干脆
        # 记成空。现在各入口硬赋值自己的名字，上游是谁另记一份，两个事实都在台账里。
        via = (os.environ.get("HARNESS_ENTRY_VIA") or "").strip()
        if via:
            rec["via"] = via
        if skipped:
            rec["skipped"] = True
        if weakened:
            rec["weakened"] = list(weakened)
        if mode_override:
            rec["mode_override"] = mode_override
        if eol:                      # 行尾来源（防护 3）；空 = 这个 gate 不涉及行尾，不落键
            rec["eol_source"] = eol.get("source")
            rec["eol_value"] = eol.get("value")
        ver = _engine_version()                                   # 打包路线：规则版本（VERSION 单一来源）
        if ver:
            rec["ver"] = ver
        rec["engine_sha"] = _engine_sha()   # 这次判定实际用的内容（5.7-A：抓瞬时改弱）
        if rules:            # 覆盖度（2026-09-12 第四轮）：规则级触发/豁免统计
            rec["rules"] = rules
        target = _log_file()
        if not os.path.exists(target) or os.path.getsize(target) == 0:
            rec["sv"] = 1        # 片首行自描述 schema 版本；sv 进规范化载荷，链把首行一并盖住
        # 行内链（2026-09-29）：「取链尾 → 算 h → 追加」在文件锁窗口内完成，防并发分叉；
        # 抢锁失败降级为不加锁尽力写 —— h 可能基于旧链尾形成分叉，校验侧（gate log --verify）
        # 会把分叉行点名：可见的诚实例外，台账绝不因为锁反过来拦门禁。
        try:
            with _file_lock_ctx(target + ".lock"):
                rec["h"] = _chain_h(_chain_head(), rec)
                with open(target, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            rec["h"] = _chain_h(_chain_head(), rec)
            with open(target, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        # 2026-09-16（剖析报告 N8）：原先是裸 `except Exception: pass` —— 全仓唯一一处
        # "写失败没人知道"的台账（对照：运维台账 agentguard/cli.py 的 ops 写入会打印）。
        # 台账是审计链的一环，哑掉等于审计链静默断；仍然**不阻断**（日志绝不挡提交），但出声。
        sys.stderr.write(f"[run_gate] ⚠ 判定台账写入失败（不影响本次判定）：{e}\n")
    if rc == 1:  # 只在拦截时通知；通过/配置错不推（本地噪音）
        _notify_on_block(gate_name, scope, n_files, rc, ms)


# ---------- 拦截推送飞书（个人模式，2026-09-06） ----------
# 配置来源：config/harness.local.toml（个人）或共享 config/harness.toml 的 notify 段。
# 未配置 = 完全关闭，零影响（不发请求、不打印、不报错）。
# 原则与 _log_run 一致：通知绝不能反过来拖慢/阻断门禁——daemon 线程 + 短超时 + 全静默。

def _notify_webhook():
    """读取个人/共享 notify.feishu_webhook；未配置返回 None。"""
    try:
        sys.path.insert(0, HARNESS)
        import _config
        for path in (None,):  # None = 生产加载链（含 local 覆盖层）
            cfg = _config.load(path)
            n = (cfg.get("notify") or {}) if isinstance(cfg, dict) else {}
            url = (n.get("feishu_webhook") or "").strip()
            if url:
                return url
    except Exception:
        pass
    return None


def _notify_throttled(gate_name):
    """同 gate 60 秒内已推送过则 True（节流状态文件坏了视为未节流）。
    并发抢锁失败时降级为未节流（项目原则：出问题先看台账，宁可重发不漏拦截信息）。"""
    try:
        # 目录必须**先于**取锁建好：哨兵锁文件 `<state>.lock` 就住在这个目录里，
        # 目录不存在时 os.open 当场 ENOENT（OSError），被下面的降级分支吞掉返 False ——
        # 症状不是报错，而是节流**静默失效**（重复发飞书通知）：状态一个键都写不进去，
        # 而"建目录"那行原先写在 with 里面，取锁失败就永远走不到（死代码）。
        # 目录取自 NOTIFY_STATE 自身（不是 LOG_DIR），这样测试把 NOTIFY_STATE 指到别处时
        # 建的也是那个目录 —— 两处必须同源，否则又会漂。
        _dir = os.path.dirname(NOTIFY_STATE)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        with _file_lock_ctx(NOTIFY_STATE + ".lock"):
            state = {}
            if os.path.exists(NOTIFY_STATE):
                with open(NOTIFY_STATE, encoding="utf-8") as f:
                    state = json.load(f)
            now = time.time()
            if now - float(state.get(gate_name, 0)) < NOTIFY_MIN_INTERVAL:
                return True
            state[gate_name] = now
            with open(NOTIFY_STATE, "w", encoding="utf-8") as f:
                json.dump(state, f)
            return False
    except (BlockingIOError, PermissionError, OSError):
        # 并发抢锁失败 / 锁文件 IO 异常 → 降级为未节流（宁可重复通知不漏拦截信息）
        return False
    except Exception:
        return False


def _notify_send(url, text):
    """POST 飞书自定义机器人（标准库 urllib；异常全静默）。"""
    try:
        import urllib.request
        req = urllib.request.Request(
            url, data=json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def _notify_on_block(gate_name, scope, n_files, rc, ms):
    """拦截通知入口：未配置直接 return（零开销路径）；daemon 线程后台发。"""
    try:
        url = _notify_webhook()
        if not url:
            return
        if _notify_throttled(gate_name):
            return
        who = f"{getpass.getuser()}@{socket.gethostname()}"
        text = (f"🚫 门禁拦截 [{gate_name}] · {who}\n"
                f"文件数: {n_files} · 耗时: {ms/1000:.1f}s · {time.strftime('%H:%M:%S')}\n"
                f"明细: 到对应端仓跑 python harness/run_gate.py --summary 或看提交输出")
        t = threading.Thread(target=_notify_send, args=(url, text))
        t.start()  # 非 daemon：拦截时进程即将退出，须等线程发完(≤3s)；放行路径不起线程零开销
    except Exception:
        pass


def _summary(limit=500):
    files = [p for p in _ledger_files() if os.path.exists(p)]
    if not files:
        print(f"(暂无运行日志: {LOG_DIR})")
        return 0
    lines = []
    for p in files:
        with open(p, encoding="utf-8", errors="replace") as fh:
            lines += fh.read().splitlines()
    lines = [l for l in lines if l.strip()][-limit:]
    recs = []
    for l in lines:
        try:
            recs.append(json.loads(l))
        except Exception:
            pass
    if not recs:
        print("(日志为空或不可解析)")
        return 0
    total = len(recs)
    blocked = sum(1 for r in recs if r.get("rc") == 1)
    err_cfg = sum(1 for r in recs if r.get("rc") == 2)
    # 2026-09-17（评审 v13 复查遗留项②）：摘要原先只数 rc —— 于是「本机把 gate 关了」
    # 和「跑了且通过」在这里也是同一个数字（都是没被拦截的那一堆）。这两类判定恰恰是
    # 最该被看见的，所以摘要要单独把它们的次数与 gate 名点出来。
    skipped = sum(1 for r in recs if r.get("skipped"))
    weakened = sorted({r.get("gate", "?") for r in recs if r.get("weakened")})
    n_weak = sum(1 for r in recs if r.get("weakened"))
    by_gate = {}
    for r in recs:
        g = by_gate.setdefault(r.get("gate", "?"), {"n": 0, "block": 0, "ms": 0})
        g["n"] += 1
        g["block"] += 1 if r.get("rc") == 1 else 0
        g["ms"] += r.get("ms", 0)
    # 2026-09-23 评审报告 L16（实测）：台账里混着**历史测试条目**（`t_pc` 18 / `test_pc` 7 /
    # `t_on` 6 / `t_off` 2 / `myproj_pc` 1 / `tmpc_pc` 1，都 ≤2026-09-16）—— 它们是夹具在临时
    # 配置下**真跑出来的判定**（不是伪造），但当时用的不是这份配置；与受管 gate 平铺在一张表里，
    # 读者会读成"这台机器有这么多 gate"。实测修复前 `--summary` 正是把 `test_pc`/`t_on`/`t_off`
    # 当受管 gate 列了出来（评审报告点名的就是这个）。
    #
    # 处置取**标注**，不取删除：这是审计日志，**删历史行本身就是一次不可审计的动作**；
    # 错的不是那几行记录，是聚合时没区分来源。判据 = 当前配置里有没有这个名字
    # （与 `_config_gates` / `--list` 同一条判据，不另立一套"哪些名字像夹具"的猜测规则）。
    # ★ 比例/计数**仍按窗口内全部行**算：那句话的字面意思是"最近 N 条里有多少被拦"，
    # 无论来源都成立；把未登记名的条数与拦截数在上方单列出来，读者自己就能扣 ——
    # 悄悄改口径会让老读者读到的数与原来对不上，那是另一种失真。
    gates_cfg, cfg_err = _config_gates()
    known = set() if cfg_err else set(gates_cfg)
    unregistered = {k: v for k, v in by_gate.items() if k not in known}
    n_unreg_rows = sum(v["n"] for v in unregistered.values())
    title = (f"=== 门禁运行摘要（最近 {total} 条，共 {len(by_gate) - len(unregistered)} 个 gate）==="
             if not cfg_err else
             f"=== 门禁运行摘要（最近 {total} 条，共 {len(by_gate)} 个 gate）===")
    print(title)
    print(f"  拦截 {blocked} 次 ({blocked*100//total}%)  配置错误 {err_cfg} 次")
    print(f"  跳过(未执行任何规则) {skipped} 次   被削弱 {n_weak} 次"
          + (f"（{', '.join(weakened)}）" if weakened else ""))
    if n_weak:
        print("    被削弱 = 本机把该 gate 关了（local_disabled）或走了调试后门放行策略覆盖"
              "（policy_override:…）；细节见台账 weakened 字段。")
    print(f"  {'gate':<18}{'次数':>6}{'拦截':>6}{'平均ms':>9}")
    for k in sorted(by_gate, key=lambda x: -by_gate[x]["n"]):
        if k in unregistered:
            continue
        g = by_gate[k]
        print(f"  {k:<18}{g['n']:>6}{g['block']:>6}{g['ms']//max(g['n'],1):>9}")
    if unregistered:
        print(f"  ── 另有 {len(unregistered)} 个**未登记名**（共 {n_unreg_rows} 条，"
              f"其中拦截 {sum(v['block'] for v in unregistered.values())} 次；"
              f"上方比例含它们）──")
        print("    这些名字**不在当前 config 里**：历史夹具记录（测试在临时配置下真跑出来的判定，"
              "不是伪造），")
        print("    或已改名的旧 gate。审计日志不删历史行，故在此单列，不与受管 gate 混算。")
        for k in sorted(unregistered, key=lambda x: -unregistered[x]["n"]):
            g = unregistered[k]
            print(f"  {k:<18}{g['n']:>6}{g['block']:>6}{g['ms']//max(g['n'],1):>9}")
    if cfg_err:
        print("  ⚠ 配置读不出来 → **无法区分**登记过的 gate 与历史名，上面按全量列出（不猜）。")
    last = recs[-1]
    # 2026-09-17：这里原先直接下标取 scope/ts/gate —— 台账里出现一条**结构不全**的行
    # （换过 HARNESS_LOG_FILE、被别的工具追加过、手工编辑过）就让 `--summary` 抛 KeyError，
    # Python 以 **rc=1** 退出，而 rc=1 的契约语义是「代码不合规」。摘要是个只读报表，
    # 它不该有能力报出一个方向相反的结论。缺字段就按缺字段显示。
    print(f"  最近一次: {last.get('ts', '?')} {last.get('gate', '?')} "
          f"{last.get('scope', '?')} rc={last.get('rc', '?')}")
    return 0


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


def _dispatch(argv):
    """纯参数分流：把「不看 gate、只看 argv[0]」的自检/查询入口挑出来。

    返回 rc；**返回 None = 不是分流入口**，调用方接着按 gate 名走主流程。
    与主流程分开，是为了让「哪些 argv 根本不进调度器」一眼可见 —— 原先这四条
    分支与后面 110 行调度逻辑混在同一个 main() 里，改任一边都得通读全文。

    纪律：本函数只认 argv，不读不写任何模块级状态（`TestNotify` 直接打桩
    run_gate 的 8 个模块级名字，通知链因此必须留在 main() 那一侧，不许搬进来）。
    """
    if argv and argv[0] in ("--coverage",):
        # 覆盖度自评（2026-09-12 第四轮）：按路径加载 harness/coverage.py
        # （不写 `import coverage`——那会跟同名第三方包打架）
        import importlib.util as _ilu
        _p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coverage.py")
        _spec = _ilu.spec_from_file_location("harness_coverage", _p)
        _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
        return _m.main(argv[1:])
    if argv and argv[0] in ("--summary",):
        limit = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 500
        return _summary(limit)
    if argv and argv[0] == "--engine-sha":
        # 引擎内容哈希的**只读出口**：给 CI 校验提交尾注、给 `gate log --verify --engine`
        # 反查台账，都从这里取同一个值（不另写一份哈希实现）。
        # 刻意放在配置加载之前：配置坏了也要能问「这次跑的是哪份引擎内容」。
        print(_engine_sha())
        return 0
    if argv and argv[0] in ("--validate", "-V"):
        # --structure-only（CI 用）：端仓不在机器上时不阻断（见 _validate docstring）
        return _validate(structure_only="--structure-only" in argv)
    if not argv or argv[0] in ("--list", "-l"):
        gates, cfg_err = _config_gates()
        if cfg_err:
            print(f"[run_gate] ✗ 配置读不出来（rc=2）：{cfg_err}")
            return 2
        print("=== agentguard gates ===")
        for k, g in sorted(gates.items()):
            print(f"  [{'ON ' if g.get('enabled') else 'off'}] {k}: {g.get('script')} @ {g.get('_repo_path') or '(未配置)'}")
        # 2026-09-18（跨平台批次 2）：逐行 `@ (未配置)` 在同事的新机器上是**一整屏**，
        # 而真正要说的只有一句「本机还没配路径」。harness_self 除外 —— 它的路径由引擎注入，
        # 永远有值，拿它一起判会让「全部未配置」这个条件永不成立。
        _live = [k for k, g in gates.items() if g.get("enabled") and k != "harness_self"]
        _nopath = [k for k in _live if not gates[k].get("_repo_path")]
        # 2026-09-18（跨平台批次 2 补齐）：路径**配了**、但本机不存在 —— 同事换盘符 / 换用户名 /
        # 在 WSL 上 clone 之后最常见的形态，而它此前在这里是**完全静默**的：逐行打 33 个
        # `@ D:/MyProject/...`，没有一行是真的，而每一行看起来都正常（换机器形态里最隐蔽的一种）。
        # 判据看**磁盘**（isdir），与 `--validate` 的 ⊘ 同源 —— 「配置里写没写」与「在这台机器上
        # 在不在」是两件事；本批之所以要治静默，正是因为后者从来没有谁替你看。
        _missing = [k for k in _live
                    if gates[k].get("_repo_path") and not os.path.isdir(gates[k]["_repo_path"])]
        if _live and len(_nopath) == len(_live):
            print(f"\nℹ 启用的 {len(_live)} 个受管 gate **全部**没有仓库路径 —— 本机此刻一个门禁也不生效。")
            print("  这不是配置坏：共享 config/harness.toml 里**不含**任何机器路径，")
            print("  路径全在 gitignored 的 config/harness.local.toml（各人一份）。首次接入：")
            print("      sh sandbox/setup_machine.sh    # 首次会提示你编辑 config/harness.local.toml 后重跑")
        elif _missing and not _nopath:
            print(f"\nℹ 启用的 {len(_live)} 个受管 gate **全部**指向本机不存在的路径 —— 本机此刻一个门禁也不生效。")
            print("  路径是**配好的**（来自 config/harness.local.toml），但那些目录在这台机器上不存在。")
            print("  常见成因：换了盘符 / 换了用户名，或在 WSL 里 clone（那里要写 WSL 视角的路径，如 /mnt/d/...）。")
            print("  核对：sh sandbox/setup_machine.sh")
        elif _nopath:
            print(f"\nℹ {len(_nopath)}/{len(_live)} 个启用的受管 gate 没有仓库路径：{', '.join(_nopath[:6])}")
            if _missing:
                print(f"  ℹ 另有 {len(_missing)}/{len(_live)} 个指向本机不存在的路径：{', '.join(_missing[:6])}")
        elif _missing:
            print(f"\nℹ {len(_missing)}/{len(_live)} 个启用的受管 gate 指向本机不存在的路径：{', '.join(_missing[:6])}")
        return 0
    return None


def main():
    args = sys.argv[1:]
    rc = _dispatch(args)
    if rc is not None:
        return rc

    gate_name = args[0]
    rest = args[1:]
    # 2026-09-14 修复（采纳评审观察项方案 B，fail-closed）：未登记名此前与「已禁用」同路径——
    # 打印「未启用，跳过」并 rc=0；gate 改名/拼错会变成"假绿"（调用方以为检查过了）。
    # 现在：未登记 → 明确 rc=2；已登记但 enabled=false 仍保持「未启用，跳过」rc=0。
    _gates, _cfg_err = _config_gates()
    if _cfg_err:
        print(f"[run_gate] ✗ 配置读不出来（rc=2；注意这不是「代码不合规」）：{_cfg_err}")
        return 2
    if gate_name not in _gates:
        print(f"[run_gate] gate {gate_name} 未登记：可能拼错或已改名；用 --list 查看全部已登记 gate。")
        return 2
    g = _gates[gate_name]
    if not g.get("enabled", False):
        # 中 15（评审 v13）：这条 rc=0 此前与「跑了且通过」在**任何地方**都不可区分 ——
        # MCP 把它渲染成 ✅ rc=0 PASS，台账里连一条记录都没有（_log_run 在子进程之后）。
        # 退出码不动：0/1/2/3 是跨三入口的契约，且 auto-gate 对 enabled=false 的 gate 本就
        # 按 rc=0 放行。改的是**可区分性**：台账落一条 skipped 记录，MCP 侧按 gate 名单
        # 识别后渲染 ⏭ SKIPPED，不再冒充 PASS。
        print(f"[run_gate] gate {gate_name} 未启用（enabled: false），跳过"
              f"（本次**未执行任何规则**，不等于检查通过）。")
        _log_run(gate_name, "skipped", 0, 0, 0, skipped=True,
                 weakened=_weaken_marks(gate_name))
        return 0
    # 评审 v9（Codex P1-1）：调用方显式指定的**实际目标仓**优先 —— config 的 repo 只是策略归属
    # （pre-commit.py:105 早就这么声明了，但调度器一直没接）。worktree 场景二者不同：
    # 旧行为拿主仓当基准算 rel/changed_lines → 新违规被当存量 → rc=0 放过（已实测复现）；
    # 而且它还把 `--repo <路径>` 当"未知参数 + 文件"，把目录塞给检查器 → 假拦 Permission denied。
    _rest, _i, repo_override = [], 0, ""
    while _i < len(rest):
        if rest[_i] == "--repo":
            if _i + 1 >= len(rest):
                print("[run_gate] ✗ --repo 缺参数值 → 不放行（fail-closed）")
                return 2
            repo_override = rest[_i + 1]; _i += 2; continue
        _rest.append(rest[_i]); _i += 1
    rest = _rest
    if repo_override:
        # 相对路径按**调用方 cwd**解析后立刻绝对化：下面的子进程以 cwd=HARNESS 启动，
        # 相对路径留在 argv 里会被解析到引擎仓（在受管仓里执行 `--repo .` 时尤其致命）。
        repo_override = os.path.abspath(repo_override)
        if not os.path.isdir(repo_override):
            print(f"[run_gate] ✗ --repo 指定的路径不是目录: {repo_override} → 不放行（fail-closed）")
            return 2
        print(f"[run_gate] 检查基准=调用方指定仓 {repo_override}（策略归属: {g.get('_repo_path')}）")
    repo = repo_override or g.get("_repo_path")
    if repo:
        repo = os.path.abspath(repo)
    if not repo or not os.path.isdir(repo):
        print(f"[run_gate] gate {gate_name} 仓库路径无效: {repo}")
        return 2
    script = None
    # 阶段2（2026-09-06）：gate→脚本映射走 checks/registry.py 显式注册（新增规则集
    # 不再改 run_gate）；registry 读不到或未登记该 gate 时回退 config 的 script 字段。
    try:
        sys.path.insert(0, CHECKS)
        from registry_loader import script_for
        script = script_for(gate_name)
    except Exception as e:
        print(f"[registry-warn] 注册表加载异常，回退 config.script: {e}")
    if not script:
        script = os.path.join(ROOT, g["script"])
    # 中 14（评审 v13）：`os.path.join(ROOT, script)` 遇到**绝对路径**会直接丢弃 ROOT ——
    # `script: D:/任意/x.py` 原样执行。触达面是本机能写 config/harness.local.toml（在 .gitignore
    # 内，恰好是本地覆盖层的典型用法）或能设 HARNESS_CONFIG 的人/agent；外部受管仓贡献者够不着，
    # 但这是执行边界，应当收死：解析结果（含软链）必须落在工程根之内，越界即 rc=2。
    # 同一道闸也盖住 registry 那条路 —— 两条来源在下面汇合后统一判定，不各写一份。
    try:
        _real, _root_real = os.path.realpath(script), os.path.realpath(ROOT)
        _inside = os.path.commonpath([_real, _root_real]) == _root_real
    except ValueError:      # 跨盘符（Windows）—— 不可能在根之内
        _inside = False
    if not _inside:
        print(f"[run_gate] ✗ 门禁脚本解析到工程根之外: {script}\n"
              f"            （工程根 {ROOT}）→ 拒绝执行（rc=2，fail-closed）。"
              f"script 只允许写根内的相对路径。")
        return 2
    if not os.path.exists(script):
        print(f"[run_gate] 门禁脚本不存在: {script}")
        return 2

    # 组装命令：把相对文件转成绝对（相对仓库根），并注入 --repo
    # 2026-09-09: 同时注入 --gate —— 端脚本可能硬编码 GATE_NAME 默认值，不传会导致
    # gate 级配置（encoding/legacy_baseline 等）全部读到别的 gate 的（首启暴露）。
    cmd = [sys.executable, script, "--repo", repo]
    # 2026-09-16 修复（评审 v13 严重 1）：上面那句"同时注入 --gate"**当时只对
    # backend_layer_check.py 生效**（条件里写死了脚本名）。后果：PC/H5 走 run_gate
    # ——也就是 pre-commit / post-edit / auto-gate / CLI / MCP 的**全部**真实入口——
    # 时 GATE_NAME 一直停在脚本里硬编码的端 gate 名，load_policy 读的是别家 gate 的
    # mode / disabled_rules / legacy_baseline / line_ending；而 rule_base.disabled_rules
    # 是"按名查到了就返回"，按 repo 反查的兜底分支因此永不执行（PC/H5 侧）。
    # 实测后果：其它 gate 的 `disabled_rules` 静默失效、`mode: loose` 被按 strict 执行。
    # 端脚本集合取自 checks/registry.py 的 ENDPOINT_SCRIPTS（唯一数据源，不另维护一份清单）；
    # harness_self_check.py 这类非端脚本不受影响（它不解析 argv，塞 --gate 没有意义）。
    try:
        sys.path.insert(0, CHECKS)
        from registry import ENDPOINT_SCRIPTS as _EPS
        _endpoint_scripts = {os.path.basename(p) for p in _EPS.values()}
    except Exception:
        _endpoint_scripts = {"pc_code_check.py", "h5_code_check.py", "backend_layer_check.py"}
    if os.path.basename(script) in _endpoint_scripts:
        # 策略归属**只**由 run_gate 的位置参数决定，调用方自带的 --gate 一律不采纳。
        # 为什么不能"以调用方为准"（2026-09-16 实测）：端脚本是从 argv 认 gate 的，而
        # 三个入口把用户给的"文件名"原样拼进 argv（MCP 的 files 只 str() 不校验、CLI 同理），
        # 于是一个长得像旗标的文件名就能改写策略——实测 `run_gate t_on <file> --gate t_off`
        # 会按 t_off 的 disabled_rules 放行（rc=1→0），台账里还记成 t_on 的 PASS。
        # 即"被检查方可以自己把门禁放宽"，对门禁是硬伤，故 fail-closed：只认位置参数。
        # 旗标和它的值一起摘掉，免得被下面的文件收集当未知旗标告警、值又被拼成仓库路径。
        if "--gate" in rest:
            _gi = rest.index("--gate")
            print(f"[run_gate] ⚠ 忽略调用方 --gate（策略以 gate 参数 {gate_name} 为准）")
            del rest[_gi:_gi + 2]
        cmd += ["--gate", gate_name]
    scope, n_files = "targeted", 0
    files = []                    # 三个分支都可能有/无文件参数；下面的 L17 守卫要读它
    _mode_note, _mode_weaken = None, []
    # 透传模式/全量
    if "--mode" in rest:
        # --mode 透传分支：文件参数也做绝对化 + 统计（2026-09-12 P3-4：原实现原样透传、
        # n_files 恒 0，gate_log 统计失真且与默认分支行为不一致）
        files = []
        skip = False
        prev_mode = False
        _mode_val = None
        # 只放行 run_gate 自己认识的旗标（2026-09-23 H1）：原先 `if a.startswith("-")` 把
        # **任意** `-` 开头的 token 原样透传给检查脚本 —— 于是 `--list-components` 这种
        # "不跑规则、直接打组件清单并 exit 0"的开关也能从位置参数通道进来。
        # 与默认分支（下面那句"忽略未知参数"）对齐：不认识的旗标出声、丢掉，不透传。
        _FWD_FLAGS = ("--all",)
        for a in rest:
            if a == "--mode":
                prev_mode = True          # 下一个 token 是 mode 的值，必须一起带上
                cmd += [a]
                continue
            if prev_mode:
                prev_mode = False
                _mode_val = a
                cmd += [a]                # N3 修复（第二轮评审）：原先这里直接丢弃 →
                                          # 命令变成 `--mode <文件>`，文件被吞、退化全量扫描
                continue
            if a.startswith("-"):
                if a in _FWD_FLAGS:
                    cmd += [a]; continue
                print(f"[run_gate] ⚠ 忽略未知参数: {a}")
                continue
            files.append(a if os.path.isabs(a) else os.path.join(repo, a))
        cmd += files
        n_files = len(files)
        # 只算，不打印（2026-09-23 L17 实测）：这行原先打在这里，而 L17 守卫在分支链**之后**
        # —— 于是 `run_gate t --mode loose`（裸调用）先说"→ 已记入台账 mode_override"，
        # 紧接着 fail-closed 返回、一行都没记。**写进输出的承诺与实际做的事相反**，
        # 与 L17 要治的是同一种病（台账/输出不可信）。故挪到守卫之后。
        _mode_note, _mode_weaken = _mode_override(g, _mode_val)
    elif "--all" in rest:
        cmd += ["--all"]
        scope = "all"
    else:
        files = []
        skip = False
        for a in rest:
            if skip:
                skip = False; continue
            if a in ("--mode",):
                skip = True; continue
            if a.startswith("-"):
                # 未知旗标不静默吞掉（2026-09-03）：告警后丢弃，防止调用方误以为参数生效
                print(f"[run_gate] ⚠ 忽略未知参数: {a}")
                continue
            files.append(a if os.path.isabs(a) else os.path.join(repo, a))
        n_files = len(files)
        cmd += files

    # 评审 2026-09-23 L17（实测）：裸 `run_gate <gate>`（无文件、无 --all）落到这里时，
    # cmd 里**一个文件参数都没有** —— 而各检查脚本按设计「无参 = 全量」，于是这次调用
    # **退化成整仓全量扫描**（台账里 2 条 21 分钟记录就是这个形态），可记进台账的却是
    # `scope=targeted, files=0`：一行看着像"只查了 0 个文件的定向检查"、实际跑了一整仓。
    # 这比慢更坏 —— 它让台账不可信，而台账是本项目"可审计"的承诺所系。
    #
    # 「这次是不是整仓扫描」的判据**与三个端脚本同源**，不另立一套：脚本自己就是这么判的
    # （pc_code_check.py:512 `is_full = "--all" in sys.argv or not clean`、h5_code_check.py:340
    # 同形、backend_layer_check.py 的 target_files 为空即全量）—— 即「传了 --all」与
    # 「一个位置文件参数都没传」在判据上是**同一件事**，台账必须按同一口径记。
    # 修复前本条只认 `elif "--all" in rest` 那一个分支，于是 `t --all --mode loose` 走了
    # `--mode` 分支、真扫一整仓却记成 scope=targeted/files=0（L17 同一现场，实测）；
    # `--repo <目录>` 单独出现同样真扫一整仓、同样被记成 targeted（目录不是文件，
    # 谈不上"定向查这几个文件"）。两处收到这一个口径上，台账 scope 与下面的守卫读同一个值。
    #
    # 但"该记成什么"与"该不该放行"是两件事，后者仍 fail-closed：
    #   有文件参数 / 有 --all / 有 --repo <目录>  = 调用方点了名 → 放行，按上面的口径记账；
    #   三样都没有（裸调用）                      = 无从判断是不是漏传了文件列表 → rc=2 指路。
    # 与同文件既有的前置 rc=2 约定（未登记 gate、配置读不出来）一致：**判都没判**，不落台账。
    #
    # 影响面（2026-09-23 实测，不是推断 —— 上一版这里写的是"无自动路径走这一形态…故不会拦住
    # 任何既有调用"，而它漏了 `--repo <目录>` 单独出现这一形态，当场拦红了 4 条既有锁）：
    #   · 自动路径**全部走不到**这一形态：post-edit 空改动直接 return 0、pre-commit 先验暂存
    #     副本、auto-gate 三个分支要么带 --all 要么带非空文件、doctor/demo 传单个文件、
    #     sweep 显式 --all、acceptance 传 probe 文件、CI 走 --validate。
    #   · 走到这一形态的是**手敲**：台账里 8 条贵得离谱的 targeted/files=0 行
    #     （实测某些 gate 2×1275s、3×405s、78s…）entry 字段全为空
    #     ＝ 直接调 run_gate.py（经 CLI/MCP/钩子的行都带 entry）—— 正是本守卫要拦的形态。
    #   · 两个入口**点名自己的意图**（同批改；argv 语义不变，脚本判据 is_full 本来就含它）：
    #     cli.py `run <gate>` 与 mcp.py `{gate}` 在不传文件时以前也是全量，现在由它们显式补
    #     `--all` —— 同一件事不再被记成 targeted。
    #   · `--repo <目录>` 单独出现算"点了名"：两个既有夹具走的就是这个形态
    #     （test_repo_key_registry 的 `harness_self --repo ROOT`、test_review_v13_batch8 的
    #     `某个端 gate --repo <目标仓>`），它们本意就是整仓扫描 ⇒ 拦掉它们是越界，已收窄。
    _all_scan = "--all" in cmd
    if not files and not _all_scan and not repo_override:
        print(f"[run_gate] ✗ {gate_name}: 没有文件参数，也没有 --all，也没有 --repo <目标仓>。\n"
              f"  三个端脚本都是「无位置参数 = 整仓全量扫描」（数分钟），这一次会真跑一整仓，"
              f"而调用方没说要查什么 —— 不猜。\n"
              f"  要整仓扫描：加 `--all`，或点名目标仓 `--repo <目录>`；"
              f"只想查改动的文件：把文件路径传进来。",
              file=sys.stderr)
        return 2
    if _all_scan or not files:
        scope = "all"

    if _mode_note:
        print(f"[run_gate] ⚠ 本次判定口径由调用方覆盖: {_mode_note}"
              f"（与 gate 配置的 mode 不同）→ 已记入台账 mode_override。")

    print(f"[run_gate] {gate_name}: {' '.join(cmd)}")
    t0 = time.time()
    # 覆盖度埋点（2026-09-12 第四轮）：子进程把规则级统计写进临时文件，这里读回并入 gate_log。
    # 走环境变量 + 临时文件 → 直接跑检查脚本时统计只留在内存，stdout/stderr 零变化（hook 回灌不受影响）。
    stats_path = os.path.join(tempfile.gettempdir(),
                              f"harness_stats_{os.getpid()}_{int(time.time() * 1000)}.json")
    env = dict(os.environ, HARNESS_STATS_FILE=stats_path)
    r = subprocess.run(cmd, cwd=HARNESS, env=env)
    stats = _read_stats(stats_path)
    _log_run(gate_name, scope, n_files, r.returncode, int((time.time() - t0) * 1000),
             rules=stats.get("rules"),
             weakened=list(dict.fromkeys(_weaken_marks(gate_name) + _mode_weaken)),
             eol=stats.get("eol"),          # 行尾来源（防护 3）：子进程经 stats 文件回传
             mode_override=_mode_note)      # 口径覆盖（H1）：当时按什么判的，用了才出现

    # 2026-10-12（外部评审 CRITICAL #1）：check ⑦ 从 harness_self 挪到通用层。
    # 业务仓提交时，主 gate 通过后追加 AGENTS.md 机器无关性检查 —— 防同事把绝对路径写进 AGENTS.md。
    # harness_self gate 不调（它检查的是引擎仓自己，不是业务仓）。
    if r.returncode == 0 and gate_name != "harness_self":
        agents_md_check = os.path.join(CHECKS, "agents_md_check.py")
        if os.path.isfile(agents_md_check):
            print(f"[run_gate] 追加检查：业务仓 AGENTS.md 机器无关性")
            r2 = subprocess.run([sys.executable, agents_md_check], cwd=HARNESS)
            if r2.returncode != 0:
                return r2.returncode

    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
