# -*- coding: utf-8 -*-
"""用户实体规范（2026-09-14）的回归锁：R10 时间字段列类型、R11 禁止 [Length]。

规范原文（用户口述）：
  1. 后台时间定义加上 [Column(TypeName = "datetime")]，查询条件用到就不报错了；
  2. 创建实体时遇到 [Length(50)] 标签要删除 —— 长度由代码/数据库控制，
     硬编码后改字段必须改代码重新发版，扩库字段也没用。
"""
import os
import pathlib
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "checks"))

ENTITY_REL = "RMC/Eagle.RMC.Entities/User.cs"


def _ctx(content, rel=ENTITY_REL, changed=(3, 4, 5, 6, 7, 8), is_new=True, legacy=False, targeted=True):
    import rule_base
    return rule_base.CheckContext(path=pathlib.Path(rel), rel=rel, raw=content.encode("utf-8"),
                                  content=content, script="", template="", mode="strict", repo="",
                                  changed_lines=frozenset(changed), legacy_exempt=legacy,
                                  is_new_file=is_new, targeted=targeted)


BAD = 'public class U {\n  public DateTime CreateTime { get; set; }\n  [Length(50)]\n  public string Name { get; set; }\n}\n'
GOOD = ('public class U {\n  [Column(TypeName = "datetime")]\n  public DateTime CreateTime { get; set; }\n'
        '  [Column(TypeName="datetime")] public DateTime? UpdateTime { get; set; }\n'
        '  public string Name { get; set; }\n}\n')


class TestR10DatetimeColumn(unittest.TestCase):
    def test_missing_column_attribute_is_reported(self):
        import backend_ruleset as B
        hits = B.R10DatetimeColumnRule().check(_ctx(BAD))
        self.assertEqual([h.line for h in hits], [2],
                         "DateTime 属性缺 Column(TypeName=datetime) 必须报（否则查询条件用到会类型不匹配）")

    def test_attribute_above_or_inline_accepted(self):
        import backend_ruleset as B
        self.assertEqual(B.R10DatetimeColumnRule().check(_ctx(GOOD)), [],
                         "特性写在上一行或同行都要认（别把正常写法报成问题）")

    def test_nullable_datetime_also_checked(self):
        import backend_ruleset as B
        src = 'public class U {\n  public DateTime? FinishTime { get; set; }\n}\n'
        self.assertEqual(len(B.R10DatetimeColumnRule().check(_ctx(src))), 1,
                         "DateTime? 同样要声明列类型（可空时间字段一样会踩）")

    def test_other_attribute_in_between_still_holds(self):
        """中间夹着别的特性也要能找到 Column（如 [Required] + [Column(...)]）。"""
        import backend_ruleset as B
        src = ('public class U {\n  [Required]\n  [Column(TypeName = "datetime")]\n'
               '  public DateTime CreateTime { get; set; }\n}\n')
        self.assertEqual(B.R10DatetimeColumnRule().check(_ctx(src)), [])


class TestR11NoLength(unittest.TestCase):
    def test_length_attribute_is_reported(self):
        import backend_ruleset as B
        hits = B.R11NoLengthAttributeRule().check(_ctx(BAD))
        self.assertEqual([h.line for h in hits], [3], "[Length(50)] 在实体里必须报（要求删除）")

    def test_same_family_also_reported(self):
        import backend_ruleset as B
        src = ('public class U {\n  [StringLength(100)]\n  public string A { get; set; }\n'
               '  [MaxLength(20)]\n  public string B { get; set; }\n}\n')
        self.assertEqual(len(B.R11NoLengthAttributeRule().check(_ctx(src))), 2,
                         "同族的 StringLength/MaxLength 一并覆盖（同一个坑）")


class TestScopeAndSeverity(unittest.TestCase):
    def test_scope_is_entities_only(self):
        import backend_ruleset as B
        for rel in ("RMC/Eagle.RMC.Business/X.cs", "RMC/Eagle.RMC.Controller/X.cs"):
            self.assertFalse(B.R10DatetimeColumnRule().in_scope(_ctx(BAD, rel=rel)),
                             "%s 不是实体目录，不该套实体规范" % rel)
            self.assertFalse(B.R11NoLengthAttributeRule().in_scope(_ctx(BAD, rel=rel)))

    def test_changed_lines_only_block(self):
        """两条都只在**新增/改动行**阻断（error_in_targeted + 按行过滤）→ 存量历史文件不误拦。"""
        import backend_ruleset as B
        for rule in (B.R10DatetimeColumnRule(), B.R11NoLengthAttributeRule()):
            self.assertTrue(rule.error_in_targeted, "%s 必须只在 targeted 改动行拦" % rule.id)
            legacy_no_change = _ctx(BAD, changed=(), is_new=False, legacy=True)
            self.assertEqual(rule.severity(legacy_no_change).name, rule.severity_default.name,
                             "%s：legacy 仓里没改动的存量不该被拦" % rule.id)

    def test_registered_in_backend_rules(self):
        import backend_ruleset as B
        ids = [r.id for r in B.BACKEND_RULES]
        self.assertIn("R10", ids)
        self.assertIn("R11", ids)


