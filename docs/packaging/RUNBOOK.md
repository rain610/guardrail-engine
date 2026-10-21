# 打包接入 Runbook（agentguard · 推荐版）

> 读者：要在一台机器 / 一个端仓接入 agentguard 门禁的人（**不需要读过任何其他文档**）。
> 一页速查 → 三入口 → 台账 → 旁路 → 回滚 → 排障 → 已知限制 → 演进建议。
> 路径约定：`<HARNESS>` = agentguard 引擎根。两种来源都成立：
> ① `pip install agentguard && agentguard init` 之后是 `~/.agentguard/engine`；
> ② clone 源码时是工程根（如 `D:/Rain/project/agentguard`）。
> ① 的形态下，下文 `python <HARNESS>/agentguard/cli.py …` 都可换成 `agentguard …`。

---

## 0. 一页速查（"装一次即可用"）

```bash
# ⓪ 只有 pip 包时：先把随包引擎落到本机（clone 源码的跳过）
pip install agentguard && agentguard init

# ① 自检（3 秒，任何目录）——同时打印全部接入方式
python <HARNESS>/agentguard/cli.py quickstart

# ② 让 agent 能直接用（MCP：一行配置）
python <HARNESS>/agentguard/cli.py mcp --print-config
#    Claude Code:  claude mcp add agentguard -- "<python>" "<HARNESS>/agentguard/mcp.py"
#    OpenClaw:     openclaw mcp add agentguard --command "<python>" --arg "<HARNESS>/agentguard/mcp.py"
#    Codex:        codex mcp add agentguard -- "<python>" "<HARNESS>/agentguard/mcp.py"

# ③ 提交期硬兜底（每个端仓执行一次）
python <HARNESS>/agentguard/cli.py hooks install --repo <端仓路径>

# ④（可选）agent 侧技能包
python <HARNESS>/agentguard/cli.py skill install --target claude   # 或 opencode / codebuddy

# ⑤ 第一次判定
python <HARNESS>/agentguard/cli.py check <你改过的文件>
```

判读结果：`rc=0` 通过；`rc=1` 拦截（按输出里的 ❌ 修复）；`rc=2` 配置/定位错误；`rc=3` 非受管仓库。

> 交付自检：文档/证据出现乱码类字符时，用 `python <HARNESS>/sandbox/check_mojibake.py` 扫描（U+FFFD / PUA / 双层编码 token；2026-09-14 起随证据一起入库）。

---

## 1. 三个入口（同一份引擎，结果一致）

| 入口 | 用什么 | 适用场景 |
|---|---|---|
| **MCP 工具** | `harness_check_file` / `harness_run_gate` / `harness_list_gates` / `harness_summary` / `harness_coverage` / `harness_doctor` | 宿主已配置 agentguard 时，agent 会话内直接调用，无需终端 |
| **CLI** | `python <HARNESS>/agentguard/cli.py run/check/summary/...` | 人工终端、脚本、CI |
| **git 钩子** | `core.hooksPath` 共享分发器（安装见 §0-③） | **硬兜底**：agent 侧什么都不装，提交也会被拦 |

> 三入口共用同一判定内核与同一本台账；出现结论不一致 = bug，直接报 harness 负责人。

## 2. 判定台账（可审计）

- **门禁台账**：`reports/gate_log.jsonl`。每条含：`ts`(时间) / `entry`(来源入口: hook|cli|mcp) / `ver`(引擎版本) / `gate` / `scope` / `files` / `rc` / `ms` / `rules`(规则级统计)。
  - `skipped: true` = 本次**没有执行任何规则**（gate 未启用）。它与「跑了且通过」都是 `rc=0`，靠这个字段区分（评审 v13 中 15）。
  - `weakened: [...]` = 本次判定被**允许的**手段削弱过，只在发生时出现：`local_disabled`（本机 `config/harness.local.toml` 把该 gate 关掉了）/ `policy_override:gates.<gate>.<字段>`（`HARNESS_ALLOW_POLICY_OVERRIDE=1` 放行了策略覆盖）。一条 `grep weakened reports/gate_log.jsonl` 即可捞出全部被削弱的判定。
  - 两个字段干净跑时**都不写**（每行都挂空标记等于没标记）；`cli.py log` 表格的 `mark` 列把它们渲染成 `skip` / `weak:…`，`harness/run_gate.py --summary` 也会单独报「跳过 N 次 / 被削弱 N 次（哪些 gate）」。
- 查看：`cli.py log --last 50`（表格）｜导出：`cli.py log --export out.csv --format csv|json`｜聚合：`cli.py summary`｜覆盖度：`cli.py coverage`。
- **运维台账**：`reports/ops_log.jsonl`（安装/卸载/旁路声明等动作），查看：`cli.py ops`。
- **写入失败策略**：台账写入失败**不会**反向阻拦门禁（设计如此，日志绝不挡提交）；但会在 `cli log` 里显示"不可解析"行数供排查。

