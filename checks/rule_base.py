# -*- coding: utf-8 -*-
"""
agentguard 统一规则基类（阶段1 规则模型统一，2026-09-04）
================================================================
把三端检查脚本从「各自内部逻辑」收敛为「统一 Rule 实例列表」。
设计分工（用户确认）：Rule 只产 Finding 数据，输出格式由各 ruleset 自行决定。
阶段1 不动 harness/ 任何文件、不做 glob 发现（那是阶段2）、不拆业务端插件。

规则接口：
    class Rule:
        id / name / severity_default
        applicable(ctx) -> bool          # 是否适用于此文件（按扩展名/段/目录收窄）
        check(ctx) -> list[Finding]     # 产出发现
        severity(ctx) -> Severity       # 双态钩子（如 backend R5/R6 targeted 升 error）
"""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional
import json
import os
import re
import sys

# UTF-8 兜底（2026-09-13 评审 v3 H1 系统性修法）：宿主管道可能是 GBK/ASCII，
# 打印中文/✗ 会抛 UnicodeEncodeError；errors=replace 最坏只是个别字符变问号。
# 清单由 sandbox/list_entries.py 机器推导（tests/engine/test_entry_level.py 有对应锁）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Severity(str, Enum):
    ERROR = "error"
    WARN = "warn"
    INFO = "info"


@dataclass
class Finding:
    rule_id: str
    message: str                       # 最终展示文案（各 ruleset 负责对齐历史字符串）
    # None = 未指定，由 Rule.run 用规则级 severity 填；显式赋值 = 本条命中自己的级别
    # （示例规则用它把字符串上下文里的 console.log 降为 warn）。
    severity: Optional[Severity] = None
    line: Optional[int] = None
    fix: str = ""
    see: str = ""
    using: str = ""                    # backend 专用：命中的 using/方法名


@dataclass
class CheckContext:
    """一次文件检查的标准输入。由各脚本的 build_ctx 构造。"""
    path: Path                        # 绝对路径
    rel: str = ""                     # 相对 repo 的路径
    raw: bytes = b""
    content: str = ""
    script: str = ""                  # <script> 段（Vue SFC）
    template: str = ""                # <template> 段
    mode: str = "strict"              # gate 的 strict/loose
    targeted: bool = False            # 是否只扫本次改动文件（backend R5/R6 双态）
    repo: str = ""
    legacy_exempt: bool = False       # 存量基线豁免（2026-09-09）：gate 配 legacy_baseline: true 时，
                                      # 代码风格类规则（R1/R5/R6/R9 等）对该仓存量违规只 info 不 error；
                                      # ENC/R7 等硬约定不豁免（防乱码/防混仓是事故级）。
    # K1（评审 v6，P1）：script 段**相对行号** → 文件**绝对行号**的偏移量。
    # extract_script 返回的是 <script> 段子串（行号从段内 1 起算），而 changed_lines 是
    # git diff 算出的文件绝对行号 —— 两者直接比较会同时漏报（改行违规静默放过）和误报
    # （存量命中"复活"）。规则产出命中行号时必须加这个偏移。
    script_line_offset: int = 0
    is_new_file: bool = False         # 该文件**未纳入版本库**（untracked）＝本次新建（2026-09-13 外部评审 v5 G1）。
                                      # 为什么需要它：legacy 门的降级判据是 `changed_lines 为空 → 存量`，
                                      # 而 untracked 新文件的 git diff 恒为空 → **新代码被当成存量放行**
                                      # （编辑期 rc=0、提交期才 rc=1，反馈自相矛盾；14 个 legacy 门都受影响）。
    changed_lines: frozenset = None   # 本次新增/修改的行号集合（git diff -U0 计算，2026-09-09）。
                                      # **三态**（中 C，评审 v13）：
                                      #   None       = 拿不到 diff 信息（全量扫描 / 新建文件 / git 不可用）
                                      #                → 整文件口径，不过滤。调用方负责把"不适用"
                                      #                显式写成 None；compute_changed_lines 只在真读不到
                                      #                时才返回 None。
                                      #   frozenset()= 读到了 diff，且**本次没有新增任何行**
                                      #                （纯删除型改动）→ 定向口径下命中数为 0。
                                      #   {…}        = 落在这些行的命中才算本次引入。
                                      # 三态必须分开：空集同时表示"没有 diff 信息"和"diff 里没有新增行"，
                                      # 后者在定向口径下退回整文件扫描 → 存量违规升 ERROR 拦提交
                                      # （实测：只删掉一行，文件里没碰过的 console.log 也被判阻断级）。
    git_sensor_failed: bool = False   # 评审 2026-09-23 M12：本次的 None 是不是**传感器故障**读出来的。
                                      # 三态把两种成因合并进了同一个 None（"文件本次没动" / "git 读不到"），
                                      # 而 legacy 降级判据恰好只看"None ⇒ 存量"——故障于是静默放宽门禁。
                                      # 这里把它们分开带上：由 compute_changed_lines_ex 报告，
                                      # 各 build_ctx 落进 ctx；只有 fail-closed 档（HARNESS_GIT_REQUIRED=1）
                                      # 会读它，见 git_sensor_failed()。缺省档下这个字段不改变任何判定。


