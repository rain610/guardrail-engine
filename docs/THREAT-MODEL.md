# Threat Model & Trust Boundaries

> Guardrail Engine 的安全边界、威胁分类、设计取舍。
> 本文档是开源前 P0 交付物，回应评审建议 #6。

---

## 1. 系统边界

```
┌─────────────────────────────────────────────────────────┐
│  Engine Repo (guardrail-engine)                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐ │
│  │ checks/  │  │ harness/ │  │ guardrail_engine/     │ │
│  │ 规则实现 │  │ 调度层   │  │ CLI + MCP + 台账      │ │
│  └──────────┘  └──────────┘  └───────────────────────┘ │
│  ┌──────────────────────────────────────────────────┐   │
│  │ protected-patterns.txt  (受保护面唯一事实源)     │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
         │ 安装/引用          │ hook 复制
         ▼                    ▼
┌─────────────────────────────────────────────────────────┐
│  Business Repo (被管仓库)                               │
│  ┌────────────┐  ┌────────────┐  ┌──────────────────┐  │
│  │ .git/hooks/│  │ 业务代码   │  │ AGENTS.md /      │  │
│  │ pre-commit │  │            │  │ 行内豁免         │  │
│  │ commit-msg │  │            │  │ (harness-ok:)    │  │
│  └────────────┘  └────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Ledger (台账)                                          │
│  .guardrail/ledger.jsonl                                │
│  - 每次 gate 运行产出一行                               │
│  - 行链哈希（row-chain）保证序列完整性                  │
│  - engine_sha 归因到引擎版本                            │
│  - anchor SHA256 锚定首行                               │
└─────────────────────────────────────────────────────────┘
```

### 信任锚点

| 锚点 | 位置 | 作用 |
|------|------|------|
| `engine_sha` | 台账每行 + init marker | 归因到引擎代码快照；`log --verify --engine` 重算比对 |
| `protected-patterns.txt` | Engine Repo 根 | 受保护文件面的唯一事实源；`harness_guard` 与 `commit-msg` 都读它 |
| `harness_guard` hook | Engine Repo `.git/hooks/` | 阻断「改弱门禁 → 不提交 → 提交业务代码」攻击 |
| row-chain hash | 台账每行 `prev_hash` 字段 | 链式哈希，篡改任意行会断链 |
| anchor SHA256 | 台账首行 | 锚定整条链的起点 |

---

## 2. 威胁actors

### 2.1 意外型（Accidental）

**场景**：AI agent 写了不符合规则的代码（硬编码 IP、残留 console.log、行尾混用）。

**缓解**：
- pre-commit hook 自动拦截，agent 必须修复后才能提交。
- 规则输出指向黄金范式（`📖 See: golden-patterns/...`），agent 可自学修正。
- 行内豁免（`harness-ok: RULE_ID reason`）要求显式声明，避免静默放过。

**残余风险**：agent 可能写出规则未覆盖的坏代码。这是规则集厚度的问题，不是引擎缺陷。

### 2.2 疏忽型（Careless）

**场景**：开发者用 `git commit --no-verify` 跳过门禁。

**缓解**：
- 台账记录每次 gate 运行；`--verify` 可审计哪些提交没跑过门禁。
- CI 层可配置「无台账凭证的提交拒绝合并」（需团队策略配合）。

**残余风险**：**`--no-verify` 是 git 级逃生舱，无法在客户端阻止。** 这是设计取舍，不是漏洞。信任模型假设提交者有物理写权限，引擎的职责是*事后发现*而非*物理阻止*。

### 2.3 恶意型（Malicious）

#### 2.3.1 改弱门禁脚本

**攻击**：修改 `checks/*.py` 或 `harness/*.py` 让规则失效，但不提交改动，然后提交业务代码。

**缓解**：
- `harness_guard` 检测 Engine Repo 是否有未提交的受保护文件改动；有则阻断业务仓的 commit。
- 受保护文件面由 `protected-patterns.txt` 定义，覆盖所有规则实现与调度层。
- Engine Repo 自身的 `commit-msg` hook 要求 `[harness]` 标记 + `Engine-Sha` 尾注。

