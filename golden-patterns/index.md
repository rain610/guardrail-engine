# 黄金范式库索引（Golden Patterns · harness 参照层）

> 从客户 gxgk **已验收生产代码**提取的可照抄代码骨架，解决"AI 编码不符合项目既有风格"的
> 根因——给模型真实样板，而非规则文字。
> 本目录是工作源（唯一维护点）。旧存档：`<VAULT>/concepts/黄金范式/`（只读快照，不再更新）。
> ⚠ `<VAULT>` = 团队知识库根目录，**只有负责人本机上有**；它的解释写在用户级守则里
>   （`~/.agentguard/GLOBAL_AGENTS.md`，哨兵区之外），**不在本仓定义** —— 与
>   `templates/00_知识分层设计.md` 同一约定（那里讲了为什么）。同事机器上没这个目录，忽略即可。

## 两级范式
1. **技术骨架级**（跨业务通用）：列表/表单/Controller 的标准写法，任何页面都适用
2. **业务端到端级**（单业务域深度）：某业务域从后端状态机→Controller→前端→跨客户差异一张图

## 何时用（加载时机）
- **任何要写/改三端代码前**：新增页面、Controller、Repository，或改列表/表单/弹窗/上传/状态展示时
- developer / builder 接到编码任务，**先读对应栈的黄金范式再动代码**
- 不确定某写法在项目里"长什么样"时

## 规则层（权威约定/边界/例外，与样板层互补）
- `rules/backend_CODE_RULES.md` —— 后端 .NET 全套编码规则（11 章，含基类能力速查 §11.3）
- `rules/pc_CODE_RULES.md` —— PC Vue 全套编码规则（13 章，含页面 9 模式/组件清单/权限）
- `rules/_00_覆盖率对照.md` —— 规则↔样板覆盖审计（标出 PC 待补样板的缺口）
- `rules/_01_门禁规则候选.md` —— 规则→checks 可判定规则候选表（含分级建议）
> 规则层定位：规范的"允许/禁止/例外"那一半；样板层（下方 backend/pc/h5/endtoend）是"照抄什么"。规则对齐真实架构前先做覆盖审计，别直接当 error 级门禁。

## 按端速查（改哪端，读该端）

> **按任务选哪份，先看端级模板**（那才是入口；本表是平铺目录）：
> `templates/AGENTS_pc_slim.md` · `templates/AGENTS_h5_slim.md` · `templates/AGENTS_backend_slim.md`
> 端级模板里有「按任务 → 必读哪份」表 + 该端门禁规则 ID 速查。
> ⚠ 本表是**手维目录**，会落后于目录实况。它漂过一次：2026-09-19 查出 6 份新风范整批漏登记
>   （它们 2026-09-07 就建好了，本表 09-12 改过一轮仍没带上）。
>   2026-09-19 已重新对齐（PC 端 11 份全部在表内）。
>   这类「同一份映射两个手维副本」正是 `templates/00_知识分层设计.md` 讲的那个病。
>   **拿不准时以 `ls golden-patterns/<端>/` 为准。**
>   ✅ **2026-09-20 已加锁**：`tests/test_docs_consistency.py::TestGoldenPatternsIndexIsComplete`
>   遍历 `golden-patterns/<端>/` 的直接子文件，**漏登记报错、指名了不存在的文件也报错**。
>   本表再漂会被测试拦下，不用等人肉发现。（这把锁只管**映射**，管不了范式正文的对错。）

