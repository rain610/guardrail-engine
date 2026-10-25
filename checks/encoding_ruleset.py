# -*- coding: utf-8 -*-
"""
agentguard 编码卫生规则集（2026-09-07，CodeBuddy 实战暴露后补）
====================================================================
背景：AI 助手往某个 .NET 仓的 Program.cs 追加代码时，跟随文件既有编码(GBK)写入，
而该仓主流编码是 UTF-8 BOM（绝大多数 .cs 文件）——结果 diff 乱码、VS 按 UTF-8 探测
打开就乱码。教训：AI 的编码决策是「跟随打开的文件」而非「跟随仓主流」，
遇到异类文件就翻车，必须有机械化规则兜底。

规则：文件编码必须符合仓约定。约定来自 config/harness.toml 的 gates.<gate>.encoding
（缺省 utf-8-bom）。判定级别：
  - error：文件既不是约定编码，也不是合法的约定编码（真损坏/错编码，必须修）
  - info ：文件是「仓主流的异类」（存量历史文件，报告观察，不拦改动）
允许的编码形态：utf-8-bom / utf-8 / gbk（与 _detect_encoding 对应）。

本模块**默认不注册**成 gate：编码约定因仓而异（给全局默认会把另一类仓整片报红）。
它随包发布，供自建检查脚本接线 —— 用法见 README「自带的可选规则集」。
"""
import re
from rule_base import Rule, Finding, Severity, CheckContext, git_sensor_failed

BOM_UTF8 = b"\xef\xbb\xbf"


