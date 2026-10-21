# Changelog

版本号的单一来源是 `VERSION`。每次发版时，在升级 `VERSION` 的同一次提交里，把
`[Unreleased]` 中属于本版的内容移入 `## [<版本>] — <日期>` 段。

本仓库自 2026-10-20 起以 MIT 协议开源。此前为内部开发阶段，其详细记录不随本仓库
分发；本文件从首个开源版本起重新记录。

## [Unreleased]

## [0.1.0] — 2026-10-20

开源首发（发布以 `v*` tag 为准）。

### Added

- **开源发布**：MIT 许可证；移除业务侧组件与宿主专有引用，规则、示例与文档改为通用形态。
- **零第三方依赖**：引擎层（`checks/`、`harness/`、`agentguard/`）只使用 Python 标准库。
- **CI**（`.github/workflows/ci.yml`）：Python 3.11 / 3.12 / 3.13 矩阵；零依赖静态扫描；
  快速与全量测试；`ResourceWarning` 句柄泄漏判据。
- **Release**（`.github/workflows/release.yml`）：tag 与 `VERSION` 一致性校验 → 全量测试 →
  变异冒烟（验证测试锁真的会红）→ 打包 wheel 与内部安装包 → 真产物校验 →
  干净 venv 安装冒烟 → 创建 GitHub Release。
- **Nightly**（`.github/workflows/nightly.yml`）：每天定时跑全量套件与同样的慢闸门，
  让只在发布时才跑的东西每天都被看见。
- **通用门禁框架**：配置驱动的 gate 清单、`strict` / `loose` 策略、行内豁免、
  台账与链式校验；`run_gate.py` / `auto-gate.py` / `cli.py` / MCP 四个入口；
  附 3 条示例规则（`NO-CONSOLE` / `NO-HARDIP` / `EOL-UNIFIED`）。

### Changed

- 共享配置只声明仓库键名；各仓库在本机的路径放在 git 忽略的本地覆盖层。
- 测试套件按文件并行执行，提供 `--fast`（编辑回路）与 `--full`（提交 / CI）两档。
