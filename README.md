# eaglermc_harness

> 多客户、多端共用的统一代码门禁工程。
> 当前覆盖 12 家客户、PC / H5 / .NET 三端，共 35 个 gate（34 业务 + 1 自检，全部启用）。

`eaglermc_harness` 把可机械判定的代码约束从各业务仓库收敛到一处：

- **统一规则**：PC / H5 / 后端共享同一套规则模型与调度入口。
- **统一接入**：AGENTS.md、技能、MCP、CLI、编辑器 hooks、git hooks 共用同一份判定内核。
- **统一留痕**：每次判定写入台账，可查看运行摘要、规则覆盖度与接入状态。
- **零第三方依赖**：引擎只依赖 Python 标准库，装好 Python 即可运行。

**定位要说准：它不是 lint 工具。** ESLint / SonarQube 那类**代码分析器**只告诉你"这里
可能有问题"；本工程是**策略强制平面**（policy / enforcement plane）——它决定**这次提交过不过**。
对标物是 Claude Code 的 hooks / permissions、Codex 的 execpolicy，而不是 ESLint。

它也不替代 `AGENTS.md`：`AGENTS.md` 管架构意图、业务语义这类**需要人 / 模型判断**的事，
本工程管**能被机械判定**的事。两者是分工。

### 硬前置条件

| 依赖 | 最低版本 | 用途 |
|---|---|---|
| Python | **3.11+** | 引擎运行时（3.10 及以下缺少 `write_text(newline=)` 等 API） |
| Git for Windows | 2.x | git hooks / diff / worktree 识别（`sh` 需在 PATH 或固定安装位）。**两档行为不同**：编辑期读不到 diff 只告警一次、rc 不变（fail-open，历史行为）；设 `HARNESS_GIT_REQUIRED=1` 则**读不到 diff 一律不按存量降级**（fail-closed）。⚠️ 与 node 那行相反，**本档缺省不开** —— 理由见下节与 `docs/packaging/INSTALL.md` 的前置条件节 |
| Node.js | 22+ | PC/H5 SYNTAX 规则的 `node --check` 语法检查。**两档行为不同**：编辑期扫描缺 node 只 skip；**提交期缺 node 直接拦下**（`harness/hooks/pre-commit.py` 把 `HARNESS_NODE_REQUIRED` 默认设为 `1`，fail-closed）。临时绕过见 `docs/packaging/INSTALL.md` 的前置条件节 |

### 适用边界

> **"零依赖"≠"开箱通用"。**

本工程的 35 个 gate 绑定 **Eagle RMC 项目**的三端仓库（PC Vue / H5 UniApp / .NET 后端）。
规则（如 `PC-AXIOS`、`H5-UPLOAD`、`BACK-LAYER`）基于该项目的代码约定设计。
如果你的项目不是 Eagle RMC，需要：

1. 修改 `config/harness.yaml` 的 `repos` 和 `gates` 段，指向你的仓库。
2. 逐条审查 `checks/*_ruleset.py` 中的规则，关闭不适用的（`disabled_rules`）。
3. `components_allowlist.yaml` 需替换为你的组件清单。

引擎层（`harness/` + `checks/` 框架）是通用的；规则层（`*_ruleset.py`）是项目特定的。

本文中的 `<HARNESS>` 指本工程根目录。求值方式（统一解析链，优先级从高到低）：

```bash
# ① 环境变量（CI/测试覆盖；主名 EAGLERMC_HARNESS_DIR，旧名 HARNESS_DIR 兼容）
echo "$EAGLERMC_HARNESS_DIR"

# ② 本机配置文件（config/harness.local.yaml → harness: 键）
# ③ 指针文件（~/.eagle-agents/harness.path，向后兼容）
# ④ 从 cwd 向上找（兜底）

# 不确定工程根时跑
sh sandbox/where.sh
```

门禁内部使用相对路径，clone 到任意目录都能运行；只有客户端 hook 配置需要写入本机绝对路径。

---

## 能带来什么

一句话：**把"规范"从"靠人记、靠模型自觉"变成"靠机器判"**，并且让**"门禁自己有没有
失效"也变成一个能查的数**。

| 痛点 | 本工程怎么解 | 依据 |
|---|---|---|
| 规范写在文档里，模型记不住，上下文一长就被稀释 | 常驻只留 ~1K 的「地图」，细节按需加载 | `AGENTS.md` 收敛为唯一权威源；`golden-patterns/` 按端读取 |
| 写出来的代码"像不像本项目的代码"靠运气 | 黄金范式：从已验收的生产代码里提取可照抄的骨架 | 技术骨架级（列表页 / 表单抽屉 / Tab 页 / 批量操作…）+ 业务端到端级；红线是**没有来源路径 = 不合格** |
| 约定只对"愿意遵守的人"有效 | 机械规则在编辑期与提交期各拦一次，忘了也会被拦 | 35 个 gate；`pre-commit` 经 `core.hooksPath` 兜底 |
| 一上规则就被存量旧账淹没 → 规则当天被关掉 | 双态 severity：本次改动行 / 新文件 = `error` 拦截；存量历史 = `info` 只观察 | 后端反模式方法名（`GetData` / `Deletes` / `InitData`）在 gxgk 后端仓实测 **260 处 / 114 个文件**（该仓共 6490 个 `.cs`；数取自 R5 规则自身全量扫描）——全 error 活不过一天 |
| "门禁跑没跑、哪条规则响过"说不清 | 每次判定写台账（月度分片 + 行内链哈希，锚点入库防篡改）；覆盖度把"没响"拆成四种可行动结论 | `reports/gate_log-YYYY-MM.jsonl`；`gate log --anchor/--verify`；`python harness/run_gate.py --coverage` |
| 门禁被改弱、被绕过，而无人知道 | 保护循环 + 自证体系（变异冒烟 / 契约锁 / 丢锁看守） | `checks/harness_guard.py`、`sandbox/mutation_smoke.py`、`tests/test_lock_inventory.py` |
| 引入工具链的依赖成本 | **零第三方依赖**，只用 Python 标准库，且有 AST 守卫强制 | `tests/test_stdlib_only.py` |

**不承诺什么**（一并说清，避免"以为有、其实没有"）：

- **不代替人判断**：多数规则是启发式，目标是挡住已知事故，不证明代码绝对正确。
- **不覆盖服务端强制**：`core.hooksPath` 写在端仓 `.git/config` 里，**新 clone 的仓库不会
  自带门禁**；`--no-verify` 始终是出口。要收紧只能走流程（见 `docs/packaging/RUNBOOK.md`）。
