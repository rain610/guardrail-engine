# Guardrail Engine（原 AgentGuard）—— 统一门禁工程地图（所有 AI agent 通用）

> 本文件是给任何 AI agent 读的「这是哪里、有哪些规则、怎么触发」。Claude Code /
> Codex / OpenCode / CodeBuddy / Hermes 只要能执行 shell 命令即可遵守本工程。
> 核心原则：**人类掌舵，Agent 执行。门禁是代码库的硬边界，不是建议。**

## 这个工程是什么

Guardrail Engine 是一个**通用代码门禁框架**，用于在编辑期和提交期自动检查代码是否符合团队约定。
各仓库通过固定路径引用本工程。约束规则全部集中在 `checks/`，策略集中在配置，
**规则是单一事实源，不要在业务代码仓库里各写一套。**

## 仓库结构速览

- `config/harness.toml` —— gate 清单与策略：enabled / strict|loose / run_on / 行尾。
  `[repos]` 段**只声明键名、值一律留空**（受管仓库名单是团队策略，与机器无关；键名不许动）。
- `config/harness.local.toml` —— **本机事实**：各仓库在自己磁盘上的路径（git 忽略）。
  键值留空 = 这台机器没配该仓，逐条 `⊘` 提示但不阻断；键名不在共享配置里才是真配置错。
  改策略不碰脚本。
- `checks/*.py` —— 各端门禁脚本与规则集。
- `golden-patterns/index.md` —— 黄金范式索引：按任务组织的**代码骨架库**（请求层 / 列表页 /
  表单弹窗 / 后端分层 / 校验 / 日志 / 配置 + 业务端到端范本）。规则命中时输出
  `📖 See: <仓库相对路径>`，顺着它读；`reference/` 是完整参考实现，`templates/` 教怎么造新范式。
- `harness/run_gate.py` —— 统一调度器（按 gate 名调 checks）。
- `harness/coverage.py` —— 覆盖度自评（`run_gate.py --coverage`）：哪些规则真触发过、哪些一次没响。
- `harness/agents/auto-gate.py` —— AI agent 通用入口（在仓库目录下跑，自动识别 gate）。
- `harness/hooks/*` —— git hook 触发点（pre-edit / post-edit / pre-commit）。
- `sandbox/install_hooks.sh` —— 给端仓库装 git pre-commit（agent 无关的强制层）。

## Agent 必须遵守的规则

1. **改码前**（pre-edit）：确保改动清单 / 方案已和用户确认，不要自己设计大改动突袭。
2. **改码后进门禁**：在改动仓库的根目录下，或带上 `--repo`，跑通用门禁——
   ```
   python <HARNESS>/harness/agents/auto-gate.py
   ```
   或指定 repo：
   ```
   python <HARNESS>/harness/run_gate.py <gate名> <文件...>
   ```
   退出码：`0` 通过 / `1` 拦截 / `2` 配置错 / `3` 非受管仓。
3. **不加规则外的例外**：`harness-ok:` 注释只用于**文件级规则不适用**的场景，
   且必须写明理由。规则 ID 必须写对、理由必填。
4. **不改引擎层除非被要求**：`checks/` + `harness/` + `guardrail_engine/`（含兼容壳 `agentguard/`）
   受保护，改动需在提交消息带 `[harness]` 标记。
5. **零第三方依赖**：引擎层只用 Python 标准库，新增依赖需明确批准。外部 ESLint / Semgrep
   适配 gate 是**可选的外部进程**（默认关闭），不计入引擎依赖；工具没装时开启该 gate 会
   fail-closed 到 rc=2。

## 行内豁免（合规写法被误报时的唯一出路）

有些规则机械判不出来。这时**不要改规则、不要删代码绕过**，在命中行的
**本行或紧邻上一行**加标记：

```js
// harness-ok: NO-CONSOLE 调试日志已用条件封装
if (DEBUG) console.log("debug info")
```

- 标记必须落在**注释**里 —— 字符串字面量里的不算。
- 规则 ID 必须写对，**并且跟一句非空理由**。
- 先看清这条规则**是否适用**。被一条明显不适用的规则拦住 → 改配置或报给维护者。

