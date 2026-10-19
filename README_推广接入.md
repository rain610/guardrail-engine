# eaglermc_harness 推广接入指南

> **路径说明（分享给同事时必读）**：本文中的 `<HARNESS>` 指 eaglermc_harness 工程根目录。
> 取值按统一解析链（优先级从高到低）：
> ① 环境变量 `EAGLERMC_HARNESS_DIR=<你的路径>`（CI/测试覆盖；旧名 `HARNESS_DIR` 兼容）；
> ② `config/harness.local.yaml` → `harness:` 键（本机配置）；
> ③ `~/.eagle-agents/harness.path`（向后兼容）；
> ④ 从 cwd 向上找（兜底）。
> 不确定工程根时跑 `sh <HARNESS>/sandbox/where.sh` 直接打印。

> 本仓库是 Eagle RMC 多客户 × 三端（pc / h5 / backend）的统一代码门禁工程。
> 已纳管 **12 个客户分支**（gxgk / hy / hy_base / hy-hzy / **中新** zhongxin / ntrx / zhongdj / keji / keji_v3（**仅后端**）/ 712 / 712_v3 / HanBoGaoXin 瀚博高新）、**35 个 gate**（全部启用）。
> 本文档面向**需要接入本 harness 的同事**，3 步即可完成，无需理解内部实现。
>
> ⚠️ 上列是**客户 / 分支名**；写进 `config/harness.local.yaml` 时用的是**配置键名**
> （如 `c712_pc` / `hanbogaoxin_backend` / `hy_hzy_pc`）—— 照「712」「瀚博高新」去 local.yaml 里找是找不到的。
> 键名以 `config/harness.yaml` 的 `gates:` 段为准（全量键名都在那儿，值留空 = 这机器还没配），
> 本机路径示例见 `config/harness.local.example.yaml`。

---

## 目录

