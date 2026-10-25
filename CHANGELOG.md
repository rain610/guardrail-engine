# Changelog

版本号的单一来源是 `VERSION`。每次发版时，在升级 `VERSION` 的同一次提交里，把
`[Unreleased]` 中属于本版的内容移入 `## [<版本>] — <日期>` 段。

本仓库自 2026-10-20 起以 MIT 协议开源。此前为内部开发阶段，其详细记录不随本仓库
分发；本文件从首个开源版本起重新记录。

## [Unreleased]

（暂无）

## [0.2.0] — 2026-10-25

### Changed

- **项目改名 `guardrail-engine`（迁移期保留全部旧名）**：发行包 / 导入包为
  `guardrail-engine` / `guardrail_engine`，主命令 `guardrail-engine`；仓库、文档与
  MCP 服务名同步。旧名不会当场失效：`agentguard` 命令与导入包、`~/.agentguard`
  配置目录、`AGENTGUARD_*` 环境变量、`.agentguard-init.json` 标记、旧 MCP 键
  `agentguard` 都继续可用（新名优先，旧名兜底）。为了让老接入不断，`agentguard/`
  作为兼容壳随 wheel 一起分发，v0.3 移除。以下**保持不变**：`harness-ok:` 豁免标记、
  `harness/` `checks/` `config/harness.toml` 磁盘布局、hook 文件名
  `agentguard-gate.py`、Claude 插件 ID `agentguard@agentguard-tools`、OpenClaw 扩展
  `extensions/agentguard`。
- 自带引擎的 wheel 与离线安装包的目录名改为 `guardrail-engine-package-v<版本>`；
  `sandbox/pack_release.py` 产出的内容与校验口径同步。
- **调度层拆分**：`harness/run_gate.py` 只保留调度、台账与通知，配置体检拆到
  `harness/_validate.py`，引擎版本/内容哈希拆到 `harness/_engine_meta.py`；
  `_config.py` 的内置 YAML 兼容解析拆到 `_yaml_compat.py`、`ConfigError` 拆到
  `_errors.py`。对外行为与既有导入名不变（`run_gate._validate` 等仍可调用），
  变异锚点与受保护文件清单同步指向新模块。
- **测试按领域重组**：`tests/` 分为 `engine/`（引擎行为）、`packaging/`（打包安装升级）、
  `tooling/`（仓库工具）、`docs/`（文档一致性）、`review/`（历次评审回归）五个包，
  共享夹具 `_tmpclean.py` / `_engine_copy.py` 保留在根目录。`run_tests.py` 改为递归发现
  （只下钻含 `__init__.py` 的包），`tests/lock-inventory.json` 的模块键带组前缀；
  运行命令不变。

### Added

- **PyPI 可信发布（OIDC，不用长期 token）**：`release.yml` 新增 `publish` job ——
  `python -m build` 先打 sdist、**再从 sdist** 打 wheel（sdist 漏带引擎会当场失败），
  干净 venv 安装并 `init` 冒烟后由 `pypa/gh-action-pypi-publish` 上传。首次发布前需在
  PyPI 网页端登记 trusted publisher（见 `docs/packaging/RUNBOOK.md`）。
- **英文 README**：新增 `README.en.md`，与中文 `README.md` 互链。
- **外部分析器适配 gate（架构 A）**：新增 `checks/external_adapter.py` 与 `eslint_check.py` /
  `semgrep_check.py`，把 ESLint / Semgrep 的 JSON 结果归一成统一 `Finding`，复用同一条输出、
  `harness-ok:` 行内豁免、退出码与台账通道。两个 gate 默认 `enabled = false`；命令可用
  `AGENTGUARD_ESLINT` / `AGENTGUARD_SEMGREP`（普通命令或 JSON 数组）与
  `AGENTGUARD_ADAPTER_TIMEOUT` 覆盖。工具没装、超时、输出不是 JSON、ESLint 以配置错退出、
  Semgrep 报告分析错误 —— 一律 `rc=2`（fail-closed，「没跑成」不等于「通过」）。
  `mode` / `legacy_baseline` / `disabled_rules` 对适配 gate 不适用，规则取舍归工具自身配置。
  新增 19 条锁与 8 条变异（M146–M153）守住这些路径。
- **一键升级刷新引擎副本**：`upgrade.py` 新增 [5/7] —— 解包本包轮子自带的引擎，对 `agentguard init`
  复制出来的副本跑 `init --force`（源不依赖本机 pip 安装状态，`--skip pip` 也成立）；装完**逐字节
  对账**，旧副本改名归档可回退。只认 init 写下的所有权标记，仓库与来历不明的目录一律不碰。
