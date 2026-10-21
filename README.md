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
| 规范写在文档里，AI / 开发者记不住 | 常驻只留 ~1K 的「地图」，细节按需加载 |
| 约定只对"愿意遵守的人"有效 | 编辑期 + 提交期双重拦截，忘了也会被拦 |
| 一上规则就被存量旧账淹没 | 双态 severity：新代码 `error` 拦截，存量 `info` 观察 |
| "门禁跑没跑"说不清 | 每次判定写台账，可查看运行摘要与规则覆盖度 |
| 引入工具链的依赖成本 | **零第三方依赖**，只用 Python 标准库 |

引擎层**不依赖任何个人** skill / 工具：同事 clone 下来即可运行。

## 前置条件

| 依赖 | 最低版本 | 用途 |
|---|---|---|
| Python | **3.11+** | 引擎运行时 |
| Git | 2.x | diff / worktree 识别 |
| Node.js（可选） | 22+ | 语法检查规则（`node --check`） |

## 快速开始

### 1. 安装

```bash
git clone https://github.com/rain610/agentguard.git
cd agentguard
```

### 2. 配置

编辑 `config/harness.local.yaml`，声明你的仓库路径：

```yaml
repos:
  my_project: /path/to/your/project
```

### 3. 定义规则

在 `checks/` 目录创建规则集，或直接参考自带的 3 条示例规则（`checks/example_ruleset.py`）：

```python
# checks/my_ruleset.py
from example_ruleset import Rule

NO_TODO = Rule(
    rule_id="NO-TODO",
    severity="block",
    pattern=r'\bTODO\b',
    message="禁止 TODO 注释残留",
    fix_hint="实现或删除 TODO",
)
```

### 4. 注册 gate

在 `config/harness.yaml` 添加 gate：

```yaml
gates:
  my_project:
    enabled: true
    repo: my_project
    script: checks/my_code_check.py
    run_on: [pre_commit, post_edit]
```

### 5. 运行

```bash
# 定向检查（仅本次改动）
python harness/agents/auto-gate.py

# 全量扫描
python harness/run_gate.py my_project --all

# 配置体检
python harness/run_gate.py --validate
```

### 6. 安装 hooks（可选）

```bash
# 提交期 hook
sh sandbox/install_hooks.sh my_project

# 编辑期 hook
python sandbox/install_shim.py
```

## 示例规则

项目自带 3 条示例规则（`checks/example_ruleset.py`），展示如何编写自定义规则：

| 规则 ID | 检查内容 | 严重级别 |
|---|---|---|
| `NO-CONSOLE` | 禁止 `console.log` 残留 | block |
| `NO-HARDIP` | 禁止硬编码 IP 地址 | block |
| `EOL-UNIFIED` | 行尾不统一（CRLF / LF 混用） | block |

运行示例规则：

```bash
python checks/example_code_check.py --repo . --all
```

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
台账与体检          gate_log.jsonl / coverage / doctor
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

`checks/example_ruleset.py` 是更轻的实例式写法（`Rule(rule_id, severity, pattern, message, fix_hint)`），
适合单行正则型规则；需要按文件类型收窄、或多态判定严重级别时用 `rule_base.Rule` 子类。

### 规则类型

| 类型 | 适用场景 | 示例 |
|---|---|---|
| 单行正则 | 反模式检测 | `console.log`、硬编码 IP |
| 整文件规则 | 文件级约束 | 编码、行尾、文件命名 |
| 结构化规则 | 复杂逻辑 | 组件收敛、分层依赖 |

## 配置

主配置：`config/harness.yaml`

```yaml
repos:
  my_project: ""  # 本机路径在 harness.local.yaml 填

gates:
  my_project:
    enabled: true
    repo: my_project
    script: checks/my_code_check.py
    mode: strict           # strict | loose
    run_on: [pre_commit, post_edit]
    line_ending: auto      # auto | CRLF | LF
    encoding: utf-8        # 后端常用 utf-8-bom
    legacy_baseline: true  # 存量降级为 info
    disabled_rules: []     # 本 gate 关闭的规则 ID
```

### 本地覆盖层

| 优先级 | 来源 | 用途 |
|---|---|---|
| 1 | 环境变量 `AGENTGUARD_CONFIG` | CI 或特殊布局 |
| 2 | `config/harness.local.yaml` | 本机仓库路径 |
| 3 | `config/harness.yaml` | 共享基线 |

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
| `2` | 配置错误 | 检查配置 |
| `3` | 非受管仓库 | 跳过 |

## 审计证据强度

`sandbox/audit_reconcile.py` 拿台账对照提交历史，匹配用的是**时间窗**而不是提交内容指纹。
因此它的产物是**线索级**证据：只能说明"提交前后本机跑过同一 gate 且通过"，不能证明
"这次提交的内容被检查过"。

- 可用于人工复核与追责线索；
- **不要写成**"提交可自证"，也不要对外宣称已满足强制审计。

## 扩展

### 添加新规则

1. 在 `checks/` 创建或修改 ruleset
2. 在对应的 `*_code_check.py` 注册规则
3. 运行测试验证：`python sandbox/run_tests.py --fast`

### 添加新 gate

1. 在 `config/harness.yaml` 添加 gate 配置
2. 在 `checks/registry.py` 登记 `gate -> script`
3. 运行配置体检：`python harness/run_gate.py --validate`

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
# 快速测试（编辑回路；跳过真打包/真安装/真 git 链路，本机约 50s）
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
