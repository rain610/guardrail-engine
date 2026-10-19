# agentguard-tools（Claude 兼容插件市场 · 内部）

本目录由 `build_marketplace.py` 构建生成，包含内部门禁插件 **agentguard**
（MCP 工具 + 使用技能；同源来自 agentguard 打包路线，壳只转发 —— ADR-0001）。

## 消费方式（双宿主，均已在开发机实测）

Claude Code：

```bash
claude plugin marketplace add <此目录>
claude plugin install agentguard@agentguard-tools
```

OpenClaw：

```bash
openclaw plugins marketplace list <此目录>
openclaw plugins install agentguard@agentguard-tools
```

## 说明

- **完全内部使用**（公司内网分发）；不含任何对外发布形态。
- 构建产物按机器打戳（配置含本机 python 绝对路径）；换机器请重新运行
  `python integrations/plugins/build_marketplace.py --harness <HARNESS> --out <DIR>`。
- 插件只读调用门禁；`accept`/改基线等"人的动作"永不出现在工具面里。