**残余风险**：攻击者若能直接修改 Engine Repo 的 `.git/hooks/harness_guard` 本身，可禁用保护循环。这需要本地写权限 + 对 hook 机制的了解。缓解：hook 脚本本身也在 `protected-patterns.txt` 里（`^harness/hooks-dir/(pre-commit|commit-msg)$`），但 `.git/hooks/` 下的副本不在 git 跟踪内——这是 git hook 机制的固有限制。

#### 2.3.2 篡改台账

**攻击**：直接编辑 `.guardrail/ledger.jsonl` 删除或修改历史记录。

**缓解**：
- row-chain 哈希：每行包含上一行的 hash，篡改任意行会断链。
- `log --verify` 重算整条链，发现不一致即报警。
- anchor SHA256 锚定首行，防止「删除旧行 → 重建链」攻击。

**残余风险**：攻击者可以*删除整个台账文件*然后从零开始。这需要配合删除 `.git/hooks/` 下的 hook 副本才能持续。缓解：CI 层可要求台账文件只能追加（git diff 检查 `ledger.jsonl` 的删除行数为 0）。

#### 2.3.3 替换安装副本

**攻击**：在 `pip install` 或 `git archive` 后、`init` 前，替换引擎文件。

**缓解**：
- init 时记录的 `engine_sha` 是引擎代码的快照指纹。
- `log --verify --engine` 重算当前引擎文件的 SHA 并与台账归因比对。
- 用户可在 init 后手动核对 `engine_sha` 与发布标签的已知值。

**残余风险**：引擎无法自证「安装副本未被篡改」——这是信任锚的起点问题。缓解：发布时在 GitHub Releases 附带 `engine_sha` 签名清单，用户 init 后比对。

---

## 3. Tamper-Evident ≠ Tamper-Proof

**核心设计取舍**：

| 性质 | 含义 | Guardrail Engine 做到？ |
|------|------|------------------------|
| Tamper-proof | 物理上阻止篡改 | ❌ 做不到（提交者有本地写权限） |
| Tamper-evident | 篡改后能被检测到 | ✅ 行链哈希 + engine_sha 归因 + anchor |
| Tamper-resistant | 增加篡改难度 | ✅ harness_guard 保护循环 + 受保护文件面 |

**为什么不做 tamper-proof？**

- 门禁工具运行在被管仓库的本地环境，提交者对文件系统有完全控制。
- 任何本地工具都无法阻止 `rm -rf .git/hooks/` 或二进制替换。
- 试图做 tamper-proof 会引入 rootkit 级复杂度，且仍不可靠。

**正确策略**：接受 tamper-evident，把「发现篡改」的能力做到极致：
- 行链哈希让单行篡改断链。
- engine_sha 让引擎版本漂移可审计。
- harness_guard 让「改弱门禁 → 提交业务代码」的攻击窗口收窄到「必须同时改 hook 副本」。
- CI 层可追加「台账只能追加」的 git diff 检查。

---

## 4. 信任边界清单

### 4.1 Engine Repo vs Business Repo

**边界**：Engine Repo 是「门禁代码」，Business Repo 是「被管业务代码」。

**信任假设**：
- Engine Repo 的维护者（你）是可信的。
- Business Repo 的提交者是不可信的（可能疏忽、可能被 AI 误导）。

**跨边界操作**：
- `sandbox/install_hooks.sh` 把 hook 脚本从 Engine Repo 复制到 Business Repo 的 `.git/hooks/`。
- 复制后，Business Repo 的 hook 副本**脱离 Engine Repo 的版本控制**——这是 git hook 机制的固有限制。
- 缓解：hook 脚本本体在 Engine Repo 受 `protected-patterns.txt` 保护，改动需 `[harness]` 标记。

### 4.2 共享配置 vs 本地配置

**边界**：`config/harness.toml` 是团队共享策略，`config/harness.local.toml` 是机器本地覆盖。

**信任假设**：
- 共享配置通过 git 版本控制，改动可审计。
- 本地配置不入 git，每台机器自管。