- [一、harness 由什么组成](#一harness-由什么组成你-clone-下来就有不用挑文件) —— clone 下来都有什么，不用挑文件
- [二、同事接入](#二同事接入3-步--1-步可选) —— **新同事从这条开始**
- [三、各 AI 工具如何"吃进"门禁](#三各-ai-工具如何吃进门禁无需你配) —— 接入模式（自觉 / 工具 / 强制）怎么选
- [四、常见误区澄清](#四常见误区澄清来自实测) —— venv / 绝对路径 / GBK 这些流传很广的说法
- [五、相对路径支持](#五相对路径支持可选增强) —— 同事和 harness 放同一父目录时可以不写绝对路径
- [六、门禁输出怎么读](#六门禁输出怎么读重要--拦截) —— **❌ 不等于拦截**，看输出前先看这节
- [七、常见问题 FAQ](#七常见问题-faq)
- [八、紧急绕过](#八紧急绕过)
- [九、验证清单（接入后自检）](#九验证清单接入后自检) —— 接完自查，全 ✅ 才算闭环
- [十、接手后怎么验门禁"真的在工作"（维护者视角）](#十接手后怎么验门禁真的在工作维护者视角) —— 维护者自查
- [附：git 提交期门禁的三种状态](#附git-提交期门禁的三种状态对照表) —— 「提交好像没被检查」按表定位
- [附：审计台账能证明什么](#附审计台账能证明什么证据强度声明) —— 对外表述别写过头

---

## 一、harness 由什么组成（你 clone 下来就有，不用挑文件）

| 组成 | 路径 | 作用 |
|---|---|---|
| 门禁入口 | `harness/agents/auto-gate.py` | 一行命令自动识别 gate、跑检查（任何 AI 工具通用） |
| 规则脚本 | `checks/*.py` | 各端（pc/h5/后端）规则与检查逻辑 |
| 配置（策略） | `config/harness.yaml` | gate 清单与策略；`repos:` 段**只留键名、值全空** |
| 配置（本机路径） | `config/harness.local.yaml`（你本机，git 忽略） | 各仓在**你这台机器**上的路径 |
| 配置读取 | `harness/_config.py` | 零依赖 mini-yaml + 本地覆盖层解析 |
| 黄金范式 | `golden-patterns/` | AI 写代码前的参照样板 |
| 工程地图 | `AGENTS.md` | 受管仓库与流程说明 |
| pre-commit 钩子 | `harness/hooks/pre-commit.py` | 提交前兜底（调用 guard + run_gate） |
| 一键接入 | `sandbox/setup_machine.sh` | 新机器自动装 hook（幂等），结尾自动跑 doctor 终检 |
| 接入自检 | `sandbox/doctor.sh` | 四项自检：UTF-8 兜底 / R9 双向探针（拦的是真违规不是崩溃）/ hook 活性（hooksPath→共享分发器 + gate enabled）/ skill 软链；可单独跑 |
| 装钩脚本 | `sandbox/install_hooks.sh` | 单仓接 `core.hooksPath`（worktree 见 FAQ-3；重跑幂等，会清理旧拷贝 hook） |
| 现场演示 | `sandbox/demo_gate.sh` | 一条命令演示"违规→拦截→修复→放行"全流程（分享时用） |
| 接入验证（多客户端） | `sandbox/verify_integrations.sh` | 真跑验证：让 AI 自己写违规文件看门禁拦不拦（Claude/OpenCode/CodeBuddy/Codex）+ 钩子契约 + 反向锁 + pre-commit 兜底 + 清场校验；结果见 `docs/2026-09-13_多客户端接入验证报告.md` |
| 各客户端接入说明 | `integrations/{claude-code,codex,opencode,codebuddy,trae-cn,qoder-cn,zcode}/README.md` | 放哪里（规则/插件/skills）+ 人工验证清单 + 能力定型；规则正文单一来源 = `integrations/agent-rule-snippet.md` |
| 分享 PPT | `docs/harness分享PPT.html` | 18 页 HTML 幻灯片（浏览器打开，←→ 翻页；重点页：第 5 页纯 AGENTS.md 对比、第 6 页为什么不直接用现成工具、第 8 页保护循环、第 11 页三种接入模式（含各型步骤）、第 12 页三种接入方式、第 15 页已知边界与下一步、第 16 页维护者导览；工具清单第 10 页、覆盖度第 13 页、耗时实测第 14 页） |
| 工程自检 CI | `.github/workflows/ci.yml`（唯一 CI） | 推代码后自动跑：零依赖守卫 / 配置体检 / 单元+链路套件（**端仓门禁不在 CI 里**，见 README.md「CI」节；内网 GitLab 侧无可用 runner，故不再保留 `.gitlab-ci.yml`） |

**依赖：纯 Python 标准库，无第三方包（不需要 pip install）。** Python 3.11+（引擎用到 `write_text(newline=)` 等 3.10+ API；开发/CI 环境为 3.11、3.12）。

---

## 二、同事接入（3 步 + 1 步可选）

> **零适配原则**：不管同事用 CodeBuddy / Trae / Qoder 还是其他 AI 工具，接入步骤完全相同——
> 硬约束在 git hook（所有工具的 commit 都走 git），软约束在 AGENTS.md（各家原生/兼容读取）。
> 工具差异（如 Trae 设置开关）见第三节表格，永远不需要为此改仓库内容。

> 省事路子：另有**内部安装包**（CLI / 插件市场 / 安装说明一体；`fix_paths.py` 一键适配本机）——下载见仓库 README 文末「打包与分发」的 Release 链接。

### 第 1 步：clone
```sh
git clone https://resources.ehsway.com:8929/developer/eaglermc_harness.git
```
> **sh 是什么 / 没装 git-bash 怎么办**：`sh` = 用 bash 解释器执行脚本，**装好 Git for Windows 就自带**
> （开始菜单搜"Git Bash"，或任意文件夹右键 → Git Bash Here）。如果还没装 Git，终端一条命令即可：
> ```powershell
> winget install --id Git.Git -e --source winget
> ```
> （PowerShell/CMD 里执行；winget 是 Windows 10+ 自带包管理器。装完**重开终端**，`git`/`sh` 即可用。
> 实在不能装 Git 的机器，可用仓库里等价的纯 Python 入口代替 .sh 脚本：
> `python eaglermc_harness/harness/agents/auto-gate.py`（日常门禁）——
> 但 hook 安装仍建议在有 git-bash 的机器上做一次。）

```sh
# 说明：harness 工程仓库已迁至公司 GitLab（2026-09-10，push-to-create 落地，与业务仓同源内网直连）。
#       旧 GitHub 源（github.com/rain610/eaglermc_harness）保留为镜像，不再作为接入入口。
# 业务仓（gxgk/hy/hzy 等）同在公司 GitLab。本 harness 仓库 clone 后，业务仓路径在各自仓库里，
#       通过 local.yaml 关联（第 2 步）。
# 建议统一放 <HARNESS>（也可任意位置，见第2步）
```

### 第 2 步：生成本机路径覆盖层
```sh
# 在 harness 工程根执行（Windows git-bash / msys）：
cp config/harness.local.example.yaml config/harness.local.yaml
```
用编辑器打开 `config/harness.local.yaml`，把 `repos:` 段改成**你机器上各端仓库的真实路径**
（参考模板里的「场景 A」）。该文件已被 `.gitignore` 忽略，只在本机生效，不提交、不影响他人。

> **布局不设要求**：你**不需要使用 git worktree** —— 把每个客户仓直接 clone 到任意目录、
> 写进 `local.yaml` 即可；用 worktree 也只是个人习惯（相关支持见 FAQ-3）。目录名、盘符、
> 是否 worktree，都是各机器自己的事，harness 一行都不强求。

> 若各端仓库和 harness 都在同一父目录下（如都 clone 到 `D:/dev/`），也可不改路径——
> 见文末「相对路径说明」。

### 第 3 步：一键装 pre-commit 钩子
```sh
sh <HARNESS>/sandbox/setup_machine.sh
```
脚本会：① 读 config（含你的 local 覆盖层）② 枚举所有「启用且路径存在」的 gate
③ 配置各仓 `core.hooksPath` 指向 harness 共享分发器（`harness/hooks-dir/`）④ 输出成功/跳过明细。
改 hook 逻辑只改 harness 一处，所有接入仓即时生效（2026-09-12 hooksPath 模式）。
⑤ 结尾自动跑 **doctor 终检**（四项：UTF-8 兜底 / R9 双向探针 / hook gate 活性 / skill 软链），
逐项打 ✅/❌——**全绿才算接入完成**，有 ❌ 按输出处理后重跑。
重复运行安全（幂等）。

**完成。** 此后每次 `git commit` 自动跑门禁；AI 工具改完代码也可手动跑：
```sh
python <HARNESS>/harness/agents/auto-gate.py
# 退出码：0=通过  1=拦截需修复  2=配置错  3=非受管仓库(跳过)
```

### 第 4 步（可选，建议）：生成全局守则

上面三步做完，**门禁**已经在工作了。这一步补的是**各 AI 工具自动加载的用户级入口**：
`~/.eagle-agents/GLOBAL_AGENTS.md` —— 三份端级模板
（`golden-patterns/templates/AGENTS_{pc,h5,backend}_slim.md`）都指向它。

```sh
python <HARNESS>/sandbox/global_agents.py <HARNESS>   # 生成/重渲染
python <HARNESS>/sandbox/global_agents.py --check     # 体检，不写文件
```

> 装过**编辑期 hook** 的同学不用单独跑 —— 那一步（`sandbox/install_shim.py`）会顺带渲染它。
> 本文这条 3 步路线不含编辑期 hook，所以单独列出来。

三点要知道：

- **哨兵分区**：文件里 `<!-- HARNESS:BEGIN -->` 到 `<!-- HARNESS:END -->` 之间由 harness 重渲染
  （**区间内手改会被覆盖**，写进去的规则改动也就靠这次重渲染到达你这里）；
  **区间之外是你自己的，harness 永不触碰** —— 本机独有的规矩写在那里。
- **`git pull` 不够，得重跑一次上面的命令**：守则**正文**在引擎仓里
  （`sandbox/global_agents.py`，全仓唯一一份），但**落到你机器上的那份是渲染产物**，
  不是指针 —— 引擎里改了守则，要重跑一次才更新到你这里。其余接入形态都是指针
  （见 README「升级」节那张表），**别把这一样当成它们**。
- **你挪过引擎仓，也得重跑一次**：正文里那几处绝对路径（引擎仓、`run_gate.py`、
  `auto-gate.py`）是**渲染那一刻烘进去的**，所以每个人拿到的都是自己的路径 ——
  但也正因为是烘进去的，仓一挪它就指空了。`--check` 会把这件事说出来
  （只提醒，不影响退出码）：重跑一次即对齐，哨兵区之外你自己的部分不受影响。
- 分层设计（谁装正文、谁装指针、为什么）见 `golden-patterns/templates/00_知识分层设计.md`。

---

## 三、各 AI 工具如何"吃进"门禁（无需你配）

**前提认知：仓库内统一，工具间零适配。** AGENTS.md 是唯一事实源（薄壳文件只指路不写规则，
规范改动只动 AGENTS.md 一处）。三个国产 AI IDE 对 AGENTS.md 的原生支持已逐家查证官方文档（2026-09-10）：

| 工具 | 触发方式 | AGENTS.md 原生支持（官方文档实证） | 备注 |
|---|---|---|---|
| **CodeBuddy** | 根 `AGENTS.md`（无 `CODEBUDDY.md` 时 fallback 加载） | ✅ 实测：**仓里不放任何 CodeBuddy 方言文件时，它自动加载完整 AGENTS.md** | 所以**刻意不放** `CODEBUDDY.md` / `.codebuddy/` 薄壳，就让它走 fallback；CLI 非交互须 `--permission-mode bypassPermissions` |
| **Trae CN** | 根 `AGENTS.md`（原生读取） | ✅ 原生支持，但**要在设置里手动开启** AGENTS.md 开关 | 新同事 checklist 必含此项 |
| **Qoder CN** | 根 `AGENTS.md`（原生读取） | ✅ 官方：拷进项目自动识别，零配置；CLI 默认上下文文件名即 AGENTS.md | 不需要任何 `.qoder/` 薄壳 |
| **Claude Code** | 根 `CLAUDE.md` 薄壳指向 AGENTS.md | —（走 CLAUDE.md 约定） | `~/.claude/skills/eaglermc-harness` 已注册 |
| **Codex CLI / OpenCode** | 根 `AGENTS.md` | ✅（AGENTS.md 规范发源工具） | OpenCode 全局 AGENTS.md 已配 |
| **Hermes** | 直接调 `auto-gate.py` | —（跨工具通用入口） | 任何 agent 均可 |

**三种接入模式（自觉 / 工具 / 强制）——同一份门禁，区别只在"谁来保证它一定跑"。三型叠加不互斥（自觉导航 → 工具主用 → 强制兜底），都正式支持：**

| | **自觉型**（零改造） | **工具型**（顺手默认） | **强制型**（推荐兜底） |
|---|---|---|---|
| 谁保证门禁跑 | AI 自己（规则要求它跑） | AI 顺手一调 / 人 CLI 同源 | 工具 / git 自动触发 |
| 要改本机配置吗 | **不用** | 装一条通道（一行：插件 / MCP / 技能） | 要（一次性 ~5 分钟） |
| 覆盖 | 所有读规则的 AI 工具 + 人工 | 装了通道的宿主（含无 hook 的，如 OpenClaw） | 编辑期：支持 hook 的工具；提交期：**任何工具 + 人工** |
| 漏跑风险 | 有（模型可能忘） | 没调用就没回执（有下层兜底） | 编辑期≈0；提交期 git 兜底 |
| 典型失效 | **静默失效**（没跑你也不知道） | **没调用 → 无回执** | **契约不符**（钩子跑了但模型没收到——换工具要重验） |
| 适合谁 | 不想动配置的同事、临时机器 | **日常主用** | 底线 |

### 自觉型：3 步（不改本机任何配置）

1. `git clone https://resources.ehsway.com:8929/developer/eaglermc_harness.git`
2. 在端仓里让 AI 打开（根 `AGENTS.md` 已内置"改完必须跑门禁"的要求，Claude Code 走 `CLAUDE.md` 桥接，其余原生读）
3. 改完对 AI 说一句「**跑一下 auto-gate**」，或自己执行：`python <HARNESS>/harness/agents/auto-gate.py`
   提交前跑一次，输出 `✅` / `rc=0` 才提交（`❌` 必须改；`harness-ok: <规则ID> <理由>` 是行内豁免）

### 工具型：2 步（装一条通道）

1. **装一条通道**（三选一）：技能 `python <HARNESS>/eaglermc_gate/cli.py skill install --target claude`（或 opencode / codebuddy）；
   MCP 注册 `claude mcp add` / `openclaw mcp add` / `codex mcp add`（命令实录见 `docs/packaging/COMPAT.md`）；
   或插件形态（`python <HARNESS>/integrations/plugins/build_marketplace.py --harness <HARNESS> --out <DIR>` 后按市场安装）。
2. **会话里直接用**：对 AI 说"跑一下门禁 / 查一下这个文件"，或直接调用 `harness_*` 工具；跑完留回执（台账可查）。
   建议叠加提交期兜底（见下）：工具型靠"顺手"，漏网的交给强制层。

### 强制型：3 步（配一次，以后不用管）

1. **提交期兜底（工具无关，必做）**：`sh <HARNESS>/sandbox/install_hooks.sh <gate名>`
2. **编辑期钩子（按工具配一个）**：
   Claude Code `~/.claude/settings.json`｜Codex `~/.codex/hooks.json`｜OpenCode 仓内插件｜
   CodeBuddy `~/.codebuddy/settings.json`｜Trae `~/.trae-cn/hooks.json`（设置 → Hooks，可"从 Claude 导入"）｜
   Qoder `~/.qoder-cn/settings.json`｜ZCode 设置 → 钩子 → 新建钩子（GUI）
   通用命令：`python <HARNESS>/integrations/hooks/eaglermc-harness-gate.py <client> PostToolUse`
   （`<client>`：claude / codex / codebuddy / qoder / zcode；细则见 `integrations/<客户端>/README.md`）
3. **验证（两步都要看）**：`sh <HARNESS>/sandbox/doctor.sh` + `sh <HARNESS>/sandbox/verify_integrations.sh <gate> <客户端>`
   判据：**① 门禁日志新增一行（rc=1）** 且 **② agent 回复里出现门禁结论**（只看 ① 会漏掉"钩子跑了但模型没收到"）

> **判据（一句话）**：只有"提示层"不算接入完成——至少要有**工具型**（顺手可调）或**强制型**（拦住），有条件就都补齐。
> 本机实测结论与逐家契约见 `docs/2026-09-13_多客户端接入验证报告.md`。

> **钩子 ≠ 方言规则文件**：规则正文**只留 AGENTS.md**（三家国产 IDE 都原生读，别复制到 `.trae/rules`、
> `.qoder/rules` 里造成漂移）；它们各自的目录只用来放**钩子配置**（`.trae/hooks.json`、Qoder settings 的
> `hooks` 段）——那是**强制层**，不是规则的第二份副本。

> **30 秒探针（每个 IDE 都建议做，验证加载 + 防旧版本）**：开 IDE 后问 agent
> 「AGENTS.md 第一行写了什么」——答一遍 = 正常；答两遍 = 叠加双载（查设置去重，防 token 双份）；
> 答不出 = 根本没加载（多为 IDE 版本过旧，升级即可，不用改文件布局）。

**项目级文件最终形态 = 「两件套」（2026-09-10 收敛，2026-09-22 拍板：端仓只放这两个文件，
多一份就多一处漂移面）**：
- `AGENTS.md` —— **唯一权威源**，所有规范正文只在这里改（覆盖 Codex + OpenCode + 三家国产 IDE ——
  Trae / Qoder 原生读，CodeBuddy 走 fallback）
- `CLAUDE.md`（覆盖 Claude Code）—— 仅一行 `@AGENTS.md`（Claude Code **不原生读 AGENTS.md**，官方 issue #6235
  于 2026-08 关闭只给 workaround；官方推荐桥接即首行 @import，Windows 下优于软链——软链需管理员权限）
- **不要创建** `CODEBUDDY.md` / `.codebuddy/` / `.trae/` / `.qoder/`：三家国产 IDE 已统一吃 AGENTS.md
  （CodeBuddy fallback / Trae 原生开关 / Qoder 自动识别），方言薄壳是漂移源。
  ⚠️ **订正一句本文件自己说过的话**：2026-09-20 那次逐仓复核，清的是 `CODEBUDDY.md` / `.trae/` /
  `.qoder/` **三个名字**，清单里**没有 `.codebuddy/`** —— 所以当时写的「33 个受管仓零残留」只对
  那三个名字成立，读成「方言薄壳已全量清除」就过头了。2026-09-22 补扫，PC 两仓各发现一份
  `.codebuddy/rules/eaglermc-harness/RULE.mdc`（已删，见上「两件套」）。
  **教训：清点「某类文件还剩几份」时，先数「这类文件有哪些名字」** —— 少列一个名字，扫得再仔细也是零。
  2026-09-22 按扩大的名字清单重扫 33 仓（含 `.cursor` / `.windsurf` / `.cline` / `.roo` / `.continue` /
  `.gemini` / `.kiro` 等）：除后端两仓 `.codebuddy/plans/` 下的**工作计划草稿**（与门禁无关，保留）外，零残留。

> **三 IDE 拦截率实验（团队级验收，做过才算"换 agent 不降拦截率"闭环）**：拿同一份必拦违规
> fixture（如 `Status == 10`）让三个 IDE 各自的 agent 真去改并提交，三个都应被同一个 pre-commit
> 拦下。**判据是拦截理由必须是真实规则违规（输出含 `❌ R9` 等），不是崩溃**——GBK 崩溃 /
> worktree ValueError 也会"全被拦"但是假阳性。doctor.sh 的探针就是这条判据的单机自动化版。

上面那条「不要创建」不是纸面规矩 —— 2026-09-22 逐字看过 PC 两仓各自那份
`.codebuddy/rules/eaglermc-harness/RULE.mdc`（两份逐字相同）：它**不是指路，是把规则正文抄了一份** ——
门禁命令、退出码语义、受管仓范围、范式阅读顺序全在里面重写了一遍，并把维护机的绝对路径
`D:/Rain/project/eaglermc_harness` 写死 **3 处**（其中一处是让 AI 去执行
`python D:/Rain/…/auto-gate.py`）。同事 clone 下来这些路径全是死的，而规则每改一次它就要同步一次。
何况它连 CodeBuddy 的推荐接法都不是：`integrations/codebuddy/README.md` 讲的是**用户级**
`~/.codebuddy/rules/`（另一回事），仓级这份是当年「三件套」设想的孤儿产物 —— 无生成者、无读取者 → 已删。

> **这里刻意不写「✅ N 仓已有」**。它是**每台机器各自的事实**（取决于你 clone 了哪几个仓），
> 写死一个数就必然有一天是假的 —— 这两行原先写着「✅ 6 仓已有」，2026-09-19 实测已是 29 个。
> 要知道**本机**现状就跑（只读，不改任何仓）：
> `python <HARNESS>/sandbox/agents_audit.py` —— 它的 ② 段会直接点名「哪些仓还没有 `AGENTS.md`」。
>
> 维护机 2026-09-19 的读数（**当快照看，别当实时事实**）：11 客户 × 三端 = 33 个受管仓中，
> `AGENTS.md` 有 **29** 个（缺 `hy_pc` / `hy_h5` / `zhongxin_pc` / `zhongxin_h5`）、
> `CLAUDE.md` 有 **30** 个（缺 `hy_pc` / `zhongxin_pc` / `zhongxin_h5`）。
> 后一项目前没有自动点名工具，是逐仓看根目录数出来的。
> （同日读数里还列着 `RULE.mdc` **2** 个 —— 2026-09-22 已按上面的「两件套」删净，故不再列。）

---

## 四、常见误区澄清（来自实测）

### ❌ 误区：「脚本里写死了 Hermes 的 venv python，同事机器会崩」
**事实**：所有 `.py` 都用 `python`（系统 PATH）或 `sys.executable`（运行脚本的 python），**没有写死任何 venv 路径**。
- `auto-gate.py` / `pre-commit.py` → `sys.executable`
- `install_hooks.sh` / `setup_machine.sh` → 直接调 `python`

唯一前提是**同事机器 `python` 命令可用**（Python 3.11+，纯标准库无依赖）。若 `python` 指向 Hermes venv 也正常（只要能跑标准库脚本）。

### ❌ 误区：「harness.yaml 里 `_repo_path` 写死绝对路径，换机器必漏判」
**事实**：共享 `config/harness.yaml` 里**根本没有仓库路径**——`repos:` 段只声明**键名**、值一律留空
（哪些仓受管是团队策略，与机器无关）。路径是**本机事实**，写在本地覆盖层里：
- `config/harness.local.yaml`（你本机，`.gitignore` 已忽略）填 `repos:` 段各键的路径
- 或环境变量 `HARNESS_CONFIG` 指向任意配置文件（**替换**本地层，不是叠加）
- 优先级：`HARNESS_CONFIG` > `harness.local.yaml` > 共享 `harness.yaml`
- 没写的段（如 `gates` 策略）自动继承共享 yaml

**你永远不该改共享 `harness.yaml` 里的路径**——改自己的 `local.yaml` 即可。
**键名在共享文件里已声明，别动**：值留空 = 「这机器还没配」，逐条 `⊘` 提示、**不阻断**；
键名不在共享配置里 = **真配置错**，任何机器都拦（rc=2）。这条分界线是 2026-09-19 刻意做出来的。

### ❌ 误区：「GBK 控制台会崩」
**事实**：**全部门禁入口脚本**都已加 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` 兜底，
`harness_guard.py` 输出改 ASCII（`[X]` 代替 ❌）。Windows GBK 控制台不再崩溃。
（这里**刻意不写「N 个脚本」**：入口清单由 `sandbox/list_entries.py` **机器推导**、随工程一起长大，
写死一个数就必然有一天是假的 —— 与本文件另一半「不写『✅ N 仓已有』」是同一条纪律。想知道本机现状跑
`python <HARNESS>/sandbox/list_entries.py`；`doctor.sh` 的 1/4 节会逐个核对有没有漏。）

---

## 五、相对路径支持（可选增强）

`harness.local.yaml` 的 `repos:` 值支持相对路径（相对 harness 工程根）。
若你和同事都把整套 clone 到**同一父目录**（如 `D:/dev/eaglermc_harness` + `D:/dev/eaglermc_pc_net-gxgk`），
可直接在 `local.yaml` 写：
```yaml
repos:
  gxgk_pc: ../eaglermc_pc_net-gxgk
```
`_config.py` 会自动按 harness 工程根解析。否则用绝对路径最稳。

---

## 六、门禁输出怎么读（重要：❌ ≠ 拦截）

跑门禁时输出里可能出现大量 `❌` 行，但**最终显示"通过"**——这不矛盾：

- `❌` 行分两级：**error 级**（拦提交）和 **info 级**（仅观察，不拦）
- 最终一行 `=== 结果: 共 N 处记录, 其中 error 级 M 处 ===` 才是判决：
  - `error 级 0 处` → **通过**（打印的 ❌ 都是 info 观察项，不用管）
  - `error 级 >0` → **拦截**，只需修带 error 的那几条

**两条豁免机制**（为什么老代码的 ❌ 不拦你）：

| 机制 | 语义 |
|---|---|
| **legacy_baseline（仓级存量豁免）** | 首次纳管的仓配 `legacy_baseline: true`：该仓**存量历史违规**只 info 观察，新代码照拦。存量清理完后改 false 恢复严格 |
| **按行豁免（changed_lines）** | 改老文件时只检查**你本次新增/修改的行**——文件里前人遗留的 console.log、`State == 0` 裸写等不会拦你提交。新建文件则整文件都算"你写的"，照拦 |

⚠ 「新代码照拦」的**准确口径**（2026-09-21 补，此前写得过宽）：照拦的是**新建文件**
和**行级规则**；而那 4 条**整文件规则**（`VAuthRule` / `VForKeyRule` / `ComponentRule` / `CrlfRule`）
的结论是"整个文件的属性"、**没有行号**，`legacy_baseline: true` 的仓里**改存量文件的任意一行**
都不会让它们升回拦截 —— 一律只进 INFO 台账。这是设计意图（无法归因就不拦，见
`checks/rule_base.py` 的 `WholeFileRule`），不是漏判。另：**当前 34 条业务 gate 全部是
`legacy_baseline: true`**（12 个老仓于 2026-09-21 对齐；`keji_v3_backend` 2026-09-23 首次纳入
即按此口径 —— 新客户线同样先"存量只 info、新代码照拦"），所以「存量清理完后改 false」
目前是全线待决事项，不是某几个仓的现状。

一句话：**你写的代码你负责，前人的债不拦你提交。**

---

## 七、常见问题 FAQ

**Q1：跑门禁打出一堆 ❌ 但显示通过，要处理吗？**
不用。那些是 info 观察项（存量或非本次新增行），见上节。只有 `error 级 >0` 拦截时才需按 `✅ FIX` 提示修复。

**Q2：改了一行老代码，提交被整文件的存量违规拦了？**
不应该发生（按行豁免已覆盖三端）。若遇到，确认该违规行是否在**你本次的 diff 里**（`git diff -U0 -- <文件>` 看 `+` 行）——在 `+` 行里就是你新写的，按 FIX 修；不在则是豁免失效，报给 harness 负责人。

**Q2b：明明是合规写法（如上传签名在 `common.uploadUrl` 里统一封装），规则还是拦我？**
给命中行的**本行或紧邻上一行**加标记即可豁免该行（2026-09-12 起支持）：
```js
// harness-ok: H5-UPLOAD 签名已由 common.uploadUrl 统一加 Authorization
uni.uploadFile({ url: u, filePath: p })
```
规则 ID 必须写对（写成别的规则名不生效），理由写在标记后——diff 里可见，复核有抓手。**这是唯一合法的"我确认这样写没问题"出口**，别用删代码或放宽规则来绕。

**Q3：worktree 提交报"路径无效/崩溃拦截"？**
worktree 共享主仓的 git config。重跑 `sh <HARNESS>/sandbox/install_hooks.sh` 会重新配置 `core.hooksPath` 指向共享分发器并清理旧拷贝 hook——gate 由 pre-commit.py 按提交时的 cwd 自动识别，无需写死。

**Q4：退出码 2（配置错）？**
`harness.local.yaml` 里 repos 路径拼写错误，或该 gate 的仓库在你机器上没 clone。对照 `config/harness.local.example.yaml` 检查。

**Q5：想看门禁长什么样再决定接不接？**
跑 `sh <HARNESS>/sandbox/demo_gate.sh hy_hzy_backend`——自动演示"故意违规 → R9 拦截 → 修复成 SysConst → 放行"全过程，跑完自动清理，不碰真实代码。

---

## 八、紧急绕过

门禁误拦或需紧急提交时：
```sh
git commit --no-verify
```
（按团队规范，非必要不绕；绕过前先确认是真误拦还是代码真有问题。）

---

## 九、验证清单（接入后自检）

**首选：看 doctor 输出**（setup 结尾自动跑，或手动 `sh <HARNESS>/sandbox/doctor.sh`）：
四项全 ✅ 即接入闭环。四项含义：

- [ ] **UTF-8 兜底**：全部门禁入口脚本带 GBK 防崩溃兜底（缺一个就在那个入口误拦所有提交）
- [ ] **R9 双向探针**：违规探针被拦（rc=1）**且输出含 R9 真实违规标记**（不是崩溃假拦），合规版放行（rc=0）——「拦下来」≠「拦对了」
- [ ] **hook 活性**：各端仓 `core.hooksPath` 指向本工程共享分发器（`harness/hooks-dir/`），且该 gate 在 config 中 enabled（防「hooksPath 没接 + gate 关着」双重失效静默放行）
- [ ] **skill 软链**：codebuddy/claude/opencode 软链可达（⚠ 缺失不算失败，仅少自动加载）

手动补充验证（AI 工具侧行为）：
- [ ] 在你用的 IDE 里问 agent「AGENTS.md 第一行写了什么」→ 答且只答一遍（30 秒探针，见第三节）
- [ ] 故意改一个 `.cs` 触发 R9（状态裸数字）→ `git commit` 被 pre-commit 拦，拦截理由含 `R9` 字样
- [ ] 改完 `harness.yaml` 后跑 `python harness/run_gate.py --validate` → 35 个 gate 全 ✅

---

## 十、接手后怎么验门禁"真的在工作"（维护者视角）

三条命令，从粗到细：

```sh
python harness/run_gate.py --validate      # 配置体检：gate/路径/脚本逐项验证
sh sandbox/doctor.sh                       # 环境体检：钩子活性、软链、清单一致性
python harness/run_gate.py --coverage      # 覆盖度自评：哪些规则真的触发过、哪些一次没响
```

`--coverage` 是最能回答"我这套门禁是不是在空转"的那条：它会区分「规则不适用/写法失效」和
「该端根本没跑过」，并列出豁免率过高的规则（口径可能过宽）。

---

## 附：git 提交期门禁的三种状态（对照表）

同事遇到「提交好像没被检查」时，按下表定位（`git config core.hooksPath` 是第一抓手）：

| # | 状态 | 判定方法 | 提交时的表现 | 怎么修 |
|---|---|---|---|---|
| ① | **未接入**（正常） | `git config core.hooksPath` 为空 | git 静默跳过，无任何输出 | 想接入就跑 `sandbox/install_hooks.sh <gate>` |
| ② | **已设 hooksPath，但分发器文件不存在** | `core.hooksPath` 有值，但该目录下**没有** `pre-commit` | **git 静默跳过、零输出**（harness 侧此刻无法出声——这是 git 行为，仓内兜不住） | 重跑 `install_hooks.sh`；`doctor.sh` 的 3/4 节会报 ❌ |
| ③ | **分发器在，但实现缺失**（clone 半残/被误清理） | 分发器存在、`harness/hooks/pre-commit.py` 不存在 | **rc=2 + 修复指引**（fail-closed，会阻断提交） | 恢复工程仓完整性；`doctor.sh` 的 2.5/4 节会报 ❌ |

> ②与③的差别是刻意的：②是 git 层面的"根本没调用我们"，③是我们被调用了但自己半残——
> 后者必须出声并阻断（否则等于**全部端仓的提交期门禁无声关闭**）。

---

## 附：审计台账能证明什么（证据强度声明）

`reports/gate_log.jsonl` 记录"某时刻、某 gate、某模式、rc=?"。`sandbox/audit_reconcile.py` 用**时间窗**
（提交前 15 分钟～提交后 120 秒）把两者对上，因此它的结论是**线索级**：

- ✅ 可以说明：提交前后本机跑过该 gate 且通过（用于"当时到底有没有门禁在跑"的排查）。
- ❌ 不能说明：**这次提交的内容**被检查过 —— 窗口内任意一次无关文件的 rc=0 也会命中。
- 需要"提交内容级"证据时，目前的做法是**违规探针 + 合规反锁 + 台账**三件一起留档（见《验收报告》），
  而不是单看台账。对外表述请用"有门禁运行记录"，不要写成"提交可自证"。

---

_维护：harness 负责人。接入问题先查本文档，再问负责人。_