class Rule:
    id: str = "base"
    name: str = ""
    severity_default: Severity = Severity.ERROR
    # 文件级规则（2026-09-16）：结论是**整个文件的属性**（编码、文件类型），不是"哪一行写错了"。
    # 这类规则的 Finding 行号只能给常量锚点（当前都是 line=1），因此**必须**免于按行过滤——
    # 否则编辑存量文件的中间某行时，改动行集合不含 1，结论会被静默丢弃（见 filter_findings_by_lines）。
    # 现仅 ENC / R7 为 True；PC/H5 的 H5-CRLF、组件收敛类同理（它们不走按行过滤）。
    file_level: bool = False

    def applicable(self, ctx: CheckContext) -> bool:
        """该规则是否适用此文件。默认全适用，子类按需收窄。"""
        return True

    def check(self, ctx: CheckContext) -> List[Finding]:
        return []

    def severity(self, ctx: CheckContext) -> Severity:
        """双态 severity 钩子。默认返回 severity_default。

        存量基线降级（评审 v13 补遗 B，高）：gate 配 `legacy_baseline: true` 的仓（34 个 gate 里
        21 个），存量违规只报观察不拦提交。这段语义原先只在 `_RegexScriptRule.severity`（PC/H5
        正则规则）与 `_BackendRule.severity`（后端）里**各写一份**，而文件级规则全都没接：
        H5-CRLF / H5-VFOR / H5-COMP / PC-VFOR / PC-COMP / PC-SYNTAX / PC-VAUTH / PC-ELDENY
        既无 severity 覆写，又不走按行豁免（本来就是整文件口径）→ 同一份 legacy 仓里，正则命中
        降成「观察项」，行尾污染、既有 v-for 缺 key、老死组件引用照出 ❌ —— 改一行就把提交拦死。
        与两处既有实现逐字同语义（新建文件不算存量，评审 v5 G1），故收敛到基类当默认。
        不要这个语义的规则照旧覆写 severity（例：R7「前端文件混入后端仓」、ENC「编码」是硬约定）。
        """
        # M12：`not git_sensor_failed(ctx)` 是 fail-closed 档（HARNESS_GIT_REQUIRED=1）下
        # 「读不到 diff 就不算存量」的落点；缺省档该函数恒 False，本行与改动前逐字等价。
        if getattr(ctx, "legacy_exempt", False) and not ctx.changed_lines \
                and not getattr(ctx, "is_new_file", False) \
                and not git_sensor_failed(ctx):
            return Severity.INFO
        return self.severity_default

    def run(self, ctx: CheckContext) -> List[Finding]:
        """统一入口：applicable + check，按本规则 severity 标注。"""
        if not self.applicable(ctx):
            return []
        out = []
        for f in self.check(ctx):
            if f.severity is None:
                f.severity = self.severity(ctx)
            # 评审 2026-10-15（批次 1）：范式指路单一来源。规则类声明 `see` 类属性
            # （指向 golden-patterns/ 具体文档§节），本处统一盖章到 Finding 上 ——
            # 后端规则在 check() 里自带 see（`see=self.see`），`f.see or` 保持其原值不变；
            # PC/H5 规则原先不带，从此零改动获得同款能力。锁：
            # tests/test_paradigm_pointers.py（护盾 M133：去掉盖章即红）。
            f.see = f.see or getattr(self, "see", "")
            out.append(f)
        return out


def format_finding(fnd: Finding) -> str:
    """Finding → 门禁输出行（PC/H5 转换点共用，**唯一**格式化出处；模块级函数）。

    为什么 ID 必须进输出（2026-10-15 批次 1）：① 行内豁免要求「规则 ID 必须写对，
    写错不生效」，而端级模板承诺「被拦时门禁会把 ID 直接打给你」—— 原先 PC/H5 的
    转换点只拿 `fnd.message`，rule_id 就地丢弃，agent 只能靠猜 ID；② 后端一直是
    `❌ RULE L行:` 格式，三端就此对齐。
    see 非空时另起一行 `📖 See:`（与 backend_layer_check 的输出格式逐字同款）：
    agent 被拦的那一刻是它最愿意读范式的时刻，文案只说「应走项目封装组件」
    不给路径，走廊就断在最后一步。只放**只读文档路径**，不放豁免语法
    （09-30 注入边界不变；锁：tests/test_paradigm_pointers.py，护盾 M134）。
    """
    line = "[%s] %s" % (fnd.rule_id, fnd.message) if fnd.rule_id else fnd.message
    if fnd.see:
        line += "\n     📖 See: " + fnd.see
    return line


class WholeFileRule(Rule):
    """**整文件结论类**规则的基类：行尾污染、v-for 缺 :key、死组件引用、空壳指令…

    与基类默认 severity 的差别只有一处：**存量仓里不因"改了别的行"而降不动级**。

    为什么必须单独一类（评审 v13 补遗 B 的实质）：这类规则的结论**没有行号**——
    "这个文件含 CRLF"「这个文件里有 v-for 缺 :key」是**整个文件的属性**，无法判断命中的是
    存量还是本次新增。而基类默认降级要求 `changed_lines 为空`，于是 legacy 仓里**改一行**
    的提交（changed_lines＝{那一行}）拿不到降级 → 既有 CRLF / 既有 v-for 缺 key / 老死组件
    引用照出 ❌ 把提交拦死 —— 正是 legacy_baseline 承诺要避免的事（评审里某个老仓被死组件
    引用拦死的场景）。注意评审建议的公式（`legacy_exempt and not changed_lines and not is_new_file`）
    对**行级**规则成立，对**无行号的文件级结论不成立**：这里取"无法归因就不拦"。
    新建文件照拦（整份都是新代码，与存量无关）。
    """
    file_level = True      # 无行号结论，永远不参与按行过滤（filter_findings_by_lines 读它）

    def severity(self, ctx: CheckContext) -> Severity:
        # M12（评审 2026-09-23）：这一类本就不看 changed_lines（"无法归因就不拦"），
        # 但"无法归因"的前提是**文件确实是存量**。传感器故障时连这个前提都没有——
        # 一个刚被写成 CRLF 的新文件与老文件在故障路径下长得一模一样 ⇒ fail-closed 档下不降级。
        if getattr(ctx, "legacy_exempt", False) and not getattr(ctx, "is_new_file", False) \
                and not git_sensor_failed(ctx):
            return Severity.INFO
        return self.severity_default


_NODE_WARNED = False


def warn_node_missing_once():
    """node 缺失时，PC/H5 的语法校验会跳过——**必须出声**。

    第四轮外审第 4 条追问过"语法规则从未触发，是不是 node 缺失静默跳过"。实测本机 node 可调，
    但代码里 `except FileNotFoundError: return None` 与"没检查"完全无法区分 = 典型假绿。
    改为一次性 stderr 告警（stderr 不是结论通道，不影响判定）。
    """
    global _NODE_WARNED
    if _NODE_WARNED:
        return
    _NODE_WARNED = True
    try:
        import sys as _sys
        _sys.stderr.write("[⚠ harness] 未找到 node —— 语法校验（SYNTAX 规则）本次已跳过，"
                          "不会拦语法错（假绿风险）。\n"
                          "            修：安装 Node.js 或把 node 加入 PATH。本提示每次运行只出一次。\n")
    except Exception:
        pass


_GIT_DIAG_WARNED = False



_NODE_PROBE_WARNED = [False]


