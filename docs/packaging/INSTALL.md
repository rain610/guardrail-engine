# AgentGuard 离线安装包 · 安装说明（v__VERSION__）

<!-- 仓库单一来源：docs/packaging/INSTALL.md —— 由 sandbox/pack_release.py 渲染进安装包（自动替换版本 / 轮子名两类占位符），请勿在包内单独改正文。 -->

> 本包以 MIT 许可证发布：可自由使用、修改与再分发，保留版权与许可声明即可（全文见包内 `LICENSE`）。

## 包内容

- `__WHEEL__`（CLI + MCP 薄服务 + **完整门禁引擎**：checks / harness / config / sandbox / golden-patterns）
- `marketplace/`（Claude Code / OpenClaw 插件市场 bundle）
- `bootstrap.py`（引擎引导：把门禁引擎仓 clone 到本机 —— **接入的第一步**，见「零」）
- `fix_paths.py`（换机器路径适配：一键改写包内配置到你的机器）
- `upgrade.py`（一键升级：拿新包刷新本机装过的副本——默认预演，`--yes` 执行；**不做任何撤回**）
- `uninstall.py`（一键卸载：把本机装过的东西逐项撤回——默认预演，`--yes` 执行）
- `OPENCLAW.md`（OpenClaw 接入完整步骤）
- `LICENSE`（MIT 许可证全文）
- `SHA1SUMS.txt`（本包文件校验）

## 先决条件

> ENC/编码说明：首次接入 legacy 仓时，**本次改动到的文件要顺手转成约定编码**（
> 未改动的存量不翻涌、按 INFO 降级；改动过的文件编码不符会直接拦）——见 README 能力边界。

### 前置检查（装之前先跑这三条，30 秒）

```bash
python -c "import sys; print('Python %d.%d.%d' % sys.version_info[:3]); assert sys.version_info >= (3, 11), '需要 Python 3.11+（当前版本过低）'"
python -c "import shutil; print('git  :', shutil.which('git')  or '✗ 未找到')"
python -c "import shutil; print('node :', shutil.which('node') or '✗ 未找到')"
```

- **Python ≥ 3.11**（实测 3.11 / 3.12 / 3.13）：本包与门禁引擎都用它跑。低于 3.11 会在启动时
  **直接给人话报错并退出**（退出码 2），不会让你撞看不懂的 AttributeError。
- **解释器叫什么名字不用你操心，但也别被它坑**：`python` 这个名字不是到处都有 —— Ubuntu / WSL
  上通常**只有 `python3`**；反过来 Windows 上 `python3` 常常只是 Microsoft Store 的占位别名
  （`command -v` 找得到、一跑就失败）。所以门禁的各入口**不认名字、只认能不能跑**：按
  **`$HARNESS_PYTHON` → `python3` → `python` → `py -3`** 的顺序逐个试，取第一个
  `--version` 退出码为 0 且输出 `Python 3` 的；一个都找不到就 **fail-closed**（rc=2 + 人话提示），
  **不静默放行、也不把"没装解释器"误诊成"路径不对"**。
  你的解释器名字特殊（conda 环境、自己编译的）时，显式指定一次即可 —— 写进 shell 配置长期生效：
  `export HARNESS_PYTHON=python3`。
- **git**：提交期门禁必需（pre-commit 钩子 / 改动文件收集 / 篡改可发现自检）。另有一条**缺省关闭**的
  fail-closed 档：门禁靠 `git diff` 判断"这个文件本次动了哪些行"，读不到时就按存量降级 ——
  而"读不到"有两种成因，**恰好相反**：① 文件本次真的没动（两处 diff 都成功、正文为空）→ 降级是对的；
  ② git 探针失败 / 没装 git / diff 超时 → **根本没读成**，降级 = 静默放宽（缺省档只在 stderr 告警一次）。
  设 `HARNESS_GIT_REQUIRED=1` 即：**读不到 diff 时不再按存量降级**，4 处降级判据同时生效。
  ⚠️ 与 node 不同，这一档**故意不在 pre-commit 里默认打开**：node 那条只影响"本次改动文件的语法检查"，
  坏掉时"没查"一眼可见；而 diff 传感器坏掉时是**反向**的 —— 整文件口径 + 不降级会让
  `cli.py check <某文件>` 之类把该文件的**历史**违规整份报成 ❌，在存量尚未清零的仓里等于把提交
  彻底堵死、还堵得毫无信息量。所以它是「怀疑传感器坏了时手动打开」的档，不是日常默认。
  真·非 git 目录**不触发**本档（`git rev-parse` 明确答"不是工作树"是合法输入，与探针故障分档）。