### 本机对账（审计线索，2026-09-14 起 / 整改 A1）

用「提交 × 台账」对账，找出本机作者「有代码改动、但提交时刻（前 15 分钟窗口内）没有门禁通过记录」的提交：

```bash
python <HARNESS>/sandbox/audit_reconcile.py            # 全部端仓，近 14 天
python <HARNESS>/sandbox/audit_reconcile.py --since 7d --gate example
```

产物 `reports/reconcile_<时间戳>.md`：四分类（✅ 有记录 / ◻ 早于台账期 / ⚠ 无记录 / ⛔ 存在非通过记录）+ **全部受管仓**的钩子体检 + `.git/config` 修改时间佐证。
「几个仓」不写死 —— 那是**每机事实**（仓路径在 gitignored 的 `config/harness.local.toml` 里，各人不同），
要看本机清单：`python <HARNESS>/harness/run_gate.py --list`。
口径提醒：产物是**审计线索**不是判罪（台账写失败本身是静默的）；「早于台账期」「配置修改于提交之后」的标注用于区分接线过渡期的历史提交。

## 3. 紧急旁路（仅限紧急，必须留痕）

```bash
python <HARNESS>/agentguard/cli.py bypass --repo <端仓> --reason "≥10 字的原因（为什么、给谁看）"
git -C <端仓> commit --no-verify -m "<提交说明>"
```

- `bypass` 会把"声明旁路"写入运维台账并打印可见提示（人员/时间/理由/真实命令）。
- ⚠️ **定性：`bypass` 是"声明留痕入口"，不是权限闸**（评审 2026-09-23 L19）—— 它不拦任何提交、
  也不改变门禁判定，任何调用方都能声明它。它买到的只有"可见、可审计"这一件事。
- 注意：`--no-verify` 本身无法被钩子拦截；台账记录的是你的**声明**。事后必须向负责人说明。
- 不要用旁路替代修复；连续旁路会出现在台账里，可被审计。

## 4. 卸载与回滚

**整机退出（一键）**：安装包内 `python uninstall.py`（预演）→ 加 `--yes`（执行）——把技能副本 / 提交期钩子 / Claude 插件 / OpenClaw 扩展 / pip 包 / 编辑期 shim 逐项撤回（对照包内「安装说明」四）。以下为按单项的细粒度命令：

```bash
# 提交期钩子：卸载（核心 = 撤回 core.hooksPath；不删除任何文件）
python <HARNESS>/agentguard/cli.py hooks uninstall --repo <端仓>

# 技能包：卸载（改名归档，可逆：<target>.disabled-<时间戳>）
python <HARNESS>/agentguard/cli.py skill uninstall --target claude

# 编辑期 shim：卸载（两支文件改名归档，可逆；7 家客户端的编辑期门禁入口）
python <HARNESS>/sandbox/install_shim.py --uninstall

# MCP：在宿主侧移除
#   claude mcp remove agentguard ｜ openclaw mcp unset agentguard ｜ codex mcp remove agentguard

# OpenClaw 扩展：删除 ~\.openclaw-autoclaw\extensions\agentguard → 重启桌面端（或 uninstall.py 自动移出归档）
```

**归档的代价**（别只看"可逆"两个字）：改名归档**不删文件**，所以每退一次就永久留下一份完整副本
（技能是一整棵目录树）。反复「卸载 + 重装」只涨不落 —— `cli.py skill status` 会列出残骸路径与份数，
确认不要了手工删 `<技能名>.disabled-*` 即可（门禁只归档，不替你删）。
**编辑期 hook 的配置要另删**：shim 归档后客户端 hook 仍指向那个路径 → 到各宿主配置里删掉该 hook
（`integrations/<客户端>/README.md`），否则编辑期门禁静默失效（与装不上时同一个失效模式）。

回滚验证：卸载后 `git commit` 应立即恢复正常（不再触发门禁）；`hooks status` 应显示"未设置"。
遗留检查：若 `.git/hooks/pre-commit` 仍存在（旧拷贝模式残留），工具不会代删，请人工确认后处理。

## 5. 排障（症状 → 原因 → 处置）