- **不保证零漏检**：单行正则看不见跨多行调用、别名导入、生成代码 —— 已实测的边界逐条列在
  [规则能力边界](#规则能力边界)，每条都由测试钉住。

---

## 快速导航

| 我想…… | 先看 |
|---|---|
| 先搞清它是什么、值不值得用 | [能带来什么](#能带来什么) |
| 第一次安装并跑通门禁 | [快速开始](#快速开始) |
| 日常改完代码怎么查 | [日常工作流](#日常工作流) |
| 选择保障层级和安装通道 | [接入策略与通道](#接入策略与通道) |
| 查具体命令 | [命令速查](#命令速查) |
| 加规则、加客户、改引擎 | [维护导览](#维护导览) |
| 跑测试、看覆盖度 | [质量与自证](#质量与自证) |
| 打包、发版、排障 | [打包与分发](#打包与分发) |
| 升级到新包 | [升级](#升级) |
| 不需要了，退干净 | [卸载](#卸载) |
| 了解能力边界 | [规则能力边界](#规则能力边界) |

---

## 快速开始

> 这是推荐的完整接入路径。第二次运行 `setup_machine.sh` 会安装提交期 hook，
> 因此最终属于“强制型”保障。只想先体验时，可以只配置 `repos`，改完代码后执行
> `auto-gate.py`，暂不安装 hook。

### 1. 新机器接入

```bash
# 1) clone 工程
git clone https://resources.ehsway.com:8929/developer/eaglermc_harness.git

# 2) 首跑：生成 config/harness.local.yaml
sh <HARNESS>/sandbox/setup_machine.sh

# 3) 编辑 local.yaml，把 repos 改成你机器上的实际路径
#    完整示例：config/harness.local.example.yaml

# 4) 二跑：逐仓安装提交期 hook，并自动运行 doctor
sh <HARNESS>/sandbox/setup_machine.sh
```

单独复查接入状态：

```bash
sh <HARNESS>/sandbox/doctor.sh
```

判定口径：`doctor` 中 `❌` 必须处理，`⚠` 是提示。

> **同事机器差异（新接入必读）**
>
> - **本门禁不依赖任何个人 skill 或个人工具**：`worktree-manager` / `huayu-manager` 之类是维护者
>   自用的辅助 skill（批量建 worktree、初始化华宇线），**有则更省事，没有也完全可用** —— 门禁运行
>   只依赖 `config/harness.yaml` + 端仓在磁盘上的路径 + git 三样。
> - **仓库路径是「本机事实」，共享配置里只留键名**：目录名（如 `*-hy_base`）、盘符、是否使用
>   git worktree 与你大概率不同，所以 `config/harness.yaml` 的 `repos:` 段**只声明键名、值一律留空**；
>   你的实际路径写在 `config/harness.local.yaml` 里（示例见 `config/harness.local.example.yaml`）。
>   **不要求 git worktree**：直接 clone 各仓到任意路径即可（用不用 worktree、目录怎么命名，纯属个人习惯）。
>   **别改共享 yaml**；本机覆盖只允许改 `repos` 路径映射，改已注册 gate 的策略字段
>   （enabled/mode/script/…）会被忽略并打印提示。
> - **只配你机器上真有的那几个仓就行**：键名在共享文件里已声明，值留空的 gate 会被逐条列为 `⊘`
>   并在末尾汇总一句，**不阻断提交**；反过来，引用一个共享文件里没声明的键名才是**真配置错**，
>   任何机器都拦 —— 这是「还没配」与「配错了」的分界线。
> - **worktree / 多布局场景**：hook 装一次就够（`core.hooksPath` 共享分发器），提交时按 `cwd`
>   自动识别所属 gate；worktree 与主仓共享的**原有** hook 会被备份到
>   `<common-dir>/eaglermc-original-hooks/`，并在跑门禁前链式调用，不会被静默吞掉。
> - 文档里的路径一律写占位符 `<HARNESS>`；不确定工程根时跑 `sh <HARNESS>/sandbox/where.sh`。

### 2. 日常使用

在任意受管端仓根目录执行：

```bash
python <HARNESS>/harness/agents/auto-gate.py
```

它会自动识别客户与端，只检查本次改动文件。

也可以让 AI 直接执行：

```text
改完代码后跑一下 auto-gate，有问题按输出修到通过。
```

---

## 日常工作流

推荐流程：

```text
读规则 -> 改代码 -> 跑 auto-gate -> 修复问题 -> 提交
                   |                 |
                   |                 +-- 提交期 pre-commit 再兜底一次
                   +-- 编辑期 hook 可自动触发并回灌结论
```

### 查看改动

```bash
# 自动识别 gate，仅查本次改动文件
python <HARNESS>/harness/agents/auto-gate.py

# 指定仓库
python <HARNESS>/harness/agents/auto-gate.py --repo <端仓路径>
```

### 查看结果

| 退出码 | 含义 | 处理 |
|---|---|---|
| `0` | 通过 | 可以继续 |
| `1` | 门禁拦截 | 按 `❌` 行修复 |
| `2` | 配置或定位错误 | 查 gate、repo 路径与配置 |
| `3` | 非受管仓库 | 跳过即可 |

输出约定：

- `❌ ...`：阻断级问题，必须修复。
- `⚠ ...`：提示或观察项，不一定阻断。
- `harness-ok: <规则ID> <理由>`：定向行内豁免，写在命中行或紧邻上一行（适用端：PC / H5 / 后端）。
  规则 ID 必须写对、理由必填（一句话静默所有规则被设计上禁止）。**文件级规则不适用**：
  后端的 `ENC` / `R7`（编码、该不该出现在这个仓）判的是"整个文件该不该是这样"，
  给它们开一行注释的后门就等于废掉硬约定，所以在那些规则上标记**不生效**、门禁照拦。
  遇到疑似误报且这条规则对你这个客户不适用 → 改 `disabled_rules` 配置或报 harness 负责人，别硬加标记。

紧急旁路：

```bash
python <HARNESS>/eaglermc_gate/cli.py bypass --repo <端仓> --reason "说明原因"
git -C <端仓> commit --no-verify
```

旁路只应作为事故处理手段，事后必须说明。

⚠️ **`bypass` 不是权限闸**（评审 2026-09-23 L19）：它不拦任何提交、也不改变门禁判定 ——
任何调用方都能声明它，甚至可以不声明直接 `--no-verify`（`--no-verify` 本身无法被钩子拦下）。
它唯一的作用是**把这个动作写进运维台账**，让一次绕过「可见、可审计」。
把它当成"没声明就跑不了"会得到一个危险的错觉：真实差别是「留痕的绕过」与「没人知道的绕过」。

---

## 接入策略与通道

接入选择分两步：

1. 选择**保障层级**：谁来保证门禁一定会跑。
2. 选择**安装通道**：具体通过技能、MCP、插件还是 hooks 接入。

### 1. 选择保障层级

| 类型 | 谁保证运行 | 适合场景 | 主要风险 |
|---|---|---|---|
| **自觉型** | AI 按 AGENTS.md 主动调用 `auto-gate.py`，不安装 hook | 临时机器、先体验 | 模型可能漏跑，无回执 |
| **工具型** | AI 通过 skill / MCP / CLI 调用 | 日常主用，希望顺手可用 | 没调用就不会产生本次判定 |
| **强制型** | 编辑器 hook + git pre-commit | 要求“忘了也会被拦” | 客户端契约或安装状态需要验证 |

推荐组合：**工具型日常使用 + 强制型提交兜底**。自觉型适合导航和低摩擦起步，不应单独作为验收依据。

### 2. 选择安装通道

| 通道 | 服务的层级 | 怎么装 |
|---|---|---|
| **技能** | 工具型 | `python <HARNESS>/eaglermc_gate/cli.py skill install --target claude` |
| **MCP** | 工具型 | 适合任何支持 MCP 的宿主，配置见 `docs/packaging/COMPAT.md` |
| **插件** | 工具型 | `python <HARNESS>/integrations/plugins/build_marketplace.py --harness <HARNESS> --out <DIR>`，适用于 Claude Code / OpenClaw |
| **编辑期 hook** | 强制型 | 安装转发器后，按客户端配置 `integrations/<客户端>/README.md` |
| **提交期 hook** | 强制型 | `sh <HARNESS>/sandbox/install_hooks.sh <gate名>` |

自觉型不需要额外安装通道，但仍需 clone 工程并配置 `repos`。端仓根已有 `AGENTS.md`，
AI 改完代码后执行 `auto-gate.py` 即可。

### 3. 强制型安装与验证

**提交期兜底**

```bash
sh <HARNESS>/sandbox/install_hooks.sh <gate名>

# 例：
sh <HARNESS>/sandbox/install_hooks.sh gxgk_pc
```

**编辑期 hook**

```bash
python <HARNESS>/sandbox/install_shim.py
python <HARNESS>/sandbox/install_shim.py --check
```

产出**三样**：转发器 `~/.eagle-agents/hooks/eaglermc-harness-gate.py`、指针 `harness.path`，
**以及全局守则 `~/.eagle-agents/GLOBAL_AGENTS.md`** —— 三份端级模板
（`golden-patterns/templates/AGENTS_{pc,h5,backend}_slim.md`）都指向它，而它此前没有生成者
（只有没有任何调用方的 `bootstrap_agent_env.sh` 会写），照安装说明做完那个文件根本不存在。

守则是**哨兵分区**的：`<!-- HARNESS:BEGIN -->` 到 `<!-- HARNESS:END -->` 之间由 harness 重渲染
（所以引擎仓里守则改了，重跑一次本命令就能到达你这里），**区间之外是你自己的、永不触碰**；
也正因如此，卸载时刻意不退它。分层设计见 `golden-patterns/templates/00_知识分层设计.md`。

守则正文里那几处绝对路径是**渲染那一刻烘进去的**（`__HARNESS__` 占位符换成本机路径，
所以每个人拿到的都是自己的路径）。挪过引擎仓之后它们就指空了 —— `--check` 会当场说出来
（只提醒，不影响退出码）：重跑一次上面那条命令即对齐。

再按客户端配置 hook。配置位置见：

```text
integrations/<客户端>/README.md
```

**验证**

```bash
sh <HARNESS>/sandbox/doctor.sh
sh <HARNESS>/sandbox/verify_integrations.sh gxgk_pc claude
python <HARNESS>/sandbox/check_contracts.py --all
```

验证必须同时满足：

1. 台账（当月片 `reports/gate_log-YYYY-MM.jsonl`）新增了对应记录。
2. AI 回复中能看到门禁结论。

只看第 1 条不够，因为“hook 跑了”不等于“模型收到了结论”。

### 4. 支持的客户端

| 客户端 | 规则入口 | 编辑期 hook | 工具型接入 |
|---|---|---|---|
| **Claude Code** | `CLAUDE.md` -> `AGENTS.md` | 已实跑 | MCP / 技能 / 插件 |
| **Codex** | 原生读 `AGENTS.md` | 已实跑 | MCP |
| **OpenCode** | 原生读 `AGENTS.md` | 已实跑 | 技能 |
| **CodeBuddy** | 原生 fallback 到 `AGENTS.md` | 已实跑 | 技能 |
| **Trae CN** | 原生读 `AGENTS.md` | 已实跑 | - |
| **Qoder CN** | 原生读 `AGENTS.md` | 已实跑 | - |
| **ZCode** | `AGENTS.md` + hooks | 已实跑 | - |
| **Hermes** | `AGENTS.md` + 技能 | 通用入口 | - |
| **OpenClaw** | 插件自带技能 | - | 插件 |
| **其他 MCP 宿主** | 任意 agent 走 `auto-gate.py` | - | MCP |

提交期 git hook 与客户端无关，覆盖所有工具和人工操作。

---

## 命令速查

以下命令默认在 `<HARNESS>` 根目录执行。如果当前在端仓中，请改用
`python <HARNESS>/...` 或 `sh <HARNESS>/...`。

| 场景 | 命令 |
|---|---|
| 列出 gate | `python harness/run_gate.py --list` |
| 自动识别并检查改动 | `python harness/agents/auto-gate.py` |
| 指定 gate 定向检查 | `python harness/run_gate.py <gate> <文件...>` |
| 指定 gate 全量扫描 | `python harness/run_gate.py <gate> --all` |
| 临时宽松模式 | `python harness/run_gate.py <gate> --mode loose <文件...>` |
| 配置体检 | `python harness/run_gate.py --validate` |
| 运行摘要 | `python harness/run_gate.py --summary` |
| 覆盖度报告 | `python harness/run_gate.py --coverage` |
| 安装端仓提交 hook | `sh sandbox/install_hooks.sh <gate>` |
| 安装编辑期转发器 + 全局守则 | `python sandbox/install_shim.py` |
| 全局守则状态 / 单独重渲染 | `python sandbox/global_agents.py --check` ｜ `python sandbox/global_agents.py <HARNESS>` |
| 接入自检 | `sh sandbox/doctor.sh` |
| 快速测试 | `python sandbox/run_tests.py --fast` |
| 全量测试 | `python sandbox/run_tests.py --full` |

---

## 门禁如何工作

### 分层

```text
客户端提示层        AGENTS.md / skill
       |
工具调用层          MCP / CLI / plugin
       |
强制拦截层          编辑期 hook + git pre-commit
       |
判定引擎            run_gate.py -> checks/*_check.py -> *_ruleset.py
       |
台账与体检          gate_log.jsonl / coverage / doctor / contracts
```

### 检查范围

| 端 | 主要检查 |
|---|---|
| PC | 代码卫生、CRLF、SFC 结构、语法、组件收敛、`v-for :key` |
| H5 | 代码卫生、LF、语法、上传确认、组件收敛、`v-for :key` |
| 后端 | 分层依赖、方法名反模式、非法前端文件、状态魔法数字、编码卫生 |

### 扫描口径

| 口径 | 什么时候用 | 语义 |
|---|---|---|
| **定向** | `auto-gate.py`、`post-edit`、`pre-commit` | 只看本次改动文件与新增行 |
| **全量** | `run_gate.py <gate> --all` | 人工摸底或审计，按整文件口径列出问题 |

定向模式的目标是“不为一整份历史文件负责”，只拦住本次新增问题。

### 和 AGENTS.md 的关系

二者不是替代关系：

| 层 | 负责什么 |
|---|---|
| `AGENTS.md` | 架构意图、业务语义、红线与人工判断 |
| `harness` | 可机械判定的规则、自动触发、台账与验收 |

只写 `AGENTS.md` 成本最低，但没有强制力；加 harness 后才能在编辑期和提交期获得机械拦截与留痕。

---

## 配置

主配置：`config/harness.yaml`

| 字段 | 说明 |
|---|---|
| `repos` | 各客户、各端仓库路径 |
| `gates.<gate>.enabled` | 是否启用 |
| `gates.<gate>.repo` | 引用 `repos` 中的仓库 key |
| `gates.<gate>.script` | 对应检查脚本 |
| `gates.<gate>.mode` | `strict` 或 `loose` |
| `gates.<gate>.run_on` | 触发点，如 `pre_commit`、`post_edit` |
| `gates.<gate>.line_ending` | 期望行尾：`auto`（**默认，不写这一行**，按本机本仓的检出事实判定）/ `CRLF` / `LF`（显式值=锁定，优先级最高） |
| `gates.<gate>.encoding` | 后端编码约定，默认 `utf-8-bom` |
| `gates.<gate>.legacy_baseline` | 存量仓过渡开关 |
| `gates.<gate>.disabled_rules` | 本 gate 关闭的规则 ID |

### 本地覆盖层

不要为了适配个人机器去改共享 `config/harness.yaml`。使用本地覆盖层：

| 优先级 | 来源 | 用途 |
|---|---|---|
| 1 | 环境变量 `HARNESS_CONFIG` | CI 或特殊布局 |
| 2 | `config/harness.local.yaml` | 本机仓库路径覆盖 |
| 3 | `config/harness.yaml` | 共享基线 |

典型内容：

```yaml
repos:
  gxgk_pc: E:/code/eaglermc_pc_net-gxgk
  gxgk_h5: E:/code/eaglermc_mobile_net-gxgk
  gxgk_backend: E:/code/eaglermc_back_net-gxgk
```

`harness.local.yaml` 已被 `.gitignore` 忽略，不需要提交。

本工程不通过 git submodule 绑定端仓，只按配置中的仓库路径和 gate 名工作。本地配置解析异常会输出
`[config-warn]`，不会静默当作路径正常。

---

## 维护导览

> 写给后续维护本工程的同事与 AI agent。
>
> 三条纪律：
> 1. 改动受保护文件时，提交消息带 `[harness]`。
> 2. 引擎层（`checks/` + `harness/`）保持零第三方依赖。
> 3. 壳只转发，不重做行号或坐标系运算。见 `docs/adr/0001-packaging-principles.md`。

| 你要改什么 | 权威入口 | 验证 |
|---|---|---|
| **加 / 改一条门禁规则** | 定级与候选：`golden-patterns/rules/_01_门禁规则候选.md`；实现：`checks/` 对应 ruleset 写 `Rule` 子类 | `tests/test_checks.py`；`python sandbox/run_tests.py --fast` |
| **维护黄金范式** | 制造工艺：`golden-patterns/templates/00_黄金范本制造模板.md`；缺口：`golden-patterns/rules/_00_覆盖率对照.md`；登记：`golden-patterns/index.md` | 对照真实代码抽查；跨客户差异必须 git 实证 |
| **加端 / 加客户** | `checks/registry.py` 加 gate -> 脚本映射；`config/harness.yaml` 加 gate 条目 | `python harness/run_gate.py --validate`；`sh sandbox/doctor.sh` |
| **改引擎核心** | 先读 `docs/adr/0001-packaging-principles.md`；本仓自检 gate `harness_self` 会把关 | `python sandbox/run_tests.py --full` |
| **发版 / 分发** | 改 `VERSION`；写 `docs/releases/v<版本>.md`；打 tag 推送 | Release workflow；包内 `SHA1SUMS.txt` |
| **接入 / 排障 / 卸载** | `docs/packaging/RUNBOOK.md`、`INSTALL.md`、`COMPAT.md`、`OPENCLAW.md` | 各文档自带验证命令 |

- 造范本必须有来源路径，**无来源路径 = 不合格**。
- 跨客户结论必须用 `git` 实证，不凭印象。
- 单条规则快速写法见下方“新增规则”。

### 新增规则

在对应 ruleset 中新增 `Rule` 子类，并加入 `XXX_RULES`：

```text
checks/pc_ruleset.py
checks/h5_ruleset.py
checks/backend_ruleset.py
```

新增规则后至少运行：

```bash
python -m unittest tests.test_checks -v
python sandbox/run_tests.py --fast
```

### 新增端或客户

1. 在 `checks/registry.py` 登记 `gate -> script`。
2. 在 `config/harness.yaml` 或本地覆盖层增加 gate。
3. 运行配置体检与 doctor。

```bash
python harness/run_gate.py --validate
sh sandbox/doctor.sh
```

### 改完之后怎么生效（要不要重新发包）

总原则一句话：**对方是「指向你的仓库」还是「装了一份副本」？指向的 → `git pull`；副本的 → 重装或等新包。**

- `checks/` 规则 / `golden-patterns/` 范式 / `harness/` 引擎：提交推送后，同事在自己 harness 克隆上 `git pull` 即取即用（提交钩子、MCP、技能都按路径指向仓库）——**不用发包**。
- 技能副本（`skill install` 装到宿主目录）：重跑同一条安装命令即可（幂等、增量刷新）——**不用发包**。
- 插件（Claude Code / OpenClaw）与人用 CLI（wheel）：属于「装出来的副本」，刷新的标准动作是重跑安装（升级步骤见 `docs/packaging/INSTALL.md`）；**要正式发新版本时**：改 `VERSION` → 写 `docs/releases/v<版本>.md` → 打 tag 推送（CI 自动出包 + GitHub Release；内网 GitLab 手动传一份）。**发包只影响副本型接入**——端仓钩子 / MCP 侧永远是 pull 即最新。
- 文档（README / INSTALL / OPENCLAW / 分享 PPT）：推仓库即最新；包内「安装说明.md」是渲染副产物，下次打包自动跟上——**不用专门发**。

---

## 质量与自证

### 测试

```bash
# 日常快跑
python sandbox/run_tests.py --fast

# 并行全量，严格句柄检查
python sandbox/run_tests.py --full

# 串行全量，排查用
python -W error::ResourceWarning -m unittest discover -s tests
```

测试覆盖规则实现、真实入口链路、hook 契约、配置解析、打包契约与跨平台路径。

### 覆盖度

门禁不仅要能拦，还要能回答“规则到底有没有响过”：

```bash
python harness/run_gate.py --coverage
python harness/coverage.py --days 30
python harness/coverage.py --last 200 --json
```

覆盖度报告会区分：

| 状态 | 含义 |
|---|---|
| 活跃 | 窗口内触发过 |
| 从未触发 | 对应端跑过，但规则一次没响 |
| 该端窗口内无运行 | 没有数据，不能判断规则好坏 |
| 按客户禁用 | 配置明确关闭 |

看到“从未触发”时，先确认规则适用范围，再用真实违规样本做正例对照。

### 保护循环

工程自身有 hooks 保护：

| hook | 作用 |
|---|---|
| `pre-commit` | 编译检查 `checks/*.py`，防止改坏门禁脚本 |
| `commit-msg` | 改动受保护文件但没有 `[harness]` 标记时拦截 |
| 端仓 `pre-commit` | 业务门禁前先跑 `harness_guard` |

### CI

`.github/workflows/ci.yml` 只跑工程自身自检：

1. 零第三方依赖守卫。
2. 配置体检 `--structure-only`。
3. 单元与链路套件。

端仓门禁不跑外部 CI，因为业务仓在内网；本地编辑期与提交期才是主要拦截点。

---

## 打包与分发

统一分发壳位于 `eaglermc_gate/`：

- `cli.py`：命令行入口。
- `mcp.py`：单文件 MCP 薄服务。⚠️ **单线程串行**（评审 2026-09-23 L8）：一次只处理一个请求，
  跑长工具时**连宿主的 ping 都在排队**，有些宿主会据此判"服务死了"、甚至重启 server。
  两个长调用是 `harness_doctor`（约 1-2 分钟）与 `harness_run_gate` 的 `all: true` 全量扫描（数分钟，
  还受 `EAGLERMC_MCP_TIMEOUT` 默认 180 秒约束，超大仓会以超时报回）。这两个请走 CLI：
  `cli.py run <gate> --all` / `sh sandbox/doctor.sh`。该说明也写在宿主读得到的 `INSTRUCTIONS`
  与两个工具的 description 里——**写进协议本身，而不是只写在这里**。
- skill：给 agent 的说明书。
- plugin：技能 + MCP 一次装齐。

### 常用命令

```bash
python <HARNESS>/eaglermc_gate/cli.py quickstart
python <HARNESS>/eaglermc_gate/cli.py check <文件>
python <HARNESS>/eaglermc_gate/cli.py hooks install --repo <端仓>

python <HARNESS>/eaglermc_gate/cli.py skill install --target claude
python <HARNESS>/eaglermc_gate/cli.py skill status

python <HARNESS>/sandbox/pack_release.py
```

### 发布新版本

1. 修改 `VERSION`。
2. 写 `docs/releases/v<版本>.md`，模板见 `docs/releases/_TEMPLATE.md`。
3. 打 tag 并推送。

```bash
git tag v<版本>
git push origin v<版本>      # GitHub：Actions 自动建 Release
git push gitlab v<版本>      # 内网：tag 也要推（Release 不自动建，见下）
```

GitHub Actions 会执行自检、构建 wheel 与内部安装包，并创建 Release。

**内网 GitLab 侧（手动，因为没有 runner）**：该实例 Specific/Shared/Group runners 三栏全空 ✓，
所以内网 Release 要自己建：`Releases → New release` → 选 tag → 正文粘 `docs/releases/<tag>.md`
→ **Assets → Links** 填（指向仓内镜像，点开即下载 ✓）：

```text
https://resources.ehsway.com:8929/developer/eaglermc_harness/-/raw/main/docs/releases/assets/eaglermc-gate-<tag>.zip
```

镜像文件放 `docs/releases/assets/`（与 GitHub 附件同一文件、已核 sha256，~50KB 量级）✓。

Release 下载（**两个渠道是同一个 zip**，sha256 相同）：

- **内网 GitLab（推荐，同事不用梯子）**：`https://resources.ehsway.com:8929/developer/eaglermc_harness/-/releases`
  → 选版本 → 在 **Assets** 里点 `eaglermc-gate-v<版本>.zip` 即下载（约 50 KB）
  → 解压后按包内《安装说明.md》走（新机器先 `python fix_paths.py --harness <你的 harness 路径>`）。
  ⚠️ 内网无 runner，Release 是**网页手动建**的（流程见 `docs/releases/README.md`）——
  新版本发出来后需要有人补建一条，否则内网 Releases 页看不到它。
- GitHub：`https://github.com/rain610/eaglermc_harness/releases`（需能访问 GitHub）
- 校验：`docs/releases/assets/README.md` 里列了每个 zip 的 sha256 与字节数（下载后 `sha256sum` 对一下）。

---

## 升级

拿到新包后，**一条命令刷新本机装过的那几路**，默认只预演：

```bash
python "<新包目录>\upgrade.py" --harness "<你的 eaglermc_harness 路径>"        # 预演：只打印将做什么
python "<新包目录>\upgrade.py" --harness "<你的 eaglermc_harness 路径>" --yes   # 执行
```

六步按序执行（前一步不过，后面不做）：

| 步骤 | 动作 | 说明 |
|---|---|---|
| [1/6] | 路径适配 | **必须第一步，不可跳过**：新包里的 `.mcp.json` 又是占位符，由包内 `fix_paths.py` 现场填本机路径 |
| [2/6] | Claude Code 插件 | `marketplace add <本包>/marketplace` → `plugin update` |
| [3/6] | OpenClaw 扩展 | 就地把新 bundle 覆盖进 `extensions/eaglermc-gate`（重启桌面端生效） |
| [4/6] | 人用 CLI（pip 包） | **只在装过时**刷新，装完对账产物是否真的换了 |
| [5/6] | 技能副本 | 转发 `cli.py skill install --dir <宿主技能目录>` |
| [6/6] | 编辑期 shim **+ 全局守则** | **只在装过时**刷新（`sandbox/install_shim.py`）。全局守则是**渲染出来的正文**（不是指针），所以它跟别的接入不一样 —— 引擎里改了守则，得这次刷新才到得了你这里；没装过 shim 的机器不受本步覆盖，要守则就单独跑 `sandbox/global_agents.py <HARNESS>` |

设计上有三条硬约束：**不做任何撤回、不删任何文件**（旧文件只列出来交给人判断）；
**升级 ≠ 新增通道**（没装过的不替你装，那是「安装」）；**与目录布局无关**——无论你把包解压成
"每版一个新目录"还是"覆盖到固定目录"，走的都是同一条 `marketplace add <本包>/marketplace`
（实测同名市场会被静默改指到新路径，无需先 `remove`）。

两个"看着升了、其实没升"的坑：

- **同版本号**：`claude plugin update` 会打印绿色的 "already at the latest" 然后什么都不做；
  pip 的 `pip install -U` 同版本号也是 rc=0 且零字节变化。判断依据是**内容**，不是版本号。
- **漏跑第 [1/6] 步**：`.mcp.json` 里还留着 `__PY__`，MCP 起不来、编辑期门禁**整体无声失效**。
  「我的机器没变」不构成跳过的理由 —— **变的是包，不是机器**。

> ⚠️ **不要为了升级去卸载**：`uninstall.py` 是**退出**语义（离职 / 换机 / 不再用），
> 它会留下门禁关着的一段空窗，且每个端仓还要重新 `--unhook` 再装回去。升级请用 `upgrade.py`。
> 手动等到等价命令表、以及"怎么判断我这台机器现在装的是哪一版"，见
> `docs/packaging/INSTALL.md` 的「五、升级」。

---

## 卸载

不需要了、要退干净时，**一条命令**，默认只预演：

```bash
python "<本包目录>\uninstall.py"                                  # 预演：逐项列出将做什么
python "<本包目录>\uninstall.py" --yes                            # 执行
python "<本包目录>\uninstall.py" --yes --unhook "<某个端仓>"        # 顺带撤回该仓的提交期钩子（可多次）
python "<本包目录>\uninstall.py" --yes --skip openclaw,pip         # 想保留某几项
```

处理六项：

| 装过什么 | 怎么退 | 方式 |
|---|---|---|
| 技能副本 | 改名归档为 `*.disabled-<时间戳>` | ✅ 自动，可逆 |
| 提交期钩子（`core.hooksPath`） | 只对 `--unhook` 点名的仓库撤回 | ⚙️ 逐仓手动点名 |
| Claude Code 插件 | `plugin uninstall` + `marketplace remove` | ✅ 自动 |
| OpenClaw 扩展 | 移出 `extensions/` 归档 | ✅ 自动 |
| 人用 CLI（pip 包） | `pip uninstall eaglermc-gate` | ✅ 自动 |
| 编辑期 shim | 改名归档（转发 `install_shim.py --uninstall`） | ✅ 自动，可逆 |
| 全局守则 `GLOBAL_AGENTS.md` | **不退**：哨兵区之外是你自己写的规矩，撤走等于把它们一起拿走 | — 留在原地 |

**归档是可逆，不是"干净"。** 每条卸载都会**永久留下一份完整副本**（`<名字>.disabled-<时间戳>`），
工具**从不删除**，要删得你自己动手。所以退完之后，盘点残留是卸载者的责任：

```bash
pip show eaglermc-gate        # 应无此包
claude plugin list            # 应无 eaglermc-gate
python <HARNESS>/eaglermc_gate/cli.py skill status   # 列出技能残留
```

还有一处**必须手动收尾**：归档掉 shim 之后，各宿主的钩子配置**仍指向那个路径**，只归档文件、
不改配置，编辑期门禁会**静默失效**（不报错，只是不再拦）。`~/.eagle-agents/` 下的
`eaglermc-harness-gate.py.disabled-<时间戳>` 与 `harness.path.disabled-<时间戳>` 就是这两处残留。

本工具**不碰你的 `<HARNESS>` 仓库**（它只是引擎，卸载不影响它）；包目录本身不用了直接删即可。
退出码：`0` = 完成或本来就没有可卸的；`1` = 有未完成项或参数问题。

> 上面是速览。**逐项权威说明**（含每项的命令原文、判定依据与排障）见
> `docs/packaging/INSTALL.md` 的「四、卸载（不需要了怎么退干净）」。

---

## 工程结构

<details>
<summary>展开主要目录示例</summary>

下面是主要目录示例，不追求逐文件完整；新增文件时这里不需要同步维护。

```text
eaglermc_harness/
├── config/
│   ├── harness.yaml
│   ├── harness.local.yaml
│   └── harness.local.example.yaml
├── checks/
│   ├── rule_base.py
│   ├── pc_code_check.py
│   ├── h5_code_check.py
│   ├── backend_layer_check.py
│   ├── pc_ruleset.py
│   ├── h5_ruleset.py
│   ├── backend_ruleset.py
│   ├── components_allowlist.yaml
│   ├── components_allowlist_h5.yaml
│   └── harness_guard.py
├── harness/
│   ├── run_gate.py
│   ├── _config.py
│   ├── agents/auto-gate.py
│   └── hooks/
├── integrations/
│   ├── claude-code/
│   ├── codex/
│   ├── opencode/
│   ├── codebuddy/
│   ├── hermes/
│   ├── hooks/
│   └── eaglermc-harness-skill/
├── golden-patterns/
├── sandbox/
├── tests/
├── reports/
├── eaglermc_gate/
├── docs/
├── AGENTS.md
└── README.md
```

</details>

关键目录：

| 目录 | 职责 |
|---|---|
| `checks/` | 规则实现与薄 loader |
| `harness/` | 调度器、配置读取、hook 入口 |
| `config/` | gate 与仓库策略 |
| `integrations/` | 各 AI 客户端接入说明与转发器 |
| `sandbox/` | 安装、自检、演示、打包、审计工具 |
| `golden-patterns/` | 改码参照与黄金范式 |
| `tests/` | 单元测试与链路回归 |
| `eaglermc_gate/` | CLI / MCP 分发壳 |
| `reports/` | 运行台账与审计产物，git 忽略 |

---

## 规则模型

三端检查脚本共享 `checks/rule_base.py`：

```text
Rule
├── id / name / severity_default
├── applicable(ctx)
├── check(ctx) -> [Finding]
└── severity(ctx)
```

各端规则集中在：

```text
checks/pc_ruleset.py
checks/h5_ruleset.py
checks/backend_ruleset.py
```

`*_code_check.py` 负责 CLI、文件收集、输出与退出码，规则逻辑不分散到调度层。

---

## 耗时参考

以下为 gxgk 真实仓库的历史实测，具体时间受机器和后台负载影响。

| 场景 | 耗时 |
|---|---:|
| 针对性 1 文件，含 node 语法 | 约 1.5s |
| 针对性 1 文件，跳过 node | 约 0.5s |
| PC 全量，约 2900 文件 | 约 2m15s |
| H5 全量，约 1500 文件 | 约 1m07s |
| 后端全量，约 6500 个 `.cs` | 约 4m10s |

全量扫描主要用于人工摸底和审计，不在日常提交路径。

---

## 存量基线

| gate | 指标 | 数值 | 日期 |
|---|---:|---:|---|
| gxgk_pc | 组件收敛问题 | 43 | 2026-08-26 |
| gxgk_pc | 行尾混用 | 20 | 2026-08-26 |

新客户首次纳入时优先使用 `legacy_baseline` 过渡，清理存量后再转严格。

⚠ 2026-09-21 现状（总数 2026-09-23 加 `keji_v3_backend` 后为 35 个 gate）：
那 **34 条业务 gate 全部是 `legacy_baseline: true`**（含 12 个老仓，
另 1 条是与客户仓无关的 `harness_self` 自检），
所以「转严格」目前是**待决事项**而不是某几个仓的现状描述；而「清理存量后再转」这一步
**现在才有数据基础** —— 存量命中是本轮才进 `gate_log.jsonl` 的（此前被按行过滤丢弃），
清没清干净要拿台账数说话。详见 `CHANGELOG.md` 的 `[Unreleased]` 那两条订正。

---

## 规则能力边界

门禁中的多数规则是**启发式规则**，目标是挡住已知事故，不是证明代码绝对正确。

可以机械识别：

- 单行反模式，如 `console.log(`、硬编码内网 IP、`new XxxRepository(`。
- 行尾、编码、SFC 结构等结构化问题。
- 组件收敛和已知废弃组件引用。

“识别”不等于“必然阻断”：命中级别由规则和 gate 配置决定。例如 `new XxxRepository(` 当前是观察项；
编码规则在 legacy 仓对存量 tracked 文件也可能降级，具体以对应 gate 输出为准。

**降级判据里有一处「读不到 ≠ 没动过」的盲区**（评审 2026-09-23 M12）。上面那些降级都建立在
「本次没动过 ⇒ 命中是存量」之上，而判据只看到一个 `changed_lines is None`，它有两种正好相反的成因：

| 成因 | 事实 | 按存量降级 |
|---|---|---|
| 两处 diff 都成功、正文为空 | 文件**本次真的没动** | ✅ 正解 |
| git 探针失败 / 没装 git / diff 超时 | **根本没读成**，不是"读到了空" | ❌ 把「查不出来」当成「查过了、没问题」 |

后一种在缺省档下静默降级（只在 stderr 告警一次），4 处降级判据（`rule_base.Rule` /
`WholeFileRule` / `backend_ruleset._BackendRule` / `encoding_ruleset.EncodingRule`）同时中招。
设 `HARNESS_GIT_REQUIRED=1` 即为 fail-closed 档：**读不到 diff 时不再按存量降级**。
⚠️ 本档**缺省不开**，与 Node.js 那行（提交期默认 `HARNESS_NODE_REQUIRED=1`）**不同**：
diff 传感器一旦坏掉，`cli.py check <某文件>` 这类存量体检会把该文件的**历史**违规整份报成 ❌
（整文件口径 + 不降级），在存量尚未清零的仓里等于把提交彻底堵死、且堵得毫无信息量；
node 那条只影响「本次改动文件的语法检查」，坏掉时"没查"能被人一眼看出。
所以它是「怀疑传感器坏了时手动打开」的档，不是日常默认。真·非 git 目录**不触发**本档
（`git rev-parse` 明确答"不是工作树"是合法输入，与探针故障分档）。

可能漏检：

- 跨多行调用。
- 别名导入。
- 生成代码。
- 框架变体。
- 字符串拼接。

### 已实测的具体边界

上面那五条是分类；下表是**逐条跑过**的具体样例（`checks/*_ruleset.py` 里的
`heuristic = True` 就指这里）。每行都由 `tests/test_review_v13_batch4.py` 执行验证 ——
断言的是"这条现在**确实**不报"。这不是替漏检找理由：一旦有人收紧了正则，
该测试立刻变红，逼人回来更新本表。边界只写在代码注释里，等于没人知道。

| 规则 | 不报的写法 | 原因 |
|---|---|---|
| PC/H5-CONSOLE | `console["log"]("x")` | 模式认的是 `.log(`，下标取值形态不匹配 |
| PC/H5-IP | `//192.168.1.1/x`（协议相对） | 模式要求 `http(s)://` 前缀 |
| PC-AXIOS | `const a = require("axios")` | 只认 ESM `import` 语句（本仓是 ESM） |
| PC/H5-TODO | `TODO: 见知识库` | **有意保留**：约定允许引用知识库的 TODO |
| PC/H5-VFOR | v-for 之后 300 字符内**任一** `:key` | 窗口内不区分属主，兄弟元素的 `:key` 也算数 |
| PC-VALIDATE | `const f = formRef.value; f.validate((valid) => {})` | 只认 `X.value.validate(` / `this.$refs.X.validate(` 两种**直取**形态，中转变量不看语义（2026-10-07 实测不报） |
| PC-VALIDATE | `<eagle-form :ref="name">`（动态 ref） | ref 名要能从模板字面量里抽出来才配得上对；动态 ref 无从判定（实测不报） |
| 全部单行正则 | 跨多行调用 / 别名导入 / 生成代码 | 按行匹配，不看语义 |
| **全部 `_RegexScriptRule` 子类**（PC/H5 正则卫生规则） | **存量行**（改动前就有的命中） | **增量语义**：`changed_lines` 非空时**只拦新增行**，存量不拦提交；为空（全量扫描 / 新建文件）才退回整文件计数。→ 判「门禁 ⟷ 文档冲突」前必须先读基类，否则会凭空造出矛盾 |

**末行与前六行不是一类**：前六行是「正则不够严」（收紧模式即修），末行是「**生效范围**」（改的是判据语义，不是模式）。
它的两半各有一条锁：`tests/test_entity_standards.py::test_changed_lines_only_block`（存量不拦）、
`tests/test_entry_level.py::test_full_scan_reports_untouched_lines`（全量退回整口径）。

2026-09-16 这一轮把四处**零判断成本**的绕过堵掉了：`console.log (`（中间一个空格）、
`https://192.168.1.1`、`Todo`/`ToDo`（原先只认全小写）、
`import axios, { X } from "axios"`（原先要求 `axios` 与 `from` 紧邻）。
四处**都只放宽匹配面**，除 TODO 一处外都是严格超集（旧写法能命中的一个不少）。

TODO 那处唯一的放松是 `todo: 见知识库` 这种"全小写 + 知识库例外"的写法：
旧的两段式 `\btodo\b|TODO(?!:.*知识库)` 让例外只对"恰好大写且恰好带冒号"的行生效，
与 README 写明的约定不符，现统一为「不分大小写 + 同一例外」。
实测 22 个端仓 `src` 下这类行 **0 处**（`git grep -E "\btodo\b"` 命中里含"知识库"的为 0），
放松没有实际影响；收紧侧则是实打实的新增命中，数量见下。

### 待裁定：组件引用的路径写法（2026-09-16 实测）

**这不是理论绕过，而是本仓组件引用的主要形态。** 组件收敛规则（PC-COMP / H5-COMP）从
`<eagle-x />`、`'eagle-x'` 两种形态取名，**看不见路径写法**：

```ts
// utils/registerGlobComp.ts —— 决定全站有哪些全局组件的那个文件
import EagleTabsNew from "@/components/eagle-tabs-new.vue";   // eagle-tabs-new 在 DENY 里
import EagleMenuProject from "@/components/eagle-menu-project.vue"; // eagle-menu-project 在 DEAD 里
```

全仓实测（`git ls-files src` 范围内的 .vue/.js/.ts，按"路径写法引用了 DENY/DEAD 组件"计数）：

| 仓 | 命中 | 集中位置 |
|---|---|---|
| gxgk_pc / ntrx_pc / keji_pc / c712v3_pc / ZhongXin_pc | 53–55 | `utils/registerGlobComp.ts` |
| HanBoGaoXin_pc | 53 | 同上 |
| zhongdj_pc / c712_pc | 8 | `registerGlobComp.ts` + `views/layout/*.vue` |
| hy_pc / hy_base_pc / hy_hzy_pc | 11 | `views/layout/*.vue`（`eagle-menu` 等 DEAD 组件） |

⚠ **2026-09-18 复量更正：下面这段此前的结论有一半不成立。**

上表数的是"路径写法的**出现次数**"，它回答不了另一个量：**该组件是否已被裸形态抓到**。
逐文件复量（`reports/_comp_path_scan.py`，纯只读、不写台账）后：

- `utils/registerGlobComp.ts` 那 53–55 处**不是逃逸**。该文件同时写着
  `app.component("eagle-tabs-new", EagleTabsNew)` —— **注册文件必然这么写**，
  旧规则在那一行就抓到了。实跑该文件**今天就是红的**（rc=1，报出 eagle-tabs-new /
  eagle-radio-new / eagle-date-new / eagle-checkbox-group-new / eagle-menu-project /
  eagle-radio-img / eagle-table-image-column-new）。故接线对这类文件 **Δ=0**；
  本节原先那句"改坏 `registerGlobComp.ts` 里的组件引用不会被拦"**是错的，已删**。
- **真盲区在另一类**：`views/layout/*.vue` 与 `views/modules/**` —— 只有路径写法、
  旧形态一个都看不见（hy_pc / hy_base_pc / hy_hzy_pc 各 5 个文件；gxgk_pc / c712v3_pc /
  hanbogaoxin_pc / keji_pc / ntrx_pc / zhongxin_pc 各 37 个；c712_pc / zhongdj_pc 各 4）。
- **范围收窄**：8 个 H5 gate 实测净新增 **0 处** —— 本条只涉及 PC，与 H5-COMP 无关
  （原写"PC-COMP / H5-COMP"不准）。

★ **但真盲区的后果不是"抓住了逃逸的违规"，而是"名单本身可能不对"**：净新增命中的组件是
`eagle-menu` / `eagle-menu-common` / `eagle-menu-index` / `eagle-menu-project` /
`eagle-page-tag`（**DEAD**，其定义写的是「**零引用**死组件，建议删除」）与
`eagle-page-new`（DENY）—— 而它们正被上述**业务文件**引用着。
**这与 `eagle-choose-tag` 是同一个 bug**（allowlist 自记：2026-09-15 因"hy PC 仓实测
30 处引用（非死组件）"把它移出 dead，原因是原判定「引用仅1」的**数据源过时**）：
引用计数看不见路径写法，于是把"用着的"判成了"死的"。**故次序应是先修引用计数、据此重判
名单，再谈接线**；直接接上等于拿假前提拦真代码 —— gxgk_pc / hy_pc / hy_base_pc / hy_hzy_pc
四个 `mode: strict` 的 gate 会真拦，合计 52 个文件
（**上界**：未逐文件核对它们是否已因其它规则而红）。

⚠ **订正（2026-09-21）**：原写「四个 strict 且 `legacy_baseline=false` 的 gate」——
**`legacy_baseline=false` 这个前提已经一个都不剩**。实测 `config/harness.yaml`：35 个 gate
里那**全部 34 条客户仓 gate**（不含引擎无条件注入的 `harness_self`）的 `legacy_baseline`
**都是 `true`**（12 个老仓在 2026-09-21 那批里与另外 21 个对齐，
见 `CHANGELOG.md` 的 `[Unreleased]`），`mode: strict` 仍在。而组件白名单命中的
`ComponentRule` 是一条 **ERROR 级整文件规则**、**不参与按行过滤**，在 `legacy_baseline: true`
下它对**非新建文件**降为 INFO。所以这 52 个文件的既有命中现在是**进台账而不拦提交**
（真拦不拦得住，要看它们是否另外命中了**行级**规则）。两条口径合起来的意思是：
**「接线」的阻断力比这段原文写的要小** —— 要它真拦得住，得先修引用计数、
再把名单重判对，光"把规则接上"不够。

**未完成（待补）**：「到底哪些组件真的在被引用」这一环节**耗时较长，尚未做** ——
故 DENY/DEAD 名单本轮**不动**。留档：`reports/_comp_path_scan.py`（可重跑的只读探针）、
`reports/_comp_path_scan.txt`（逐 gate 净新增清单）。

故本轮**只测量、不动手**：接线与否由仓库负责人定，定了再改（改动落在
`checks/pc_ruleset.py` / `checks/h5_ruleset.py` 的 `_extract_components`，本表随之更新）。

上线验收或对外证明时，不要只引用“规则已启用”。至少同时保留：

```bash
python <HARNESS>/sandbox/acceptance_check.py <gate>
```

它把违规探针、合规反锁、台账记录与 hook 活性放在同一条验证链里。

---

## 详细文档

| 文档 | 内容 |
|---|---|
| `README_推广接入.md` | 同事接入完整说明 |
| `docs/新同事接入指南.md` | **从零接入的导览** —— 把 README / INSTALL / sandbox 脚本串成一条线，卡住了看它 |
| `docs/新同事接入引导.html` | **同一件事的图解版**（单文件、不联网，浏览器直接开）：多出「八家 AI 工具逐一接线」与「黄金范式 / 门禁规则怎么加、加完怎么生效」两块，**要动手改引擎就看它** |
| `docs/packaging/INSTALL.md` | 安装、升级、卸载 |
| `docs/packaging/RUNBOOK.md` | 日常排障、回滚、对账 |
| `docs/packaging/COMPAT.md` | 客户端兼容矩阵 |
| `docs/packaging/OPENCLAW.md` | OpenClaw 接入 |
| `docs/adr/0001-packaging-principles.md` | 打包与壳层原则 |
| `golden-patterns/index.md` | 黄金范式地图 |
| `AGENTS.md` | agent 工程地图 |
