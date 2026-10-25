# 客户端接入兼容矩阵（guardrail-engine · 推荐版）

> 验证等级：**✅ 已验证（本轮实测）** ｜ **◐ 部分验证（见备注）** ｜ **✗ 未验证（不默认可用的默认项）**。
> 验证环境：Windows 11 · Git-for-Windows(MSYS bash) · Python 3.13 · 2026-09-14。
> 原始证据：`证据/client_registrations.txt`、`证据/verify_entries.txt`（本轮交付包）。

## 1. MCP 入口（`guardrail_engine/mcp.py` · 单文件 · 零第三方依赖）

| 客户端 | 接入方式 | 状态 | 验证内容 / 备注 |
|---|---|---|---|
| 任意 MCP 宿主（协议层） | 配置 `command` + `args` | ✅ | 严格握手（initialize → initialized → tools/list → tools/call）、6 工具清单、错误路径（-32601/-32602）、stdout 协议纯净性；夹具端到端 rc=1/BLOCKED |
| Claude Code 2.1.269 | `claude mcp add -s user` | ✅ | `Added stdio MCP server agentguard ... to user config`；`claude mcp list` 可见 |
| Claude Code 插件形态 | `.claude-plugin/plugin.json` + `.mcp.json` | ✅（校验级） | `claude plugin validate --strict`：**0 错误 / 0 警告**；端到端自证 PASS；会话级加载（`--plugin-dir`）与市场分发 ◐（未在本轮验证） |
| OpenClaw 2026.6.8 | 插件 bundle（`plugins install`，市场源） | ✅ | 2026-09-14 10:17 安装（用户侧执行）；inspect：Format: bundle / capabilities: skills, mcpServers；技能 `agentguard` 就绪；doctor 清洁；agent 会话实测可调用（AVAILABLE） |
| Codex 0.154.0 | `codex mcp add` | ✅ | `Added global MCP server 'agentguard'`；`codex mcp list` 可见 |

**注册命令实录（可直接复用 / 可逆）**。服务名统一用 `guardrail-engine`；迁移期旧键 `agentguard` 仍可读：

```bash
# OpenClaw（保存前自动 probe）
openclaw mcp add guardrail-engine --command "<py>" --arg "<HARNESS>/guardrail_engine/mcp.py" \
  --env GUARDRAIL_ENGINE_DIR=<HARNESS 正斜杠形式>
# 移除： openclaw mcp unset guardrail-engine

# Claude Code（user 级；注意 -e 是变参，注册时不要塞多个裸词）
claude mcp add -s user guardrail-engine -- "<py>" "<HARNESS>/guardrail_engine/mcp.py"
# 移除： claude mcp remove -s user guardrail-engine

# Codex
codex mcp add guardrail-engine --env GUARDRAIL_ENGINE_DIR=<HARNESS 正斜杠形式> \
  -- "<py>" "<HARNESS>/guardrail_engine/mcp.py"
# 移除： codex mcp remove guardrail-engine
```

## 2. CLI 入口（`guardrail_engine/cli.py`）

| 环境 | 状态 | 备注 |
|---|---|---|
| Windows 11 + PowerShell / cmd | ✅ | 全子命令实测（run/check/log/ops/skill/hooks/bypass/setup/quickstart） |
| Git-for-Windows bash | ✅ | hooks 安装/卸载、doctor 转发实测 |
| Ubuntu CI / Linux | ✅ | 代码全标准库、路径处理有跨盘兜底；**CI 首跑双绿**（py3.11 51s / py3.12 53s；run 34796814674，2026-09-14） |
| macOS | ✗ | 未验证 |

## 3. git 提交钩子（硬兜底，`core.hooksPath` 共享分发器）

| 场景 | 状态 | 备注 |
|---|---|---|
| Windows 端仓：违规拦截 | ✅ | 真 git 夹具：commit(bad) rc=1 + 修复指引；commit(good) rc=0 |
| Windows 端仓：一键安装/卸载/回滚 | ✅ | install → hooksPath 生效；uninstall → 恢复正常提交（不删除任何文件） |
| 未纳管仓库 fail-closed | ✅ | 既有能力（前几轮实测；本轮回归不变） |
| Ubuntu / 无 bash 环境 | ◐ | 钩子依赖 bash；Windows 无 bash 时 CLI 明确报错而非静默 |

## 4. 技能包（`integrations/guardrail-engine-skill`）

| 宿主 | 状态 | 备注 |
|---|---|---|
| claude / opencode / codebuddy 目录 | ✅ | 安装器在隔离 HOME 实测（安装/状态/卸载-改名归档） |
| 宿主内"技能被加载并执行" | ◐ | 本机三目录在位（doctor 既有检查通过）；完整行为闭环待宿主侧人工确认 |

## 5. 其它环境注意

- 控制台编码：三壳均带 UTF-8 兜底（`list_entries --check` 覆盖，36 个入口全绿）。
- 无 `python` 于 PATH 时：钩子链 fail-closed 报错（不会静默放行）；处置见 RUNBOOK §5。
- 本矩阵随每轮验证更新；新增客户端验证后同步修订本文件与交付报告。