class TestEntityScopeCoversAllRepos(unittest.TestCase):
    """作用域必须是"任何 *Entities 目录"，而不是三条固定前缀（2026-09-14 实扫全部后端仓后的结论）。"""

    # 实扫结果（D:/MyProject/aiauto 下全部后端仓的 *Entities 目录）
    REAL_DIRS = [
        "Core/Eagle.Entities/User.cs",
        "Module/Eagle.Module.Entities/M.cs",
        "RMC/Eagle.RMC.Entities/User.cs",
        "RMCEx/Eagle.RMCEx.Entities/X.cs",
        "RMCHY/Eagle.RMCHY.Entities/Y.cs",
        "Support/Eagle.Support.Entities/S.cs",
        "Audit/Eagle.Audit.Entities/A.cs",
        "DataReport/Eagle.DataReport.Entities/D.cs",
    ]

    def test_all_real_entity_dirs_in_scope(self):
        import backend_ruleset as B
        for rel in self.REAL_DIRS:
            for rule in (B.R10DatetimeColumnRule(), B.R11NoLengthAttributeRule()):
                self.assertTrue(rule.in_scope(_ctx(BAD, rel=rel)),
                                "%s 的 %s 规则没覆盖到 —— 该目录的实体不会被检查" % (rel, rule.id))

    def test_non_entity_dirs_out_of_scope(self):
        import backend_ruleset as B
        for rel in ("RMC/Eagle.RMC.Business/X.cs", "RMC/Eagle.RMC.Controller/X.cs",
                    "Module/Eagle.Module.Services/S.cs", "Plugins/EntitiesHelper/X.cs"):
            for rule in (B.R10DatetimeColumnRule(), B.R11NoLengthAttributeRule()):
                self.assertFalse(rule.in_scope(_ctx(BAD, rel=rel)),
                                 "%s 不是实体目录，不该套实体规范" % rel)

    def test_pattern_means_future_modules_auto_covered(self):
        import backend_ruleset as B
        self.assertTrue(B.R10DatetimeColumnRule().in_scope(_ctx(BAD, rel="NewModule/Eagle.New.Entities/N.cs")),
                        "新模块只要有 *Entities 目录就自动被覆盖（不用再改 ENTITY_DIRS）")


# ==============================================================
# R12：Entities 按 Table/Visual/Api 分层（2026-09-21 用户规范）
# ==============================================================

R12_LAYER = "RMC/Eagle.RMC.Entities"


def _r12_ctx(content, layer, sub, fname, **kw):
    rel = f"{R12_LAYER}/{layer}/{sub}/{fname}.cs"
    return _ctx(content, rel=rel, **kw)


