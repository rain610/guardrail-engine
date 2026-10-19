# AgentGuard 后端（.NET）—— AI 编码规则地图

> **这份文件不是某个仓的 AGENTS.md**，而是**端级知识**：所有后端客户仓共用的那一份。
> 客户仓的 `AGENTS.md` **用指针指向本文件**，不要把它复制进仓里（复制 = 漂移源）。
> 核心原则：**先循框架，后循先例，再谈自主；门禁是硬边界，不是建议。**
> 客户独有知识不在本文件，在你那个仓的 `AGENTS.md`。

## 改码前：加载知识（按需，不要全读）

写 Controller/Repository/Entity 前必须按任务类型读对应范式（golden-patterns，位置见下）：

| 任务 | 必读 |
|---|---|
| 任何 Controller / Repository | `golden-patterns/backend/backend_api_authority.md`（框架总纲） |
| 查基类有没有现成方法/扩展点 | `golden-patterns/backend/base_class_reference.md`（速查表） |
| 新建模块目录 / 返回包装 / 异常 / 日志 / 常量 | `golden-patterns/backend/architecture_rules.md` |
| 典型写法样板 / 例外修复流程 / 方案确认模板 | `golden-patterns/backend/coding_patterns.md` |

范式位置（独立门禁工程，所有客户通用）：
```
<HARNESS>/golden-patterns/backend/
```

## 端级规则

**能机械判定的一律不在这里写正文** —— 正文只有一份，在 `<HARNESS>/checks/backend_ruleset.py`。
这里只写规则本身 + **规则 ID 指针**；被拦时门禁会把 ID 直接打给你。

| # | 规则 | 谁在管 |
|---|---|---|
| 1 | **Controller → Repository 两层**，Controller 只做校验+调用，禁写业务 | 门禁 `R2`（Business 反向依赖 Controller，error）+ `R1`（Controller 直接 `new Repository`，**只观察不拦**） |
| 2 | **基类已有的方法直接继承**（GetApiPageData/GetDetail/Add/Edit/Delete/Submit/Audit...），差异走扩展点（BeforeSave/OnAudit...），禁止重写主流程、禁止另起 `GetData` 等重复名 | 门禁 `R5`（反模式方法名）+ `R6`（分页 `GetPageData` 混用） |
| 3 | **状态值禁魔法数字**，用 `SysConst.*` | 门禁 `R9`（`Status/State` 与 `0/5/10/-5/-10` 直接比较） |
| 4 | **`DataEntity` 统一返回**，业务异常 `WarningException`，日志 `LogHelper` | ⚠ **软约束，无门禁** |
| 5 | 两套平行结构（RMC 主套 / RMCEx 扩展套），同模块三层目录对齐 | ⚠ **软约束，无门禁** |

> ⚠ **第 4、5 条没有门禁兜底** —— 只能靠你读这份文件。
> **双态说明（实测于 `backend_ruleset.py`）**：`R5`/`R6`/`R9`/`R10`/`R11` 对**存量违规只 info 观察**，
> 对**本次新增/改动行是 error 拦截**；`R2`/`R7` **永远是 error**；
> **`R1` 永远是 info（只观察，从不拦）**；**`R3` 自 2026-10-08 起关闭**（见下）。
> 所以「全量扫描没报错」**不等于**「你的新代码没问题」。

## 门禁规则 ID 速查（本端全集）

被拦时对着这张表认 ID，别猜：

| 规则 ID | 拦什么 | 级别 |
|---|---|---|
| `R1` | Controller 直接 `new Repository` | **info（只观察，从不拦）** |
| `R2` | Business 层反向依赖 Controller | error |
| `R3` | `Entities` 必须纯数据层 | **已关闭（2026-10-08，见下）—— 本端不再拦** |
| `R5` | 反模式方法名 `GetData` / `Deletes` / `InitData` | 存量 info／新增 error |
| `R6` | 分页 `GetPageData` 混用 | 存量 info／新增 error |
| `R7` | 非 `.cs` 代码文件进入后端仓（`wwwroot/` 第三方资源除外） | error |
| `R9` | 状态魔法数字（`Status`/`State` 与 `0/5/10/-5/-10` 直接比较） | 存量 info／新增 error |
| `R10` | 实体时间属性必须显式声明 `[Column(TypeName = "datetime")]` | 存量 info／新增 error |
| `R11` | 实体里不得使用 `[Length(n)]`（长度由数据库/代码控制） | 存量 info／新增 error |

⚠ **`R10` / `R11` 在 md 里从来没有对应正文** —— 它们是纯门禁规则，只有 `checks/` 那一份。
这是理想状态：规则写在会被执行的地方，而不是写在需要人手同步的地方。

> ⚠ **`R3` 已关闭（2026-10-08）**：规则本体仍在 `checks/backend_ruleset.py`（id 仍是 `R3`），
> 只是 12 个后端 gate 的配置都写了 `disabled_rules: [R3]`（见 `config/harness.yaml`）。
> 原因：R3 唯一的真实命中是 12 仓 × 5 处 `Eagle.Repository.StaticManager` 运行时取值
> （实体属性 getter / 扩展方法里取参数），2026-10-04 与 10-05 已拍板接受为**分层例外**。
> 客户仓里原先的 `// harness-ok: R3 …` 标记已全部撤掉、代码复原，改由配置开关兜住 ——
> 不再往业务代码里写豁免标记。
> **但这不等于实体层可以随意引 `Business` / `Repository`**：分层本身仍是规范
> （见 `golden-patterns/backend/`），只是**不再由门禁机械拦截**，评审时要人看。

## 被拦了怎么办

按输出里的「❌ 什么错 / ✅ 怎么改」修复到通过，**不要绕过**。
紧急 `--no-verify` 须先留痕并向负责人说明。

> 跑门禁的命令与退出码语义**不在这里重复** —— 那是**用户级全局守则**（`~/.agentguard/GLOBAL_AGENTS.md`，
> 各工具自动加载）的内容。若你的 agent 读不到它，跑 `sh <HARNESS>/sandbox/bootstrap_agent_env.sh`。

## 例外修复（重大性能/安全缺陷才允许）

修复前说明四要素（位置/类型/偏离条款/理由），注释标注 `// 例外修复：...`；
改变对外行为须先经用户确认（模板见 `coding_patterns.md`）。