| 症状 | 原因 | 处置 |
|---|---|---|
| 提交"好像没被检查"、无任何输出 | hooksPath 未设置 / 指向不存在目录（git 静默跳过） | `git config core.hooksPath` 自查；`cli.py hooks install --repo <仓>`；对照 `README.md` 的三态说明 |
| 提交被拦但看不懂 | 正常拦截 | 按输出 ❌ 修复；拿 `cli.py log` 看这次判定的规则明细 |
| rc=2 配置/定位错误 | gate 登记或路径问题 | `python <HARNESS>/harness/run_gate.py --validate` |
| MCP 工具在宿主里不出现 | 配置未生效/命令写错 | `openclaw mcp probe agentguard` / `claude mcp list` / `codex mcp list` 自查；看服务 stderr |
| 控制台乱码/崩溃 | GBK 控制台 | 三个壳都带 UTF-8 兜底；其他入口见 `doctor` 第 1 节检查 |
| 文档/证据出现乱码字符（� / 锟斤拷 类） | 采集管道编码损伤（PS 管道按 cp936 解码 / Windows CLI 的 GBK 输出被按 UTF-8 采集） | 采集时：python 直接写 UTF-8 文件、CLI 输出 cp936 回退解码；提交前：`python sandbox/check_mojibake.py` 自检（本行示例字符为说明用途，mojibake-ok） |
| hooks 报 `python: command not found` | PATH 无 python | 把 python 加进 PATH；或改用 `cli.py` 直跑验证后再装钩子 |
| 疑似误报 | 规则边界 | 优先"行内豁免"（`harness-ok: <规则ID> <理由>`，见技能包《行内豁免》节）；确实不适用该仓的规则 → 报负责人改 `disabled_rules`，**不要**硬加标记 |

## 6. 插件形态（Claude Code / OpenClaw，可选）

插件是第三层接入形态（安装载体）。Claude 兼容 marketplace（`agentguard-tools`，含 `agentguard`）
已在开发机完成**双宿主实测**：Claude Code strict 校验 0 错误并安装 enabled；OpenClaw 已按 bundle 完成安装（技能 + MCP；agent 会话实测可直接调用）。**OpenClaw 完整步骤见 `OPENCLAW.md`。**

构建（换机器时重新打戳）：

```bash
python <HARNESS>/integrations/plugins/build_marketplace.py --harness <HARNESS> --out <DIR>
```

整个离线安装包（wheel + marketplace + 安装说明 + SHA1SUMS + zip）一键重建：

```bash
python <HARNESS>/sandbox/pack_release.py
```

接入（按宿主二选一）：

```bash
# Claude Code
claude plugin marketplace add <DIR>
claude plugin install agentguard@agentguard-tools

# OpenClaw
openclaw plugins marketplace list <DIR>
node <openclaw.mjs> plugins install agentguard --marketplace <MKT>   # 完整步骤/坑：OPENCLAW.md
```

升级（拿到新包后）：指向仓库的接入（MCP / git 钩子 / 编辑期 shim）**无需处理**；CLI / 插件 /
技能等副本类按 `docs/packaging/INSTALL.md`「五、升级」重跑对应安装命令即可（「原地升级」指
**包目录位置不变**，不是"安装动作可以省"——每次换包都要重跑一次 `fix_paths.py`）。
**不要为了升级跑 `uninstall.py`**：那是"退出"用的，跑完门禁会关着一段，提交钩子还得逐仓重装。

移除（可逆）：

```bash
claude plugin uninstall agentguard
claude plugin marketplace remove agentguard-tools
```

> 构建产物含本机绝对路径，不入仓库（仓库只存模板 + 构建器）。
>
> 平台边界：在受管运行时（某些桌面端宿主）上，宿主插件安装需**由人执行**——agent 发起会被写权限边界拦截。

## 7. 已知限制（诚实版）

- pip 包形态**已提供**（`pyproject.toml` + `[project.scripts] agentguard`，随离线安装包分发 wheel）；
  未提供的是**公开发布到 PyPI**，分发走 Release / 离线安装包（见 README「打包与分发」）。
- Claude 插件原型按机器打戳（配置含绝对路径），宿主市场分发形态未验证（见 `COMPAT.md`）。
- `bypass` 只记录"声明"，无法强制拦截 `--no-verify`（需钩子侧配合，属后续增量）。
- MCP/CLI 的**持久化回归测试已入库**（`tests/test_agentguard.py`，10 例：协议 fixtures / 工具行为 / 三入口一致性）。
- 台账写入失败**不阻断**门禁（设计如此，日志绝不挡提交）；2026-09-16 起会在 stderr 打一行
  `[run_gate] ⚠ 判定台账写入失败…`，不再完全静默（此前只有一个 `except: pass`，写失败了没人知道）。

## 8. 后续演进建议

1. ~~补 `tests/test_agentguard.py`~~ —— **已完成（2026-09-16 入库，10 例）**。
2. ~~M1：`pyproject` + `agentguard` 全局命令~~ —— **已完成**（`pyproject.toml` + `[project.scripts]`，保留零依赖）；
   余下：M3 技能版本对齐与更多宿主安装器；M4 插件市场形态。
3. OBS-1 行尾策略（`.gitattributes` 脚本类 `eol=lf`）与 CI `workflow_dispatch` 首跑（见评审 v7 §4）。
4. 台账可加"规则版本 → 变更日"映射，便于跨版本对比（评审 v6 Q2 的延伸）。
