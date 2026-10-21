---
name: agentguard
description: "改受管仓库代码时用：跑统一门禁（MCP 工具 / CLI / auto-gate 任一入口），改码后必须过门禁才能算完成。触发词：门禁、代码规范检查、提交被拦、黄金范式。"
---

# agentguard —— 统一门禁使用规范（跨 Agent 通用）

> **路径说明（分享给同事时必读）**：本文中的 `<HARNESS>` 指 agentguard 工程根目录。
> 取值二选一：① 设环境变量 `AGENTGUARD_DIR=<你的路径>`（旧名 `HARNESS_DIR` 兼容）；② 在工程里执行 `sh <HARNESS>/sandbox/where.sh` 直接打印。
> 门禁脚本内部一律用相对路径定位资源，**clone 到任何目录都能跑**；只有本机的编辑器/客户端钩子配置需要写绝对路径。

适用于任何接入了 AgentGuard 的代码仓库。规则是代码库的硬边界，不是建议。

## 改码前先加载黄金范式（关键）
写/改代码前，先读**黄金范式**里已验收的写法，照抄而不是凭空写：
- 入口：`<HARNESS>/golden-patterns/index.md`（按任务列出可照抄骨架：请求层 / 列表页 / 表单弹窗 /
  后端分层 / 校验 / 日志 / 配置，以及业务端到端范本）
- 完整参考实现：`<HARNESS>/golden-patterns/reference/`；正面样板会跑示例 gate 自证。
- 被门禁拦下时，命中输出里的 `📖 See:` 会直接给出一份范式的仓库相对路径 —— 顺着它读。
- 团队自己的范式放在 `golden-patterns/patterns/<端或主题>/` 或 `golden-patterns/endtoend/` 下，
  规则类上写 `see = "golden-patterns/…"` 即可对接；造新范式先读 `templates/00_范式制造模板.md`。
- 范式是"参照系"，具体字段/参数以真实代码为准，别当教条硬套。

## 什么时候跑门禁
- 改完任何受管仓库的代码（新建 / 修改 `.vue` / `.js` / `.ts` / `.cs`）后。
- 提交代码前（git pre-commit 钩子会强制拦一遍，但 agent 应主动先跑，别靠钩子兜底）。
- 用户要求「过一下门禁 / 检查代码规范」时。

## 怎么跑（三选一，**同一份引擎**，结果一致）

### 入口 1：MCP 工具（宿主已接入 agentguard 时首选）
若宿主 MCP 列表里有 `agentguard`，直接用工具（不用碰终端）：
- `harness_check_file` {path} —— 改完某个文件立刻查（自动匹配所属 gate）
- `harness_run_gate` {repo: "<端仓路径>"} 或 {gate: "<gate名>", files: [...]}
- `harness_summary` / `harness_coverage` —— 看判定台账与规则覆盖度
- 返回文本里含 `rc=` 与裁决词：PASS / BLOCKED / CONFIG_ERROR / NOT_MANAGED

接入（给同事/宿主配置）：`python <HARNESS>/agentguard/mcp.py --print-config`
会打印开箱即用的 JSON 配置；然后把它复制进宿主的 MCP 配置即可。

### 入口 2：CLI（任何终端可用）
```
python <HARNESS>/agentguard/cli.py check <你改过的文件>
python <HARNESS>/agentguard/cli.py run <gate名> [文件...] [--all]
python <HARNESS>/agentguard/cli.py quickstart        # 一次性打印全部接入方式
```

### 入口 3：原命令（兜底，等价）
在改动的端仓库根目录下执行：
```
python <HARNESS>/harness/agents/auto-gate.py
```
或显式指定仓库：
```
python <HARNESS>/harness/agents/auto-gate.py --repo <端仓库绝对路径>
```
全量扫描加 `--all`。

== 重要 == 不要用 `$PWD` 传目录给 `--repo`——git-bash 下是 `/d/...`，匹配不上。
直接 cd 到仓库根跑即可（脚本用 os.getcwd() 自动识别）。

## 退出码语义（三个入口一致）
0 = 门禁通过
1 = 门禁拦截（修复后重跑；MCP 文本里是 BLOCKED）
2 = 配置 / 定位错误（看输出；MCP 里是 CONFIG_ERROR）
3 = 非受管仓库（跳过即可；MCP 里是 NOT_MANAGED）

