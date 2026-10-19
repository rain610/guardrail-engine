# OpenClaw 接入指南（agentguard）

面向内部：把 agentguard 门禁以「Claude Code 插件市场同款 bundle」的形式装入 OpenClaw；安装完成后，agent 会话即可直接调用门禁工具（技能 + MCP 双通道）。本文件是 RUNBOOK「客户端接入」章节的展开版：完整命令、验证与坑位以本文为准。

## 0. 前置条件

- 宿主机：Windows（本机验证环境；macOS / Linux 未验证）。
- Node：用桌面端自带运行时 `D:\Program Files\AutoClaw\resources\node\node.exe`。
- OpenClaw 入口：`D:\Program Files\AutoClaw\resources\gateway\openclaw\openclaw.mjs`。
- 两个关键环境变量只在桌面端进程里存在，shell 中默认为空，**必须先显式设置**：

| 变量 | 值 |
| --- | --- |
| `OPENCLAW_STATE_DIR` | `C:\Users\HP\.openclaw-autoclaw` |
| `OPENCLAW_CONFIG_PATH` | `C:\Users\HP\.openclaw-autoclaw\openclaw.runtime.json` |

## 1. 安装（主路：按市场安装）

```powershell
$env:OPENCLAW_STATE_DIR   = 'C:\Users\HP\.openclaw-autoclaw'
$env:OPENCLAW_CONFIG_PATH = 'C:\Users\HP\.openclaw-autoclaw\openclaw.runtime.json'
$node = 'D:\Program Files\AutoClaw\resources\node\node.exe'
$ow   = 'D:\Program Files\AutoClaw\resources\gateway\openclaw\openclaw.mjs'
$mkt  = '<本地市场根>\agentguard-tools-marketplace'
& $node $ow plugins install agentguard --marketplace $mkt
```

市场根包含 `.claude-plugin/marketplace.json` 与 `agentguard` bundle（技能 + MCP 服务定义），由以下命令生成：

```bash
python integrations/plugins/build_marketplace.py --harness <HARNESS 仓库根> --out <本地市场根>\agentguard-tools-marketplace
```

> 若使用「内部安装包」里的 marketplace：先运行一次
> `python "<包目录>\fix_paths.py" --harness "<你的 HARNESS 路径>"` 把包内配置适配到本机
> （包内为打包机打戳的绝对路径；从仓库自建 marketplace 无需此步）。

兜底路径（bundle 直装）：把 bundle 目录复制到 `~\.openclaw-autoclaw\extensions\agentguard` 后重启桌面端。

## 2. 验证

```powershell
& $node $ow plugins list
```

- 期望：`agentguard` 出现在扩展目录 `~\.openclaw-autoclaw\extensions\agentguard`，状态 enabled / loaded。
- agent 会话内实测两个探针：`harness_doctor` 返回 rc=0；`harness_list_gates` 返回 35/35（全部启用）。
  （本行是**随包出货**的文档，写的是"你应当看到什么"，所以必须是当下的真值；数字由
  `tests/test_docs_consistency.py` 的数字锁看住 —— 写成别的数当场红。）
- 已完成实测（2026-09-14）：技能与 MCP 均 AVAILABLE；记录见 `evidence/e1_openclaw_install.txt`。

## 3. 常见坑

1. **环境变量丢失**：`OPENCLAW_STATE_DIR` / `OPENCLAW_CONFIG_PATH` 不在用户级或机器级环境变量里（由桌面端进程注入）；shell 里直接跑安装命令会装错位置或报错——先显式设置再执行。
2. **市场参数**：优先 `--marketplace <市场根>`；路径含空格必须加引号（PowerShell 用单引号）。
3. **装完没生效**：重启桌面端或重开 agent 会话让扩展加载；仍有问题时先看 `plugins list` 的状态输出。

## 4. 卸载 / 回滚

- 一键（推荐）：安装包内 `python "<包目录>\uninstall.py" --yes`——会把本机各项接入（含本节扩展）逐项撤回；默认先预演、`--yes` 才执行。
- 手动（等价，最稳）：删除 `~\.openclaw-autoclaw\extensions\agentguard` 目录，重启桌面端。
- CLI 卸载：走 `plugins` 子命令，准确命令名以 `& $node $ow plugins --help` 输出为准。

---

维护：agentguard 打包路线（feat/packaging）；相关背景见 `RUNBOOK.md`「客户端接入」与 `COMPAT.md`。