def _detect_encoding(raw: bytes) -> str:
    """探测字节流的编码形态：utf-8-bom / utf-8 / gbk / 具名的其它形态。

    低 21（评审 v13）：原实现对一切非 UTF-8 都贴 `gbk` 标签 —— UTF-16（含 BOM）、
    GB18030、Big5 全进同一分支，而下游文案会照着标签劝人「另存为 GBK」：
    拿 Big5 当 GBK 重存 = **二次损坏**，比不报更糟。现在：
      ① 先按 BOM 认 UTF-16/32（这类文件**能**被确定命名，就别含糊）；
      ② 含 NUL 字节的按"疑似 UTF-16/32 或二进制"报 —— GBK 能把 UTF-16LE 的 ASCII 段
         解得"看起来正常"，只靠 try-decode 分不出来，NUL 是最稳的信号；
      ③ 只有真的能用 gbk 严格解出来才叫 gbk（返回值直接参与 `actual == expect` 比较，
         所以这步不能省：省了会把真的 GBK 文件判成"非约定编码"，反而拦下正常提交）；
      ④ 其余给一个不含承诺的描述串 —— **不**写具体编码名。
    """
    if raw.startswith(BOM_UTF8):
        return "utf-8-bom"
    # ① BOM 可判定族。顺序有讲究：utf-32-le 的 BOM 以 utf-16-le 的 BOM 开头，
    #    先判 32 才不会把 UTF-32LE 认成 UTF-16LE。
    for bom, name in ((b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"),
                      (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            return name
    # ② 无 BOM 的 UTF-16/32（文本文件里出现 NUL 本身就不正常）
    if b"\x00" in raw:
        return "含 NUL 字节（疑似 UTF-16/UTF-32 或二进制，不是文本编码）"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    # ③ 真的解得开才叫 GBK（GB18030 是 GBK 的超集，GBK 解得开就按 GBK 报）
    try:
        raw.decode("gbk")
        return "gbk"
    except UnicodeDecodeError:
        pass
    # ④ 解不开的：只描述"不是 UTF-8 也不是 GBK"，不猜具体编码名
    return "非 UTF-8 且非 GBK（可能是 GB18030 / Big5 / 其它本地编码，需人工确认）"


class EncodingRule(Rule):
    """文件编码必须符合仓约定（targeted 改动文件 = error；全量存量异类 = info）。
    注意：自带 targeted 双态，不继承 backend 的 _BackendRule（dirs 语义不适用）。"""
    id = "ENC"
    name = "文件编码不符合仓约定"
    severity_default = Severity.INFO  # 全量扫描时存量异类只观察
    error_in_targeted = True          # 本次改动文件编码错误 = error 拦截
    # 文件级规则（2026-09-16）：编码是整个文件的属性，Finding 行号只能是常量锚点(1)。
    # 自报 file_level 让按行过滤器放行它——否则编辑存量文件的第 50 行时，1 ∉ changed_lines，
    # 下面这条 "error 硬拦" 的结论会被静默丢弃（实测坐实的缺陷，见 rule_base.filter_findings_by_lines）。
    file_level = True

    def severity(self, ctx: CheckContext) -> Severity:
        # 存量基线豁免（2026-09-09）：legacy_baseline 仓的存量异类编码文件只 info——
        # 仓内本就有 ~7% 无 BOM 存量（抽样实测），整文件转码污染 diff 比乱码风险更伤；
        # 新建文件（untracked）不走豁免（targeted 且新文件无"存量"可言）——原先注释这么写但代码
        # 做不到（"无法区分新增/存量"）；2026-09-14 Codex 评审 v8 后 CheckContext 已有 is_new_file
        # → 现在真的按"新文件不豁免"执行（编码是文件属性，改动行对它无意义，所以只看 is_new_file）。
        # 评审 v3 P1（2026-09-15）：旧判据只排除**新文件**，于是 legacy 仓里"已跟踪的老文件
        # 被改坏编码"（改成 GBK）也只报 INFO、rc=0 —— 编码是**文件属性**，本次改动过的文件
        # 就没有"存量"可豁免。现在：改动过（changed_lines 非空）或新建 → 硬拦；只豁免**未改动**的存量。
        # 评审 2026-09-23 L1：上面这句里的"改动过"与括号里的"非空"**不是同一件事**，而代码写的
        # 是后者 —— 差在**纯删除型改动**（changed_lines == frozenset()，三态里表"动过、只是没有
        # 新增行"）。那种情况下 `bool(frozenset())` 为假 → 被当成"未改动"→ 走存量豁免报 INFO。
        # 可恰恰是这一种最危险：编辑器（VS 尤其）保存是**整文件重写**，删一行也能把整个文件
        # 悄悄改成 GBK —— 正是本规则要拦的那个事故（见 check() 里 2026-09-07 的 see）。
        # 与 PC/H5 早就在用的三态写法（`if ctx.changed_lines is not None:`，见 pc_ruleset.py）同源。
        # M12（评审 2026-09-23）：传感器故障时**无法证明**文件没被动过 —— 而本规则要拦的
        # 恰恰是"改一行把整个文件悄悄存成 GBK"这类事故，故障路径下它长得和"没动过"一模一样。
        # fail-closed 档下按"动过"处理（缺省档 git_sensor_failed 恒 False，行为不变）。
        _touched = (getattr(ctx, "changed_lines", None) is not None) \
            or getattr(ctx, "is_new_file", False) \
            or git_sensor_failed(ctx)
        if ctx.legacy_exempt and self.error_in_targeted and not _touched:
            return self.severity_default
        if self.error_in_targeted and ctx.targeted:
            return Severity.ERROR
        return self.severity_default

    def __init__(self, expect="utf-8-bom"):
        super().__init__()
        self.expect = expect

    def applicable(self, ctx: CheckContext) -> bool:
        return bool(ctx.raw)

    def check(self, ctx: CheckContext) -> list:
        actual = _detect_encoding(ctx.raw)
        if actual == self.expect:
            return []
        msg = f"编码为 {actual}，仓约定 {self.expect}（VS/编辑器会乱码，请统一转码）"
        return [Finding(self.id, msg, line=1,
                        fix=f"编辑器另存为 {self.expect}，或用工具批量转码后提交",
                        see="2026-09-07 AI 助手往 GBK 异类 Program.cs 追加内容产生乱码，实测后立此规则")]


def rules_for(encoding: str) -> list:
    """按 gate 的 encoding 配置生成规则实例（供 *_layer_check 薄 loader 接线）。"""
    return [EncodingRule(expect=encoding)]