| 端 | 文件 | 内容 |
|----|------|------|
| 后端 .NET | `backend/backend_api_authority.md` | **后端主流程接口权威索引（先读这个！）**：三层基类继承关系 + `GetApiPageData` 真实签名 |
| 后端 | `backend/base_class_reference.md` | 基类**速查表**：有没有现成方法/扩展点，先查这里再写 |
| 后端 | `backend/architecture_rules.md` | 新建模块目录 / 返回包装 / 异常 / 日志 / 常量 |
| 后端 | `backend/coding_patterns.md` | 典型写法样板 / 例外修复流程 / 方案确认模板 |
| 后端 | `backend/backend_controller_repository.md` | Controller+Repository（继承基类/增删改查/软删/分页） |
| 后端 | `backend/backend_business_rules.md` | 列表/树查询 GetQueryable、校验拦截 WarningException、X-Sign 签名 |
| 后端 | `backend/backend_download_complex.md` | 二进制下载/导出（PhysicalFile/FileStream/CreateByteResponse） |
| 后端 | `backend/backend_excel_import_export.md` | **Excel 导入导出横切**：ExcelImportEntity 契约/ExcelOperator 规则引擎/行级校验入库/动态导出/踩坑9条 |
| 后端 | `backend/entity-layering.md` | **Entities 分层与命名规范（必读）**：Table/Visual/Api 三目录 × Entity/ApiEntity/Config 后缀约定，禁 `*Input.cs` / `*Param.cs` 查询条件类，**Controller 文件内不得内嵌任何类**，门禁 R12（含 F7）配套 |
| 后端 | `backend/eagle_rmc_coding_rules_agents.md` | 编码总规则（供 agent） |
| PC Vue | `pc/pc_dev_sop.md` | **页面开发 8 步 SOP**（类型判定→套骨架→接口→条件→列→按钮→抽屉→验证）+ 各类型差异 + Checklist |
| PC Vue | `pc/pc_page_patterns.md` | **页面模式大全（先读这个！）**：5 类型速查 + 列表/弹窗/抽屉/详情/Tab/批量/审批 + 常见坑 + 禁用手法，带真实出处 |
| PC Vue | `pc/pc_components_request.md` | 选组件 / 权限控制 / request 封装 |
| PC Vue | `pc/pc_conventions.md` | 目录结构 / 路由 / .env / 命名 / 状态管理 / UI 交互 |
| PC Vue | `pc/pc_vue_list_page.md` | 列表页（eagle-page + 手写 el-table 两种，批量操作/分页从0起） |
| PC Vue | `pc/pc_vue_form_drawer.md` | 表单/抽屉（v-model computed 桥接/validate/新增编辑共弹窗） |
| PC Vue | `pc/pc_tab_page.md` | 列表页 Tab 化：模式A同url内联Tab(单tab也加)/模式B异url index容器+tabs独立子页 |
| PC Vue | `pc/pc_tree_list.md` | 左树+右列表（flex 布局、@on-Choose 联动改筛选、根节点不限） |
| PC Vue | `pc/pc_batch.md` | 批量操作（selection+getSelections/selectedList+判空+confirm+postLoading；可带业务过滤） |
| PC Vue | `pc/pc_dict_params.md` | 字典/参数（request.getBatchParams 批量 A;B;C，禁硬编码选项、禁 N+1） |
| PC Vue | `pc/pc_detail_page.md` | 独立详情页（router.push+query 跳转，区别于弹窗） |
| H5 uniapp | `h5/h5_list_status_card.md` | 列表卡片+状态tag（eagle-row-card/tagName/tagType/--兜底） |
| H5 uniapp | `h5/h5_constant_button.md` | 原生 WebView 常驻按钮 + 禁止拍照 canvas 占位图 + 签名上传 |
| H5 uniapp | `h5/组件手册/索引.md` | **H5 eagle-* 组件手册**（eagle-page/row-card/upload/choose 等 **24** 份，实体在 `组件手册/h5/` 下） |

## 业务端到端范本（新增类似业务照此）

| 文件 | 范本 |
|------|------|
| `endtoend/relatedparty_audit_endtoend.md` | **相关方资质审核**（状态机/Controller/权限/跨客户差异）——先示范 |
| `endtoend/special_check_endtoend.md` | **监督检查**（三层各自状态机 + 跨客户差异） |
| `endtoend/traincert_endtoend.md` | **证书管理**（有效期三态状态机 + SP 批量刷新 + partial 三文件拆分） |
| `endtoend/tree_select_endtoend.md` | **树形选择横切（三端）** ToTreeData 扁平转树 / eagle-tree / CompanyPNo 坑 |

## 造新范本（业务多、增量做）
- **先读 `templates/00_黄金范本制造模板.md`**（8 章节 + 提取流程 + 批量策略 + 质量红线）
- 造好存进 `endtoend/`（文件名 `{业务}_endtoend.md`），并在上面的「业务端到端范本」表登记 —— 漏登记会被锁拦下
- 简单低频业务可用 4 章轻量版（文件地图/状态机/接口/照抄清单）

## 关键约定（不要用错）
- 后端：**主列表接口是 `GetApiPageData` 不是 `GetPageData`**；Controller 薄壳只编排调 Repository；
  列表 `DbSetND` 起步 + 公司隔离 + `Status==Normal` + `ToPageData`；软删 `OnDelete` 置状态；
  校验 `WarningException`；签名 `X-Sign`
- PC：分页**从 0 起**，查询/重置 `page.value=0`；批量操作用 `@selection-change` 存行数组 +
  `判空→confirm→BatchDelete`；状态 `el-tag size=small`
- H5：状态 `tagName` 用后端 `XXXStateName` + `tagType` getTagType 颜色；字段 `?:'--'` 兜底；
  原生 WebView 入口**必须常驻显式按钮**（不依赖 popup/长按）

## 已知坑（看完照样范易踩）
- 别把规则当教条：范式是"参照系"，具体业务字段/参数以 05 文档和真实代码为准
- 后端若用 `MasterController<TRepo,TEntity>` 传统基类，增删改是内置的，别重复手写
- PC 编辑/新增共弹窗必须 `computed get/set` 桥接 v-model，别直接改 prop
- H5 上传必须带 `Authorization + X-Sign` 签名，否则后端报"参数不完整"
- **partial 分部类漏判（重灾区）**：改/评审 Repository/Controller 前先查对照表
  `<VAULT>/concepts/技术规范/跨文件Partial类对照表.md`（**只在负责人本机**），或用 codebase-memory（**可选工具**，本机没装就跳过）
  `search_graph(name_pattern=类名)` 实时列出同类全部文件
- **历史反模式别模仿**：`GetData`/`Deletes`/`InitData`、无意义空 override、绕过基类重写
  增删改不用 `base.xxx()`、先 `ToList()` 再内存分页、循环内逐条查库

## 维护
- 新格式/新组件范式：从已验收代码提取存进本目录（harness 为源）
- 来源仓库：`D:/MyProject/aiauto/agentguard_*_net-gxgk`（gxgk 为基准，其他客户差异大再各建）
- 修改后 commit 带 `[harness]` 标记；`<VAULT>` 里的旧档为只读快照，不再维护