- **引擎内容的可追溯性**（`Engine-Sha`）：带 `[harness]` 的提交在消息末尾声明本提交的
  `Engine-Sha: <16 位>`，CI 的 `provenance` job 按提交内容重算比对；`run_gate.py --engine-sha`
  是只读出口（配置坏也能回答）；`agentguard/cli.py log --verify --engine` 把台账里的
  `engine_sha` 与提交历史中声明过的集合对账 —— 只有"用从未提交的引擎跑出的判定"会红，
  正常升级不会被误报。
- **`sandbox/demo_gate.py`**：60 秒演示的 Python 版，PowerShell / cmd 下无需 `sh` 也能跑；
  同样先证明违规被拦、再证明修复后放行。
- **`.github/CODEOWNERS`**：把 `checks/`、`harness/`、`guardrail_engine/`、`agentguard/`、`config/`、
  锁清单与 `.github/` 指给维护者；README 说明还需在 GitHub 分支保护里开启
  `Require review from Code Owners` 才真正阻断合并。
- **可选规则集登记**：`checks/encoding_ruleset.py`（编码卫生）在 README 写明接线方式与两个
  易漏点（`raw=` 原始字节、`targeted=True`），模块 docstring 说明默认不注册的原因。

### Fixed

- **第四轮评审收尾（2026-10-26）**：
  - 测试重组留下的 `sys.path` 浅一层：`tests/engine/test_notify_throttle_concurrent.py` 现在
    显式指向工程根；新增 `-P`（不注入 cwd）导入锁，复现嵌入式 Python 的 `-m` 行为。
  - 陈旧 pyc 用例在「本平台不复用 (mtime, 字节数) 匹配的 pyc」时 `skipTest` 并写明原因，
    不再把平台差异记成失败（不是改成 expect B）。
  - `guardrail_engine/` 与兼容壳 `agentguard/` 纳入 `harness_guard` 受保护清单、CODEOWNERS
    与 `engine_sha` 内容面：改弱 CLI/MCP/`run`/`done`/`log --verify` 现在可被发现并追不到归属。
  - CODEOWNERS 的锁测试路径跟上重组（`tests/tooling/test_lock_inventory.py`）。
  - PyPI 长描述改用 `README.en.md`（仓库 README 仍是中文主文档）；`external_adapter` 的
    命令行描述、`_tmpclean` / 锁清单注释里的旧品牌与旧路径同步清理。
- **改名后的升级链路补齐**（全量套件实测）：pip 通道的产物对账同时扫
  `guardrail_engine/` 与兼容壳 `agentguard/`（旧机器上只有后者时不再读成「读不到产物」）；
  升级测试夹具补上新包、`integrations/guardrail-engine-skill` 源路径与新 init 标记
  `.guardrail-engine-init.json`；`harness.path` 指针按迁移期双读校验。
- **外部适配层封死三个边界**（2026-10-25 第三轮评审 §4.1 / §4.2 / §4.4）：
  Semgrep 分支现在与 ESLint 一样检查进程退出码（`rc >= 2` = 配置/环境错误，不再被一份
  合法空 JSON 洗白）；工具返回相对路径时按 `repo` 拼接后再读豁免行（`harness-ok:` 恢复
  生效）；`--mode` 旗标被接收并忽略，不再以 argparse 参数错误 `rc=2` 退出；同时 `run_gate`
  对适配 gate 不再写 `mode_override` / `weakened` 台账字段（它们没有 strict/loose 可放宽，
  记了等于伪造「本次判定被削弱」）。新增 4 条锁与 M151 / M152 / M153 三条变异。
- **外部适配 gate 默认不再挂 `pre_commit`**：外部工具读工作区，提交期存在「暂存坏版本 +
  工作区改回好版本」的绕过路径（评审 §4.3）。默认只挂 `post_edit`，README 与模块文档写明
  该边界；要提交期拦截需在调用方实现暂存副本。
- **对外宣称收敛**：README 改为「退出码语义 / 豁免语法一致，严重级按工具映射」，并补
  「外部 gate 不参与静态覆盖度」与 2.4KB 全局守则的出处（评审 §4.5 / §5.2）。
- **`test_lock_inventory` 的 pyc 探针不再依赖 `-c` 的 cwd / `PYTHONPATH` 语义**：用 argv
  把模块目录显式塞进子进程 `sys.path`，嵌入式 Python 下不再把「模块找不到」误读成锁失败
  （评审 §5.1）。`checks/registry.py` 头部同步为「四个 gate」。
- **`--validate` 不再对损坏的本地覆盖层报「全部健康」**：覆盖层解析失败会记成程序化信号，
  配置体检据此 `rc=2` 并点名文件与原因（单次 gate 运行仍用共享配置继续，不阻断提交）。
