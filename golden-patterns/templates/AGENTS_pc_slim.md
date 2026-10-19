# AgentGuard PC 端（Vue 3 + Element Plus）—— AI 编码规则地图

> **这份文件不是某个仓的 AGENTS.md**，而是**端级知识**：所有 PC 客户仓共用的那一份。
> 客户仓的 `AGENTS.md` **用指针指向本文件**，不要把它复制进仓里（复制 = 漂移源）。
> 核心原则：**先循框架，后循先例，再谈自主；门禁是硬边界，不是建议。**
> 客户独有知识不在本文件，在你那个仓的 `AGENTS.md`。

## 改码前：加载知识（按需，不要全读）

写页面前按任务类型读对应范式（位置见下）：

> **本表覆盖 `golden-patterns/pc/` 下全部 11 份**；拿不准时以 `ls golden-patterns/pc/` 为准
> （目录是机器实况，本表是手维——两者不一致时信目录）。

| 任务 | 必读 |
|---|---|
| **动手前先走流程**（类型判定→套骨架→接口→条件→列→按钮→抽屉→验证，8 步） | `pc/pc_dev_sop.md` |
| 新增/修改任何页面（模式大全 + **5 类型速查** + 常见坑 + 禁用手法） | `pc/pc_page_patterns.md` |
| 列表页整页骨架（类型1 带Tab / 类型2 简单 / 类型4 行删除）+ 通用交互 + 列渲染技巧 | `pc/pc_vue_list_page.md` |
| Tab 页（模式A 内联 / 模式B 容器 + tabs 子页 / 模式C 纯壳） | `pc/pc_tab_page.md` |
| 表单 / 抽屉 / `user-choose` 多选人员 | `pc/pc_vue_form_drawer.md` |
| 左树 + 右列表 | `pc/pc_tree_list.md` |
| 批量操作（勾选 + 批量删） | `pc/pc_batch.md` |
| 字典 / 参数（禁硬编码选项） | `pc/pc_dict_params.md` |
| 独立详情页（区别于弹窗） | `pc/pc_detail_page.md` |
| 选组件 / 权限控制 / request 封装 | `pc/pc_components_request.md` |
| 目录结构 / 路由 / .env / 命名 / 状态管理 / UI 交互 | `pc/pc_conventions.md` |

> 入口也可以用技能 **`eagle-rmc-pc-page-dev`**（页面开发 SOP + 5 类骨架速查）——
> 它是**薄路由**，正文就是上表指向的 `golden-patterns/pc/`，入口文件
> `<HARNESS>/integrations/eagle-rmc-pc-page-dev-skill/SKILL.md`；
> 装了它的 agent 靠触发词自动激活，**客户仓的 `AGENTS.md` 不必再重复指向它**。
> （2026-09-21 补：这根指针此前只写在各 PC 仓的 `AGENTS.md` 里，端级模板一次都没提过它。）

范式位置（独立门禁工程，所有客户通用）：
```
<HARNESS>/golden-patterns/pc/
```

## PC 端级事实（本端全客户通用）

- **先找标本页再动手**：写页面前先在本仓 `src/views` 找同类已验收页面做参照，禁止自由发明结构。
  五类标本页清单见 `golden-patterns/pc/pc_page_patterns.md` §6.0。

> 与 H5 同一个道理（`AGENTS_h5_slim.md` 的「先找本仓同类已验收页面做参照」）：**先循先例**是本端
> 最高优先级的工作方式，所以它属于端级模板，不属于任何单个客户仓。

## 端级规则

**能机械判定的一律不在这里写正文** —— 正文只有一份，在 `<HARNESS>/checks/pc_ruleset.py`。
这里只写规则本身 + **规则 ID 指针**；被拦时门禁会把 ID 直接打给你。

