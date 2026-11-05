# Contributing to Guardrail Engine

> 本文是给外部贡献者的入口。重点：**如何加一条规则并让它过变异冒烟** —— 这是把高门槛变成护城河入口的那份文档。

---

## 贡献前必读

### 这个项目是什么

Guardrail Engine 是一个**零依赖的代码门禁框架**。它不提供即装即用的规则集（那是 gitleaks / pre-commit 生态的事），而是给你：

- 规则执行平面（pre-edit / post-edit / pre-commit 三时机）
- tamper-evident 台账（行链哈希 + engine_sha 归因）
- 变异冒烟自证（证明规则真的有牙齿）
- 覆盖度自评（哪些规则真触发过、哪些一次没响）

### 什么贡献会被接受

| 类型 | 接受度 | 说明 |
|------|--------|------|
| Bug 修复 | ✅ 高 | 先开 issue 讨论，附最小复现 |
| 新规则（通用型） | ✅ 中 | 必须过变异冒烟 + 误报边界文档化 |
| 新规则（业务特定） | ❌ 低 | 请 fork 后自建规则集，不要合入主线 |
| 文档改进 | ✅ 高 | 中英文同步，别只改一份 |
| 重构 | ⚠️ 先讨论 | 大重构可能破坏 engine_sha 归因链 |
| 新增依赖 | ❌ 拒绝 | 引擎层零依赖是硬约束（外部适配器可选且默认关闭） |

### 什么贡献会被拒绝

- 引入第三方依赖到引擎层（`checks/` / `harness/` / `guardrail_engine/`）。
- 弱化受保护文件的完整性检查（`harness_guard` / `protected-patterns.txt`）。
- 删除或跳过变异冒烟测试。
- 改 `engine_sha` 计算逻辑但不更新台账归因。

---

## 开发环境搭建

### 前置条件

- Python 3.11+
- Git 2.x
- Bash（Windows 用 Git Bash）

### 克隆与安装

```bash
git clone https://github.com/rain610/guardrail-engine.git
cd guardrail-engine
pip install -e .              # 开发模式安装
python sandbox/doctor.sh      # 端到端自检
```

### 跑测试

```bash
# 快跑（1099 例，约 90 秒）
python sandbox/run_tests.py --fast

# 全量（含变异冒烟，约 40 分钟）
python sandbox/run_tests.py

# 单测
python -m unittest tests.engine.test_checks -v
```

### 跑门禁

```bash
# 配置体检
python harness/run_gate.py --validate

# 全量扫描
python harness/run_gate.py example --all

# 查看 engine_sha
python harness/run_gate.py --engine-sha
```

---

## 如何加一条规则

### 第一步：确定规则类型

| 类型 | 放哪 | 例子 |
|------|------|------|
| 通用规则（跨项目适用） | `checks/example_ruleset.py` 或新建 `checks/<your_ruleset>.py` | `NO-CONSOLE`、`NO-HARDIP` |
| 团队特定规则 | fork 后自建规则集 | 业务字段命名规范 |

### 第二步：写规则实现

所有规则继承 `checks/rule_base.py` 的 `Rule` 基类。示例：

```python
from checks.rule_base import RegexRule

NO_TODO = RegexRule(
    id="NO-TODO",
    pattern=r"\bTODO\b",
    message="禁止 TODO 注释残留",
    fix_hint="实现或删除 TODO",
    severity="block",           # block / warn / info
    see="golden-patterns/patterns/cross-cutting/todo-policy.md",  # 可选：命中时指路
)
```

**关键约束：**

- `id` 必须全局唯一（台账按它归因）。
- `severity` 三态：`block`（拦截）、`warn`（提示）、`info`（观察）。
- `see` 指向黄金范式（可选但推荐）—— 被拦的人能顺着手势读到「照这个写」。
- 误报边界必须文档化（示例规则的 docstring 演示了怎么写）。

### 第三步：写测试

每条规则至少需要：

1. **正向测试**：合法代码必须放行。
2. **反向测试**：违规代码必须拦截。
3. **边界测试**：字符串字面量里的 `TODO`、注释里的 `TODO`、多行块注释的续行。

示例：

```python
# tests/engine/test_no_todo.py
import unittest
from checks.example_ruleset import NO_TODO

class TestNoTodo(unittest.TestCase):
    def test_blocks_todo_in_code(self):
        result = NO_TODO.check("TODO: fix this", "test.js")
        self.assertTrue(result.blocked)

    def test_allows_todo_in_string(self):
        result = NO_TODO.check('const msg = "TODO: implement"', "test.js")
        self.assertFalse(result.blocked)  # 字符串里降级为 warn

    def test_allows_todo_in_comment(self):
        result = NO_TODO.check("// TODO: later", "test.js")
        self.assertFalse(result.blocked)  # 注释里不判
```

### 第四步：过变异冒烟

**这是把高门槛变成护城河入口的关键步骤。**

变异冒烟回答：「如果有人在生产代码里写了 `TODO`，你的规则真的能拦住吗？」

做法：

1. 在 `tests/tooling/test_lock_inventory.py` 的 `LOCKS` 字典里登记你的规则：