- `test_lock_inventory` 的陈旧字节码探针显式注入 `PYTHONPATH`：在 `PYTHONSAFEPATH=1`
  等 cwd 不在 `sys.path` 的环境下不再把「import 不到模块」误读成锁失败。
- **release / nightly 的干净 venv 冒烟改为验证 wheel 真的自带引擎**：先 `agentguard init` 到临时目录、
  检查所有权标记，再在新引擎上取版本 —— 原先指向 checkout，轮子里没有引擎也能过。
- **`harness/hooks-dir/` 的两个分发器改为 100755 入库**：POSIX 上 git 直接 exec hook，100644 会被
  **静默忽略**（提交照常放行），Linux CI 与用户机器上的 hook 实际从未生效；Windows 检出看不出差别。
  新增索引 mode 看守，防止再次退回去。
- **干净环境下第二次 `agentguard init` 不再误报「源=目标」**：引擎源改为优先取自本文件的安装位置
  （`<引擎根>/agentguard/..`），不再依赖 `find_root()` —— 首次 init 写完指针后它会解析到刚装出的副本，
  于是幂等重跑按重叠拒绝（本机有 `harness.local.toml` 时先命中配置，所以只在 CI 暴露）。
- 测试夹具改用仍存在的 `checks/example_code_check.py`：原先指向已删除的 `checks/pc_code_check.py`，
  在带 Node 的 CI 上 M10/M11 拿到 `rc=2`（配置错）而不是「门禁拦住」。
- `test_init_bundle` 的幂等用例改用**冻结的引擎源**：并行套件里
  `TestSentinelBlocksDirtyEngine` 会故意往工程仓探针文件追加一行，活树当源时两次 init 之间
  源被改过 → 误报「内容不同」（全量并行跑实测复现）。
- 文档与元数据订正：README 的安装首步改为 `pip install .`（PyPI 未发布）、补 Git Bash
  前置条件与 `RULESETS` 登记说明、快跑耗时改为实测区间；`pyproject` 描述改为 wheel 自带引擎。
- **变异冒烟给「杀整树」补上 POSIX 半边**：`M143` 撤掉的是 Windows 分支的 taskkill `/T`，
  在 Linux 上走的是 killpg、该分支永不执行 —— Nightly L2 因此把它误报成「变异后仍然绿」。
  `M143` 现声明 `blind_on: posix`，并新增互补的 `M145`（把 killpg 退化成 proc.kill，Windows 上盲），
  两条互相兜底；平台盲区名单同步登记，仍由 `test_lock_inventory` 钉死「没有一条护盾两个平台都瞎」。

### Changed

- `agentguard init` 在副本根写下 `.agentguard-init.json`（kind / version / source），供升级识别
  所有权；幂等重跑会给旧版 init 装出来的副本补写。
- **配置主格式迁移 TOML**：`config/harness.toml` + `config/harness.local.toml`，用标准库
  `tomllib` 解析；旧版 `.yaml` 仍可读，两种格式并存时 TOML 优先并出声提示被忽略的那份。

## [0.1.0] — 2026-10-20

开源首发（发布以 `v*` tag 为准）。

### Added

- **开源发布**：MIT 许可证；移除业务侧组件与宿主专有引用，规则、示例与文档改为通用形态。
- **零第三方依赖**：引擎层（`checks/`、`harness/`、`agentguard/`）只使用 Python 标准库。
- **CI**（`.github/workflows/ci.yml`）：Python 3.11 / 3.12 / 3.13 矩阵；零依赖静态扫描；
  快速与全量测试；`ResourceWarning` 句柄泄漏判据。
- **Release**（`.github/workflows/release.yml`）：tag 与 `VERSION` 一致性校验 → 全量测试 →
  变异冒烟（验证测试锁真的会红）→ 打包 wheel 与离线安装包 → 真产物校验 →
  干净 venv 安装冒烟 → 创建 GitHub Release。
- **Nightly**（`.github/workflows/nightly.yml`）：每天定时跑全量套件与同样的慢闸门，
  让只在发布时才跑的东西每天都被看见。
- **通用门禁框架**：配置驱动的 gate 清单、`strict` / `loose` 策略、行内豁免、
  台账与链式校验；`run_gate.py` / `auto-gate.py` / `cli.py` / MCP 四个入口；
  附 3 条示例规则（`NO-CONSOLE` / `NO-HARDIP` / `EOL-UNIFIED`）。

### Changed

- 共享配置只声明仓库键名；各仓库在本机的路径放在 git 忽略的本地覆盖层。
- 测试套件按文件并行执行，提供 `--fast`（编辑回路）与 `--full`（提交 / CI）两档。
