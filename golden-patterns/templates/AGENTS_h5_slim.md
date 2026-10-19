# AgentGuard H5 端（uni-app + Vue 2.6）—— AI 编码规则地图

> **这份文件不是某个仓的 AGENTS.md**，而是**端级知识**：所有 H5 客户仓共用的那一份。
> 客户仓的 `AGENTS.md` **用指针指向本文件**，不要把它复制进仓里（复制 = 漂移源）。
> 核心原则：**先循框架，后循先例，再谈自主；门禁是硬边界，不是建议。**
> 客户独有知识（本客户特有的 API / 构建 mode / 知识库位置）**不在本文件**，在你那个仓的 `AGENTS.md`。

## 改码前：加载知识（按需，不要全读）

| 任务 | 必读 |
|---|---|
| 新增/改列表页、状态卡 | `golden-patterns/h5/h5_list_status_card.md` |
| 底部常驻操作栏、固定按钮 | `golden-patterns/h5/h5_constant_button.md` |
| 该用哪个组件 / 某组件怎么调 | `golden-patterns/h5/组件手册/索引.md` → `组件手册/h5/eagle-*.md`（**24 份**） |

范式位置（独立门禁工程，所有客户通用）：
```
<HARNESS>/golden-patterns/h5/
```

## H5 端级事实（本端全客户通用 —— 这才是这份文件存在的理由）

1. **Vue 2.6 Options API**：不用 Composition API / `<script setup>`；生命周期用 `onLoad/onShow`；
   单位用 `rpx`，不用 `px`
2. **安卓 WebView 壳限制（本端最高频返工原因）**：功能入口必须是**常驻显式按钮** ——
   原生层拦截长按与 popup，依赖 popup/长按才出现的入口在原生端**弹不出来**
3. **列表用 `eagle-page` / `eagle-page-plus`**（自带分页 + 下拉刷新），不要手写滚动加载
4. **先找本仓同类已验收页面做参照**，禁止自由发明结构（先循先例）
5. 多环境用 `.env.{mode}` 区分；构建 `npm run build:h5 -- --mode <客户>`

## 机械规则：正文在 `checks/`，这里只给规则 ID

**规则正文只有一份，在 `<HARNESS>/checks/h5_ruleset.py`** —— 这里不抄第二份。
被拦时门禁会把规则 ID 直接打给你，对着下面这张表认：

| 规则 ID | 拦什么 |
|---|---|
| `H5-CONSOLE` | `console.log` 残留 |
| `H5-IP` | 硬编码 IP |
| `H5-TODO` | `TODO` 残留 |
| `H5-COMP` | 组件收敛（deny / dead / 平行版） |
| `H5-VFOR` | `v-for` 缺 `:key` |
| `H5-SYNTAX` | 语法校验 |
| `H5-CRLF` | 行尾污染 —— 期望行尾**由门禁按 gate 自动判定**（`eol_source`） |
| `H5-UPLOAD` | 上传须走签名 —— **仅 gxgk 适用**，其余客户在 config 里用 `disabled_rules` 关掉 |

⚠ **不要在任何 md 里写死「行尾必须是 LF」（或 CRLF）**：本端 10 个仓里 7 个写 LF、2 个写 CRLF
（剩 1 个没提）—— **同一个端两种互相矛盾的说法**，因为行尾本来就是**仓级事实**，由门禁自己探。
这类"抄进 md 的机器事实"是本文档要消灭的头号对象。

## 被拦了怎么办

按门禁输出里的「❌ 什么错 / ✅ 怎么改」修复到通过，**不要绕过**。
确实是合规写法被误判时，在命中行**本行或紧邻上一行**加行内豁免
`// harness-ok: <规则ID> <理由>` —— **规则 ID 必须写对**（写错不生效，设计上不允许一句话静默所有规则）。
紧急 `--no-verify` 须先留痕并向负责人说明。

> 跑门禁的命令与退出码语义**不在这里重复** —— 那是**用户级全局守则**（`~/.agentguard/GLOBAL_AGENTS.md`，
> 各工具自动加载）的内容。若你的 agent 读不到它，跑 `sh <HARNESS>/sandbox/bootstrap_agent_env.sh`。
