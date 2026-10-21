# AgentGuard

[![CI](https://github.com/rain610/agentguard/actions/workflows/ci.yml/badge.svg)](https://github.com/rain610/agentguard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](https://github.com/rain610/agentguard)

> 零依赖、可扩展的代码门禁引擎 —— 把"规范"从"靠人记"变成"靠机器判"。

AgentGuard 是一个**通用代码门禁框架**，用于在编辑期和提交期自动检查代码是否符合团队约定。

它不是 lint 工具（ESLint / SonarQube 那类代码分析器），而是**策略强制平面**（policy enforcement plane）—— 决定"这次提交过不过"。对标物是 Claude Code 的 hooks / permissions、Codex 的 execpolicy。

它也不替代 `AGENTS.md`：`AGENTS.md` 管架构意图、业务语义（需要人/模型判断），AgentGuard 管**能被机械判定**的事。两者是分工。

## 核心价值

| 痛点 | AgentGuard 怎么解 |
|---|---|
| 规范写在文档里，AI / 开发者记不住 | 常驻只注入不到 1K 的指针，细节按需加载 |
| 约定只对"愿意遵守的人"有效 | 编辑期 + 提交期双重拦截，忘了也会被拦 |
| 一上规则就被存量旧账淹没 | 双态 severity：新代码 `error` 拦截，存量 `info` 观察 |
| "门禁跑没跑"说不清 | 每次判定写台账，可查看运行摘要与规则覆盖度 |
| 引入工具链的依赖成本 | **零第三方依赖**，只用 Python 标准库 |

引擎层**不依赖任何个人** skill / 工具：同事 clone 或 `pip install` 完即可运行。

## 前置条件

| 依赖 | 最低版本 | 用途 |
|---|---|---|
| Python | **3.11+** | 引擎运行时 |
| Git | 2.x | diff / worktree 识别 |
| Node.js（可选） | — | 仅当自定义规则调用 `node` 时需要；示例规则不依赖 |

## 快速开始

### 0. pip 安装（wheel 自带引擎，不需要 clone）

```bash
pip install agentguard      # 从源码装：pip install .
agentguard init             # 把随包引擎落到 ~/.agentguard/engine，并写 ~/.agentguard/harness.path
agentguard quickstart       # 自检 + 打印 MCP 接入配置
```

`init` 只复制、不删除：目标已存在且内容不同时要求 `--force`，旧目录会改名归档为
`<dir>.disabled-<时间戳>`；可用 `--dir` 指定引擎安装位置。装完后：
`agentguard list` 看 gate，`agentguard run <gate> <文件>` 定向检查，
`agentguard mcp` 启动 MCP 服务。源码树里也可以直接跑 `python agentguard/cli.py init`。

### 1. clone 源码看效果（不需要配置）

```bash
git clone https://github.com/rain610/agentguard.git
cd agentguard

sh sandbox/demo_gate.sh    # 在临时目录造一个 console.log → 拦住 → 改对 → 放行
sh sandbox/doctor.sh       # 端到端自检：门禁探针 / hook 活性 / skill 软链
```

`demo_gate.sh` 只写系统临时目录，不碰任何业务仓，也不需要先改配置。它自带两向断言：
该拦的必须 `rc=1` 且点名 `NO-CONSOLE`，改对必须 `rc=0`，否则脚本自己以非零退出 ——
所以「跑完没报错」是真的验过，而不是演示脚本演了一遍。

### 2. 接入你自己的仓

先让本机有一份本地配置（它被 git 忽略，只影响你这台机器）：

```bash
sh sandbox/setup_machine.sh
```

首次运行会从 `config/harness.local.example.toml` 生成 `config/harness.local.toml`，
提示你填路径后退出。把内置 `example` gate 指向你的仓 —— **键名 `example_repo` 已在共享
配置里声明，本机只填值**：

```toml
# config/harness.local.toml
[repos]
example_repo = "/path/to/your/project"
```

然后：

```bash
# 全量扫一遍
python harness/run_gate.py example --all

# 以后在业务仓里只查本次改动（自动识别 gate）
cd /path/to/your/project
python /path/to/agentguard/harness/agents/auto-gate.py

# 配置体检：必须 rc=0
python /path/to/agentguard/harness/run_gate.py --validate
```

> 不要在本机配置里新增或改写 `[repos]` 的键名。键名是共享 `config/harness.toml` 里的
> 团队策略，本机只负责填路径；键名不存在时 `--validate` 会 rc=2 —— 这正是「拼错 /
> 忘了登记」唯一会被发现的时刻。要管多个仓，见「添加新 gate」。

### 3. 定制规则

示例 gate 的规则都在 `checks/example_ruleset.py` 的 `EXAMPLE_RULES` 列表里，
改这个列表即可，不需要新建脚本：

```python
NO_TODO = RegexRule(
    id="NO-TODO",
    pattern=r"\bTODO\b",
    message="禁止 TODO 注释残留",
    fix_hint="实现或删除 TODO",
)
```

把它加进 `EXAMPLE_RULES`，然后回到 agentguard 工程根：

```bash
cd /path/to/agentguard
python harness/run_gate.py example --all
python harness/run_gate.py --validate   # 规则 ID / 配置键都会被体检
```

### 4. 安装 hooks（可选，推荐）

```bash
# 提交期 hook（在业务仓根执行；gate 名可省略，按 cwd 自动识别）
cd /path/to/your/project
sh /path/to/agentguard/sandbox/install_hooks.sh

# 编辑期 hook（给 AI 客户端转发 post-edit 事件）
python /path/to/agentguard/sandbox/install_shim.py
```

## 示例规则

项目自带 3 条示例规则（`checks/example_ruleset.py`），展示如何编写自定义规则：

| 规则 ID | 检查内容 | 严重级别 |
|---|---|---|
| `NO-CONSOLE` | 代码里的 `console.log` 残留 | block（字符串字面量里降为 warn） |
| `NO-HARDIP` | 硬编码 IP 地址（每段 ≤255，排除版本号） | block |
| `EOL-UNIFIED` | 行尾混用 CRLF / LF | block |

运行示例 gate：

```bash
python harness/run_gate.py example --repo <你的仓> --all
```

这些规则是**启发式**，不是生产级解析器。能力边界写在规则集头部，也由自检输出：

- 注释里的内容不算命中（`//`、`/* */`、`<!-- -->`、Python `#`）；
- 字符串里的 `console.log` 只提示、不阻断 —— 它可能是日志文案或文档示例；
- `1.2.3.4` 这类版本号不判 IP（看 `version=` / `v=` 等上下文），非法 IPv4 也不判；
- 多行块注释的**续行**、跨行字符串不做状态跟踪（单行启发式）。

需要更强的判定时，用 `checks/rule_base.py` 的 `Rule` 子类自己写 `applicable()` / `check()`。

## 黄金范式：拦截之后的下一步

门禁只回答「这行不能这么写」。规则还可以带一条 `see` 指针，把「那该怎么写」送到被拦的那一刻：

```text
  ❌ src/logger.js:3 [NO-CONSOLE] 禁止 console.log 残留（调试代码不应进入提交）
     FIX: 删除 console.log 行，改用统一日志封装（见范式）
     📖 See: golden-patterns/patterns/cross-cutting/logging.md
```

但黄金范式不只是「一条 lint 的修法」。它是一个按任务组织的**代码骨架库**：

| 要写什么 | 读哪篇 |
|---|---|
| 请求层（token / 响应壳 / 401 / 错误提示） | [`patterns/web/request-layer.md`](golden-patterns/patterns/web/request-layer.md) |
| 列表页（后端分页 / 筛选 / 批量 / 行操作） | [`patterns/web/list-page.md`](golden-patterns/patterns/web/list-page.md) |
| 表单弹窗（新增编辑共用 / 校验 / 提交锁） | [`patterns/web/form-dialog.md`](golden-patterns/patterns/web/form-dialog.md) |
| 后端分层（Controller / Repository / 软删 / 分页） | [`patterns/backend/controller-repository.md`](golden-patterns/patterns/backend/controller-repository.md) |
| 校验与错误（400/404/500、不吞异常） | [`patterns/backend/validation-and-errors.md`](golden-patterns/patterns/backend/validation-and-errors.md) |
| 日志 / 配置 | [`logging.md`](golden-patterns/patterns/cross-cutting/logging.md)、[`config-and-env.md`](golden-patterns/patterns/cross-cutting/config-and-env.md) |
| 从零做一个业务模块 | [`endtoend/task-management.md`](golden-patterns/endtoend/task-management.md) |

- 入口：[`golden-patterns/index.md`](golden-patterns/index.md)
- 参考实现：[`golden-patterns/reference/`](golden-patterns/reference/)（正面样板会跑示例 gate 自证）
- 造自己的范式：[`golden-patterns/templates/00_范式制造模板.md`](golden-patterns/templates/00_范式制造模板.md)

给规则挂范式只需两步：把**已验收**的写法放进 `golden-patterns/`，在规则类上写
`see = "golden-patterns/…"`。路径一律仓库相对；规则与范式一起评审 —— 范式是参照系，
不是绕过规则的许可。

## 架构

```text
客户端提示层        AGENTS.md / skill
       |
工具调用层          CLI / MCP / plugin
       |
强制拦截层          编辑期 hook + git pre-commit
       |
判定引擎            run_gate.py -> checks/*_check.py -> *_ruleset.py
       |
台账与体检          reports/gate_log-<YYYY-MM>.jsonl / coverage / doctor
```

### 关键目录

| 目录 | 职责 |
|---|---|
| `checks/` | 规则实现与薄 loader |
| `harness/` | 调度器、配置读取、hook 入口 |
| `config/` | gate 与仓库策略 |
| `integrations/` | 各 AI 客户端接入说明 |
| `sandbox/` | 安装、自检、打包工具 |
| `agentguard/` | CLI / MCP 分发壳 |
| `tests/` | 单元测试 |

## 规则模型

引擎侧统一基类是 `checks/rule_base.py` 的 `Rule`：

```python
class Rule:
    id: str
    name: str = ""
    severity_default: Severity = Severity.ERROR

    def applicable(self, ctx) -> bool:    # 该规则是否适用于此文件
        ...

    def check(self, ctx) -> list[Finding]:
        """返回违规列表"""
        ...

    def severity(self, ctx) -> Severity:  # 双态钩子，默认返回 severity_default
        ...
```

`checks/example_ruleset.py` 的 `RegexRule` 是同一协议下的正则适配器：

```python
RegexRule(id, pattern, message, fix_hint="",
          see="", string_severity=None, version_exempt=False, validator=None)
```

它和自建规则一样走 `Rule.run(ctx)`：`check(ctx)` 返回 `Finding`，严重级用
`Severity.ERROR / WARN / INFO`。单条命中需要不同级别时（例如字符串里的
`console.log` 只提示）直接给 `Finding.severity` 赋值；其余由规则级
`severity_default` 与存量降级钩子决定。

需要按文件类型收窄、或按上下文判定严重级别时，直接继承 `rule_base.Rule` 并覆写
`applicable()` / `check()`。`rule_base` 还提供提交期读暂存区（`read_bytes`）、
注释区间识别（`comment_spans`）、行内豁免（`inline_waiver`）等公共件。

### 规则类型

| 类型 | 适用场景 | 示例 |
|---|---|---|
| 单行正则 | 反模式检测 | `console.log`、硬编码 IP |
| 整文件规则 | 文件级约束 | 行尾混用 |
| `rule_base.Rule` 子类 | 需要文件类型 / 上下文 / 多态 severity | 自定义 `applicable()` + `severity()` |

## 配置

主配置：`config/harness.toml`（Python 3.11+ 标准库 `tomllib` 读取，零第三方依赖）

```toml
[repos]
example_repo = ""      # 键名留在共享配置；本机路径在 harness.local.toml 填

[gates.example]
enabled = true
repo = "example_repo"
script = "checks/example_code_check.py"
mode = "strict"           # strict | loose
run_on = ["pre_commit", "post_edit"]
line_ending = "auto"      # auto | CRLF | LF
encoding = "utf-8"        # 后端常用 utf-8-bom
legacy_baseline = false   # 存量降级为 info
disabled_rules = []       # 本 gate 关闭的规则 ID
```

`gates.<名>.repo` 引用的键**必须先出现在 `[repos]` 段**（`harness_self` 例外：它由引擎注入）；
`config.script` 指向的脚本必须存在。`--validate` 会同时检查这两件事，以及
`run_on` / `mode` / `line_ending` 等字段的值域。

### 本地覆盖层

| 优先级 | 来源 | 用途 |
|---|---|---|
| 1 | 环境变量 `HARNESS_CONFIG` | CI 或特殊布局 |
| 2 | `config/harness.local.toml` | 本机仓库路径 |
| 3 | `config/harness.toml` | 共享基线 |

旧版 `config/harness.local.yaml` / `config/harness.yaml` 仍可被读入（兼容层），
但两种格式同时存在时 **TOML 优先**并会在 stderr 提醒；新配置请一律用 TOML。

## 命令速查

| 场景 | 命令 |
|---|---|
| 列出 gate | `python harness/run_gate.py --list` |
| 自动检查改动 | `python harness/agents/auto-gate.py` |
| 定向检查 | `python harness/run_gate.py <gate> <文件...>` |
| 全量扫描 | `python harness/run_gate.py <gate> --all` |
| 配置体检 | `python harness/run_gate.py --validate` |
| 运行摘要 | `python harness/run_gate.py --summary` |
| 覆盖度报告 | `python harness/run_gate.py --coverage` |
| 安装提交 hook | `sh sandbox/install_hooks.sh <gate>` |
| 安装编辑期 hook | `python sandbox/install_shim.py` |
| 接入自检 | `sh sandbox/doctor.sh` |
| 快速测试 | `python sandbox/run_tests.py --fast` |
| 全量测试 | `python sandbox/run_tests.py --full` |
| 紧急旁路（留痕） | `python agentguard/cli.py bypass --repo <端仓> --reason "≥10 字的原因"` |

> `bypass` 只是**声明留痕入口，不是权限闸**：它不拦任何提交，也不改变门禁判定。

## 退出码

| 退出码 | 含义 | 处理 |
|---|---|---|
| `0` | 通过 | 继续 |
| `1` | 门禁拦截 | 按输出修复 |
| `2` | 配置 / 调用错误（含点名的目标文件不存在、不可读） | 检查配置与传入的文件 |
| `3` | 非受管仓库 | 跳过 |

## 审计证据强度

`sandbox/audit_reconcile.py` 拿台账对照提交历史，匹配用的是**时间窗**而不是提交内容指纹。
因此它的产物是**线索级**证据：只能说明"提交前后本机跑过同一 gate 且通过"，不能证明
"这次提交的内容被检查过"。

- 可用于人工复核与追责线索；
- **不要写成**"提交可自证"，也不要对外宣称已满足强制审计。

## 扩展

### 添加新规则

1. 在 `checks/example_ruleset.py` 的 `EXAMPLE_RULES` 里加一条 `RegexRule(...)`；
   自建规则集则在检查脚本里改 import
2. `python harness/run_gate.py example --all` 试跑
3. `python harness/run_gate.py --validate` —— 规则 ID、配置键都会体检

### 添加新 gate（多仓 / 独立规则集）

三处都要动，少一处的症状是「看起来配好了」：

1. **共享** `config/harness.toml` 的 `[repos]` 段加键名（值 `""`）—— 键名是团队策略，
   与机器无关
2. **共享** `config/harness.toml` 的 `[gates.<名>]` 表加 gate，`repo =` 指向该键，`script =`
   指向检查脚本。新脚本可从 `checks/example_code_check.py` 复制，把
   `from example_ruleset import ...` 换成自己的 ruleset
3. **本机** `config/harness.local.toml` 的 `[repos]` 里填该键的路径（只有这一步是本机的）

建议在 `checks/registry.py` 登记 `gate -> script`；未登记会走 `config.script` 兜底，
`--validate` 会给出提示，不算失败。最后跑：

```bash
python harness/run_gate.py --validate      # 必须 rc=0
python sandbox/run_tests.py --fast
```

### 行内豁免

合规写法被规则误报时，在命中行或紧邻上一行的**注释**里写标记：

```js
// harness-ok: NO-CONSOLE 调试日志已用条件封装
if (DEBUG) console.log("debug info")
```

- 行内豁免：`harness-ok: <规则ID> <理由>`（适用端：示例 gate）
- 字符串字面量里的标记不算；规则 ID 必须写对，理由必填。
- 规则集通过 `rule_base.inline_waiver()` 接入同一判据。

## 与 AGENTS.md 的关系

| 层 | 负责什么 |
|---|---|
| `AGENTS.md` | 架构意图、业务语义、红线与人工判断 |
| `AgentGuard` | 可机械判定的规则、自动触发、台账与验收 |

只写 `AGENTS.md` 成本最低，但没有强制力；加 AgentGuard 后才能在编辑期和提交期获得机械拦截与留痕。

## 支持的 AI 客户端

| 客户端 | 接入方式 |
|---|---|
| Claude Code | AGENTS.md + hooks |
| Codex | AGENTS.md + hooks |
| OpenCode | AGENTS.md |
| CodeBuddy | AGENTS.md |
| Trae CN | AGENTS.md |
| Qoder CN | AGENTS.md |
| ZCode | AGENTS.md |
| 其他 MCP 宿主 | MCP 服务 |

## 测试

```bash
# 快速测试（编辑回路；跳过真打包/真安装/真 git 链路，本机约 1 分钟）
python sandbox/run_tests.py --fast

# 全量测试（提交前/CI；含真打包/真安装/真 git 链路）
python sandbox/run_tests.py --full

# 单元测试
python -m unittest discover -s tests
```

发布链（`.github/workflows/release.yml`）与每日定时（`.github/workflows/nightly.yml`）
还会跑三类慢闸门：变异冒烟（105 条变异，验证锁真的会红）、真打包后的真产物锁、
干净 venv 安装并执行 `agentguard --version`（轮子只含壳，需用 `AGENTGUARD_DIR`
指向引擎检出）。本机复现：先 `python sandbox/pack_release.py`，再跑
`python tests/test_release_artifact.py TestRealArtifactPasses -v`；venv 安装命令见
`.github/workflows/release.yml` 的「干净 venv 安装冒烟」步骤。

## 文档

| 文档 | 内容 |
|---|---|
| `docs/packaging/INSTALL.md` | 安装、升级、卸载 |
| `docs/packaging/RUNBOOK.md` | 日常排障、回滚、对账 |
| `docs/packaging/COMPAT.md` | 客户端兼容矩阵 |
| `docs/adr/` | 架构决策记录 |
| `AGENTS.md` | agent 工程地图 |

## 许可证

MIT License - 详见 [LICENSE](LICENSE)

## 贡献

欢迎提交 Issue 和 Pull Request。