**风险**：本地配置可覆盖共享策略（如禁用某条规则）。

**缓解**：
- `run_gate.py --validate` 检查配置一致性。
- CI 层可拒绝「本地配置禁用 block 级规则」的提交（需团队策略配合）。

### 4.3 行内豁免 vs 规则判定

**边界**：`harness-ok: RULE_ID reason` 允许开发者声明「这条规则不适用于本行」。

**信任假设**：
- 豁免声明是开发者显式负责的行为。
- AI agent 不应自行添加豁免（AGENTS.md 已禁止）。

**风险**：agent 或开发者滥用豁免绕过规则。

**缓解**：
- 豁免必须在注释中、必须写规则 ID、必须写理由。
- 台账记录每次豁免命中，`log --verify` 可审计豁免频率。
- CI 层可配置「豁免率超过阈值报警」。

---

## 5. 攻击面总结

| 攻击 | 可行性 | 影响 | 现有缓解 | 需追加 |
|------|--------|------|----------|--------|
| `--no-verify` 跳过门禁 | 高（git 原生） | 中（单次绕过） | 台账审计 | CI 拒绝无凭证提交 |
| 改弱 checks/*.py 不提交 | 中（需改 Engine Repo） | 高（规则失效） | harness_guard 阻断 | — |
| 篡改台账 | 中（需本地写权限） | 高（审计失效） | row-chain + anchor | CI 检查台账只能追加 |
| 替换安装副本 | 低（需中间人） | 高（引擎被控） | engine_sha 归因 | 发布签名清单 |
| 删除 .git/hooks/ | 高（rm 即可） | 中（保护循环失效） | 无（git hook 固有限制） | 文档说明 + CI 兜底 |

---

## 6. 安全开发实践

### 6.1 维护者

- 每次发布附带 `engine_sha` 签名清单（GitHub Releases）。
- 定期跑 `log --verify --engine` 检查台账与引擎版本一致性。
- Engine Repo 的 `[harness]` 提交必须附 `Engine-Sha` 尾注（CI 已强制）。

### 6.2 用户

- init 后核对 `engine_sha` 与发布清单。
- 不要在 Business Repo 的 `.git/hooks/` 外手动修改 hook 脚本。
- 发现台账断链立即报告（见 SECURITY.md）。

### 6.3 团队策略

- CI 层配置「台账只能追加」的 git diff 检查。
- CI 层配置「无台账凭证的提交拒绝合并」。
- 定期审计 `harness-ok` 豁免频率，异常高则调查。

---

## 7. 已知未解决项

1. **`.git/hooks/` 副本的版本控制**：git hook 机制本身不支持 hook 脚本的版本追踪。当前依赖「Engine Repo 的 hook 本体受保护 + 用户不手动改副本」的信任假设。无完美解，只能文档说明 + CI 兜底。

2. **台账删除后重建**：攻击者可删除整个 `ledger.jsonl` 然后从零开始。row-chain 只能检测「链内篡改」，不能检测「链被替换」。缓解：CI 检查台账文件的 git diff 只能有新增行、不能有删除行。

3. **本地配置的信任**：`harness.local.toml` 不入 git，无法审计。若本地配置禁用 block 级规则，门禁会静默放过。缓解：`--validate` 检查 + 文档说明。

---

## 附录：术语表

| 术语 | 含义 |
|------|------|
| Engine Repo | guardrail-engine 工程仓库，存放规则实现与调度层 |
| Business Repo | 被管理的业务仓库，通过 hook 引用 Engine Repo |
| engine_sha | 引擎代码快照的 SHA256，用于归因与完整性校验 |
| row-chain | 台账的行链哈希机制，每行包含上一行的 hash |
| anchor | 台账首行的 SHA256，锚定整条链的起点 |
| harness_guard | 保护循环 hook，阻断「改弱门禁 → 提交业务代码」攻击 |
| protected-patterns.txt | 受保护文件面的唯一事实源 |
| tamper-evident | 篡改后能被检测到 |
| tamper-proof | 物理上阻止篡改（本引擎不承诺） |