## 黄金范式（写码前的正向指引）

门禁回答「不能这么写」，`golden-patterns/` 回答「照这个写」。它不是规则清单，而是按
「你要写什么」组织的已验收骨架：请求层、列表页、表单弹窗、后端分层、校验与错误、日志、配置，
外加一个从常量到页面的业务全链范本。规则类声明 `see` 后，命中输出会多一行
`📖 See: <仓库相对路径>` —— 被拦的那一刻先读它，再改代码。

入口是 `golden-patterns/index.md`；`reference/` 下的正面样板必须自己也能过门禁（测试会跑）。
团队新范式放进 `golden-patterns/patterns/<端或主题>/` 或 `golden-patterns/endtoend/`，
先读 `golden-patterns/templates/00_范式制造模板.md`，并在规则上写 `see` 对接。

## Git 强制层（无需 agent 配合，自动生效）

- 端仓库 `pre-commit`：commit 先跑对应门禁，未通过阻断。
- 端仓库 `harness_guard`：防 AI 为过门禁而暗改受保护文件。
- 本工程自身两层钩子：checks 语法门禁 + commit-msg 需 `[harness]` 标记（防改坏门禁脚本）。

## 保护循环（防 AI 改门禁敷衍了事）

- 改 `checks/*.py` 或 `config/harness.toml` 属于 harness 维护，需 commit 消息带 `[harness]`。
- 带 `[harness]` 的提交还要在消息末尾加一行 `Engine-Sha: <16 位哈希>`，值取自
  `python harness/run_gate.py --engine-sha`；CI 会按该提交内容重算并比对（不一致即失败）。
  台账里的 `engine_sha` 靠这组尾注归因（`guardrail_engine/cli.py log --verify --engine`）。
- 规则对齐真实架构，别照搬教条。

## 快速命令

```bash
# 列出所有 gate
python harness/run_gate.py --list

# 自动识别并检查改动
python harness/agents/auto-gate.py

# 指定 gate 定向检查
python harness/run_gate.py <gate> <文件...>

# 指定 gate 全量扫描
python harness/run_gate.py <gate> --all

# 配置体检（全舰队 AGENTS.md 检查也在这里；提交期只查本次目标仓）
python harness/run_gate.py --validate

# 门禁子进程时限（秒，默认 1800；超时杀整棵进程树、按 rc=2 处理，台账记 timed_out）
export GUARDRAIL_GATE_TIMEOUT=1800

# 运行摘要
python harness/run_gate.py --summary

# 覆盖度报告
python harness/run_gate.py --coverage

# 安装提交 hook
sh sandbox/install_hooks.sh <gate>

# 安装编辑期转发器
python sandbox/install_shim.py

# 接入自检
sh sandbox/doctor.sh

# 快速测试
python sandbox/run_tests.py --fast
```

## 规则模型

所有规则继承 `checks/rule_base.py` 的 `Rule` 基类。

### 示例规则

项目自带 3 条示例规则（`checks/example_ruleset.py`）：

| 规则 ID | 检查内容 | 严重级别 |
|---|---|---|
| `NO-CONSOLE` | 禁止 `console.log` 残留 | block |
| `NO-HARDIP` | 禁止硬编码 IP 地址 | block |
| `EOL-UNIFIED` | 行尾不统一（CRLF / LF 混用） | block |

运行示例规则：

```bash
python checks/example_code_check.py --repo <仓库路径> --all
```

## 路径约定

`<HARNESS>` 指本工程根目录。求值方式（统一解析链，优先级从高到低）：

```bash
# ① 环境变量
echo "$GUARDRAIL_ENGINE_DIR"

# ② 本机配置文件（config/harness.local.toml → harness 键）
# ③ 从模块所在目录（__file__）向上找（源码树 / 安装副本自定位）
# ④ 指针文件（~/.guardrail-engine/harness.path）—— 仅 pip/wheel 形态需要

# 不确定工程根时跑
python guardrail_engine/cli.py where
```

## 给 Agent 的提醒

- 看不懂规则先读配置文件，不要凭感觉猜；有歧义问用户，别自行假设业务逻辑。
- 详细文档见 `README.md` 和 `docs/` 目录。