| # | 规则 | 谁在管 |
|---|---|---|
| 1 | **eagle-xxx 优先**：全局组件（权威清单 = PC 基线仓 `registerGlobComp.ts`，2026-09-19 实测 **86** 个），禁止绕过封装直接用 `el-table`/`el-form`/`el-dialog` 重写 | 门禁 `PC-COMP`（组件收敛，error）+ `PC-ELFORM`（直用 el-form/el-table/el-dialog，error，2026-10-15 起含 el-dialog）；写法见 `pc_components_request.md` |
| 2 | **请求走 `@/utils/request`，消息走 `@/utils/message`**，禁止直用 axios / ElMessage | 门禁 `PC-AXIOS`（error）+ `PC-ELMSG`（warn） |
| 3 | **列表必须后端分页**（eagle-page + GetApiPageData），禁止前端全量拉取后 slice | ⚠ **软约束，无门禁** |
| 4 | **删除/提交/批量必须 `message.confirm` 二次确认** + `*Loading` 系列加载态 | ⚠ **软约束，无门禁** |
| 5 | **v-auth 是空壳禁用** —— 权限用 store getter + `v-if` 或行级字段 `disabled` | 门禁 `PC-VAUTH`（error） |
| 6 | **新增业务页面用 JS**（`<script setup>` 不带 `lang="ts"`），工具层才用 TS | ⚠ **软约束，无门禁** |
| 7 | 新增依赖前必须说明必要性并经用户确认；新增全局组件必须注册到 `registerGlobComp.ts` | ⚠ **软约束，无门禁**（漏注册会表现为 `PC-COMP` 认不出该组件） |
| 8 | **`eagle-form` 的校验回调是无参回调** —— `ref` 绑 `<eagle-form>` 时必须写 `validate(() => {})`：封装层只在校验**通过**时零参调 `callback`、不透传 `valid`，所以照抄原生 `el-form` 的 `(valid) => { if (!valid) return }` 里 `valid` 恒 `undefined`、回调一进来就 `return`，表现为**按钮点了没反应且控制台零报错**（`if(valid){ok}else{fail}` 更隐蔽：恒走失败分支）。判据只看 `ref` 绑的标签，不看变量名；绑原生 `<el-form>` 的 `(valid)` 是**正确写法**，别批量改。正文见 `pc_CODE_RULES.md` §9.1 / §10.5，样板见 `pc_vue_form_drawer.md` §一 | 门禁 `PC-VALIDATE`（error；2026-10-07 全 PC 仓实测存量 1，已提交） |

> ⚠ **第 3、4、6、7 条没有门禁兜底** —— 它们只能靠你读这份文件。
> 这也正是这份文件**必须短、必须准**的理由：写得越长，越没人读完，软约束就越等于不存在。

## 门禁规则 ID 速查（本端全集）

被拦时对着这张表认 ID，别猜：

| 规则 ID | 拦什么 | 级别 |
|---|---|---|
| `PC-CONSOLE` | `console.log` 残留 | error |
| `PC-IP` | 硬编码 IP | error |
| `PC-TODO` | `TODO` 残留 | error |
| `PC-AXIOS` | 直接 `import axios` | error |
| `PC-VAUTH` | 空壳 `v-auth` 指令 | error |
| `PC-COMP` | 组件收敛（deny / dead / 平行版） | error（strict）／loose 放行 dead |
| `PC-VFOR` | `v-for` 缺 `:key` | error |
| `PC-VALIDATE` | `eagle-form` 校验回调写成 `(valid)` 签名 | error |
| `PC-ELFORM` | 直用原生 `el-form` / `el-table` / `el-dialog`（应用 eagle-form / eagle-page / eagle-model；`src/components/` 平台包装件除外；**存量 350 文件只拦改动行**） | error |
| `PC-MODAL` | `eagle-model` 传 `hasLayer=false` 却是没补容器的表单弹窗 | error |
| `PC-SFC` | SFC 结构校验 | error |
| `PC-EOL` | 行尾污染（期望行尾由门禁按 gate 自动判定） | error |
| `PC-SYNTAX` | 语法校验 | error |
| `PC-ELMSG` | 直接 `ElMessage` / `ElMessageBox` | **warn（不拦）** |
| `PC-ELDENY` | 直用 `el-dialog`（整文件观察层；**改动行拦截已由 `PC-ELFORM` 承担**） | **warn（不拦）** |

## 被拦了怎么办

按输出里的「❌ 什么错 / ✅ 怎么改」修复到通过，**不要绕过**。
确实是合规写法被误判时，在命中行**本行或紧邻上一行**加行内豁免
`// harness-ok: <规则ID> <理由>` —— **规则 ID 必须写对**（写错不生效，设计上不允许一句话静默所有规则）。
紧急 `--no-verify` 须先留痕并向负责人说明。

> 跑门禁的命令与退出码语义**不在这里重复** —— 那是**用户级全局守则**（`~/.agentguard/GLOBAL_AGENTS.md`，
> 各工具自动加载）的内容。若你的 agent 读不到它，跑 `sh <HARNESS>/sandbox/bootstrap_agent_env.sh`。

## 跨仓库联动（改后端/移动端）

联动修改其他仓库前，**必须先读目标仓的 `AGENTS.md`**（后端规则 = Controller→Repository 两层，
与本前端规则互不套用）；未指明目标项目时先询问用户，不得擅自跨仓修改。