```python
LOCKS = {
    "NO-TODO": {
        "module": "checks.example_ruleset",
        "test": "tests.engine.test_no_todo",
        "mutations": [
            {
                "id": "M999-no-todo-pattern-weakened",
                "file": "checks/example_ruleset.py",
                "line": 123,  # pattern = r"\bTODO\b" 那行
                "change": r'r"\bTODO\b"',  # 原内容
                "to": r'r"TODO"',  # 弱化：去掉词边界
                "expect": "test_blocks_todo_in_code",  # 弱化后应该失败的测试
            },
        ],
    },
}
```

2. 跑变异冒烟：

```bash
python sandbox/mutation_smoke.py --only=M999-no-todo-pattern-weakened
```

3. 期望结果：

- 对照（未变异）必须绿。
- 弱化 `pattern` 后，`test_blocks_todo_in_code` 必须红（因为去掉词边界会误报更多）。
- 如果弱化后仍然绿 → 你的规则断言不够强，回去改测试。

**变异冒烟的哲学：** 不是「测试覆盖率高」就够了，而是「测试真的能发现规则被弱化」。这是 tamper-evident 的核心 —— 台账记录每次判定，变异冒烟证明判定逻辑没被偷偷改弱。

### 第五步：登记到配置

在 `config/harness.toml` 的 `[gates.example]` 段添加你的规则：

```toml
[gates.example]
rules = ["NO-CONSOLE", "NO-HARDIP", "EOL-UNIFIED", "NO-TODO"]
```

跑配置体检：

```bash
python harness/run_gate.py --validate
```

必须 `rc=0`。

### 第六步：提交

**提交消息纪律：**

- 改 `checks/` / `harness/` / `guardrail_engine/` 的文件 → 提交消息必须带 `[harness]` 标记。
- 带 `[harness]` 的提交 → 消息末尾必须加 `Engine-Sha: <16 位哈希>`。
- 哈希值取自 `python harness/run_gate.py --engine-sha`（每次改完代码重算）。

示例：

```bash
git add checks/example_ruleset.py tests/engine/test_no_todo.py
git commit -m "$(cat <<'EOF'
feat(rules): 新增 NO-TODO 规则（禁止 TODO 注释残留）

- 规则实现：checks/example_ruleset.py
- 测试：tests/engine/test_no_todo.py（正向 / 反向 / 边界）
- 变异冒烟：M999-no-todo-pattern-weakened（弱化词边界后测试必红）
- 误报边界：字符串字面量降级为 warn，注释里不判

Engine-Sha: abc123def456789
EOF
)"
```

CI 会重算 `engine_sha` 并与提交消息里的值比对 —— 不一致即失败。这是 tamper-evident 的最后一环。

---

## 提交前检查清单

```bash
# 1. 快跑测试
python sandbox/run_tests.py --fast

# 2. 配置体检
python harness/run_gate.py --validate

# 3. 门禁自检
python harness/agents/auto-gate.py

# 4. （可选）变异冒烟单条
python sandbox/mutation_smoke.py --only=<your-mutation-id>
```

全部 `rc=0` 后再提交。

---

## 代码风格

- **零第三方依赖**：引擎层只用 Python 标准库。
- **UTF-8 兜底**：文件头强制 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（GitHub Desktop 等 GUI 触发 hook 时控制台可能是 GBK）。
- **行尾**：Unix LF（`.editorconfig` 已声明）。
- **注释**：只写 WHY，不写 WHAT（命名应该自解释）。
- **测试命名**：`test_<what>_<when>`（如 `test_blocks_todo_in_code`）。

---

## 文档贡献

### 中英文同步

- `README.md` / `README.en.md` —— 必须同步改。
- `docs/*.md` —— 优先英文，中文可选。
- `golden-patterns/` —— 中文为主（团队示例），英文摘要可选。

### 文档质量标准

- ❌ 出现盘符、家目录、内网路径（换台机器就断）。
- ❌ 复述代码（应该指向代码）。
- ❌ 只写「应该分层」不写文件放哪（agent 无法执行）。
- ✅ 读者只看这一篇，就能执行成功。

---

## 评审流程

1. 开 PR 前先在 issue 里讨论（大改动尤其如此）。
2. PR 描述必须包含：
   - 改了什么（文件清单）。
   - 为什么改（问题描述或需求来源）。
   - 怎么验证（测试命令 + 期望结果）。
3. CI 必须全绿（快跑测试 + 配置体检 + 变异冒烟）。
4. 至少一位维护者 review。

---

## 受保护文件

以下文件改动需要 `[harness]` 标记 + `Engine-Sha` 尾注：

- `checks/*.py`
- `harness/*.py`
- `guardrail_engine/*.py`
- `agentguard/*.py`（兼容壳）
- `harness/protected-patterns.txt`
- `config/harness.toml` / `config/harness.yaml`
- `.github/workflows/*.yml`
- `pyproject.toml` / `setup.py` / `VERSION` / `MANIFEST.in`

完整清单见 `harness/protected-patterns.txt`。

---

## 行为准则

本项目采用 [Contributor Covenant](CODE_OF_CONDUCT.md) 4.0。参与即视为同意遵守。

---

## 资源

- [README](README.md) —— 快速开始与核心价值。
- [SECURITY.md](SECURITY.md) —— 漏洞报告通道。
- [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md) —— 威胁模型与信任边界。
- [CHANGELOG.md](CHANGELOG.md) —— 版本历史。
- [golden-patterns/index.md](golden-patterns/index.md) —— 黄金范式索引。