def warn_node_probe_failed_once(exc=None):
    """node **存在但调用失败**（权限/超时/兼容）时出声一次（评审 v8/Codex P1）。

    与 `warn_node_missing_once` 的区别：缺 node 是"没装"，调用失败是"装了但跑不起来"——
    两者都会让语法检查静默通过；旧写法只对前者出声，后者 `except Exception: return None` 完全静默。
    """
    if _NODE_PROBE_WARNED[0]:
        return
    _NODE_PROBE_WARNED[0] = True
    sys.stderr.write("[node-warn] node 探针调用失败（语法检查本次未生效，结果是\u300c未检查\u300d而非\u300c通过\u300d）：%r\n"
                     "           若本机确实需要语法门禁，请修好 node（权限/版本），或设 HARNESS_NODE_REQUIRED=1 让失败直接拦下。\n"
                     % (exc,))


def node_required():
    """HARNESS_NODE_REQUIRED=1 → node 不可用/调用失败时语法规则按"未检查=失败"处理。"""
    return (os.environ.get("HARNESS_NODE_REQUIRED") or "").strip() in ("1", "true", "yes", "on")


def git_required():
    """HARNESS_GIT_REQUIRED=1 → git 读不到 diff 时**不许按存量降级**（fail-closed）。评审 2026-09-23 M12。

    与 node 侧的 `HARNESS_NODE_REQUIRED` 对称，缺省关闭（= 既有行为：告警一次 + 降级）。

    为什么需要这一档：diff 读不到时 `changed_lines` 是 None，而 legacy 仓的降级判据是
    「`changed_lines` 为空 ⇒ 存量 ⇒ 降 info」—— 传感器故障恰好落在这个条件里，于是
    「legacy 仓里新代码照拦」的承诺在故障路径上**静默反转**成「整文件都当存量」。
    缺省档只出一次 stderr（stderr 不是结论通道，rc 不变），故对只读 rc 的调用方是 fail-open。
    打开这一档后，读不到 diff 的文件**不再降级**：宁可按新代码拦（存量会更吵），
    也不让传感器故障把门悄悄放宽。

    注意判据只认「**读不到**」：文件**本次没动**（两处 diff 都成功、正文为空）时
    `changed_lines` 同样是 None，但那不是故障，照旧降级 —— 否则 `cli.py check <某文件>`
    这种存量体检会把整个仓的存量违规全报成 ❌。两态由 `git_sensor_failed(ctx)` 区分。"""
    return (os.environ.get("HARNESS_GIT_REQUIRED") or "").strip() in ("1", "true", "yes", "on")


def git_sensor_failed(ctx) -> bool:
    """本次检查的 diff 传感器是否**故障**（fail-closed 档下才为真）。

    缺省档恒 False —— 所有降级判据保持既有行为，这次改动对不开档的机器**零行为差异**。"""
    return git_required() and bool(getattr(ctx, "git_sensor_failed", False))


def _warn_git_once(msg):
    """git 侧失效只提示一次（每个进程），避免每个文件刷屏；不影响判定结果。"""
    global _GIT_DIAG_WARNED
    if _GIT_DIAG_WARNED:
        return
    _GIT_DIAG_WARNED = True
    try:
        import sys as _sys
        _sys.stderr.write(f"[⚠ harness] 存量按行豁免可能未生效：{msg}\n"
                          f"            → 本次按**整文件口径**检查（新代码仍照拦，存量会更吵）。\n"
                          f"            → 若你要求「读不到 diff 时不算存量、直接拦」，设 "
                          f"HARNESS_GIT_REQUIRED=1（缺省只告警不拦，与 HARNESS_NODE_REQUIRED 同款两档）。\n")
    except Exception:
        pass


# ---------- 新增行计算（2026-09-09，改一行不为整文件历史遗留负责） ----------