- **node**：**启用 JS/TS 语法检查的 gate 必需** —— ⚠️ 本机没有 node 时**提交会被拦下**（fail-closed，
  不会静默跳过）。临时绕过：`HARNESS_NODE_REQUIRED=0 git commit …`（不建议长期用）。
  **不调 node 的规则（纯 Python 规则等）不依赖 node** ✓。
- 可选：AI 客户端（Claude Code / OpenClaw / CodeBuddy / Codex…）；只用 CLI 则不需要。
- **不需要预先 clone 引擎**：包里的 wheel 自带引擎。装完 wheel 后跑一次
  `guardrail-engine init`，引擎就落到本机（下称 `<HARNESS>`，默认 `~/.guardrail-engine/engine`）—— 见「零」

> **与本机布局无关，不依赖任何个人工具**：本包运行时只依赖 **Python + git + 你机器上的仓库路径** ——
> 不需要任何个人 skill 或个人工具（`worktree-manager` / `huayu-manager` 这类是维护者自用的辅助
> skill，有则更省事，没有也完全可用）。
> 包内 `.mcp.json` 刻意**留的是占位符**（`__PY__` / `__MCP__` / `__HARNESS__`）：换机器必须
> 先按安装说明「第 0 步」跑 `python fix_paths.py` 填成本机路径。**这一步不能跳**（见下）。

## 零、初始化引擎（wheel 自带，一条命令）

安装包里的 wheel **自带完整引擎**（规则、配置、钩子、黄金范式），所以不需要先 clone。
装完 wheel 后跑一次 `init` 即可：

```bash
pip install "<本包目录>\__WHEEL__"
guardrail-engine init          # 复制到 ~/.guardrail-engine/engine，并写 ~/.guardrail-engine/harness.path
```

`init` **只复制、不删除**：目标已存在且内容不同时要求 `--force`，旧目录会改名归档为
`<dir>.disabled-<时间戳>`（可回退）；`--dir` 可指定安装位置。它会在结束时跑一次
`run_gate.py --list` 自检 —— 看到 `self-check : list-gates rc=0` 才算引擎真的能起。

- 下面全文的 `<HARNESS>` 指的就是 **init 出来的引擎目录**（默认 `~/.guardrail-engine/engine`）。
- 引擎目录同时是 `guardrail_engine/mcp.py`、`harness/`、`checks/` 的所在地，MCP 注册与提交钩子
  都按它定位；换目录只需要重新 `init --dir`（指针会跟着改）。
- 自检（init 完跑一下，3 秒）：

  ```bash
  guardrail-engine where -v
  sh <HARNESS>/sandbox/where.sh      # 打印 <HARNESS> 的实际位置
  ```

- **没 init 会怎样**：`guardrail-engine list` / `run` 会以 rc=2 退出并明确提示 `guardrail-engine init`；
  下一步「二」的第 0 步 `fix_paths.py` 会校验 `<HARNESS>/guardrail_engine/mcp.py` 在不在，
  不在就直接退出并报 `✗ 在 X 下没找到 guardrail_engine/mcp.py`。看到这句先回头看本节。
- **想用 git 跟踪引擎更新**（开发 / 维护者）：包里仍带 `bootstrap.py`，它把引擎仓 clone 到
  本机 —— 这是一条**替代**路径，不是安装的前置条件：

  ```bash
  python "<本包目录>\bootstrap.py" --dir D:/dev/guardrail-engine
  ```

  跑完按它打印的命令继续（路径适配 + 生成覆盖层）。公开源在
  `github.com/rain610/guardrail-engine`；内网/私有镜像用 `bootstrap.py --url <地址>` 换源。

### 第 1 步：把 repos 填成本机路径（**不做 = 本机零受管**）

这是引擎就位之后**最容易漏、漏了又最不显眼**的一步，所以单列出来。

共享的 `config/harness.toml` 里**没有仓库路径**（2026-09-18 起刻意留空）：你是把仓库放在
`D:/` 还是 `E:/`、用户名是什么、在 WSL 里是不是 `/mnt/d/...`，都是**每机事实**，写死在共享文件里
对别人就是错的。所以路径放在 gitignored 的 `config/harness.local.toml`（各人一份）。