class TestR12EntityLayering(unittest.TestCase):
    """R12：Entities 必须按 Table/Visual/Api 三层分层，禁禁用后缀、禁 namespace 嵌套。"""

    # ----- F1 禁用文件名后缀 -----

    def test_input_class_is_blocked(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities { class ExportTrainingLedgerInputEntity {} }",
            "Api", "Edu/EduPlan", "ExportTrainingLedgerInputEntity"))
        self.assertTrue(any("*Input" in h.message for h in hits),
                        "*Input.cs 是禁用的查询/导出参数类后缀，必须报")

    def test_param_class_is_blocked(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities { class EduPlanParamEntity {} }",
            "Api", "Edu/EduPlan", "EduPlanParamEntity"))
        self.assertTrue(any("*Param" in h.message for h in hits))

    def test_dto_class_is_blocked(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities { class EduPlanDto {} }",
            "Visual", "Edu/EduPlan", "EduPlanDto"))
        self.assertTrue(any("*Dto" in h.message for h in hits))

    def test_normal_entity_passes_filename(self):
        """合规的 Api/…ApiEntity 文件：**一条都不许报**。

        2026-09-21 订正：原断言是
        `assertFalse(any("禁用" in h.message or "*" in h.message for h in hits))`
        —— 它只筛"消息里带 * 或带『禁用』"的那几条，而 F5（Api/ 下未以 ApiEntity 收尾）、
        F6（namespace 嵌套）的消息里**两个特征都没有**，所以把 F5 改成**永远触发**，
        本文件 35 条测试依然全绿（实测）。判据应当直接问"报了几条"，而不是"报的内容长得像不像"。
        """
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities { class EduPlanLevelExportApiEntity {} }",
            "Api", "Edu/EduPlan", "EduPlanLevelExportApiEntity"))
        self.assertEqual([h.message for h in hits], [],
                         f"合规的 ApiEntity 文件不该报任何一条：{[h.message for h in hits]}")

    # ----- F2/F3 ORM 表特性 vs 目录位置 -----

    def test_table_without_attribute_is_blocked(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities { public class EduPlanEntity : BaseEntity "
            "{ public int Id { get; set; } } }",
            "Table", "Edu/EduPlan", "EduPlanEntity"))
        self.assertTrue(any("缺 ORM 表特性" in h.message for h in hits),
                        "Table/ 下文件缺 [Table] 必须报")

    def test_table_with_attribute_passes(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            'namespace Eagle.RMC.Entities { [Table("Edu_Plan")] '
            "public class EduPlanEntity : BaseEntity { public int Id { get; set; } } }",
            "Table", "Edu/EduPlan", "EduPlanEntity"))
        self.assertEqual([h.message for h in hits], [],
                         f"合规的 Table 实体不应误报：{[h.message for h in hits]}")

    def test_visual_with_table_attribute_is_blocked(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            'namespace Eagle.RMC.Entities { [Table("T_Template")] '
            "public class TrainTemplateListEntity : MasterEntity { } }",
            "Visual", "Train", "TrainTemplateListEntity"))
        self.assertTrue(any("带 ORM 表特性" in h.message for h in hits),
                        "Visual/ 下文件带 [Table] 必须是误放，应报")

    # ----- 判据落点：锚真实行号 + 只认正文（2026-09-21 用户拍板）-----

    def test_f3_is_anchored_to_the_table_attribute_line(self):
        """F3 必须锚在 `[Table(` 那一行，**不是常量 1**。

        为什么这是锁而不是细节：R12 的 F1–F6 原先一律锚 1，而 `filter_findings_by_lines`
        在定向/提交模式下按 changed_lines 过滤 —— 改存量文件**中间某行**时 1 ∉ changed_lines，
        整条 F3 被**静默丢弃**（实测存活 0 条，同文件里的 R7 因显式 file_level=True 存活 1 条）。
        而"往 Visual/ 文件里新写一个 [Table(" 恰好就是 R12 最想拦的那一幕。锚真实行号后，
        判据自然跟上按行豁免：新写的行在 diff 里 → 拦；只动别的行 → 存量豁免。
        """
        import backend_ruleset as B
        import rule_base
        content = ('namespace Eagle.RMC.Entities {\n'          # 1
                   '[Table("Edu_Plan")]\n'                     # 2 ← 真特性在这
                   'public class EduPlanEntity {}\n'           # 3
                   '}\n')
        r = B.R12EntityLayeringRule()
        f3 = [h for h in r.check(_r12_ctx(content, "Visual", "Edu", "EduPlanEntity"))
              if "带 ORM 表特性" in h.message]
        self.assertEqual([h.line for h in f3], [2],
                         f"F3 必须锚到 [Table( 那一行：{[(h.line, h.message) for h in f3]}")

        # 两向 ①：那一行在 diff 里 → 结论必须**留下**（这条在锚 1 时是红的）
        kept, _ = rule_base.filter_findings_by_lines(f3, frozenset({2}),
                                                     file_level=r.file_level)
        self.assertEqual(len(kept), 1,
                         "新写 [Table( 的那一行在 diff 里 → F3 必须留下（否则就是漏报）")
        # 两向 ②：只改别的行 → 必须**丢掉**（存量豁免，别把 legacy 仓的提交拦死）
        kept2, _ = rule_base.filter_findings_by_lines(f3, frozenset({3}),
                                                      file_level=r.file_level)
        self.assertEqual(kept2, [],
                         "只改文件别的行 → 存量 F3 应豁免，不该拦提交（legacy_baseline 承诺）")

    def test_f6_is_anchored_to_the_namespace_line(self):
        """F6 同理：锚到嵌套 namespace 那一行（不是常量 1）。"""
        import backend_ruleset as B
        import rule_base
        content = ('// 头注释\n'                                   # 1
                   'namespace Eagle.RMC.Entities.Visual.Edu {\n'   # 2 ← 嵌套在这
                   'class EduPlanEntity {}\n}\n')
        r = B.R12EntityLayeringRule()
        hits = [h for h in r.check(_r12_ctx(content, "Visual", "Edu", "EduPlanEntity"))
                if "namespace" in h.message]
        self.assertEqual([h.line for h in hits], [2], f"F6 行号应为 2：{[h.line for h in hits]}")
        kept, _ = rule_base.filter_findings_by_lines(hits, frozenset({2}),
                                                     file_level=r.file_level)
        self.assertEqual(len(kept), 1, "新写的嵌套 namespace 在 diff 里 → F6 必须留下")

    def test_comments_are_not_treated_as_code(self):
        """注释里的 [Table] / namespace 不算代码 —— F3/F6（误报）与 F2（漏报）三个方向都钉住。

        2026-09-21 修：这三个判据原先直接吃 `ctx.content`（原文），而本文件里 R5/R9/R10/R11
        早就统一走 `_code_lines` + `_strip_strings`。修前实测：F3 对 `// [Table]` 与
        `/* [Table] */` 都误报；F2 对"Table/ 下 [Table] 只在注释里"漏报；F6 对块注释里的
        `namespace X.Table.Y` 误报。三个方向现在都必须反过来。
        """
        import backend_ruleset as B
        r = B.R12EntityLayeringRule()

        def hits(content, layer, fname="EduPlanEntity"):
            return [h.message for h in r.check(_r12_ctx(content, layer, "Edu", fname))]

        # F3 误报方向：注释里提过 [Table] → 不算"带 ORM 表特性"
        for c in ('// 曾经 [Table("Edu_Plan")]，已删', '/* [Table("Edu_Plan")] */'):
            got = hits("namespace Eagle.RMC.Entities {\n%s\nclass EduPlanEntity {}\n}\n" % c,
                       "Visual")
            self.assertEqual(got, [], f"注释里的 [Table] 不该报 F3：{got}")

        # F2 漏报方向：Table/ 下唯一一个 [Table] 在注释里 = 真没有表特性 → 必须报
        got = hits('namespace Eagle.RMC.Entities {\n/* [Table("Edu_Plan")] */\n'
                   'class EduPlanEntity {}\n}\n', "Table")
        self.assertTrue(any("缺 ORM 表特性" in m for m in got),
                        f"Table/ 下只有注释里的 [Table] = 真没有表特性，必须报 F2：{got}")

        # F6 误报方向：块注释里留着的旧 namespace 不算声明
        got = hits("/*\nnamespace Eagle.RMC.Entities.Table.Edu { }\n*/\n"
                   "namespace Eagle.RMC.Entities {\nclass EduPlanEntity {}\n}\n", "Visual")
        self.assertEqual(got, [], f"块注释里的 namespace 不该报 F6：{got}")

        # 反向对照：真代码里的 [Table] 照报 F3（防"把判据改瞎"也算过）
        got = hits('namespace Eagle.RMC.Entities {\n[Table("Edu_Plan")]\n'
                   'class EduPlanEntity {}\n}\n', "Visual")
        self.assertTrue(any("带 ORM 表特性" in m for m in got),
                        f"真特性必须照报 F3（否则是把判据改瞎了）：{got}")

    def test_f2_skips_files_that_declare_nothing(self):
        """**没有任何类型声明**的文件不报 F2 —— 剥注释那次改动的连带误报。

        背景：`_r12_code_text` 修好之前 F2 吃的是原文，于是"整份被注释掉的死文件"里
        注释着的 `[Table("…")]` **碰巧**算数、不报 F2；改对之后它们全冒出来了 ——
        13 个客户仓实测这股**新增**误报 346 条（整份注释 12 + 只剩 using 334）。
        F2 问的是"这个**实体**该不该带 ORM 表特性"，文件里根本没有实体时无从问起。
        """
        import backend_ruleset as B
        r = B.R12EntityLayeringRule()

        def f2(content):
            return [h.message for h in r.check(_r12_ctx(content, "Table", "Emp", "EmpEmployeeEntity"))
                    if "缺 ORM 表特性" in h.message]

        # 整份被注释掉的死文件（注释里还留着 [Table]）→ 静默
        self.assertEqual(f2('// namespace Eagle.RMC.Entities.Table.Emp\n'
                            '// {\n'
                            '//     [Table("Emp_Employee")]\n'
                            '//     public class EmpEmployeeEntity { }\n'
                            '// }\n'), [], "整份注释掉的死文件没有实体可分层，不该报 F2")

        # 只剩 using（类定义那几行被注释）→ 静默
        self.assertEqual(f2('using System;\n\n'
                            'namespace Eagle.RMC.Entities.Table.Emp\n'
                            '{\n'
                            '    // public class EmpEmployeeEntity { }\n'
                            '}\n'), [], "只剩 using、没有任何类型声明的文件不该报 F2")

        # 反向对照 ①：活的 class 缺 [Table] → 照报（把判据改瞎就会连这条一起放过）
        self.assertTrue(f2('namespace Eagle.RMC.Entities.Table.Emp\n{\n'
                           '    public class EmpEmployeeEntity { }\n}\n'),
                        "活的 class 缺 [Table] 必须照报 F2")

        # 反向对照 ②：interface / enum **不算**"有实体可分层" —— 2026-09-21 用户拍板收窄。
        # ⚠ 这是**一次有意的反转**：上一版判据故意比 F7 宽（把 interface/enum 也算作
        # "有东西可分层"），理由写的是"`interface ICommonAccCaseEntity` 不能被静默放过"。
        # 用户判断那仍然是拿不属于 F2 的问题问罪 —— F2 只该对**像实体的类**发问。
        # 反转的代价就得写在这儿：`ICommonAccCaseEntity`（Table/ 下 11 个受管仓各一份）
        # 现在**真的不报了**。谁要把它们救回来，改的是规则里的 `_R12_NON_ENTITY_SUFFIXES`
        # 那一层，而不是把这两条断言改回 True（那样等于连 enum 一起捞回来）。
        for decl, why in (("public interface ICommonAccCaseEntity { }", "interface（实测 11 条）"),
                          ("public enum EmpState { On = 1 }", "enum（同上）"),
                          ("public record struct EmpDto { }",
                           "record struct 虽算类，但 Dto 后缀不像实体")):
            self.assertEqual(f2('namespace Eagle.RMC.Entities.Table.Emp\n{\n    %s\n}\n' % decl), [],
                             "%s 不构成'像实体的类'，不该报 F2" % why)

        # 反向对照 ③：像实体的普通类（无后缀）照旧报 —— 收窄不能把这一类一起扫掉。
        self.assertTrue(f2('namespace Eagle.RMC.Entities.Table.Emp\n{\n'
                           '    public class EmpOption { }\n}\n'),
                        "无后缀的普通类（如 JTSetOption / SpecialOption）像实体，必须照报 F2")
        # record / struct 形式的实体也照报（只认 class 会让 AI 改用 record 绕过）
        self.assertTrue(f2('namespace Eagle.RMC.Entities.Table.Emp\n{\n'
                           '    public record class EmpFileEntity { }\n}\n'),
                        "record class 形式的实体必须照报 F2")

    def test_f2_narrowed_to_entity_like_classes(self):
        """F2 收窄（2026-09-21 用户拍板）：只有**像实体的类**才问"缺 [Table]"。

        实测 11 个受管后端仓 443 → 260。被放行的 183 条形状见规则里
        `_R12_NON_ENTITY_SUFFIXES` 上方的注释。本锁守的就是"被放行"这一侧 ——
        少守一侧，将来有人把判据改宽回来（或误删黑名单里的词）不会有人知道。
        """
        import backend_ruleset as B
        r = B.R12EntityLayeringRule()

        def f2(content):
            return [h.message for h in r.check(_r12_ctx(content, "Table", "Emp", "EmpEmployeeEntity"))
                    if "缺 ORM 表特性" in h.message]

        def body(decl):
            return 'namespace Eagle.RMC.Entities.Table.Emp\n{\n    %s\n}\n' % decl

        # 不像实体 → 静默（每一种形状各给一条，形状来自实测量到的分桶）
        for decl, why in (
                ("public class VmEmpPick { }", "Vm* 前缀（实测 148 条）"),
                ("public class EmpPickViewModel { }", "*ViewModel（同上）"),
                ("public class EmpPaperMapping { }", "*Mapping（实测 11 条）"),
                ("public class EmpQueryDto { }", "*Dto（实测 7 条）"),
                ("public class EmpSaveRequest { }", "*Request（实测 3 条）"),
                ("public class EmpLoginResponse { }", "*Response（实测 3 条）"),
                ("public class EmpExportParam { }", "*Param（F1 同族禁用后缀）"),
                ("public class EmpImportInput { }", "*Input（F1 同族禁用后缀）")):
            self.assertEqual(f2(body(decl)), [], "%s 不像实体，不该报 F2" % why)

        # 反向：像实体 → 照报（*Entity 收尾 243 条 + 无后缀普通类 17 条）
        for decl in ("public class EmpFileEntity { }",
                     "public class EmpExportApiEntity { }",
                     "public class JTSetOption { }",
                     "public record struct CostFile { }"):
            self.assertTrue(f2(body(decl)), "%s 像实体，必须照报 F2" % decl)

        # `Vm` 是前缀判据，不能宽松成"以 Vm 开头就放行" —— VmsXxx 不是 ViewModel 命名。
        self.assertTrue(f2(body("public class VmsEntity { }")),
                        "前缀判据要求 Vm 后紧跟大写，VmsEntity 该照报（否则一刀切放行）")

    # ----- F4/F5 Visual/Api 后缀错配 -----

    def test_visual_with_apisuffix_is_blocked(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities { class EduResListApiEntity {} }",
            "Visual", "Edu/EduRes", "EduResListApiEntity"))
        self.assertTrue(any("Visual/" in h.message and "ApiEntity" in h.message for h in hits))

    def test_api_without_apisuffix_is_blocked(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities { class EduPlanLevelExportEntity {} }",
            "Api", "Edu/EduPlan", "EduPlanLevelExportEntity"))
        self.assertTrue(any("Api/" in h.message and "ApiEntity" in h.message for h in hits))

    # ----- F6 namespace 嵌套 -----

    def test_nested_namespace_is_blocked(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities.Visual.Edu { class EduResListEntity {} }",
            "Visual", "Edu/EduRes", "EduResListEntity"))
        self.assertTrue(any("namespace" in h.message for h in hits))

    def test_flat_namespace_passes(self):
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_r12_ctx(
            "namespace Eagle.RMC.Entities { class EduResListEntity {} }",
            "Visual", "Edu/EduRes", "EduResListEntity"))
        self.assertFalse(any("namespace" in h.message for h in hits),
                         f"平铺 namespace 不该误报：{[h.message for h in hits]}")

    # ----- 作用域边界 -----

    def test_non_layered_entities_not_in_scope(self):
        """旧仓没有 Table/Visual/Api 三层结构（如 RMCHY）→ R12 不适用。"""
        import backend_ruleset as B
        rel = "RMCHY/Eagle.RMCHY.Entities/EduPlan/X.cs"
        hits = B.R12EntityLayeringRule().check(_ctx(
            "namespace Eagle.RMC.Entities { class X {} }", rel=rel))
        self.assertEqual(hits, [],
                         f"无分层结构的旧实体仓不应触发 R12：{[h.message for h in hits]}")

    def test_non_entities_dirs_out_of_scope(self):
        import backend_ruleset as B
        for rel in ("RMC/Eagle.RMC.Business/X.cs",
                    "RMC/Eagle.RMC.Controller/X.cs",
                    "Core/Eagle.Entities/User.cs"):  # 旧单层实体目录
            self.assertFalse(
                B.R12EntityLayeringRule().applicable(_ctx("", rel=rel)),
                f"{rel} 不该套 R12")

    # ----- 双态 severity -----

    def test_registered_and_severity(self):
        import backend_ruleset as B
        ids = [r.id for r in B.BACKEND_RULES]
        self.assertIn("R12", ids, "R12 必须挂到 BACKEND_RULES，否则不会真生效")
        r = B.R12EntityLayeringRule()
        self.assertTrue(r.error_in_targeted, "R12 必须 targeted 升 error（与 R10/R11 一致）")

    def test_legacy_no_change_is_info_only(self):
        """存量没改动的历史文件 → 只 info 不 error（与 R10/R11 同语义）。"""
        import backend_ruleset as B
        r = B.R12EntityLayeringRule()
        # 模拟 legacy 仓、文件没改动、非新建
        ctx = _r12_ctx(
            "namespace Eagle.RMC.Entities { class ExportTrainingLedgerInputEntity {} }",
            "Api", "Edu/EduPlan", "ExportTrainingLedgerInputEntity",
            changed=(), is_new=False, legacy=True, targeted=True)
        sev = r.severity(ctx)
        self.assertEqual(sev.name, "INFO",
                         f"存量没改动不该被拦，实际 severity={sev}")