## 门禁拦截时怎么办
- 读 check 输出里的「❌ 什么错 / ✅ 怎么改」，修复到通过，不要绕过。
- 紧急可 `git commit --no-verify`，但要**先留痕**：`cli.py bypass --reason "≥10 字原因"`，
  并且事后主动向用户/负责人说明。
- 不要删端仓库的 pre-commit 钩子（会被 harness_guard 拦截）。

## 合规写法被拦时：行内豁免（唯一合法出口）
有些规则机械判不出来（例：某台设备的固定地址由硬件决定，配置里改不了）。这时**不要改规则、
不要删代码绕过**，在命中行的**本行或紧邻上一行**加标记：
```js
// harness-ok: NO-HARDIP 设备发现地址由硬件铭牌固定，不可配置
const DEVICE_PROBE = "192.168.100.1"
```
- 规则 ID 必须写对（写成别的规则名不生效——设计上不允许一句话静默所有规则）
- 理由写在标记后面：标记会留在 diff 里，人工复核看得见
- 只在"确实是合规写法"时用；能改代码改合规就别加标记
- **先看这条规则对当前仓库是否适用**（仓/客户专属规则会用 `disabled_rules` 关掉）
- 被一条明显不适用的规则拦住 → 报给 harness 负责人改配置，别硬加标记

## 门禁什么时候会自动跑（run_on）
`config/harness.yaml` 里每个 gate 有 `run_on: [pre_commit, post_edit]`，是**真开关**：
- `post_edit`＝AI 改完文件的编辑期检查（auto-gate 走这个）。没声明它时编辑期不跑，**不是你写错了，是策略如此**
- `pre_commit`＝提交时强制拦（端仓 `core.hooksPath` 共享钩子）
- 别指望"编辑期没报＝能提交"：**提交时永远会检查**

## 判定台账（可审计，出问题先看它）
- 每次判定都会追加进 `reports/gate_log.jsonl`：时间 / 来源入口（hook/cli/mcp）/ 引擎版本 / gate / 结果 rc / 规则级统计
- 查看：`python <HARNESS>/agentguard/cli.py log --last 50`（表格；`--export out.csv` 可导出）
- 或问 MCP：`harness_summary` / `harness_coverage`
- 台账写入失败**不会**反拦门禁（日志绝不挡提交），属预期行为

## 任务完成前收尾（一条命令，所有 agent 必做）
改完代码，**用一条命令收尾**（跑门禁 + 打可验证凭证，2026-10-15 起）：

```sh
python <HARNESS>/agentguard/cli.py done --repo <端仓库根>
```

> 在端仓根目录直接跑可省 `--repo`（按**当前目录**识别仓库）；凭证里的 `repo=` 字段
> 就是它实际扫的仓——识别错了一眼可核。

它跑门禁 → 从台账回读本次记录 → 打出一行**凭证**（直接粘进你的回复）：

```
[harness] gate=my_project repo=/path/to/project rc=0 files=2 ts=2026-10-16T10:31:22 h=3fa9c2d1
```

- 凭证里的 `h` 是**台账行的链哈希前 8 位**：你编不出对得上的 h，用户却可以一条命令复核：
  `python <HARNESS>/agentguard/cli.py log --gate <gate名> --last 1`；
- `rc=3`（非受管仓库）不产凭证，属正常跳过；
- 没跑门禁就交任务 = 没完成 —— 对 Hermes 等**没有编辑期钩子**的 CLI agent 尤其如此。

## 引擎自检（改 harness 自身后必跑）
- 套件：`python -m unittest discover -s tests`（改动 checks/harness 后必跑）
- 配置体检：`python harness/run_gate.py --validate`（改完 harness.yaml 跑；CI 用 `--structure-only`）
- 零依赖守卫：`tests/test_stdlib_only.py`——引擎层只许标准库

## 规则与范式从哪看
- gate 策略：`<HARNESS>/config/harness.yaml`
- 规则实现：`<HARNESS>/checks/`（规则集与对应入口脚本）
- 黄金范式：`<HARNESS>/golden-patterns/index.md`
- 工程地图：`<HARNESS>/AGENTS.md`

## 记住
- 改 harness 自身（checks/、config/、**agentguard/** 打包壳）属维护，commit 消息要带 `[harness]` 标记。
- 规则对齐真实架构；有歧义问用户，不要自己猜业务逻辑。
- 三个入口（MCP / CLI / git 钩子）共用同一份引擎与同一本台账——结果不一致就是 bug，报 harness 负责人。