但**键名不用你写**：共享配置的 `[repos]` 段已经声明了全部受管仓的键（值一律留空）——
「哪些仓受管」是团队策略，跟机器无关。你这台机器上没 clone 的那些，留着空值就行，
不是配置错、不用删、也不影响别人。

> **别动键名**（漏写 / 拼错）—— 那与「值留空」是完全两回事。键名不在共享配置里 = 真配置错：
> `--validate` 阻断（rc=2）、提交期自检报 ❌。这是有意的 —— 那是「键名拼错」与「新 gate
> 忘了登记」唯一会出声的时刻。

**没填会怎样**：门禁**不会**报错中断，只是**什么都不做** —— 提交钩子跑起来、判定、看起来一切正常，
但配置里一条仓库路径都没有，于是没有一条门禁规则真的执行。这比报错难发现得多，所以引擎把它
做成了**看得见**的 —— 三处都会说出来：

```bash
python <HARNESS>/harness/run_gate.py --list        # 未配置的 gate 逐条显示 `@ (未配置)`，末尾给提示
python <HARNESS>/harness/run_gate.py --validate    # 这些 gate 显示 ⊘ 并汇总（**不阻断**：CI 干净容器就是这个形态）
```

（第三处是运行期：提交钩子 / 各门禁入口会打一条 `[config-warn]` 汇总。三条说的是同一件事，
措辞不同而已。）

判据：`--list` 里那些 gate 显示的是**真路径**，不是 `(未配置)`。

**怎么填**：

```bash
sh <HARNESS>/sandbox/setup_machine.sh    # 首次会生成 config/harness.local.toml 并提示你编辑，编辑完重跑
```

编辑时只写你机器上**确实存在**的那几个仓即可，写几条生效几条（其余照旧逐条报未配置）；
模板 `config/harness.local.example.toml` 的「值留空 = 本机不受管」一节有完整说明。
路径写法：Windows 写 `D:/...`（正斜杠）；**WSL 里要写 WSL 视角的路径**（如 `/mnt/d/...`），
写 Windows 路径在 WSL 里一律指空。

- init 完还有一件事才算接通，不在本节：「二」的第 0 步路径适配（`fix_paths.py` 填**包内**占位符）。
  完整叙事见仓库 `README.md` 与 `docs/packaging/RUNBOOK.md`。

> 别把本节和「二」里的**第 0 步**搞混：本节管的是**引擎从哪来、仓库在哪**；
> 那个管的是**包内占位符**——把 `.mcp.json` 填成本机路径。两件事都要做，顺序是先本节、后那个。

## 一、人用 CLI

```bash
pip install "<本包目录>\__WHEEL__"
guardrail-engine init           # 引擎落到 ~/.guardrail-engine/engine（见「零」）
guardrail-engine quickstart     # 自检 + 使用引导
guardrail-engine --help
```

## 二、Agent 接入

> **第 0 步（每次安装/升级都要做一次）：路径适配。** 包内 marketplace 的 `.mcp.json`
> 里留的是占位符（`__PY__` / `__MCP__` / `__HARNESS__`）——插件机制要求绝对路径，所以
> 装到本机时用这个脚本填成**你本机**的 python 与引擎路径：
>
> ```bash
> python "<本包目录>\fix_paths.py" --harness "<你的 guardrail-engine 路径>"
> ```
>
> 看到 `✅ 适配完成`，且输出里三行箭头右边都是本机路径，再继续。
>
> **这一步不能跳过** —— 包括"原地升级、机器没变"的情况（「原地升级」是什么意思、
> 包该解压到哪，见第五节开头）：新包里 `.mcp.json` 又是占位符
> 了（每个包都是从模板现做的），漏跑就会留下 `__PY__` 这种字面值，MCP 服务起不来、
> 编辑期门禁整体失效。好处也在这里：漏跑是**当场失败 + 肉眼可辨**，不再是"悄悄用着
> 打包机的路径"。升级整条链路见第五节。

- Claude Code：
  ```bash
  claude plugin marketplace add <本包目录>\marketplace
  claude plugin install agentguard@agentguard-tools
  claude plugin validate --strict <本包目录>\marketplace   # 自检：期望 0 错误 0 警告
  ```
- OpenClaw：见本包 `OPENCLAW.md`（同样先做第 0 步）。
- 其他支持 MCP 的客户端（通用一行；路径以第 0 步适配结果为准）：
  ```
  command = <python 解释器>
  args    = ["<HARNESS>/guardrail_engine/mcp.py"]
  ```
