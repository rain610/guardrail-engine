# integrations/plugins —— 生态插件原型（打包路线 · 激进版起点）

本目录是"把 harness 做成宿主插件"的第一块可运行原型，形态为 **Claude Code 插件包**
（OpenClaw 可直接消费 Claude 兼容插件/市场，详见主仓 `docs/packaging/`）。

| 文件 | 作用 |
|---|---|
| `agentguard-plugin/` | 插件模板（占位符版本：`__PY__` / `__MCP__` / `__HARNESS__` / `__VERSION__`） |
| `build_plugin.py` | 构建器：按机器打戳 + 从主仓同源复制技能，产出可直接加载的插件目录 |
| `verify_plugin.py` | 端到端自证：读 `.mcp.json` 声明的命令 → MCP 握手 → 真门禁判定（rc=1/BLOCKED） |
| `agentguard-marketplace/` | 市场模板（Claude 兼容：Claude Code 与 OpenClaw 双消费） |
| `build_marketplace.py` | 市场构建器（打戳 + 组装 + 打印双宿主接入命令） |

## 快速用法

```bash
# 1) 构建（输出到任意目录）
python integrations/plugins/build_plugin.py --harness <HARNESS> --out <OUT_DIR>

# 2) 真实校验（Claude Code 自带，离线）
claude plugin validate <OUT_DIR> --json

# 3) 端到端自证（不需要模型额度）
python integrations/plugins/verify_plugin.py --plugin <OUT_DIR>

# 4) 市场形态（Claude Code 与 OpenClaw 双消费；市场含本插件）
python integrations/plugins/build_marketplace.py --harness <HARNESS> --out <MP_DIR>
claude plugin marketplace add <MP_DIR> && claude plugin install agentguard@agentguard-tools
openclaw plugins marketplace list <MP_DIR>
```

## 原则

- 插件只是**安装载体**：不含任何门禁逻辑；逻辑永远只有一份（`harness/` + `checks/` 引擎）。
- 插件里的一切调用都走 MCP 薄服务（`guardrail_engine/mcp.py`），与 CLI、git 钩子共用同一判定内核。
- 新增任何"壳"都要进 `sandbox/list_entries.py` 的 ENTRIES 与 `tests/test_packaging_contract.py` 的契约锁（ADR-0001 铁律 2）。
