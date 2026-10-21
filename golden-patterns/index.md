# 黄金范式（Golden Patterns）

门禁回答「不能这么写」，黄金范式回答「照这个写」。

这里不是规则清单，而是按「**你要写什么**」组织的已验收代码骨架：请求层、列表页、表单弹窗、
后端分层、校验与错误、日志、配置，以及一个从常量到页面的业务全链范本。
被门禁拦下时，输出里的 `📖 See:` 会把你送到对应的那一篇。

## 两层范式

1. **技术骨架级**（`patterns/`）：跨业务通用。写列表、表单、接口、校验时先来这里找同款结构。
2. **业务端到端级**（`endtoend/`）：一个业务域从常量、接口、状态机到页面怎么串起来。新增类似业务时照抄施工顺序。

`reference/` 下是骨架对应的完整参考代码，**正面样板本身必须能过门禁**（测试会跑）。

## 按任务查（写码前先看这张表）

| 你要写 / 改什么 | 先读 | 完整参考实现 | 对接规则 |
|---|---|---|---|
| 页面里发 HTTP 请求、统一鉴权与错误提示 | `patterns/web/request-layer.md` | `reference/web/request.js` | 无（软约束） |
| 列表页：筛选、分页、批量、行操作 | `patterns/web/list-page.md` | `reference/web/task-list.vue` | 无（软约束） |
| 新增/编辑共用一个表单弹窗 | `patterns/web/form-dialog.md` | `reference/web/task-form.vue` | 无（软约束） |
| 新增 Controller / Repository，业务规则落层 | `patterns/backend/controller-repository.md` | `reference/backend/TaskController.cs` | 无（软约束） |
| 参数校验、业务错误、统一异常返回 | `patterns/backend/validation-and-errors.md` | `reference/backend/GlobalExceptionMiddleware.cs` | 无（软约束） |
| 写日志、排查输出 | `patterns/cross-cutting/logging.md` | `reference/web/request.js` | `NO-CONSOLE` |
| 配地址、端口、密钥、开关 | `patterns/cross-cutting/config-and-env.md` | `reference/config/runtime-config.js` | `NO-HARDIP` |
| 从零做一个完整业务模块 | `endtoend/task-management.md` | `reference/` | 以上全部 |

## 被门禁拦下时

```text
  ❌ src/logger.js:3 [NO-CONSOLE] 禁止 console.log 残留（调试代码不应进入提交）
     FIX: 删除 console.log 行，或用条件日志封装
     📖 See: golden-patterns/patterns/cross-cutting/logging.md
```

1. 看 `[规则ID]`，确认被哪条规则拦下；
2. 打开 `📖 See:` 指向的范式，照骨架改，而不是只删掉命中那一行；
3. 重跑门禁，直到通过；
4. 如果范式本身不适用，按仓库根 `AGENTS.md` 的说明处理，并把冲突报给维护者。

## 规则层和范式层各管什么

| | 规则层（`checks/`） | 范式层（`golden-patterns/`） |
|---|---|---|
| 能判什么 | 行级/文件级的机械事实：禁用 API、硬编码、行尾、结构语法 | 结构、分层、命名、交互、状态机、错误处理策略 |
| 输出什么 | `❌ [规则ID] 原因` + `FIX` + 可选 `📖 See:` | 可照抄骨架 + 为什么 + 常见坑 + 自检 |
| 失败模式 | 只拦不改，agent 自己猜写法 | 只写不拦，靠自觉照做 |
| 怎么对接 | 规则类声明 `see` 指向范式 | 范式写清对接的规则 ID |

两者缺一不可：门禁保证下限，范式给出上限。范式**不是**绕过规则的许可。

## 现有范式

| 范式 | 内容 | 对接规则 |
|---|---|---|
| `patterns/web/request-layer.md` | axios 只在请求层出现；token、响应壳、401、错误提示集中处理 | — |
| `patterns/web/list-page.md` | 后端分页、查询重置、loading、批量确认、行按钮收敛、空状态 | — |
| `patterns/web/form-dialog.md` | 新增/编辑共用、字段拷贝、校验后提交、提交锁、关闭重置 | — |
| `patterns/backend/controller-repository.md` | Controller 薄壳、Repository 业务规则、DTO、软删、分页、批量 SQL | — |
| `patterns/backend/validation-and-errors.md` | 前端体验校验 + 服务端权威校验；400/404/409/500 分类；不吞异常 | — |
| `patterns/cross-cutting/logging.md` | 统一日志出口、级别、结构化上下文、脱敏、不刷屏 | `NO-CONSOLE` |
| `patterns/cross-cutting/config-and-env.md` | 环境配置唯一入口、启动期校验、密钥不进仓库 | `NO-HARDIP` |
| `endtoend/task-management.md` | 任务管理全链：文件地图、状态机、接口、请求路径、照抄顺序 | 以上全部 |

## 给规则挂范式

规则类声明仓库相对路径，`Rule.run()` 会把 `see` 盖到每个 `Finding` 上，
`format_finding()` 统一输出 `📖 See:`：

```python
class NoConsoleRule(Rule):
    id = "NO-CONSOLE"
    see = "golden-patterns/patterns/cross-cutting/logging.md"
```

声明一次，所有走这条规则的文件都生效。没有机械规则可挂的范式（列表页、分层、校验），
由 `index.md` 的任务表和评审流程负责指路。

## 造新范式

先读 `templates/00_范式制造模板.md`：它规定了固定章节、提取流程、质量红线和登记方式。
要点只有三条：

1. 从**已验收代码**提取，不凭印象编；
2. 正文放核心骨架与坑，完整实现放 `reference/`；
3. 入库时在本文的「按任务查」和「现有范式」表登记，漏登记会被 `tests/test_golden_patterns.py` 拦下。

## 约定

- **路径一律仓库相对**：不写盘符、家目录、内网知识库路径 —— 换台机器就断的指针不算指针。
- **只放只读文档**：不写豁免语法、不写「如何关掉这条规则」。豁免的唯一说明在仓库根 `AGENTS.md`。
- **范式跟着规则一起评审**：规则改了口径，范式要在同一批里对齐；过期范式比没有更糟。
- **范式是参照系，不是教条**：具体字段/参数以真实代码为准；范式与规则冲突时，先按规则走，再把冲突报给维护者。
- **开源参考实现不含客户业务**：`reference/` 用通用任务域演示结构，团队自己的范式另建目录。