- 编辑期 hook（**多个客户端共用同一个入口**；装了它才有"改完文件立刻检查"，只有提交期兜底
  也是能用的——这一路是可选加强，但**属于安装面，退的时候要一起退**；⚠ 唯一例外是下面第三样
  「全局守则」，它**刻意不退**，理由在那一节）：

  ```bash
  python <HARNESS>/sandbox/install_shim.py           # 生成/刷新转发器、指针与全局守则
  python <HARNESS>/sandbox/install_shim.py --check   # 体检：指针 → 实际仓库的解析链（+ 全局守则状态）
  ```

  产出**三样**：

  | 产出 | 是什么 |
  |---|---|
  | `~/.guardrail-engine/hooks/agentguard-gate.py` | 薄转发器，指向**仓库**，所以引擎更新即生效 |
  | `~/.guardrail-engine/harness.path` | 指针（运行链以它优先） |
  | `~/.guardrail-engine/GLOBAL_AGENTS.md` | **全局守则**：各 AI 工具自动加载的入口路由 |

  前两样按各客户端的方式配 hook，配置位置见 `integrations/<客户端>/README.md`。

  **第三样（全局守则）为什么在这一步**：它此前**没有生成者** —— 只有
  `sandbox/bootstrap_agent_env.sh` 会写它，那支脚本不在安装流程里、也没有任何调用方，
  于是照本文做完，各 AI 工具自动加载的入口路由根本不存在。装 shim 这一步是**必然会跑到**的一步，
  所以由它顺带渲染。

  ⚠ 它跟另外两样**不一样**，两点要知道：

  - **哨兵分区**：文件里 `<!-- HARNESS:BEGIN ... -->` 到 `<!-- HARNESS:END -->` 之间由 harness
    每次运行重渲染（写进去的规则改动才到得了已经有这个文件的人），**这个区间内手改会被覆盖**；
    **区间之外是你自己的，harness 永不触碰** —— 本机独有的规矩写在那里。
  - **卸载时不退它**（见第四节表格）：正因为区间之外属于你，"整机退出"不会把你自己写的规矩
    一起拿走。它留在原地继续可用。

  > 顺带一提：这一路（编辑期 hook）本身是**可选加强**，但全局守则归在这里 —— 意味着**不装编辑期
  > hook 的同学不会自动有全局守则**（那是他的选择，不拦）。只想要守则、不要 hook 的话，单独跑
  > `python <HARNESS>/sandbox/global_agents.py <HARNESS>` 即可（体检加 `--check`）。

## 三、提交期硬兜底（可选）

给仓库装上提交拦截（与 agent 行为无关，兜底强制）：

```bash
git config core.hooksPath <HARNESS>/harness/hooks-dir
```

## 四、卸载（不需要了怎么退干净）

一条命令（推荐）——**默认只预演，`--yes` 才动手**：

```bash
python "<本包目录>\uninstall.py" --harness "<你的 guardrail-engine 路径>"          # 预演：列出将清理的每一项
python "<本包目录>\uninstall.py" --harness "<你的 guardrail-engine 路径>" --yes     # 真执行
```

它会按「装过什么」逐项撤回（能自动做的自动做，够不着的打印现成命令）：

| 装了什么（对照「二 / 三」） | 怎么退 | 方式 |
|---|---|---|
| 技能副本（`skill install`） | 改名归档 `<目标>.disabled-<时间戳>`（可逆） | ✅ 自动（`~/.claude/skills` 等 3 个默认宿主） |
| 提交期钩子（`core.hooksPath`） | 加 `--unhook <端仓>`，每个仓库撤回一次（不删文件） | ⚙️ 按仓库手动触发 |
| Claude Code 插件 | `plugin uninstall` + `marketplace remove` | ✅ 检测到 `claude` CLI 时自动 |
| OpenClaw 扩展 | 把 `extensions/agentguard` 移出归档，**重启桌面端生效** | ✅ 自动（特征不符会拒绝，防误删） |
| 人用 CLI（pip 包） | `pip uninstall guardrail-engine` | ✅ 自动（不同 python 加 `--python`） |
| 编辑期 shim（`install_shim.py`） | 两支文件改名归档（可逆）：`~/.guardrail-engine/hooks/agentguard-gate.py` 与 `harness.path` | ✅ 自动（转发 `install_shim.py --uninstall`；特征不符会拒绝，防误删） |
| 全局守则（`~/.guardrail-engine/GLOBAL_AGENTS.md`） | **不退**（见「二」：哨兵区之外是你自己写的规矩，撤走等于把它们一起拿走） | — 留在原地，退出后照常可用 |

