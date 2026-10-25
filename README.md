# Guardrail Engine

[![CI](https://github.com/rain610/guardrail-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/rain610/guardrail-engine/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](https://github.com/rain610/guardrail-engine)

> 零依赖、可扩展的代码门禁引擎 —— 把"规范"从"靠人记"变成"靠机器判"。

[English](README.en.md) | 简体中文

> 本项目原名 **AgentGuard**，v0.2 起更名为 **Guardrail Engine**。旧命令 `agentguard`、
> 旧导入包与旧配置目录在迁移期内继续可用（见 [CHANGELOG](CHANGELOG.md)）。

Guardrail Engine 是一个**通用代码门禁框架**，用于在编辑期和提交期自动检查代码是否符合团队约定。

它不是 lint 工具（ESLint / SonarQube 那类代码分析器），而是**策略强制平面**（policy enforcement plane）—— 决定"这次提交过不过"。对标物是 Claude Code 的 hooks / permissions、Codex 的 execpolicy。

它也不替代 `AGENTS.md`：`AGENTS.md` 管架构意图、业务语义（需要人/模型判断），Guardrail Engine 管**能被机械判定**的事。两者是分工。

## 核心价值

| 痛点 | Guardrail Engine 怎么解 |
|---|---|
| 规范写在文档里，AI / 开发者记不住 | 常驻只注入约 2.4KB 的全局守则（`~/.guardrail-engine/GLOBAL_AGENTS.md`，不是仓库根的 `AGENTS.md`），细节按需加载 |
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
| Bash（Windows 用 Git Bash） | — | 运行 `sandbox/*.sh`（demo / doctor / setup）；PowerShell 不自带 `sh`（demo 另有 Python 版） |
| Node.js（可选） | — | 仅当自定义规则调用 `node` 时需要；示例规则不依赖 |

## 快速开始

### 0. pip 安装（wheel 自带引擎，不需要另 clone 引擎）

> **首个 PyPI 版本发布前**：短命令 `pip install guardrail-engine` 仍会 404。发布由 `v*` tag
> 触发的 `release.yml` 完成（OIDC 可信发布）；在那之前请在源码树根目录 `pip install .`
> （或装本地构建的 wheel）。

```bash
git clone https://github.com/rain610/guardrail-engine.git
cd guardrail-engine
pip install .               # 从源码装：wheel 自带引擎
guardrail-engine init             # 把随包引擎落到 ~/.guardrail-engine/engine，并写 ~/.guardrail-engine/harness.path
guardrail-engine quickstart       # 自检 + 打印 MCP 接入配置
```

`init` 只复制、不删除：目标已存在且内容不同时要求 `--force`，旧目录会改名归档为
`<dir>.disabled-<时间戳>`；可用 `--dir` 指定引擎安装位置。装完后：
`guardrail-engine list` 看 gate，`guardrail-engine run <gate> <文件>` 定向检查，
`guardrail-engine mcp` 启动 MCP 服务。源码树里也可以直接跑 `python guardrail_engine/cli.py init`。

### 1. clone 源码看效果（不需要配置）

```bash
git clone https://github.com/rain610/guardrail-engine.git
cd guardrail-engine

sh sandbox/demo_gate.sh    # 在临时目录造一个 console.log → 拦住 → 改对 → 放行
python sandbox/demo_gate.py  # 同上，且不需要 sh（PowerShell / cmd 直接可用）
sh sandbox/doctor.sh       # 端到端自检：门禁探针 / hook 活性 / skill 软链
```

> **Windows 需要 Git Bash**：PowerShell / cmd 不自带 `sh`（会报 `无法将"sh"项识别为...`）。
> `demo` 可直接跑 `python sandbox\demo_gate.py`；`doctor` / `setup` 等 `*.sh` 入口
> 请在 Git Bash 里执行。

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
python /path/to/guardrail-engine/harness/agents/auto-gate.py

# 配置体检：必须 rc=0
python /path/to/guardrail-engine/harness/run_gate.py --validate
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

把它加进 `EXAMPLE_RULES`，然后回到 guardrail-engine 工程根：

```bash
cd /path/to/guardrail-engine
python harness/run_gate.py example --all
python harness/run_gate.py --validate   # 规则 ID / 配置键都会被体检
```

### 4. 安装 hooks（可选，推荐）

```bash
# 提交期 hook（在业务仓根执行；gate 名可省略，按 cwd 自动识别）
cd /path/to/your/project
sh /path/to/guardrail-engine/sandbox/install_hooks.sh

# 编辑期 hook（给 AI 客户端转发 post-edit 事件）
python /path/to/guardrail-engine/sandbox/install_shim.py
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
| `guardrail_engine/` | CLI / MCP 主包（`agentguard/` 是迁移期兼容壳，v0.3 移除） |
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

## 接外部分析器（ESLint / Semgrep）

内置规则是**示例与兜底**，不是主力分析器。引擎层受「零第三方依赖」约束，标准库里只有
Python 的 `ast`；Vue / JS / TS / C# 的语法与类型判断交给成熟工具，Guardrail Engine 只负责
**策略强制**那一层：什么时候跑、跑不过怎么退出、结果怎么进台账、怎么豁免。

两个适配 gate 已注册，默认 `enabled = false`（装了工具、确认配置后再开）：

```toml
[gates.eslint]
enabled = true                 # 默认 false；工具没装时开启 = 每次触发 rc=2
repo = "example_repo"          # 指向 [repos] 里的键（本机路径在 harness.local.toml 填）
script = "checks/eslint_check.py"
run_on = ["post_edit"]         # 默认不挂 pre_commit（原因见下方「已知边界」）

[gates.semgrep]
enabled = false
repo = "example_repo"
script = "checks/semgrep_check.py"
run_on = ["post_edit"]
```

```bash
python harness/run_gate.py eslint --all          # 全量：把「.」交给工具按自身配置遍历
python harness/run_gate.py eslint src/app.js     # 定向：只查点名文件
python harness/run_gate.py semgrep --all
```

工具命令默认取 PATH 里的 `eslint` / `semgrep`，可用环境变量覆盖（普通命令或 JSON 数组）：

```bash
export GUARDRAIL_ESLINT='["npx","eslint"]'
export GUARDRAIL_SEMGREP='["semgrep","scan"]'
export GUARDRAIL_ADAPTER_TIMEOUT=120          # 秒，默认 300
```

外部工具的规则 ID（如 `no-console`、`python.lang.security.audit.eval`）直接可用于
`harness-ok:` 行内豁免。输出格式、豁免语法、退出码语义与内置规则一致；严重级由工具的
severity 映射而来（ESLint 1/2；Semgrep 按 CRITICAL/ERROR/HIGH → 阻断，MEDIUM/WARNING →
警告，LOW/INFO → 观察，未知级别按阻断处理）。

**fail-closed 语义**（这三条是本仓付过学费的地方，锁在 `tests/test_external_adapters.py`）：

| 情况 | 退出码 | 为什么不能算通过 |
|---|---|---|
| 工具没装 / 起不来 / 超时 | `2` | 一条检查都没执行 |
| 输出不是 JSON / 工具以配置错退出（rc≥2） | `2` | 结论不可信或参数错了 |
| Semgrep 的 `errors` 非空 | `2` | 有文件没扫完，结果不完整 |

`mode` / `legacy_baseline` / `disabled_rules` 对适配 gate **不生效**（`--mode` 旗标会被
接收并忽略，台账也不记 `mode_override` —— 适配 gate 没有 strict/loose 可放宽），写上会被
`--validate` 拒绝：严重级和规则取舍归工具自己的配置文件管
（`.eslintrc` / `semgrep.yml`），两边各留一个开关只会互相打架。

**已知边界**

- 外部工具读的是**工作区**文件，提交期门禁不会把暂存区内容喂给它们。因此适配 gate
  **默认只挂 `post_edit`**：挂到 `pre_commit` 后，「暂存坏版本 + 工作区改回好版本」可以
  绕过它。要用提交期拦截，先在调用方实现暂存副本，或接受工作区语义。
- 外部 gate **不参与静态规则覆盖度**（`--coverage` 只统计本仓内置规则）：规则清单归
  工具自己的配置管，开启后看到「清单外规则级数据」是正常的。

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
| 60 秒演示（无需 sh） | `python sandbox/demo_gate.py` |
| 运行摘要 | `python harness/run_gate.py --summary` |
| 覆盖度报告 | `python harness/run_gate.py --coverage` |
| 台账核链 + 引擎归因 | `python guardrail_engine/cli.py log --verify --engine` |
| 安装提交 hook | `sh sandbox/install_hooks.sh <gate>` |
| 安装编辑期 hook | `python sandbox/install_shim.py` |
| 接入自检 | `sh sandbox/doctor.sh` |
| 快速测试 | `python sandbox/run_tests.py --fast` |
| 全量测试 | `python sandbox/run_tests.py --full` |
| 紧急旁路（留痕） | `python guardrail_engine/cli.py bypass --repo <端仓> --reason "≥10 字的原因"` |

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

台账里另有一层**引擎内容**指纹（见下一节 `Engine-Sha`）：它能说明"这条判定用的是哪份
门禁代码"，但那是台账自身的归因，不是"这次提交的内容被检查过"的证明。

## 保护边界（防 AI 改门禁）

`[harness]` 提交标记只是**变更声明**，不是权限边界：任何人在本地都能写这句。
真正的强制层是仓库侧配置：

- `.github/CODEOWNERS` 把 `checks/`、`harness/`、`config/`、`sandbox/mutation_smoke.py`、
  `tests/test_lock_inventory.py`、`tests/lock-inventory.json` 与 `.github/` 指给维护者；
- 在 GitHub 的 **Settings → Branches → Branch protection rules** 里给 `main` 开启
  **Require a pull request before merging** 与 **Require review from Code Owners**。
  只提交 CODEOWNERS 而不开分支保护，规则只会「请求 review」，不会阻断合并；
- 本仓的 commit-msg hook 会拦「改了受保护文件但提交消息没有 `[harness]`」——它挡的是
  误操作；有意绕过的人不受它约束。边界仍是上面的 CODEOWNERS + 分支保护。

### 引擎内容的可追溯性（Engine-Sha）

`[harness]` 只说明"这次改了门禁"，不说明"改成了什么内容"。台账每条记录都带
`engine_sha`（引擎内容的 16 位哈希），但**不能**直接拿它跟「当前引擎」比 —— 正常升级、
甚至改一行测试锁都会让当前值变，那样每次升级全本都会红。

本仓的口径是**提交归因**：

1. 带 `[harness]` 的提交，在提交消息末尾加一行 `Engine-Sha: <16 位哈希>`；值来自
   `python harness/run_gate.py --engine-sha`（只读出口，配置坏了也能回答）；
2. CI 的 `provenance` job 会按该提交的内容**重算**并与尾注比对，不一致即失败；
3. `python guardrail_engine/cli.py log --verify --engine` 把台账里的 `engine_sha` 与
   **提交历史里声明过的集合**对账，基线是第一条带尾注的提交时间，更早的历史不追溯。

会红的是「记录用的引擎内容从未被提交」—— 这正是"瞬时改弱规则 → 跑通过 → 改回 → 提交"
留下的形状。开发期用未提交的引擎跑门禁也会落在清单里：把那份引擎提交并补尾注，
或重跑一次已提交引擎即可。核对不通过只影响这条读命令，不反向拦门禁。


## 扩展

### 添加新规则

1. 在 `checks/example_ruleset.py` 的 `EXAMPLE_RULES` 里加一条 `RegexRule(...)`；
   自建规则集则在检查脚本里改 import
2. `python harness/run_gate.py example --all` 试跑
3. `python harness/run_gate.py --validate` —— 规则 ID、配置键都会体检

需要现成的编码约定规则集？见下文《自带的可选规则集：编码卫生》
（[checks/encoding_ruleset.py](checks/encoding_ruleset.py)）—— 它是给你自己的检查脚本用的库，
默认不注册 gate。

### 添加新 gate（多仓 / 独立规则集）

四处都要动，少一处的症状是「看起来配好了」：

1. **共享** `config/harness.toml` 的 `[repos]` 段加键名（值 `""`）—— 键名是团队策略，
   与机器无关
2. **共享** `config/harness.toml` 的 `[gates.<名>]` 表加 gate，`repo =` 指向该键，`script =`
   指向检查脚本。新脚本可从 `checks/example_code_check.py` 复制，把
   `from example_ruleset import ...` 换成自己的 ruleset
3. `checks/registry.py` 登记**两处**：`ENDPOINT_SCRIPTS`（端名 → 脚本路径）与
   `RULESETS`（脚本 basename → ruleset 模块名）。只登第一处、漏了 `RULESETS`，
   门禁仍能跑，但 `--coverage` 和 `harness_self` 的能力边界清单看不到这套规则 ——
   又回到「静默盲区」。未登记时会走 `config.script` 兜底，`--validate` 只提示、不算失败
   （外部适配 gate 不经过本仓规则集，`RULESETS` 里没有它们是**正常状态**）
4. **本机** `config/harness.local.toml` 的 `[repos]` 里填该键的路径（只有这一步是本机的）

最后跑：

```bash
python harness/run_gate.py --validate      # 必须 rc=0
python sandbox/run_tests.py --fast
```

### 自带的可选规则集：编码卫生（`checks/encoding_ruleset.py`）

这个模块是**给你自己的检查脚本用的库**，默认不注册成 gate —— 编码约定是每个仓自己的
事实（.NET 仓常见 `utf-8-bom`，前端仓 `utf-8`），给一个全局默认会把另一类仓整片报红。

它解决的是"AI 跟随异类文件的编码写入"：往一个 GBK 的老 `Program.cs` 追加代码，
diff 会变乱码。判定分两档：本次**改动过或新建**的文件编码不符 = 拦截；全量扫描里的
存量异类 = 只报告。

接线方式（照「添加新 gate」的四步做，脚本里换成这个规则集）：

```python
import rule_base
from encoding_ruleset import rules_for

raw = rule_base.read_bytes(filepath)          # 走暂存区读取，别直接 open()
ctx = rule_base.CheckContext(path=..., rel=rel, raw=raw,
                             content=raw.decode("utf-8", errors="replace"),
                             repo=repo, mode=a.mode or "strict",
                             targeted=(scope == "targeted"))
for rule in rules_for("utf-8-bom"):           # 取值跟仓约定走，别照抄
    for f in rule.run(ctx):
        ...
```

两个容易漏的点：`raw=` 必须传原始字节（规则判的是字节，不是解码后的文本）；
定向口径要把 `targeted=True` 传进来 —— 缺了它 `error_in_targeted` 不生效，规则最重
只到 info（看起来装了这条规则，却从不拦提交）。

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
| `Guardrail Engine` | 可机械判定的规则、自动触发、台账与验收 |

只写 `AGENTS.md` 成本最低，但没有强制力；加 Guardrail Engine 后才能在编辑期和提交期获得机械拦截与留痕。

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
# 快速测试（编辑回路；跳过真打包/真安装/真 git 链路；本机约 1.5-8 分钟，视机器与并行度）
python sandbox/run_tests.py --fast

# 全量测试（提交前/CI；含真打包/真安装/真 git 链路）
python sandbox/run_tests.py --full

# 单元测试
python -m unittest discover -s tests
```

发布链（`.github/workflows/release.yml`）与每日定时（`.github/workflows/nightly.yml`）
还会跑三类慢闸门：变异冒烟（114 条变异，验证锁真的会红）、真打包后的真产物锁、
干净 venv 安装并执行 `guardrail-engine --version`（轮子自带引擎，不依赖引擎检出）。
本机复现：先 `python sandbox/pack_release.py`，再跑
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
