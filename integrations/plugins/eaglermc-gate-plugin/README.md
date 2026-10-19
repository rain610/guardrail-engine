# eaglermc-gate（生态插件原型 · Claude Code 形态）

把 eaglermc_harness 统一门禁装进 Claude Code：**一个插件 = MCP 工具（判定）+ 使用技能（说明书）**。
提交期硬兜底仍由 git 钩子负责（与插件无关；两者共用同一份引擎与同一本台账）。

## 安装（当前原型形态）

- 离线校验：`claude plugin validate <此目录> --json`
- 会话加载：`claude --plugin-dir <此目录>`
- 市场分发形态（规划）：见主仓 `docs/packaging/PRODUCTIZATION.md`

## 它接了什么

| 组件 | 内容 |
|---|---|
| `.mcp.json` | 启动 eaglermc-gate MCP 薄服务（单文件零依赖）。工具：`harness_check_file` / `harness_run_gate` / `harness_list_gates` / `harness_summary` / `harness_coverage` / `harness_doctor` |
| `skills/eaglermc-harness/` | 使用规范：何时跑、怎么读裁决、修复建议、安全边界（从主仓 `integrations/eaglermc-harness-skill` 同源复制） |
| `.claude-plugin/plugin.json` | 插件清单（version 取自主仓 `VERSION`，单一来源） |

## 限制（诚实版）

- `.mcp.json` 里的 python / 引擎路径**要么是占位符、要么是打包机的**，取决于这个目录怎么来的：
  - `build_plugin.py` 默认**按机器打戳**（配置里是本机绝对路径）——本机自用、`--plugin-dir` 直接加载；
  - 内部安装包走 `--keep-placeholders`，`.mcp.json` 里留 `__PY__` / `__MCP__` / `__HARNESS__`，
    装机时必须跑一次包内 `fix_paths.py` 填成本机路径（**不能跳**，见包内「安装说明.md」第 0 步）。
- 所以：**这个目录不能整体拷给别人用**。换机器要重新构建或跑适配脚本。
- 宿主内的安装/市场分发未在本轮验证——验证状态见主仓 `docs/packaging/COMPAT.md`。
- 插件只读调用门禁；`accept`/改基线等"人的动作"永远不会出现在工具面里。