> **归档是"可逆"，不是"干净"（这条以前没写）**：改名归档**不删任何文件**，所以每退一次就
> 永久留下一份**完整副本**（技能是一整棵目录树、shim 是两支文件）。反复「卸载 + 重装」只涨不落。
> 怎么看得见、怎么清：
> - `python <HARNESS>/guardrail_engine/cli.py skill status` 会列出技能残骸（`<技能名>.disabled-*`）
>   与份数；确认不要了，手工删掉那些目录即可（**门禁不会替你删**——它只归档）。
> - shim 的归档是 `...agentguard-gate.py.disabled-<时间戳>` 与 `harness.path.disabled-<时间戳>`，
>   同样在 `~/.guardrail-engine/` 下，确认不要了手工删（该目录里还有别的东西，本工具不碰目录本身）。

- 其他手写过 MCP 注册的客户端：到各自配置里删掉 `guardrail-engine` 条目（旧配的 `agentguard` 键同样可用；`claude mcp remove` 等，见 RUNBOOK）。
- **编辑期 hook 的配置**：shim 归档后，各客户端 hook 配置里仍写着那个路径 → 到宿主配置里删掉该 hook。
  只归档文件不改配置，编辑期门禁会**静默失效**（跟它装不上时是同一个失效模式）。
- 本工具**不动你的 `<HARNESS>` 仓库**；安装包目录不再需要时，整目录删除即可。
- 验证（都应查无）：`pip show guardrail-engine` ｜ `claude plugin list` ｜ OpenClaw `plugins list` ｜ `python <HARNESS>/guardrail_engine/cli.py skill status`（顺带报残骸）｜ `python <HARNESS>/sandbox/install_shim.py --check`。
  （末一条的输出里会多一行 `[check] · 全局守则：已生成` —— 那是**预期**的：全局守则按上表**刻意不退**，
  且这一行不参与该命令的成败判定；shim 查无时它照样以退出码 1 报红。）
- 想只退单项：细粒度命令（钩子 / 技能 / MCP）见仓库 `docs/packaging/RUNBOOK.md`「4. 卸载与回滚」。

## 五、升级（用新包替换旧安装）

### 5.0 先说清「原地升级」是什么意思

「**原地升级**」= **新包覆盖到同一个固定目录**，升级前后"包在哪"这个路径**始终不变**。
它不是"什么都不用管"，也不是"重新装一遍"—— 它说的是**包目录的位置不变**，而不是
"安装动作可以省"。