# ==============================================================
# R12-F7：ApiController 项目内不得内嵌非 Controller 类（2026-09-21 补）
# ==============================================================

R12_APICTL = "RMC/Eagle.RMC.ApiController"


def _f7_ctx(content, fname="EduPlanController.cs", sub="Table/Edu/EduPlan", **kw):
    return _ctx(content, rel=f"{R12_APICTL}/{sub}/{fname}", **kw)


class TestR12F7ControllerNestedType(unittest.TestCase):
    """F7：ApiController 项目内不得内嵌非 Controller 类。

    为什么必须有这个测试类：F1-F6 的适用范围锚定 `*Entities/{Table,Visual,Api}/`
    （见 _r12_layer_segment），对 ApiController 项目 applicable 恒为 False。
    2026-09-21 R12 上线时**没有拿触发它的真实 case 做回归**，于是它自己 see 字段里写着的
    ExportTrainingLedgerInput 原样漏过、并又复发了一次（RiskBuildBatchAddRequest）。
    本类即那两次的回归锁：下面两个"真实 case"必须永远为红。
    """

    def test_export_input_inside_controller_is_blocked(self):
        """真实 case 1：EduPlanController 内嵌 ExportTrainingLedgerInput（照搬关键片段）。"""
        import backend_ruleset as B
        content = (
            "namespace Eagle.RMC.ApiController\n"
            "{\n"
            "    public partial class EduPlanController : BaseApiController<EduPlanRepository>\n"
            "    {\n"
            "        [HttpPost]\n"
            "        public IActionResult ExportTrainingLedger([FromBody] ExportTrainingLedgerInput input)\n"
            "        {\n"
            "            var bytes = this.Repository.ExportTrainingLedger(null, null, null);\n"
            "            return File(bytes, \"application/vnd.ms-excel\", \"安全培训台账.xlsx\");\n"
            "        }\n"
            "    }\n"
            "\n"
            "    public class ExportTrainingLedgerInput\n"
            "    {\n"
            "        public string Conditions { get; set; }\n"
            "    }\n"
            "}\n")
        hits = B.R12EntityLayeringRule().check(_f7_ctx(content))
        self.assertEqual(len(hits), 1, f"应恰好命中内嵌类一处：{[h.message for h in hits]}")
        self.assertEqual(hits[0].line, 13,
                         "行号必须指向 class 定义行（F7 是行级判据，不能退化成常量锚点 1）")
        self.assertIn("ExportTrainingLedgerInput", hits[0].using)

    def test_batch_request_inside_controller_is_blocked(self):
        """真实 case 2：HYRiskBuildController 内嵌 RiskBuildBatchAddRequest。"""
        import backend_ruleset as B
        content = (
            "namespace Eagle.RMCHY.ApiController\n"
            "{\n"
            "    public class HYRiskBuildController : MasterApiController<HYRiskBuildRepository, HYRiskBuildEntity>\n"
            "    {\n"
            "        [HttpPost]\n"
            "        public DataEntity BatchAdd([FromBody] List<HYRiskBuildEntity> list)\n"
            "        { return this.Success(); }\n"
            "    }\n"
            "\n"
            "    public class RiskBuildBatchAddRequest\n"
            "    {\n"
            "        public string Type { get; set; }\n"
            "    }\n"
            "}\n")
        hits = B.R12EntityLayeringRule().check(
            _f7_ctx(content, "HYRiskBuildController.cs", sub="Table/HY"))
        self.assertEqual([h.using for h in hits], ["RiskBuildBatchAddRequest"],
                         f"应只命中内嵌类：{[h.message for h in hits]}")

    def test_plain_controller_passes(self):
        """只有控制器本体（含 partial 分部）→ 不报。"""
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_f7_ctx(
            "namespace Eagle.RMC.ApiController {\n"
            "    public partial class EduPlanController : BaseApiController<EduPlanRepository> { }\n"
            "    public class OtherController { }\n"
            "}\n"))
        self.assertEqual(hits, [], f"控制器本体不该报：{[h.message for h in hits]}")

    def test_record_and_struct_are_blocked(self):
        """AI 改用 record / struct 也拦得住（不吃"只认 class"的亏）。"""
        import backend_ruleset as B
        for kw in ("record", "record class", "struct"):
            hits = B.R12EntityLayeringRule().check(_f7_ctx(
                "namespace Eagle.RMC.ApiController {\n"
                f"    public {kw} ExportLedgerDto {{ public string A {{ get; set; }} }}\n"
                "}\n"))
            self.assertEqual([h.using for h in hits], ["ExportLedgerDto"],
                             f"`{kw}` 定义的参数类必须报：{[h.message for h in hits]}")

    def test_commented_out_class_is_not_blocked(self):
        """注释 / 字符串里的 class 不算违规（走 _code_lines + _strip_strings）。"""
        import backend_ruleset as B
        hits = B.R12EntityLayeringRule().check(_f7_ctx(
            "namespace Eagle.RMC.ApiController {\n"
            "    public class EduPlanController { }\n"
            "    // public class OldInput { }\n"
            "    /* public class BlockInput { }\n"
            "       public class BlockInput2 { } */\n"
            "    public string Tip = \"public class InString { }\";\n"
            "}\n"))
        self.assertEqual(hits, [], f"注释/字符串里的 class 不该报：{[h.message for h in hits]}")

    def test_f7_only_checks_controller_files(self):
        """F7 文件名闸门（2026-09-21 用户拍板收窄）：只查 `*Controller.cs`。

        立法意图是本函数 fix 正文自己写的「**Controller 类里**不得内嵌实体/DTO/参数类」，
        而实现原先问的是"ApiController **项目**里有没有非 Controller 类" —— 于是项目里的
        partial 第二片（`CustomMessageHandler_Events.cs`）与 Service 实现类（`*Service.cs`）
        全被报了。实测 11 个受管后端仓 24 → 8，放行的 16 条全是这两类。

        反向对照同样重要：**同一个内嵌类，换到非 Controller 文件名下必须静默、换回
        Controller 文件名下必须报** —— 只验静默那一侧，判据被改瞎（比如 return []）也过。
        """
        import backend_ruleset as B
        content = ("namespace Eagle.RMC.ApiController {\n"
                   "    public partial class EduPlanController { }\n"
                   "    public class ExportTrainingLedgerInput { }\n"
                   "}\n")
        r = B.R12EntityLayeringRule()

        self.assertEqual([h.using for h in r.check(_f7_ctx(content))],
                         ["ExportTrainingLedgerInput"],
                         "Controller 文件里的内嵌类必须照报（闸门不能把真违规也滤掉）")

        for fname in ("CustomMessageHandler.cs", "CustomMessageHandler_Events.cs",
                      "ClientInitializationService.cs"):
            hits = r.check(_f7_ctx(content, fname))
            self.assertEqual(hits, [],
                             "%s 不是控制器的类，整个文件都不该查：%s" % (fname, [h.using for h in hits]))

        # 闸门只认文件名收尾，不是"名字里含 Controller 就行" —— 这条守的是判据的**形状**。
        self.assertEqual([h.using for h in r.check(_f7_ctx(content, "EduPlanControllerFactory.cs"))],
                         [],
                         "`*ControllerFactory.cs` 不是控制器本体（不以 Controller 收尾），不该查")

    def test_f7_scope_does_not_leak_to_other_projects(self):
        """作用域边界：F7 只认 `*ApiController/` 目录段，不碰 Business / Controller / Entities。"""
        import backend_ruleset as B
        f7 = B._r12_in_apicontroller
        for rel in ("RMC/Eagle.RMC.Business/X.cs",
                    "RMC/Eagle.RMC.Controller/X.cs",
                    "RMC/Eagle.RMC.Entities/Visual/Edu/X.cs",
                    "RMCHY/Eagle.RMCHY.Entities/Api/HY/HYRiskBuildBatchValidateApiEntity.cs"):
            self.assertFalse(f7(_ctx("", rel=rel)), f"{rel} 不该被 F7 当成 Controller 项目")
        self.assertTrue(
            f7(_ctx("", rel="RMC/Eagle.RMC.ApiController/Table/Edu/EduPlanController.cs")),
            "ApiController 项目必须被 F7 认出来（这正是原来漏掉的那一段）")

    def test_whole_rule_scope_unchanged_for_entities(self):
        """加 F7 不得改变 R12 原有作用域：分层实体目录仍由 F1-F6 覆盖，其余照旧不适用。"""
        import backend_ruleset as B
        r = B.R12EntityLayeringRule()
        self.assertTrue(r.applicable(_ctx("", rel="RMC/Eagle.RMC.Entities/Visual/Edu/X.cs")),
                        "分层实体目录（F1-F6 的作用域）不能被 F7 的改动挤掉")
        for rel in ("RMC/Eagle.RMC.Business/X.cs",
                    "RMC/Eagle.RMC.Controller/X.cs",
                    "Core/Eagle.Entities/User.cs"):
            self.assertFalse(r.applicable(_ctx("", rel=rel)), f"{rel} 仍不该套 R12")

    def test_line_follows_changed_lines(self):
        """F7 是行级判据 → 行号准确，能正确跟随按行豁免（不为存量负责、新写必拦）。"""
        from rule_base import filter_findings_by_lines
        import backend_ruleset as B
        content = ("namespace Eagle.RMC.ApiController {\n"
                   "    public class EduPlanController { }\n"
                   "    public class ExportXxxInput { }\n"
                   "}\n")
        hits = B.R12EntityLayeringRule().check(_f7_ctx(content))
        self.assertEqual(len(hits), 1)
        kept, dropped = filter_findings_by_lines(hits, frozenset({1, 2}))
        self.assertEqual((len(kept), dropped), (0, 1), "类定义行没动过 → 应被豁免")
        kept, dropped = filter_findings_by_lines(hits, frozenset({3}))
        self.assertEqual((len(kept), dropped), (1, 0), "类定义行在改动集里 → 必须拦")


if __name__ == "__main__":
    unittest.main()