def is_untracked(repo: str, rel_path: str) -> bool:
    """该文件是否**未纳入版本库**（untracked）——即"本次新建"。

    2026-09-13 外部评审 v5 G1：legacy 门把"changed_lines 空"当存量降级，
    而新建未暂存文件的 diff 恒空 → 新文件违规被降 INFO 放行（编辑期 rc=0、提交期 rc=1）。
    这里用 `git ls-files --others --exclude-standard` 判定（与 compute_changed_lines 同款
    环境清理/超时/失败静默策略：git 不可用 → 返回 False，保持既有行为不额外拦）。
    """
    import subprocess
    if not repo or not rel_path:
        return False
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY")}
    try:
        r = subprocess.run(["git", "-C", repo, "ls-files", "--others", "--exclude-standard", "--", rel_path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=15, env=clean_env)
        return bool((r.stdout or "").strip())
    except Exception:
        return False


# ---------- 提交期「暂存内容」读取层（2026-09-15 评审 v3 P0）----------
# 背景：pre-commit 只从 index 取**文件名**，却把工作区路径交给检查器 → 检查器 open(工作区)
# → 「暂存坏版本 + 工作区改回好版本」可绕过全部规则（实测 rc=0，暂存 diff 仍是坏版本）。
# 修法：pre-commit 用 git checkout-index 把暂存内容展开到 overlay 并导出下面两个环境变量；
# 本层把**内容读取**重定向到副本，而 ctx.rel / changed_lines / is_new_file 等 git 语义
# 仍基于真实仓路径 —— **只在读取这一层重映射**，其余口径零变化。


def staged_overlay() -> str:
    """暂存内容副本目录（提交期由 pre-commit 导出）；空 = 读工作区（编辑期/全量）。"""
    return (os.environ.get("HARNESS_STAGED_OVERLAY") or "").strip()


def staged_repo() -> str:
    """本次提交仓的**真实**根路径（overlay 的对应基准）。"""
    return (os.environ.get("HARNESS_STAGED_REPO") or "").strip()


def staged_only() -> bool:
    """是否处于「只认暂存内容」的提交期模式。"""
    return bool(staged_overlay() and staged_repo())


def overlay_target(path):
    """逻辑路径 → 暂存副本物理路径。

    提交期遇到「仓内路径但副本缺失」时**抛 FileNotFoundError**（fail-closed）：
    此时唯一能读到的就是工作区版本，读它等于把 P0 的洞重新打开。
    仓外路径（allowlist/harness 自身等）原样返回。
    """
    ov, lr = staged_overlay(), staged_repo()
    if not ov or not lr:
        return str(path)
    try:
        rel = os.path.relpath(os.path.abspath(str(path)), os.path.abspath(lr))
    except ValueError:
        return str(path)
    if rel.startswith(".."):
        return str(path)
    cand = os.path.join(ov, rel)
    if os.path.isfile(cand):
        return cand
    raise FileNotFoundError(
        "暂存副本缺失：%s（提交期只允许检查 index 内容，拒绝回退到工作区版本）" % rel)


def target_exists(path) -> bool:
    """入参「能不能检查」的判定 —— 与 `overlay_target` **同一个解析**，故提交期看副本。

    高 7 的 rc=2 语义（传了路径但一个都检查不了 ≠ 检查通过）必须挂在
    「检查器将要打开的那个文件」上。若拿工作区路径判存在，会漏掉这一类：
    文件已 `git add`（index 里有）又从工作区删掉 → porcelain `AD`，
    `--diff-filter=d` 按「相对 HEAD 是新增」保留它，而工作区路径**不存在** ——
    于是「提交里明确带着这个文件」被报成 rc=2 假配置错误，整次提交被拦。

    反过来，暂存期副本缺失（`overlay_target` 抛 FileNotFoundError）在这里返回 False：
    那是「读不到 index 内容」的 fail-closed 情形，同样不该被当成"工作区里有"。
    """
    try:
        return os.path.isfile(overlay_target(path))
    except (FileNotFoundError, OSError):
        return False


def read_bytes(path) -> bytes:
    """**提交期唯一合法的内容读取入口**：读原始字节（自动重映射到暂存副本）。

    所有「读被检文件内容」的地方都必须走它；直读工作区 = P0 漏洞。
    """
    with open(overlay_target(path), "rb") as f:
        return f.read()


def is_new_in_index(repo: str, rel_path: str) -> bool:
    """HEAD 里没有该路径 = 本次新建（提交期口径）。

    提交期不能用 `is_untracked`：已 `git add` 的新文件在 index 里，`ls-files --others`
    看不到它 → untracked=False → legacy 门把**新文件**当存量降级（G1 同族洞的提交期形态）。
    """
    import subprocess
    if not repo or not rel_path:
        return False
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY")}
    try:
        r = subprocess.run(["git", "-C", repo, "cat-file", "-e",
                            "HEAD:" + str(rel_path).replace(os.sep, "/")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=15, env=clean_env)
        return r.returncode != 0
    except Exception:
        return False


def filter_findings_by_lines(findings, changed_lines, file_level=False):
    """按 changed_lines 过滤 Finding（**后端侧的按行豁免**，评审 v8/Codex 建议）。

    PC/H5 在 `_find_hits` 里按行过滤；后端规则是各自 `check()` 产 `Finding(line=i)`，
    原先**只按整规则**降级（legacy 仓把 R5/R6/R9 整条降 INFO）→ 新写的违规也只报观察。
    这里放在收集端统一过滤，与 PC/H5 语义对齐。

    中 C（评审 v13）：判据从「空即不过滤」改成 `is None` 才不过滤。空集有确切含义——
    「本次没有新增任何行」（纯删除型改动），此时定向口径的正确答案是 0 命中，而不是
    退回整文件把存量违规升 ERROR。"读不到 diff"和"没有新增行"是两件事，必须分开表示。

    `file_level=True`（调用方传 Rule.file_level）→ **一律不过滤**。
    2026-09-16 实测坐实的缺陷：ENC / R7 是**文件级**规则，Finding 行号恒为 1；编辑存量
    文件的中间某行时 changed_lines={50} 不含 1 →
      · ENC：legacy 仓里已被改成 GBK 的老文件，再改一行（本该 error 硬拦）→ 结论被静默
        丢弃、rc=0 放行。讽刺的是 encoding_ruleset 注释自己写着「编码是**文件属性**，改动行
        对它无意义」，却被套上了按行的过滤器；2026-09-15 那次 P1 修复（改动过的文件不豁免）
        正是被这里抵消掉的。
      · R7：后端仓里已存在的 .vue，改中间某行 → 同样静默放过。
    判据由**规则自报**（Rule.file_level），不靠行号猜——"行号是常量锚点"这件事本身不可靠，
    而从行号反推"这是文件级"会把真正 line=1 的行级命中误伤。
    """
    if file_level or changed_lines is None:
        return list(findings or []), 0
    keep, dropped = [], 0
    for f in findings or []:
        line = getattr(f, "line", None)
        if line is None or line in changed_lines:
            keep.append(f)
        else:
            dropped += 1
    return keep, dropped

# 已确认是 git 工作树的 repo（normcase 绝对路径）。**只缓存正结果**：测试夹具常见
# 「先当普通目录用、随后才 git init」—— 负结果一旦缓存，刚 init 的仓会被永远当非仓。
_REPO_TRUE = set()


def _is_git_worktree_ex(repo: str):
    """→ (是否 git 工作树, 探针本身是否**故障**)。评审 2026-09-23 M12。

    「不是工作树」有两种成因，旧写法把它俩并成 False 一档：
      · git 跑起来了、明确回答"不是"（非仓目录 / 非工作树）→ **合法输入**，静默退整文件口径；
      · git **没跑起来**（PATH 里没有、权限、超时、异常）→ **传感器故障**，不是"这里没有 git 仓"。
    后者一旦被当成前者，`compute_changed_lines` 会**连告警都不出**地退整文件口径 ——
    比 M12 描述的那条（好歹有一次 stderr）更静默。所以这里按成因分档，交给 fail-closed 档用。
    `rc != 0` 算**明确回答"不是"**：git 在、命令跑通、只是这个目录不在工作树里。"""
    import subprocess
    key = os.path.normcase(os.path.abspath(repo))
    if key in _REPO_TRUE:
        return True, False            # 缓存里只有正结果，命中即"跑通过"
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY")}
    try:
        r = subprocess.run(["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, env=clean_env)
    except Exception as e:                       # noqa: BLE001 — 探针故障：分档，不吞掉
        _warn_git_once("git 探针调用失败: %s" % type(e).__name__)
        return False, True
    ok = r.returncode == 0 and (r.stdout or "").strip() == "true"
    if ok:
        _REPO_TRUE.add(key)
    return ok, False


def _is_git_worktree(repo: str) -> bool:
    """repo 是否 git 工作树。非 git 目录是**合法输入**（CLI 对任意目录体检、测试夹具的
    临时目录），不是传感器故障 —— 调用方据此静默退整文件口径，不走 _warn_git_once。"""
    return _is_git_worktree_ex(repo)[0]


def compute_changed_lines_ex(repo: str, rel_path: str):
    """`compute_changed_lines` 的**四态版**：→ (changed_lines, 传感器是否故障)。评审 2026-09-23 M12。

    三态契约（None / frozenset() / {…}）一字不改 —— `compute_changed_lines` 就是它的 `[0]`，
    所有既有调用方与测试不受影响。多出来的一个是**成因**：同一个 None 底下藏着两件事，
      · 传感器故障（git 调用失败 / 探针失败）→ 第二个返回值为 True；
      · 文件本次没动（两处 diff 都成功、正文为空）→ False。
    legacy 降级判据只看"None ⇒ 存量"，分不出这两者 —— 于是故障会被读成"整文件都是存量"。
    分档的落点在 `git_sensor_failed(ctx)`：只有 HARNESS_GIT_REQUIRED=1 才消费它。"""
    return _compute_changed_lines_impl(repo, rel_path)


def _compute_changed_lines_impl(repo: str, rel_path: str):
    """git diff（staged+unstaged 合并口径）算该文件本次新增/修改的行号集合。

    返回 **(三态之一, 传感器是否故障)**（M12 起的四态版；三态契约一字未改，故障位另带）。

    三态部分（中 C，评审 v13）：
      · None        —— 没有"本次改动"可言：没给仓/路径、目录压根不是 git 仓、找不到 git、
                        全部调用失败，
                        **或者两处 diff 都是空的**（文件压根没动）→ 调用方退回整文件口径；
      · frozenset() —— 文件本次确实被动过，但新增侧一行都没有（纯删除型改动）→ 0 命中；
      · {…}         —— 落在这些行的命中才算本次引入。
    "没有新增行"与"没有 diff 信息"必须分开：前者若也退回整文件扫描，文件里没碰过的存量
    违规会被升成 ERROR 拦提交（实测复现：只删一行 → 存量 console.log 被判阻断级）。
    而"文件压根没动"是另一回事——此时"本次"没有内容，按整文件口径检查才是有意义的回答
    （显式 `cli.py check <某文件>` 存量体检、legacy 观察项都靠这条）。

    口径说明：-U0 取新增侧行号；staged 与工作区 diff 取并集，保证"改完没暂存"也被感知。"""
    import subprocess
    if not repo or not rel_path:
        return None, False          # 压根没让读（调用方未给仓/路径）＝不是故障
    # 非 git 仓目录（2026-09-21）：git 在仓外把 `diff` 退化成 --no-index 模式，该模式
    # 不认 --cached → rc=129「unknown option `cached'」→ 旧实现把它当传感器故障告警
    # （测试夹具 / CLI 体检任意目录时满屏噪音）。合法输入≠故障：静默退整文件口径。
    # M12：探针**自己**跑不起来（git 不在 PATH / 权限 / 超时）是另一回事 —— 那不是
    # "这里没有 git 仓"，而是"读不到"，如实带出去，别静默混进"合法输入"。
    _wt, _probe_failed = _is_git_worktree_ex(repo)
    if not _wt:
        return None, _probe_failed
    lines = set()
    got_info = False          # 只要有一个变体成功读到 diff，"读不到"就不成立
    saw_diff = False          # 变体里出现过非空 diff 正文 = 这个文件本次确实被动过
    # 失败不再静默（2026-09-12 第四轮）：git 不可用 / 非仓库 与"文件确实没改动"原本无法区分，
    # 两者都返回空集 → 存量按行豁免**静默失效**，表现为"改一行老文件被整文件存量违规拦住"。
    # 这是典型的"传感器静默失效"，所以给一次性 stderr 告警（stderr 不是结论通道，不污染判定）。
    global _GIT_DIAG_WARNED
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY")}
    # 提交期（staged_only）**只**看 index：与工作区 diff 取并集会把「工作区里改掉、
    # 但没进暂存区」的行也算成本次改动 → 豁免范围虚大（与 P0 同一根因的另一面）。
    variants = [["diff", "--cached", "-U0", "--"]]
    if not staged_only():
        variants.append(["diff", "-U0", "--"])
    for extra in variants:
        try:
            r = subprocess.run(["git", "-C", repo] + extra + [rel_path],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=15, env=clean_env)
            cur = None
            # 2026-09-16 修复（评审 v13 严重 3）：原先靠前缀区分"文件头"与"内容行"，而这个
            # 判定在 **hunk 内部**也生效 —— 删除行的正文若以 `--` 开头（`--x;`、SQL 注释、
            # Markdown 分隔线…），diff 行就是 `---x;`，被误判成文件头 `--- a/...` → 落到
            # "上下文行"分支把 cur 推进 1 → 其后所有新增行号整体 +1 → 真实新增行上的违规被
            # filter_hits_by_lines 滤掉 = **假放行**。对称地，新增行正文以 `++` 开头（`+++i;`）
            # 被误判成 `+++ b/...` → 该新增行完全不进集合，同属漏检。
            # 改为按结构判定：只有 `@@` 之后才是内容行，其前一律是文件头。
            in_hunk = False
            for ln in (r.stdout or "").splitlines():
                if ln.startswith("diff --git "):
                    in_hunk = False           # 多文件兜底：新文件的头重新按"头"处理
                    continue
                if ln.startswith("@@"):
                    in_hunk = True
                    # @@ -a,b +c,d @@ → 新侧起始 c
                    try:
                        cur = int(ln.split("+")[1].split(",")[0].split(" ")[0].rstrip("@"))
                    except Exception:
                        cur = None
                    continue
                if not in_hunk or cur is None:
                    continue                  # 文件头（---/+++/index/new file mode/…）不占行号
                if ln.startswith("\\"):
                    continue                  # "\ No newline at end of file"：标记行，不占行号
                if ln.startswith("+"):
                    lines.add(cur)            # hunk 内的 `+++…` 是新增内容，不是文件头
                    cur += 1
                elif ln.startswith("-"):
                    pass                      # 删除行不占新侧行号
                else:
                    cur += 1                  # 上下文行推进（-U0 下罕见）
        except FileNotFoundError:
            _warn_git_once("找不到 git 命令（PATH 里没有？）")
            return None, True                     # M12：压根没读成 = 传感器故障
        except subprocess.TimeoutExpired:
            _warn_git_once("git diff 超时")
            continue
        except Exception as e:                    # noqa: BLE001 — 任何异常都不该炸门禁
            _warn_git_once(f"git 调用异常: {type(e).__name__}")
            continue
        if r.returncode != 0:
            # 评审 2026-09-23 L2：原先 try 块**末尾**也有一份 `got_info = True` / `saw_diff = True`
            # —— 它在 rc 判断**之前**执行，于是"命令失败但 stderr/stdout 里恰有内容"会被读成
            # "读到了 diff"，甚至把 git 的报错正文当 diff 解析进 `lines`（错误消息里的 `+`/`-`
            # 开头行会被当新增行）。那两行与下面这段 rc==0 分支**逐字重复**，标注的注释
            # （"本变体调用成功"）却写在没判断返回值的位置 —— 注释在替一个没做的判断背书。
            # 现在唯一的赋值点在这里：**唯一进位条件就是 rc==0**，与注释同源。
            head = ((r.stderr or "").strip().splitlines() or [""])[0][:80]
            _warn_git_once(f"git 返回 {r.returncode}: {head}")
        else:
            got_info = True
            if (r.stdout or "").strip():
                saw_diff = True
    # 三态收口（中 C）：
    #   · 一次都没读成功          → None（读不到 = 整文件口径）
    #   · 读成功但两处 diff 全空  → None —— 这个文件**本次压根没动**，谈不上"本次新增行"。
    #     这与会话里"只删了一行"是**不同**的事：后者 diff 正文非空（有 @@ 和 - 行），
    #     只是新增侧没有行号 —— 那种必须返回空集，否则退回整文件扫存量（中 C 本体）。
    #     判据用"diff 正文是否非空"，而不是"新增行号是否为空"，两者只在这一处分岔。
    # M12：这两个分支**共用同一个 None**，但成因相反 —— 上面那个是"读不到"（传感器故障，
    # 第二个返回值 True），下面那个是"文件确实没动"（正常）。legacy 降级判据只看 None，
    # 分不出这两者，所以这里必须把成因一起带出去，别让它在中途丢掉。
    if not got_info:
        return None, True
    if not saw_diff:
        return None, False
    return frozenset(lines), False


def compute_changed_lines(repo: str, rel_path: str):
    """三态版（= `_compute_changed_lines_impl` 的 `[0]`）。签名与返回值与 2026-09-23 之前逐字一致，
    既有调用方 / 测试不需要跟着改；要"故障位"的调用方走 `compute_changed_lines_ex`。"""
    return _compute_changed_lines_impl(repo, rel_path)[0]


def filter_hits_by_lines(hits, changed_lines):
    """按 changed_lines 过滤命中集合。
    hits: [(line_no, payload), ...]；**None** → 原样返回（整文件口径）。
    frozenset() → 一条都不留（中 C：读到 diff 且没有新增行 = 本次没引入任何命中）。
    返回 (kept, dropped_n)：dropped_n 是被豁免的存量命中数。"""
    if changed_lines is None:
        return hits, 0
    kept = [(ln, p) for (ln, p) in hits if ln in changed_lines]
    return kept, len(hits) - len(kept)


# ---------- 行内豁免的定位判据（评审 2026-09-23 L4）----------
# `harness-ok: <规则ID> <理由>` 是"明确合规写法"的唯一合法出口，三端各自拼同一句标记正则
# （PC/H5 在规则集的 `_find_hits`，后端在 loader 的 `_inline_waived`；`test_docs_consistency`
# 锁着"三端实现签名逐字一致"）。它们原先都拿**原始行文本**去搜这个标记，于是
# **标记只出现在字符串字面量里时照样生效**：
#     console.log("harness-ok: PC-CONSOLE 这行自己把自己豁免了")
# 这不是"宽松"，是判据看错了地方：豁免的语义是"写代码的人在此处留了说明"，而字符串是
# **被检测的数据**——数据能自己解除对自己的检查，等于出口没上锁。
# 修法：标记必须落在**注释**区间内（`//` 到行尾、`/* */` 之间）。
#
# 为什么不是"先把字符串抹掉再搜"：注释正文里的一个撇号（`// don't ...`）会让整段后半行
# 被当成未闭合的字符串一起抹掉，标记跟着消失 —— 那是**反向**误判（合法的豁免失效），
# 而且比原缺陷更容易撞上。所以这里不建注释正文的语法模型，只**定位注释区间**：
# 区间之外的字符串照扫（`var u = "http://x"` 里的 `//` 不算注释），区间之内原样搜。
_STR_QUOTES = "\"'`"      # JS 单/双引号 + 模板串；C# 只有前两种，多一种不影响


def comment_spans(line):
    """本行的注释区间 `[(起, 止), ...]`（止是开区间边界 = `*/` 的起始下标）。

    认 `//`、`/* */` 与 `<!-- -->` 三类：前两种在 JS/TS 与 C# 里同形（SFC 取 `<script>`，
    纯脚本文件取整份）；`<!-- -->` 是 2026-10-09 补的 —— 那之前模板段不喂给任何规则，
    所以不需要它；`PC-ELFORM` / `PC-MODAL` 是按行扫 `<template>` 的规则，而模板里唯一的
    注释形态就是 `<!-- -->`，不认它 = 这两条规则**没有豁免出口**（harness "行内豁免覆盖三端"
    的承诺对模板段落空）。补在引号判定之后：字符串里的 `<!--` 照旧按字符串看待。
    字符串里的注释符不算（含 `\\` 转义与反引号模板串）。
    已知边界，都只可能让**合法的豁免失手**（fail-closed，不会凭空放行）：
      · `/* 标记 */` 跨行的**续行**上的标记不认 —— 标记写在该块注释的首行即可；
      · 模板串插值 `${...}` 里的 `//` 按字符串处理（不是注释）；
      · C# 逐字串 `@"a""b"` 会提前收尾，之后的文本按代码看待。
    """
    if not line:
        return []
    spans, i, n, q, esc = [], 0, len(line), "", False
    while i < n:
        c = line[i]
        if q:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == q:
                q = ""
            i += 1
            continue
        if c in _STR_QUOTES:
            q = c
            i += 1
            continue
        if line.startswith("<!--", i):            # HTML 注释（模板段规则的豁免出口）
            end = line.find("-->", i + 4)
            if end < 0:                           # 未在本行收尾 → 本行余下都是注释（与块注释同口径）
                spans.append((i + 4, n))
                break
            spans.append((i + 4, end))
            i = end + 3
            continue
        if c == "/" and i + 1 < n:
            nxt = line[i + 1]
            if nxt == "/":                       # 行注释：到行尾，后面不可能再有代码
                spans.append((i + 2, n))
                break
            if nxt == "*":
                end = line.find("*/", i + 2)
                if end < 0:                      # 块注释没在本行收尾 → 本行余下都是注释
                    spans.append((i + 2, n))
                    break
                spans.append((i + 2, end))
                i = end + 2
                continue
        i += 1
    return spans


_WAIVER_RE = re.compile(r"harness-ok:\s*([A-Za-z0-9_.-]+)(?:\s+(\S.*))?$")


def inline_waiver(rule_id, lineno, lines):
    """命中行或紧邻上一行的**注释**里有 `harness-ok: <规则ID> <理由>` → 返回理由。

    只看 `comment_spans` 认定的注释区间：字符串字面量里的标记不算（数据不能自己解除
    对自己的检查）。规则 ID 必须写对，理由必须非空 —— 无理由的标记不放行。
    """
    if lineno is None or lineno < 1:
        return None
    for ln in (lineno, lineno - 1):
        if ln < 1 or ln > len(lines):
            continue
        text = lines[ln - 1]
        for start, end in comment_spans(text):
            m = _WAIVER_RE.search(text[start:end])
            if m and m.group(1) == rule_id and m.group(2) and m.group(2).strip():
                return m.group(2).strip()
    return None


# ---------- 代码段归一（评审 v13 严重 2）----------
# 背景：PC/H5 的 `_RegexScriptRule.applicable` 是 `ctx.script != ""`，而 `ctx.script` 只对
# **Vue SFC** 有值（extract_script 从 <script> 取）。于是 .js/.ts/.tsx/.jsx 这些**纯脚本文件**
# 的 script 恒为空 → **PC/H5 整个正则规则族（PC-CONSOLE/IP/TODO、H5-UPLOAD/CRLF…）对它们
# 一次都没跑过**。34 个 gate 里有 22 个端是 PC/H5，.js 是这些仓的主要文件类型之一——
# 这不是漏一条规则，是一整类文件静默通过。旧测试没接住是因为 fixture 全是 .vue。
# 修法：把「本次检查的代码文本」收敛成一个函数——SFC 取 <script> 段（沿用既有偏移语义），
# 纯脚本文件取整份内容（偏移 0 → 行号即文件绝对行号，changed_lines 口径不变）。
SCRIPT_EXTS = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")


def is_script(path) -> bool:
    """纯脚本文件（无 SFC 外壳，整份内容即代码）。"""
    p = str(path or "").lower().split("?", 1)[0]
    return p.endswith(SCRIPT_EXTS)


# node --check 只认 JS；把这些扩展名的内容送进去会 100% 报「语法错误」——那是假阳性，
# 不是真发现。严重 2 让 .ts/.tsx/.jsx 也进代码段后，语法规则必须自己排除它们。
TS_EXTS = (".ts", ".tsx", ".jsx")


def is_ts(path) -> bool:
    """TypeScript/JSX 族：Node 的语法检查器解析不了，语法规则须跳过（免得整类文件被误拦）。"""
    p = str(path or "").lower().split("?", 1)[0]
    return p.endswith(TS_EXTS)


def code_region(path, content, script, offset):
    """返回本次检查的 (代码文本, 行号偏移)。

    SFC：有 <script> 段就用它（含既有偏移）；只有 <template> 的返回 ("", 0)——
    模板不是代码，不该喂给代码规则。
    纯脚本文件（.js/.ts/.tsx/.jsx…）：整份内容即代码，偏移 0（行号天然是文件绝对行号）。
    **样式文件刻意不回退**：PC/H5-TODO 的 `\\btodo\\b` 会命中 `.todo-list` 选择器、PC-IP 会
    命中注释里的示例地址——把样式文件塞给脚本规则是**制造**假阳性，不是修漏检
    （评审 v13 坐实的是 .js/.ts 被整族绕过，样式文件不在其列，故维持原状）。
    """
    if script:
        return script, offset
    if is_script(path):
        return content, 0
    return "", 0


def rel_path(path, base):
    """仓库相对路径；跨盘符（C 盘文件 × D 盘仓库）不崩——退化为 `[external] <文件名>`。

    2026-09-12（外部评审 P3-1 的补漏）：os.path.relpath 在不同挂载盘之间抛 ValueError，
    而"文件在别的盘"是真实场景（临时探针、跨盘 worktree、同事把仓库放 E 盘）。原先只给
    build_ctx 加了兜底，main() 里算 rel 的那处漏了——跨盘直接崩（实测：C 盘探针 × D 盘仓库）。
    """
    try:
        return os.path.relpath(path, base) if base else str(path)
    except ValueError:
        return f"[external] {os.path.basename(path)}"


def gate_for_repo(repo, gate_name=None, lib=None):
    """按 gate 名或仓路径解析出 gate 配置 dict（取不到返回 {}）。

    2026-09-12 第二轮评审 N7：PC/H5 原先各自内联一段"按 REPO 反查 gate"的代码，其中
    `LIB if "LIB" in dir() else <手工拼路径>` 写法在函数内恒为 False（dir() 查的是当前作用域），
    实际一直走手工拼接分支——能跑通纯属 load_policy 已经插过 sys.path。统一到这里，
    与 disabled_rules 共用同一套解析顺序（gate 名优先，其次按 repo 路径反查）。
    """
    try:
        if lib:
            import sys as _sys
            if lib not in _sys.path:
                _sys.path.insert(0, lib)
        from _config import gate, all_gates
        if gate_name:
            g = gate(gate_name) or {}
            if g:
                return g
        if repo:
            rn = os.path.normcase(os.path.abspath(repo))
            for _k, cand in all_gates().items():
                grp = cand.get("_repo_path") or ""
                if grp and os.path.normcase(os.path.abspath(grp)) == rn:
                    return cand
    except Exception:
        pass
    return {}


def legacy_baseline(repo=None, gate_name=None, lib=None):
    """该 gate 是否 legacy_baseline 仓（存量仓：整文件口径命中的卫生规则降级为观察项）。

    repo 反查优先于 gate 名：端脚本可能带 GATE_NAME 默认值，直接跑脚本没传 --gate 时
    按名字查会拿到**别的 gate** 的 legacy 设置（假豁免/漏豁免）。
    """
    g = gate_for_repo(repo, lib=lib) if repo else {}
    if not g and gate_name:
        g = gate_for_repo(None, gate_name=gate_name, lib=lib)
    return bool(g.get("legacy_baseline", False))


def disabled_rules(gate_name=None, repo=None, lib=None):
    """本 gate 在 config 里声明关闭的规则 id 集合（`disabled_rules: [SOME-RULE]`）。

    2026-09-12 新增（配置表开关）。为什么需要它：**规则的适用范围该由配置说了算**，
    而不是把某个仓的专属要求硬塞给所有 gate。触发场景：「上传必须带签名头」这类规则
    只在个别仓成立——那些仓的上传本来就不签名；全局启用会给其他仓制造"按行豁免"噪声，
    而豁免理由本身是假的。

    取法：优先按 gate_name 查；查不到再按 repo 路径反查（直接跑脚本、没传 --gate 时）。
    任何异常都返回空集合（fail-open 到"不过滤"，即保持原行为，不因配置异常误关规则）。
    """
    try:
        if lib:
            import sys as _sys
            if lib not in _sys.path:
                _sys.path.insert(0, lib)
        from _config import gate, all_gates
        g = {}
        if gate_name:
            g = gate(gate_name) or {}
        if not g and repo:
            rn = os.path.normcase(os.path.abspath(repo))
            for _k, cand in all_gates().items():
                grp = cand.get("_repo_path") or ""
                if grp and os.path.normcase(os.path.abspath(grp)) == rn:
                    g = cand
                    break
        v = g.get("disabled_rules") or []
        if isinstance(v, str):
            v = [v]
        return {str(x).strip() for x in v if str(x).strip()}
    except Exception:
        return set()



# ============================================================ 覆盖度统计（2026-09-12 第四轮）
# 目的：回答"传感器不响 = 质量高，还是检测失效？"
#   —— 按规则累计**真实运行**里的触发/豁免次数，供 harness/coverage.py 聚合成自评报告。
# 零输出契约变更：只有调用方（run_gate）设了 HARNESS_STATS_FILE 时才落盘；直接跑检查脚本时
# 统计只留在内存，stdout/stderr 一个字节都不变（hook 链路回灌给模型的输出不受影响）。
STATS = {}
# (rule_id, line_no, reason) 汇总。★ 第三个字段**只入账、不回显**（2026-09-30）：
# 它是注释里的自由文本，回显＝把攻击者的文字送进注入通道，见 waiver_summary() 的说明。
WAIVED_DETAILS = []


def bump(rule_id, kind, n=1):
    """给某规则累加一次事件。kind: error/warn/info/waived_inline/waived_legacy。"""
    d = STATS.setdefault(rule_id or "(loader)", {})
    d[kind] = d.get(kind, 0) + n


def stats_snapshot():
    """当前进程累计的规则级统计（拷贝一层，别让调用方改到内部状态）。"""
    return {k: dict(v) for k, v in STATS.items()}


def waiver_summary():
    """本次运行用掉的行内豁免汇总（一行一条，供提交时打印）。

    ★ 2026-09-30（用户的安全关切，与 ③ 同源）：**理由原文到此为止，不回显**。
    第三个字段 `reason` 是从源文件注释里**逐字**抄来的**自由文本**
    （`// harness-ok: <规则ID> <理由>`），而本函数的输出会经 gate stdout → hook 的
    `out` → **进入模型的上下文**。也就是说：**谁能让一行注释进仓，谁就能把任意文字
    送进注入通道**，而且那句话穿着**门禁自己的审计口吻**（「本次用掉…」）——
    伪造者不必自己编话术，门禁的原文就是话术。
    「**要求写理由**」（`harness-ok:<ID>\\s+(\\S.*)`，没理由不豁免）与「**把理由回显
    出来**」是**两件事**：前者保留（有锁钉着，且第三条字段照旧入账），后者取消。
    理由的家在 **diff**（AGENTS.md 原话：「理由写在标记后面：标记会留在 diff 里，
    人工复核看得见」）—— 复核的人读的是 diff，不是这行注入。
    锁：`tests/engine/test_entry_level.py::…test_waivers_used_are_reported_on_pc`（N7，断言
    **不再**回显）与 `…test_injected_context_carries_no_waiver_reason_text`（判到
    **注入正文**那一层）；护盾 `M116`（把 `— %s` 加回去就红）。
    """
    if not WAIVED_DETAILS:
        return ""
    lines = ["  ⚠ 本次用掉 %d 条行内豁免（harness-ok）：" % len(WAIVED_DETAILS)]
    for rid, lno, _reason in WAIVED_DETAILS:
        lines.append("    · %s L%d" % (rid, lno))
    return "\n".join(lines)


def emit_stats(gate="", scope="", files=0, eol=None):
    """把本次运行统计交给调用方（run_gate）。无 HARNESS_STATS_FILE → 静默 no-op。

    eol（2026-09-18 防护 3）：本 gate 的期望行尾**从哪来**（`{"value","source"}`，
    由 `checks/*_code_check.py` 的 `_resolve_eol()` 填）。只有 PC/H5 两个端有这个概念，
    所以调用方不传就是 `None` → **不落键**。

    为什么走这条已有的通道、而不是在父进程（run_gate）自己解析一次：那会让"期望行尾怎么定"
    有**两份实现**，而它们必然漂移（plan §3.5(b) 已经为此写过一段）。回传的代价是几十个字节。
    """
    path = os.environ.get("HARNESS_STATS_FILE")
    if not path:
        return
    try:
        payload = {"gate": gate, "scope": scope, "files": files,
                   "rules": stats_snapshot()}
        if eol:
            payload["eol"] = dict(eol)      # 拷一层：别让调用方之后改到已经落盘的内容
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except OSError:
        pass          # 统计失败绝不影响门禁判定（日志/观测不得反向阻断门禁）