**推荐（默认）：固定目录。** 约定包目录为 `%USERPROFILE%\guardrail-engine\`，里面直接放着
`fix_paths.py` / `marketplace/` / 轮子这些（**不是**再套一层带版本号的目录）。这样本文档
里所有 `<本包目录>` 指的永远是同一个路径，第 0 步那条命令每次**原样重打**即可，不用记住
"这版解压到哪了"。

> ⚠️ **一个包内现实**：zip 解开后自带一层带版本号的目录（`guardrail-engine-package-v<版本>\`
> —— 打包脚本按版本命名，见 `sandbox/pack_release.py`）。所以要落到固定目录，需要多一步：
>
> ```
> 第一次：解压 → 把里面那层 guardrail-engine-package-v<版本>\ 整个改名成 guardrail-engine\，
>         放到 %USERPROFILE% 下
> 以后每次：解压 → 用它那层目录**里面的内容**覆盖 %USERPROFILE%\guardrail-engine\
>         （资源管理器里"替换目标中的文件"即可，覆盖掉的是上一版的包，没有任何损失）
> ```
>
> 这一步确实有点绕。想要"解压就是固定目录、连这步都省"的话，得改打包脚本让 zip 根目录
> 不带版本号 —— 那是一次打包契约变更（`verify_release.py` 与多条出货锁都要跟着动），
> 不在本文档范围内，需要的话找 harness 负责人。

**例外：按版本目录。** 需要**并存多版 / 要能回退**时才用：解压到按版本命名的新目录
（如 `%USERPROFILE%\agentguard-0.1.3-dev\`），每次把新路径重新告诉宿主一次（见下表
「换过目录」那行）。代价是每次都得记住新路径，换来并存与回退。

> **两种布局都<u>不需要</u>为了升级跑 `uninstall.py`。** 它是**退出**用的（离职 / 换机 /
> 不再用）：跑完你的门禁会**关着一段**，提交钩子还得逐仓 `--unhook` 再装回去。升级是
> "刷新副本"，不是"退了再装"。

### 5.1 一条命令（推荐）

**推荐一条命令**（默认只预演，`--yes` 才动手；**不做任何撤回**，不卸载、不删文件）：

```bash
python "<本包目录>\upgrade.py" --harness "<你的 guardrail-engine 路径>"          # 预演：逐通道列出将做什么
python "<本包目录>\upgrade.py" --harness "<你的 guardrail-engine 路径>" --yes     # 真执行
```

它会按 [1] 路径适配（不可跳过）→ [2] Claude 插件 → [3] OpenClaw 扩展 → [4] pip 包
→ [5] 引擎副本 → [6] 技能副本 → [7] 编辑期 shim 的顺序**逐通道幂等刷新**，并打印**升级前后的
本机安装矩阵**（引擎 / pip / 插件 / 技能各自是哪一版）。三条硬规矩：**只刷新已经装过的通道**
（没装的不替你装，那是「安装」）；**旧文件只列出来交给人判断，不替人删**；**仓库一律不碰**
（引擎副本只认 `guardrail-engine init` 写下的所有权标记，旧副本改名归档可回退）。

两个当场会出声的坑（都是"看起来升了、其实没换"）：
- **同版本号**：`claude plugin update` 会打印绿色「已是最新」然后**什么都不换**；pip 那一路同理
  ——`pip install -U` 在同版本号下是 **rc=0 且一个字节都不换**（pip 原话：`is already installed
  with the same version as the provided wheel`）。脚本对这两路都不看退出码，而是**装完直接对账产物**：
  对不上就直说"这台机器上跑的仍是旧内容"，并给出路（回引擎仓升 `<HARNESS>/VERSION` 重新打包）。
  pip 那一路还会**接着代跑** `pip install --force-reinstall <本包轮子>` 再对一次账 ——
  实测同版本号下它确实能换内容，所以脚本替你跑掉这一步、并把结果报出来；
  连它也换不动（权限 / 索引 / 被别的安装遮住）才算真失败，那时才请你手工介入。
- **漏跑路径适配**：副本里那份 `.mcp.json` 若还是 `__PY__` 或指向本机不存在的位置，脚本会点名报出来
  ——这正是"编辑期门禁整体无声失效"的现场。

> **演练的退出码现在可信了**（2026-09-23 修，评审 L15）：演练支与 `--yes` 支**同一判据** ——
> 两边读的是同一份失败清单，有未完成项就**退出 1** 并逐条列出来。
> 此前演练支把它自己刚攒下的清单丢掉、一律 `return 0`，于是"演练有 ✗"与"演练通过"在退出码上
> 长得一模一样，脚本化调用（`if rc==0 then …`）会据此放行。
> 仍然成立且**从来一致**的一条：**前置条件**失败（`--pack` 指错目录、包里缺 `fix_paths.py`）
> 当场 `return 1`，后面的通道一律不做 —— 因为后面每一个都建立在它之上。

## 5.2 手动等价命令（想只刷新某一路、或 `upgrade.py` 跑不动时用）

先分清两类接入：**指向仓库的**（仓库更新即生效，无需动）；**装出来的副本**（按下表重跑安装命令刷新）。

| 接入形态 | 怎么升级 |
|---|---|
| 引擎副本（`guardrail-engine init` 出来的 `<HARNESS>`，默认 `~/.guardrail-engine/engine`） | `upgrade.py` 的 [5/7] 会自动刷新（源是本包轮子自带的引擎；装完逐字节对账，旧副本改名归档可回退）。手动等价命令：装新 wheel 后跑 `guardrail-engine init --force`；内容相同则幂等（`引擎已是最新`） |
| MCP 注册（args 指 `<HARNESS>/guardrail_engine/mcp.py`） | **无需重新注册**——路径不变；引擎内容按上一行更新 |
| git 提交钩子（`core.hooksPath → <HARNESS>/harness/hooks-dir`） | **无需重装**——路径不变；引擎内容按上一行更新（**不要**为了升级把钩子重装一遍） |
| 编辑期 shim（`~/.guardrail-engine/hooks/` 与 `harness.path`） | **无需重装**——它只存指针；只有**换过机器 / 挪过引擎目录**时才重跑 `python <HARNESS>/sandbox/install_shim.py`（体检：`--check`） |
| 全局守则（`~/.guardrail-engine/GLOBAL_AGENTS.md`） | ⚠ **这一样要重跑才更新**（与上一条不同）：它是**渲染出来的正文**，不是指针。引擎仓里那份守则改了（`git pull` 拿到）之后，跑一次 `python <HARNESS>/sandbox/install_shim.py` 才会重渲染哨兵区；**哨兵区之外你自己写的部分不受影响**。`--check` 会报三样：状态、哨兵是否齐、**以及哨兵区里烘的路径还是不是当前这个仓**（挪过引擎仓却没重跑时，它会当场说出来 —— 只提醒、不影响退出码） |
| 技能副本（`skill install` 装入宿主目录） | 重跑 `python <HARNESS>/guardrail_engine/cli.py skill install --target <宿主>`（幂等，增量更新） |
| 人用 CLI（pip 装过 wheel） | `pip install -U "<本包目录>\__WHEEL__"`，随后引擎副本由 `upgrade.py` 的 [5/7] 自动刷新（手动等价：`guardrail-engine init --force`）；**同版本号**时 pip 会说 `is already installed with the same version as the provided wheel` 而**一个字节都不换**（rc 仍是 0），改用 `pip install --force-reinstall "<本包目录>\__WHEEL__"`（`upgrade.py` 会装完对账产物，遇到同版本号时**替你跑掉这一条**再对一次账） |
| Claude Code 插件 | 固定目录：`claude plugin marketplace update agentguard-tools` → `claude plugin update agentguard@agentguard-tools` → 重启宿主<br>**换过目录**：第一步改成 `claude plugin marketplace add "<新包目录>\marketplace"` —— 同名市场会被**静默改指**到新路径，`plugin update` 照常生效<br>两种都不需要 `uninstall` / `marketplace remove`（同版本号见 5.1 第一个坑） |
| OpenClaw 扩展 | 用新包 marketplace 重装，或让 `upgrade.py` 就地覆盖 `extensions/agentguard` → 重启桌面端（完整步骤见 `OPENCLAW.md`） |

**每次换包都必须重跑「二」的第 0 步 `fix_paths.py`** —— 新包里的插件配置又是占位符了。
「我的机器没变」不构成跳过的理由：**变的是包，不是机器**。

> **引擎副本由 `upgrade.py` 的 [5/7] 自动刷新**：源是本包轮子自带的引擎（不经 pip 安装状态，
> `--skip pip` 时同样成立）；装完**逐字节对账**，内容没变时 init 幂等返回 `引擎已是最新`，
> 内容变了才归档旧目录再复制。它只认 `guardrail-engine init` 写下的所有权标记
> （`<HARNESS>/.guardrail-engine-init.json`）：`--harness` 是仓库（带 `.git`）或来历不明的目录时
> **不刷新** —— 对仓库跑 `init --force` 会把源改名归档。手动等价命令：
> `guardrail-engine init --force --dir "<HARNESS>"`。

升级后自检：`python <HARNESS>/guardrail_engine/cli.py installed`（本机安装矩阵：哪几路是哪一版）
｜ `claude plugin validate --strict "<本包目录>\marketplace"` ｜ OpenClaw `plugins list`
｜ `python <HARNESS>/sandbox/install_shim.py --check`。

换新机器：先按「二」的第 0 步跑一次 `fix_paths.py`。

> 小建议：后续升级建议先在 `<HARNESS>/VERSION` 升个号再打包（`python sandbox/pack_release.py` 自动带版本号命名），
> 这样 CLI / 插件升级走常规路径，未升级的机器一眼可辨。
> 打包脚本会拿**上一次同版本**那份包的指纹（`PACK-INFO.json` 里的 `pack_sha1`，覆盖整包内容）
> 与本次对比：同版本号但**内容不同**会出警告 —— 那正是「同事拿到新包、`plugin update` 却什么都没换」
> 的前兆，看到就升号重打。轮子是按**内容**（不是文件字节）入指纹的，所以单纯重跑一次打包不会误报。

---

更多背景与排障：见仓库 `docs/packaging/RUNBOOK.md`。
