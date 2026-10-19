# CHANGELOG

版本号单一来源：`VERSION`（打包路线 M0，2026-09-14 起）。

**每次发版必须留下与 `VERSION` 同名的版本段**（`## [<版本>] — <日期>`）。理由：本文件此前
只有 `## [Unreleased]` 一个标题，没有任何机制能把某条改动与某次发布对上，而 `docs/releases/`
下每个 tag 都有自己的发布说明（剖析报告 2026-09-17 顺带发现）。发版时把 `[Unreleased]` 里
属于本版的内容移到新的版本段下即可。

## [Unreleased]

> 本段是 2026-09-24 的**补记**：下面四批都已并入 `main` 并推了两远程，却从未进过本文件
> （与 `0.1.7-dev` 段里那段「另外三批……本次一并补记」是同一个情形 —— 那次的教训没拦住这次）。
> 共同点是**动的都是「自证体系」自己**：变异冒烟、它的过滤器、以及看着它的那些锁。
> 也正因如此，其中三条红是「本机绿、CI 红」，一条是「看着有锁、其实没咬住」——
> 都属于**只在本套体系内部才会缺席的失效**。
> ★ 上面各段里的「95 条」是**当时的真实值**，按本文件一贯口径不改。⚠ 本行原先写的是
> 「到本段末尾护盾为 **97** 条」—— 那句话写在 2026-09-24，此后 `[Unreleased]` 又长出了
> 09-29/09-30 的几段，**本段末尾已经不是那一处了**，留在这里就成了假话。按「指针必须指到
> 当下实况」改：**截至 2026-10-07 本段末尾，护盾为 126 条**（97 → 103 → 105 → 107 → 108 →
> 109 → 110 → 112 → 118 → 120 → 122 → 124 → 126，逐段在各节末尾记着；10-07 的 `PC-VALIDATE`
> **没有**新增变异护盾，只加直接回归锁，故仍是 126）。★ **2026-10-15 范式指路批 +2（M133/M134）**
> ⇒ 冒烟名单 **129** 条（lock-inventory 名单登记 127：M131/M132 簿记缺口未补，见下方 10-15 节）。★ 同一个坑本行**又差点踩第二次** ——
> 10-01 那次把指针指到 118 时，10-02 的 `M125`/`M126` 还没落地；**指针指的是「本段末尾」，
> 而本段还会长** ⇒ 每次往本段添节，这行都得跟着走一遍。

### Fixed（2026-10-15i：批次 7 —— 超时杀整棵进程树 + 全量扫描超时分档）

- **病灶**（实测）：`subprocess.run(timeout=...)` 超时只 kill **直接子进程** —— 孙进程（例：
  cli→run_gate→检查脚本里的最后那个）变孤儿继续跑。一次 `cli.py run gxgk_backend --all` 的
  「300s 超时」实测：孤儿又跑了约 **100 分钟**，父进程阻塞在管道 EOF 上，最终输出全丢
  （rc=2 超时消息）。同时 `cli.py run` 的 300s 硬上限**不可调**，大仓全扫不可能完成。
- **修法**：
  - 新增跨文件契约块 `HARNESS-KILLTREE`（`_kill_tree` + `_communicate_tree`）：Windows
    `taskkill /F /T`、POSIX `killpg`（spawn 时 `start_new_session`）；杀树后**再收尾一次
    communicate**（否则仍阻塞在读端）；`cli._run` 与钩子两端逐字节同源（对拍锁）。
  - `cli.py run` 新增 `--timeout`；`--all`（全量）默认放宽到 **3600s**，定向检查保持 300s。
  - 钩子超时路径单列留痕 `skip-hook-timeout`（与崩溃 `skip-hook-error` 区分）。
- **锁**：test_agentguard 37→40（行为：超时杀孙进程 + 必须快速返回；双端对拍；
  `--all` 超时分档）；护盾 +2 M143（taskkill 丢 `/T`）/M144（全量超时退回 300）。
- **实测**：三层进程链（cli→spawner→sleep 孙进程）超时 4s：孙进程被杀、调用 ~4s 返回
  （旧实现会阻塞到孙进程自醒）。

### Fixed（2026-10-15h：批次 6 —— 编辑期仓根改从**文件路径**反推；CodeBuddy IDE「cwd=安装目录」根因修复）

- **病灶**（CodeBuddy IDE 抓包实测，payload 原文已核验：`~/.agentguard/hook-payload-last-codebuddy.json`）：
  CodeBuddy IDE 的 `payload.cwd` 是**自己的安装目录**（`D:\...\Programs\CodeBuddy CN`）、
  不是工作区 —— 钩子旧实现拿 cwd 当仓根：① `in_tree` 判「不在树内」→ `skip-out-of-tree`
  ② `--repo cwd` 指向安装目录 ⇒ **编辑期门禁 100% 静默放行**（Write/Edit 两种工具都验过；
  `filePath` 一直是可取的绝对路径——提取环节没坏，是 cwd 用错位置）。
- **修法**：新增 `_repo_root_for(path)`（`git rev-parse --show-toplevel` → 向上找 `.git` 兜底）；
  有文件时先反推仓根再交 `--repo`；反推不到（文件不在任何 git 仓内，例：/tmp 重定向目标）
  才退回旧判据（in_tree vs cwd）。cwd 只在取不到文件时兜底。
- **顺带**：心跳/跳过记录增 `tool` 字段（区分 Read 类噪音与 Write/Edit 真漏——CodeBuddy 复核
  时点名要的区分）；已知边界：Stop 事件无文件可依、仍按 cwd，CodeBuddy IDE 形态下其 cwd
  同样不可用（编辑期闭环 + `done` 收尾承担主要保障）。
- **锁**：test_entry_level 72→74（wrong-cwd 必须真跑门禁 / 仓外文件仍跳过）；护盾 +1 M142。
- **实测**：合成 payload（cwd=假安装目录 + filePath=真实文件）→ 门禁真跑、台账落
  `via=hook:codebuddy:PostToolUse`（修复前：skip-out-of-tree 静默放行）。

### Fixed（2026-10-15g：`done` 在端仓目录里选错仓 —— CodeBuddy 实测抓到；凭证补 `repo` 字段）

- **病灶**（CodeBuddy 实测，命中假凭证风险面）：在端仓目录下跑 `cli.py done`（不带 --repo），
  被识别成 **harness_self**（工程仓）并打出 rc=0 凭证 —— 挑错仓比不产凭证更危险：
  「没查端仓」看着像「查过且通过」。
- **根因**：`_run` 的子进程默认 `cwd=ROOT`（工程仓）；而 done 不带 --repo 时由 auto-gate 用
  **os.getcwd()** 识别仓库 —— 子进程 cwd 错 = 识别错。
- **修法**：done 显式把**调用方 cwd** 传给子进程；凭证行增 `repo=<仓目录名>` 字段
  （识别错一眼可核）；SKILL 文案同步（端仓根直接跑可省 --repo）。
- **锁**：test_agentguard 新增 `test_done_uses_caller_cwd_when_no_repo_flag`（回归：
  不带 --repo、cwd=端仓 → 必须 gate=t 且不得出现 harness_self）；护盾 +1 `M141`。

### Fixed（2026-10-15f：批次 5 —— 余账清算：`AGENTGUARD_DIR` 主名统一 + doctor 指针新鲜度 + M131/M132 补登）

- **背景**：早前定的「路径链三小修」里，entry 兜底已随批次 3 落地；另两件（env 名统一 /
  doctor 指针新鲜度）与 M131/M132 簿记缺口仍是欠账（用户 2026-10-15 复盘点出「处理了吗」）。
- **env 主名统一**：解析读取层（钩子正本 / shim / opencode 插件 / check_client_configs /
  install_shim）改为 **AGENTGUARD_DIR 优先、HARNESS_DIR 兼容**；文档与文案同步主推主名
  （README / README_推广接入 / SKILL / integrations 各 README / agent-rule-snippet /
  bootstrap / setup_machine / render 注释）；**34 个业务仓指针块 `--force` 重注入**，
  「每仓恰好 1 份」校验通过。
- **doctor.sh 新增 1.6/4 指针文件新鲜度**：存在时必须指向含 `harness/run_gate.py` 的目录
  （陈旧指针 ❌ 并给修复命令；空文件 ❌；缺文件 ℹ；指向另一克隆 ⚠）。实跑：❌0 / ⚠0 全绿。
- **M131/M132 补登**：对账 smoke(134) ↔ inventory(132) 恰好只缺这两条（10-05 落地时漏登）
  —— 补入后两名册完全对齐。
- **测试同步**：五处（test_install_shim / test_client_config_check / test_review_v5 /
  test_review_v6 / test_review_2026_09_23）把环境变量隔离/钉住扩到**两名** —— 不扩则主名
  会盖过旧名，那几把锁会变成环境依赖。
- **验证**：entry_level 72 / v5 23 / v6 19 / 2026_09_23 97 / checks 83 / install_shim 8 /
  bootstrap 4 / fix_paths 23 / client_config 13 全绿；四个 sh 脚本 `bash -n` 通过；
  全量 fast 见下。
- Hermes SOUL 文案在 `integrations/hermes/README.md` §4（两句）—— harness 侧已备好，粘贴生效是用户侧动作。

### Added（2026-10-15e：批次 4 —— 钩子可见性三件：心跳 / 跳过不静默 / payload 捕获）

- **病灶**（CodeBuddy 对照实验 + 台账实读）：IDE 形态 PostToolUse 零记录，而「钩子没被调」
  与「调了提取失败被静默放行」**不可区分**（两者都不写台账）；核对还发现其 Stop 实际触发过
  （19:51:26 `via=hook:codebuddy:Stop` rc=1）——精确失效形状是「逐次反馈缺 + Stop 回灌断」。
- **三件可见性机制**（全部 best-effort，不阻塞/不抛；目录 `HARNESS_HOOK_ACTIVITY_DIR` 可覆盖）：
  ① 心跳 `~/.agentguard/hook-activity-<client>.json`（覆盖式：当日计数 + 最后决策 +
  last_target）；② 写类事件提取失败 → `hook-skips-YYYY-MM.jsonl`（60s/客户端节流；读类只
  心跳不落 skip —— 防刷屏）；③ payload 捕获：`~/.agentguard/hook-dump.on` 开关 →
  每客户端最近一份、长串截断（`<len N>`），关掉即完全不写盘。
- **可选降级**：`HARNESS_HOOK_FALLBACK_SCAN=1` 时提取失败改全仓扫（**默认关** —— 先量清
  miss 频率再决定，避免会话里每次 ~8s 拖慢 IDE）。
- **锁与验证**：test_entry_level 69→72（TestHookVisibility 3 例：写类→心跳+skip 行 /
  读类不落 skip / dump 脱敏截断 + ran 心跳）；护盾 +2 M139（拆心跳）/M140（拆跳过记录）；
  lock-inventory total 1158→1161。实验协议见 `integrations/codebuddy/README.md` §7。

### Added（2026-10-15d：批次 3 —— `cli.py done` 一步收尾 + 台账归因三件）

- **背景**（CodeBuddy 漏跑排查现场）：台账分不清来源 —— 编辑器钩子全塌缩成 entry=auto-gate；
  直调 run_gate 的记录 entry 为空（10 月实测 **79 条**）。「谁跑的」答不上来，漏跑就无法定位。
- **`cli.py done`（评审建议 #2/#3）**：一条命令 = 跑 auto-gate + 台账回读 + 打**可验证凭证**
  （`[harness] gate=… rc=… files=… ts=… h=…`，h = 台账行链哈希前 8 位，复核命令随凭证一起打）；
  rc=3 不产凭证；凭证只含机器字段（注入边界不变）。
- **归因三件**：① 钩子正本硬赋值 `HARNESS_ENTRY=hook:<client>:<event>`（via 落台账，
  CodeBuddy/Claude/Codex… 各家触发一眼可分）② `run_gate` 无标直调兜底 `entry=run_gate`
  ③ doctor / demo_gate / acceptance 三个直调脚本显式命名。`cli log` 表格与 `--export` 增 via 列。
- **锁与验证**：test_entry_level 63→69（实测值，含此前的 2 条欠账一并对准；
  TestLedgerEntryFallback 2 + TestHookEntryAttribution 2）；test_agentguard 34→36
  （TestDoneCommand 2：凭证 h 与台账一致性 / 非受管不产凭证）；护盾 +2 M137/M138（--only 双红）。
- **新增部件四问自答**：①负担：三步必漏一步 + 来源不可归因 → 一条命令 + 一列 via；
  ②锁/验收：上述 6 例 + M137/M138；③替代：skill 里的三步手工流程（已改）；
  ④报平安风险：done 在「无台账记录」时**明说"无凭证"**、不为省事造假凭证；凭证可被 agent
  伪造是诚实声明的边界 —— h 与台账链对得上才算真凭证（复核命令就在凭证下一行）。

### Fixed（2026-10-15c：el-drawer 陈旧禁止清单 —— 用户当场抓到「同一份文件自相矛盾」）

- **病灶**（用户 2026-10-15 复盘批次 2 时指出）：候选表 P4 行标题写着
  「禁直用 `el-table/el-form/el-dialog/el-drawer`」—— 09-01 的陈旧枚举，没跟上 09-19
  决定（项目无 `eagle-drawer`，抽屉直接用 `el-drawer`）。同一病症还在两处：
  `pc_CODE_RULES.md` 第二章 L67 的禁用枚举也带着 `el-drawer`（**与同文件 §6.3/§10.1
  「el-drawer 可直接用」自相矛盾**）；`pc_vue_form_drawer.md` 开头的项目约定句
  （「弹窗用 `el-dialog` / 抽屉封装 `eagle-model` / `el-drawer`」——弹窗正解是 eagle-model，
  句子本身也乱了）。
- **改法**：三处按 09-19 决定改写（禁用枚举只留 el-table/el-form/el-dialog + 例外说明，
  指向 §6.3/§10.1 与 `PC-ELFORM`）；候选表 P8 落地状态行补 09-19 移出注记。
- **锁**：`test_docs_consistency.py` 新增 `TestElDrawerDecisionIsConsistent` 2 例 ——
  ①规则文档里同一行「el-drawer + 禁」必须带放行标记（标记刻意**不收**「确无对应能力」，
  那句笼统豁免正是旧 L67 的尾巴，收它=给旧文案发通行证）；②allowlist 的 el_deny 只许
  `el-dialog`。护盾 +1 `M136`（把 L67 变异回旧文案 → 锁红）。
- ⚠ 顺带修一个环境障碍：`pc_CODE_RULES.md` 带 ReadOnly 属性，导致 edit 的 temp+rename
  写入 EPERM 连撞两次 —— 已清除该属性（仅此一个文件；重试前先读盘确认未写入，非盲目重放）。
- **验证**：test_docs_consistency 31→33 全绿；M136 `--only` 实跑红；全量 fast 见下。

### Added（2026-10-15b：批次 2 —— PC-ELFORM 扩展 +el-dialog；P7 被复量杀死；Hermes 自查入口）

- **el-dialog 规则化（候选表 P4 的改动行拦截部分落地）**：`PC-ELFORM` 扩展 `<el-dialog(?![\w-])>`
  （error，按行过滤，文案指向 eagle-model）。复量先行（没复量的数不进文档）：11 个 PC 仓
  views 下 **350** 个文件含 `<el-dialog`（跨仓同源存量页为主）、`src/components` **33** 个
  （全是 eagle-model / eagle-dialog-form 等包装件本体）、`<eagle-model>` 在 **8558** 个 .vue 在用 ——
  封装存在且是主流，与 el-table/el-form 当初上 error 的理由同构；存量 350 文件只拦改动行，不回溯。
- **新增 `src/components/` 排除（PC-MODAL 同款）**：平台包装件合法持有底层标签
  （eagle-form 含 el-form、eagle-page 含 el-table、eagle-model 含 el-dialog）——排除前，
  在包装件里新增一行 `<el-form` 会被误拦（平台维护路径 ≠ 业务违规）。行为变化：
  PC-ELFORM 对 src/components 下的 el-form/el-table/el-dialog 全部不再报。
- **el-drawer 刻意不收（对本仓评审报告的纠正）**：allowlist el_deny 注释记录 2026-09-19
  已确认 11 仓**不存在** eagle-drawer 组件、业务决定抽屉直用 el-drawer —— 评审报告里
  「el-dialog/el-drawer 升 error」的后半句按现状否决，加回来等于报指向不存在组件的假规则。
- **P7（前端全量拉取后分页）被复量杀死**：453 个 views 文件含 `.slice(`，抽样 40 条：
  编号补零 / 卡片截取前三条 / 字符串截断等合法用途为主，真分页违规极少 —— 机械判据
  误报率不可接受（PC-VALIDATE 337/465 同款教训），**不落地规则**，候选表 P7 行记实测数、
  维持观察项。纪律工作的正面样本：测量在规则出货前杀掉了它。
- **Hermes 自查入口（评审建议 #3 的 harness 侧）**：`cli.py log` 新增 `--gate <名>` 过滤
  （--export 同受约束）；窗口内无该 gate 记录 → rc=1 + 稳定标记 `NO_RECORD`
  （「查到了」与「压根没跑过」必须机读可分，否则自查形同虚设）。skill 增
  「任务完成前自查」节；hermes README SOUL 绑定从「可选」改「必做」并给出两句具体文案。
- **顺带修一处既有漂移**：端级模板 ID 速查表漏登记 `PC-ELFORM` / `PC-MODAL`
  （2026-10-09 落地时没进表）—— 本批补上；PC-ELDENY 行注明「改动行拦截已由 PC-ELFORM 承担，
  本条保留作整文件观察层」。
- **锁与验证**：test_pc_elform_modal 19→23（新建业务页报 / src/components 不报 /
  存量行不因改别行而报 / eagle-model 与 el-drawer 不误报）；test_agentguard 33→34
  （--gate 过滤 + NO_RECORD 契约）；护盾 +1 `M135`（拆 el-dialog 扫描项 → 新例红，
  `--only` 实跑 RED、对照绿 86s）。lock-inventory total 1143→1148、名单 +1。
  全量 fast 1163 例，失败仍仅基线 2 条（wheel 版本锁 / 字节码锁）。

### Added（2026-10-15：范式指路批次 1 —— PC/H5 命中输出补 `[规则ID]` + `📖 See:`）

- **病灶**（评审报告 2026-10-15 + 外部 agent 确认一致，且实读输出链路发现比报告更重的一层）：
  PC/H5 的 Finding→字符串转换点只拿 `fnd.message`，**rule_id 就地丢弃** —— 命中行只剩
  「残留 console.log ×3」这类文案，没有规则 ID；同时 PC/H5 全部规则没有 `see`
  （后端 R12 一直有 `see="golden-patterns/backend/entity-layering.md…"` 先例）。两个后果：
  ① 端级模板「被拦时门禁会把 ID 直接打给你」在 PC/H5 不成立，agent 只能猜 ID
  （豁免要求「ID 必须写对，写错不生效」）；② agent 被拦的那一刻最愿意读范式，
  文案却只说「应走 eagle-form」不给路径 —— 走廊断在最后一步。
- **改法**（只动输出文案层，判定语义零变化：命中集合 / severity / rc / 豁免判据全部不动）：
  - `Rule.run` 统一把规则类新增的 `see` 类属性盖章到 Finding（`f.see or` 保序，
    后端原有 see 不受影响）；
  - 新增模块级 `format_finding()`（唯一格式化出处）：输出 `[规则ID] 消息`，see 非空时
    另起一行 `📖 See: <文档路径>`（与 backend 的 `❌ RULE L行` / `📖 See:` 同款）；
    PC/H5 共 5 处转换点（PC syn + PC_RULES，H5 syn + infos + problems）全部改走它；
  - PC 8 条范式规则（COMP/ELDENY/ELFORM/ELMSG/MODAL/VALIDATE/AXIOS/VAUTH）+
    H5 2 条（COMP/UPLOAD）声明 see，锚点逐条实读核对真实存在（§10.1-10.2 / §6.1-6.2 /
    §9.1 / §7.1）；卫生/引擎行为类规则（CONSOLE/IP/TODO/CRLF/VFOR/SYNTAX/EOL/SFC）
    **刻意不配** —— 没有对应范式文档，硬造指针等于指歪路。
- **锁**：新增 `tests/test_paradigm_pointers.py` 7 例（必带 see / see 路径仓内存在 /
  机器无关 / run 盖章 / format 带 ID / see 不含豁免语法——与 09-30 注入边界一致）；
  护盾 +2：M133（拆 run 盖章）、M134（丢 `[规则ID]` 前缀），实跑 `--only` **双红**、
  未变异对照绿（41s）。lock-inventory：模块 +1（total 1136→1143）、名单 +2。
- ⚠ **顺带发现的簿记缺口（本批不动，留口径确认）**：`mutation_smoke.py` 里 M131/M132
  （2026-10-05 落地）一直没进 lock-inventory 名单（名单止于 M130）。本批只登记自己的两条。
- **输出样例**（探针实测）：`✗ [external] x.vue: [PC-ELFORM] 直用原生 el-table ×1（…），首个在 L2`
  + `📖 See: golden-patterns/pc/pc_page_patterns.md §6.1…`。
- **验证**：test_checks 83 / test_pc_elform_modal 19 / test_pc_validate_rule 13 /
  test_docs_consistency 31 / v13 batch2-4 + entry_level 合计 148 例全绿；
  既有失败 2 条与基线一致（`test_agentguard` wheel 版本锁、`test_lock_inventory` 字节码锁），
  非本批引入。

### Added（2026-10-07：`PC-VALIDATE` —— 候选表 P11 落地；以及这条规则**自己先写错的两处数**）

- **病灶**（客户仓实测，非构造）：`eagle-rmc-pc-net-hy-hzy` 安全生产标准化「实施依据」弹窗
  （`standardList/lawEdit.vue`）把 `ref` 绑到 `<eagle-form>` 却用了原生 `el-form` 的
  `validate((valid) => { if (!valid) return; … })` 签名。封装层
  （`src/components/eagle-form.vue` 的 `validate`）**只在校验通过时零参调 `callback()`**，
  `valid` 于是恒 `undefined` ⇒ 回调一进来就 `return`，**点确定没反应、控制台一个字都不输出**。
- **三种失效形态是实测不是推断**：复刻 `validate` 的分支逻辑跑 node 模拟（无第三方依赖），
  对「校验通过 / 失败」两个方向各跑一遍 —— `if(!valid) return` 通过时也**不提交**；
  `if(valid){ok}else{fail}` 通过时**恒走失败分支**；`(valid, obj) => { submit() }`
  提交照常但参数全 `undefined`（**这条推翻了我先前写进文案的「校验被整体跳过、照样提交」**）。
- **判据必须两步配对**（抽 `<eagle-form ref="X">` 的 ref 名 → 再找 `X.value.validate((valid`）。
  纯 grep 不可用的代价有实测数：14 个 PC 仓 / 31131 个 .vue 里文本命中 **337 个文件 / 465 处**，
  配对后真违规 **1 个** ⇒ 按文本拦误报 **336/337**，门禁第一次就会被当 bug 关掉。
- **存量那 1 个不改**：`agentguard_pc_net-gxgk` `hazardousOperation/hazardousOperationManage/v2/form.vue:1212`
  （第三种形态，`c57cf73e8` 2026-08-24 已提交、工作区干净）。它当前不致命，属埋雷 —— 不是本仓，
  不在这次改动范围，**门禁只咬新增行**，留着作为"error 级不会炸存量"的证据。
- ⚠ **本段如实记两条我自己写进去、随后被全量实测推翻的数**：「**存量 0**」和「文本命中 **244** 个文件」。
  成因同一个：探针正则 `\b(\w+)(?:\?\.)?\.validate` 把 `value` 当成了 ref 名，抓不到
  `formRef.value.validate((valid, obj)`，又只在 11 个仓的局部范围跑 ⇒ 真违规漏掉、分母也错。
  按「没复量的数不写进长期文档」重跑全量后，所有落点 —— 规则文档串、候选表 P11 行与 3b 段、
  回归锁文件头、端级入口 `AGENTS_pc_slim.md` 第 8 条、`pc_page_patterns.md` §5.6 第 13 条、
  以及本仓之外的那条项目记忆 —— 一并改成 **1 / 337·465**。
- **顺带挖出的既有边界（本条只记录，未一并改）**：`extract_template` 用非贪婪
  `<template>(.*?)</template>`，在**第一个嵌套 `</template>`**（`#body`/`#footer` 具名插槽的闭合）就截断 ——
  lawEdit.vue 155 行只截到 15 行，而目标 `<eagle-form ref>` 恰在截断之后，**用 `ctx.template` 会对
  目标案例本身静默漏检**。本规则改从 `ctx.content` 抽 ref 并加锁；**同一截断同样影响
  `PC-ELDENY` / `PC-VFOR`**，那是既有边界，另案处理。
- 文档落点：`pc_CODE_RULES.md` §9.1（三形态实测表 + 两种签名对照表）/ §10.5 / §14.3、
  `pc_page_patterns.md` §5.6 第 13 条、端级入口 `AGENTS_pc_slim.md` 第 8 条（**从"软约束"升为有门禁**，
  故"无门禁兜底"那行回退成第 3、4、6、7 条）+ 门禁 ID 速查表加 `PC-VALIDATE`、候选表 P11 行改"已落地"、
  `README.md` 已实测边界表加两行（中转变量 / 动态 `:ref` 均实测不报）。
- **验证**：新增 `tests/test_pc_validate_rule.py` 13 例全绿（含"原生 `el-form` 不许误报"和
  "嵌套 `</template>` 之后的 ref 仍要报"两把反向锁）；`tests.test_checks` 83 OK、
  `test_docs_consistency` 31 OK；`unittest discover` 基线 1117 → **1130**（+13，全绿），
  `sandbox/run_tests.py --fast` 合计 **1130 例 ✅ 全部通过**。门禁对**修好的** lawEdit.vue
  rc=0，对仓内临时植入的坏版本 rc=1 并点名 `PC-VALIDATE @ L112`（临时文件已删，确认不存在）。
- ⚠ **这条新规则第一次红是被自己的 hygiene 锁咬的**：文档串里写了 `D:\MyProject\…` 的绝对路径，
  `\M` 成了非法转义 ⇒ `test_source_hygiene` 红。锁咬得对（本仓文档本来就不该带本机路径），
  改成"14 个 PC 前端仓"不带路径后绿。这是"新代码第一次不绿，恰恰是体系在工作"。
- 护盾 **126 → 126**（本段加的是直接回归锁，没有新增变异护盾）。

### Fixed（2026-10-05：知识库根在**留守行 / 标题 / `## 项目`** 里原样出货 —— 「看着有锁、其实没咬住」的第二次同形复发）

- **病灶**（2026-10-05 实测，**真出过**）：`agents_audit._target_lines` 把 `portable()`
  （知识库根 → `<VAULT>` 占位）**只挂在处理「搬迁行」的那一个分支上** —— 首行标题 /
  `## 项目` 块 / 留守行三处都只做 `rstrip()`。⇒ 同一条知识库路径，写在搬迁行里变成
  `<VAULT>`，写在留守行里**逐字进产物**。那是**维护机**上的目录：同事 clone 客户仓后
  这一行指向一个不存在的地方，而屏幕上一切正常（与 2026-09-22 那次「`<HARNESS>` 换了、
  知识库根整类漏了」同源，只是这次漏的是**另一个位置**）。
- **为什么现在才发现**：A 组 22 仓没有哪个在留守行里带知识库根 ——
  `gxgk` / `hy_hzy` 三端 + `hanbogaoxin_backend` 这 7 个是**头一回**踩到。
  ⚠ 而当时那把锁（`TestVaultRootNeverShipsAsAMachinePath`）**全绿**：它的夹具
  `REAL_VAULT_LINE` 是 `知识库：` 开头的**搬迁**行，走的正是唯一调过 `portable()` 的分支
  ⇒ **判据看着覆盖了"知识库路径"，实际放过了另一半**。
- **改法**：新增 `agents_audit.product_line()`（**源行 → 产物行**的唯一出处：摘 `知识库：`
  标签 + `portable()`），`_target_lines` 末尾对整份产物**收口一次** ——
  不再逐处调用，将来新加一段输出也绕不过它。骨架行不含知识库根，过一遍是空操作。
- ★ **第二半（不做就会变成假警）**：生成器的逐行自检 `agents_scaffold.lost_lines` 原先在
  **本地镜像**这份换算式子（`_moved`），而且**只对搬迁行**做。产物那侧换、自检那侧不换 ⇒
  留守行带知识库根的仓会被判成「丢知识」⇒ **整仓不写**。产物本来是对的，工具却拒绝出货
  （假警比漏报更贵：它教人不再相信这个自检）。现在两侧**都问 `product_line()` 这一个函数**，
  本地镜像整个删掉 —— 「自己给自己发合格证」这条纪律从此由**构造**保证，不再靠人守。
- **锁**：`tests/test_agents_scaffold.py::TestVaultRootNeverShipsInAStayLine`（5 条）。
  `setUp` 先断言夹具确实踩在 `VAULT_ROOTS` 上、**且不是搬迁行**（退化成搬迁行就与旧锁
  重合、重新变成「看起来有覆盖」——那时候它自己先红）；另有一条把**真** `target_form`
  的产物喂给**真** `lost_lines`，钉死两侧换算一致。
- **两向注入证伪**：新护盾 `M131-portable-back-to-move-lines-only`（把收口换回"只作用在
  搬迁行" —— 刻意保留搬迁行仍换占位，那才是真实事故的形状：旧锁全绿、缺陷照常出货）与
  `M132-self-check-transforms-move-lines-only`（自检那侧只换算搬迁行）。实跑
  `--only=M131…,M132…`：**双 RED**（各 `failures=1`），未变异对照绿，47s。
  ★ 两条**必须分开**：M131 打「产物换没换」，M132 打「自检的换算式子跟没跟上」。
- **读数**：`test_agents_scaffold.py` 50 → **55** 例；总例数 1112 → **1117**；
  护盾 **124 → 126**。**全量冒烟**（动过被变异瞄着的产品文件 ⇒ 按规矩跑全量、不是 `--only`）
  = **126 个变异：命中 125，平台盲区 1（`M115`，本机 nt），失败/跳过 0，183s**。
  全量 `sandbox/run_tests.py --full -j 4` = **1117 例（skipped 0）/ 55 个文件全 ✓ /
  `⚠句柄` 0 / CI 同源正则 0 命中 / 真退出码 0 / 墙钟 349.0s**（★ 读的是本行 `REAL_EXIT=`，
  不是 `| tail` 里的 `Exited with code 0` —— 同 09-30 那条教训）。
- **出货耦合：无**（`sandbox/agents_audit.py` / `sandbox/agents_scaffold.py` 都不在
  `sandbox/pack_release.py` 的 `copy_shipped` 名单里）⇒ 发布包逐字节不变、`v0.1.7-dev`
  仍指 `a3bea26`，**不挪**。

### Added（2026-10-04：产物出生证明 —— 让「对着更早的源生成」这件事出得来声）

- **病灶**（2026-10-02 实测，**真出过**）：`sandbox/agents_scaffold.py --emit` 产出的是**拟改产物**，
  而它可能**对着更早的源**生成。`D:/Rain/agents_scaffold_out/` 那 22 份就是：源文件后来补的那半句，
  产物里没有。**而没有任何东西会报出来** —— 产物看上去完全正常，`lost_lines` 那套逐行自检
  照样过（它比的是「记录下来的那几行」，而那份记录本身就是旧的）。`agents_audit.py` 的判据再准
  也救不了：它只看**当下**的源，不看产物是哪来的。
- **改法**：每次 `--emit` 在同目录写一份 `_sources.json`（**出生证明**），逐仓记 源字节哈希 /
  源字节数 / 产物字节哈希 / 产物字节数，外加一份**引擎自指纹**（生成器 / 判据模块 / 归宿表）。
  新增**只读**模式 `--verify <目录>` 拿它复核，回答的只有一个问题：**这份产物，还是照着这台
  机器上现在那份源生成的吗？** 两层判据缺一不可 —— 只记源，「产物被手改」查不出来；只记产物，
  「源换了却没重生成」查不出来。
- ★ 四条取舍（都写在模块头注里，免得下次被当成 bug）：
  - **先记哈希、再生成**：`emit` 循环第一行就是 `src_sha = fingerprint(r["path"])`。万一有人在这两步
    中间改了源，最坏结果是复核吵一句「源已改」，而不是「产物按旧源生成、证明却按新源签发」——
    反过来的顺序没有这个性质，那是本功能唯一会骗人的写法。
  - **fail-closed**：源缺失 / 键在本机覆盖层里找不到 / 目录里冒出没登记的仓，**全算不合格**；
    没有出生证明直接 **rc=2**（**无凭可核 ≠ 没事**）—— 2026-10-04 之前生成的目录正属此类。
  - **出生证明里一个本机路径都不许有**：仓路径复核时按**键名**从 `config/harness.local.yaml` 现取。
    存了绝对路径，这张证明就只在生成它的那台机器上读得懂（同 `<VAULT>` 那个理由）。
  - **`--only` 重发要合并**：把别的仓的记录抹掉，换来的是**一片假的「未登记」**。
- **实测**：`--verify D:/Rain/agents_scaffold_out` → **rc=2**，原话「无凭可核」—— 那正是这次的现场。
  ★ **没有**拿这个真目录再跑一次 `--emit` 去"补证"：那 22 个仓的 `AGENTS.md` 此后已经换成 slim 版，
  现在重生成的会是**另一批产物**，不是"给旧产物补一枚指纹"。
- **读数**：`test_agents_scaffold.py` 29 → **43** 例（新增 14 条：两层指纹各记什么 / 源改、产物改、
  源没了、键定位不到、引擎漂移、未登记目录各报什么 / 只读 / `--check` 不写 / 局部重发合并 /
  CLI 接线 / 注入证伪）；总例数 1091 → **1105**；护盾 **120 → 122**
  （`M127-scaffold-fingerprint-is-a-constant`、`M128-verify-ignores-engine-drift`），
  两条 `--only` 实跑**双 RED**（各 `failures=1`，51s）—— ★ 两条**必须分开**：M127 证明
  「哈希真的在算」，M128 证明「算出来真的拿去比了」，合成一条只能证明其中一半。
  全量 `sandbox/run_tests.py --full -j 4` = **1105 例（skipped 0）/ 55 个文件全 ✓ /
  `⚠句柄` 0 / CI 同源正则 0 命中 / 真退出码 0 / 墙钟 272.4s**（★ 读的是本行 `REAL_EXIT=`，
  不是 `| tail` 里的 `Exited with code 0` —— 同 09-30 那条教训）。
- ⚠ **本批当时留在外面的一条**（`_manifest.md` 的整写）**已紧接下一节修掉** —— 见下。

### Fixed（2026-10-04b：逐行归宿清单 `_manifest.md` 也改成**合并写** —— 上一节自己点名的那条残留）

- **病灶**（上一节「⚠ 不在本批内」那条，2026-10-04 同日收口）：`_manifest.md` 每次 `--emit` 都是
  `open(..., "w")` 一把**整写**，而一次 `--only k1` 只产出 **k1 那一段** ⇒ 清单里只剩 k1，
  其余 21 个仓看起来凭空消失。★ 它**不会**被别的东西兜住：产物还在目录里、出生证明也还在
  （那边上一节已经是合并写）—— **两份文件互相打脸**。而这比漏报更贵：**假警教人不再相信清单**。
- **改法**：`merge_manifest()` —— 「读进来 → 换掉本轮那几个仓的段（位置不动）→ 写回」，
  本轮没碰过的段**逐字留着**，新键接在末尾。表头**用旧的那份**（人在清单顶上写的备注不是
  我们的私产）。认不出旧形状（一段 `## <键>（` 都没有）才**重写一份完整的** ——
  宁可覆盖一张认不出来的清单，也不要把半个文件拼成一份"看着完整、其实少了几段"的。
  段尾空行由渲染函数统一补，免得合并几次攒出一堆空行。
- **实测**：`test_agents_scaffold.py` 43 → **50** 例（新类 `TestManifestIsMergedNotOverwritten`，
  7 条）；总例数 1105 → **1112**；护盾 **122 → 124**（`M129-manifest-is-overwritten-again`、
  `M130-merge-drops-the-old-head`），两条 `--only` 实跑**双 RED**（各 `failures=1`，36s，
  命中 2 / 盲区 0 / 失败跳过 0）。**全量冒烟**（动过被变异瞄着的产品文件 ⇒ 按规矩跑全量、
  不是 `--only`）= **124 个变异：命中 123，平台盲区 1（`M115`，本机 nt），失败/跳过 0，169s**
  —— 上一批那对 `M127`/`M128` 也照样在 RED 名单里，说明这次没碰坏它。
  全量 `sandbox/run_tests.py --full -j 4` = **1112 例（skipped 0）/ 55 个文件全 ✓ /
  `⚠句柄` 0 / CI 同源正则 0 命中 / 真退出码 0 / 墙钟 252.4s**（★ 读的是本行 `REAL_EXIT=`，
  不是 `| tail` 里的 `Exited with code 0` —— 同 09-30 那条教训）。
- ★ **两向都验过，不是只验一半**：把 `merge_manifest` 换回旧写法（只渲染本轮）→「别人的段还在」
  那条红（`test_the_lock_is_not_vacuous` 正面证）；反过来注入「永不替换」→「该换的必须真换」
  那条红（另跑一次注入，1.5 行代码，当场贴上失败原文）。**只验前者的话，「保住了别的段」可以靠
  什么都不做来骗到** —— 那正是这一类"合并"最容易被写成的假绿。
- ⚠ 同一段里那条**仍然成立**：`--only` 之后若某个仓的自检不过，出生证明会把它的记录撤掉 ⇒
  `--verify` 报「未登记」（清单里那一段仍留着 —— 它描述的确实是**还在盘上**的那份旧产物）。
  这是**有意**的：两份文件一个说"它没重生成过"、一个说"盘上那份长这样"，都不算错。

### Changed（2026-10-01：后端四条作用域/判据对齐 —— R2/R5/R6 放宽**作用域**，R3 换判据（**10-02 再扩一条通道**），R9 放宽**判据**）

- **病灶**（**不是 grep，是跑规则本体**；12 个后端仓、72,434 个 `.cs`，读数见本节末）：
  - **R2** —— `dirs` 写死三条完整前缀 ⇒ `Module/`、`Support/`（**12/12 仓都有**）、`DataReport/`
    的 Business 层**整片不在作用域**；
  - **R5/R6** —— `dirs` 只有 `["RMC","RMCEx"]`，而 `RMCHY/` 其实是**被裸 `startswith("RMC")`
    顺带蹭进来的**（不是显式覆盖）⇒ `Core/` `Module/` `Support/` `DataReport/` `Audit/`
    `EntityGenerator/` 六个组的 `.cs` 整片在外；
  - **R3** —— `forbidden` 写死 `Eagle.RMC.*` / `Eagle.RMCHY.*` 六个**全名** ⇒
    `Module/Eagle.Module.Entities` 里 `using Eagle.Module.Business;` **抓不到**；
  - **R9** —— 正则只认**裸** `Status`/`State` ⇒ 具名状态（`AuditStatus == 5`）整片不进判据，
    而它自己的 `fix` 文案点名的恰恰是 `SysConst.NormalStatus/AuditStatus/…` 这些**具名**常量。
    ★ **规则在教一种写法，却只检查另一种写法。**
  ★ 这四条本文档挂了两周（原在 `### 仍未做` 里、标着「**只量不动**」），因为它们**不是 info**：
  R2 的命中**恒记 ERROR**（`severity_default=ERROR` 且 `error_in_targeted=False`，不受 targeted /
  legacy 影响），R5/R6/R9 对改动文件升 ERROR ⇒ 放宽 = **改变拦截行为**，属业务判断。
  本批按用户拍板落地。
- **改法：一律改「模式判定」，不再穷举名单**（与 R1 的后缀判定同一口径）—— R2 新增
  `in_business()`（任一层父目录名以 `Business` 结尾）；R3 `forbidden` 由全名改**裸段名**
  （`ns.startswith("Eagle.") and f in ns.split(".")`，形态与 R2 统一）；R5/R6 `dirs` 由 2 组扩到
  **9 组**（实扫「带 `.cs` 的顶层组恰好这 9 个」）；R9 正则放宽为 `\b\w*(?:Status|State)`
  （前缀限定在同**一个标识符**内，`\w` 不含 `.`，故不会顺带命中 `StateFlags == 10`）。
  ★ `in_business()` 与旧三条前缀**逐字等价**（实扫：不以 `Eagle` 开头的 `.Business` 目录 0 个、
  深层嵌套 0 个 ⇒ **零误收**），差别只在「新模块自动覆盖」；R3 的段名判定顺带消掉
  `Eagle.RMC.ControllerX` 这类**同前缀不同段**的旧误报。
- ★ **R3：`Repository` 留在 `forbidden` 里**，并把判据由「只看 `using` 行」**扩到也认限定名**
  （用户 2026-10-02 拍板「那 12 处限定名一并收进 R3」「『只看 using 行』这个判据要扩」「走 D 治本」）。
  ★★ **本条在 2026-10-01 → 10-02 之间被整个推翻过一次，两次的原文与错因都留在这里**，因为
  这次错的不是判据而是**我的量法**：
  - **10-01 的写法**（现已被证伪，保留作教材）：「`forbidden` 里不放 `Repository`（拍板「B」）」，
    依据是「段名判定天然会收下 Core 的 `Eagle.Repository`，而它在 12 个后端仓上量出 **225 条**
    命中、**全**落在实体文件模板头第 3 行、**没有一条**是真类型依赖」（当时还抽了 4 份逐字看，
    并据此把 `checks/backend_layer_check.py:23` 的 R3 章程里那个 `Repository` 一并删掉）。
  - **10-02 复量（跑规则本体，不是 grep；12 仓、37225 个实体作用域 `.cs`）**：
    **225 = 177 条死 using + 48 条真类型依赖**。那 48 条恰好是 **4 个路径 × 12 个仓**、用的
    **全是** `StaticManager`（声明在 `Core/Eagle.Repository/Manager/StaticManager.cs`，
    `namespace Eagle.Repository`），且多为**运行时调用**（`DangerTemplateEntity.cs` L105、
    `RentalOrderEntity.cs` L83/93、`ConfigEntityExtensions.cs` L48）。
  - ★★ **错因是判据用错了**：拿字符串 `Repository` 去 grep 正文，而在用的**类型名是
    `StaticManager`** —— 这个词里**没有 `Repository` 子串**，于是「grep 不到」被我读成了
    「不存在」。**「没搜到」只能证明我没搜，不能证明它不在。**（本仓同族已栽过一次，见
    09-30 第四批的 N7。）
  ⇒ 处置：`R3EntitiesRule.forbidden = ["Business", "Controller", "ApiController", "Repository"]`；
  `backend_layer_check.py:23` 的章程行一并复位并写上订正说明。R3 是 `severity_default=ERROR` +
  `error_in_targeted=False` ⇒ **恒 ERROR**、且**吃不到** legacy 存量降级
  （`backend_ruleset.py:313` 那条分支的第一条件就是 `error_in_targeted`）⇒ 摘掉 `Repository`
  等于让 48 处**真实的**分层违规永久隐形。
  ★ 噪声（那 177 条死 using）按用户拍板**从代码侧治**（逐仓删死 using），**不在规则侧把 48 条
  真命中一起盖掉** —— 后者是治标。
- ★ **同批查出的第二个盲区：判据只看 `using` 行**。12 个后端仓各有一份
  `RMC/Eagle.RMC.Entities/Table/Plan/PlanSiteDisposalPlanEntity.cs` L70 写的是
  `var listP = Repository.StaticManager.ParamsProvider.GetParamList(…)` —— **该文件一条
  `using Eagle.Repository;` 都没有**。它能编过，是因为实体文件的 namespace 是 `Eagle.RMC.Entities`，
  而 `Repository` 是**外层命名空间 `Eagle` 的直接成员** ⇒ 按 C#「逐层向上找 enclosing namespace」
  的规则，**不需要任何 using**。⇒ `R3EntitiesRule.check()` 加**第二条通道** `_qualified_refs()`：
  ① 带 `Eagle.` 前缀的限定名（`Eagle.Repository.X`，也含 `Eagle.Support.Repository.X` 这种中间还有段的）；
  ② 裸段名 `Repository.X`（**仅** `_BARE_NS_SEGMENTS` 里的段）。两条通道**互斥**（`using` 行走原路、
  `continue` 掉），同一行不会报两次。
  ★ 扩判据的代价自己付：`Repository` 同时是常见后缀/成员名（`MyRepository` / `a.Repository` /
  `_repositoryFactory`）⇒ 带前缀那半要求尾巴有**成员名**（命名空间本身不能当值用），裸名那半只放行
  实测确认是 `Eagle` 直接子命名空间的那一个段（`Business`/`Controller`/`ApiController` 在真实仓里
  都是 `Eagle.<组>.X` 三段名，裸写解析不到命名空间）。**反向锁一口气钉了 7 条 FP。**
  ★ 已知残留（**记录在案，有意不动**）：别名写法 `using R = Eagle.Repository;` 之后的
  `R.StaticManager` 逃得过裸名那半 —— 那行别名 using 本身已被 `_using_ns` 判成 `Eagle.Repository`
  而报出（**文件仍会被拦**，只是行号落在 using 行），为它维护一张作用域表不值。
  ⚠ **残留项（客户仓侧，harness 侧先落地）**：那 177 行死 using 现在还留在 12 个客户仓里，
  由客户仓侧逐仓清理；**清完之前**全量扫描每个仓仍会多出 12~16 条死 using 噪音（无真实依赖、
  可安全删除）。期望终态：12 仓 R3 命中 = 48（裸 `StaticManager`）+ 12（新收进的限定名）
  = **60 处真违规**，噪音归零。
- ★ **刻意不改成「拆掉 `applicable` 走全仓」**：`check_file`（`backend_layer_check.py:126`）
  **不做扩展名过滤**，targeted 模式会把 `.vue`/`.js` 一并喂进来（R7 正是为此而设）⇒ 拆掉
  `dirs` = 让 `GetData(` 在 `docs/*.js` 上也报，那是**新引入的误报面**，不是作用域对齐。
  ⚠ 这是**目录门、不是扩展名门**：`RMC/…/App.vue` **本来就在** R5/R6 作用域内（旧 `dirs` 的
  `"RMC"` 就覆盖它）—— 我第一版拿它当「不该进」的样例，**当场被自己写的锁咬红**，已订正为只钉
  「9 组之外不进」、**不去钉扩展名**（那会把既有语义说成缺陷）。
- ⚠ **一处事实订正**（原文保留在下方那段状态更新里）：旧文那句「R5/R6/**R9** 是组前缀」
  **只有 R5/R6 成立**。`R9MagicStatusRule` 既没覆写 `applicable()` 也没覆写 `in_scope()`；
  基类 `Rule.applicable`（`checks/rule_base.py:105-107`）恒返回 `True`，而 `_BackendRule` 只定义
  `in_scope`、不定义 `applicable`（`:295-302`）⇒ **R9 早就是全仓生效**，它的 `dirs` 是**死代码**、
  扩它等于空操作。故本批给 R9 改的**只有正则**，也**刻意没给它加作用域变异** —— 没有可退的判据，
  加一条只会得到一条**永远绿**的假变异（记在 `M123` 的 `why` 里）。★ 查法是**读继承链**
  （一张 `grep -n "def applicable\|def in_scope\|^class"` 表：R1/R2/R3/R7/R10/R11/R12 各自覆写，
  `_MethodNameRule` 为 R5/R6 覆写，**只有 R9 什么都没有**），不是看那行 `dirs`「长得像什么」。
- **读数**（`%TEMP%/eg_scope_measure.py`，只读；口径与引擎一致 —— `SKIP_DIRS` + 只取 `.cs` +
  走 `rule.run(ctx)` 本体，**不是 grep**；12 个后端仓、**72,434** 个 `.cs`）
  —— **`before` / `after` 两跑都跑满了 12/12 仓**（`%TEMP%/eg_scope_before.json` 1664.6s、
  `eg_scope_after.json` 1874.8s；两跑各自把 `per_repo` 逐仓读数列全，`len(per_repo)==12`）：

  | 规则 | before | after | 增量 | 来源 |
  |---|---:|---:|---:|---|
  | R2 | 0 | 0 | 0 | 存量里 Business 层一次都没反向 `using` Controller |
  | R3 | 0 | **225** | +225 | 段名判定收下 `Eagle.Repository`（全名判定恒 0） |
  | R5 | 2110 | 2134 | +24 | 新增覆盖的 6 个组里**只有 `Core`** 有命中 |
  | R6 | 2450 | 2486 | +36 | 同上，只有 `Core` |
  | R9 | 19423 | 20032 | +609 | 正则放宽到具名状态（与作用域无关） |

  ★ **R9 那一列同时是「它的 `dirs` 是死代码」的实测证据**：`before` 一跑里 R9 的命中已经分布在
  `Core` 51 / `Module` 48 / `DataReport` 19 —— 三个组**都不在** R9 的 `dirs`（`["RMC","RMCEx","RMCHY"]`）
  里，而它照样报得出 ⇒ 作用域从来没参与过判定（见上方那条事实订正）。
  ★ **R3 的 225 是「段名判定 + `Repository`」这一版判据的读数**（`RMC` 213 + `Support` 12，全部
  `L3 Eagle.Repository`）。⚠ **它没有算进 2026-10-02 新加的限定名通道** —— 那 12 处
  `PlanSiteDisposalPlanEntity.cs` 写的是**裸 `Repository.StaticManager`、一条 using 都没有**，
  这一版量器看不见（量器跑的是当时的判据）。按本节的分类（225 = 177 死 + 48 真，再 +12 限定名），
  **当下判据的实测应为 237**，其中真违规 **60**。这个数等客户仓侧清理时一并复量。

- **回归锁 +18**（`tests/test_review_2026_09_23.py` 79 → **97**；`tests/lock-inventory.json` 的
  `total` 1073 → **1091**）：四个新类 —— `TestL20R2BusinessScope`(3) / `TestL20R3SegmentForbidden`
  (10-01 时 4 条 → **10-02 时 6 条**) / `TestL21R5R6GroupScope`(5) / `TestL22R9NamedStatus`(4)。
  ★ **这四条此前一把锁都没有** —— 把四条就地放宽后 27 条相关用例全绿，所以那些绿是**到达时就是
  绿的、不算证据**（同 L5 的规矩）；★ 作用域类一律**两问一起钉**（`applicable()` **且** `check()`
  有命中）—— 只看命中数会漏掉「规则整个不适用」，而「**整片不适用**」恰恰是本批的病灶；
  ★ 每类都配一条**非空转**用例（证「旧判据拒绝、新判据收下」的样本不是空集）。
  ★ R3 那类**在 10-02 换掉了第 4 条、并补到 6 条**（同一次提交里，净 +2）：10-01 那条
  `test_repository_segment_is_deliberately_not_reported` 钉的是**已被证伪的收窄**（见上），
  已**反转**为 `test_repository_segment_is_reported_again` —— 只钉「不报」会退化成「什么都别报」
  的**反向假绿**，所以它**末尾必须**同时反向要求（同 N7 的处置口径：**反转，不是删掉**）；
  另加 `test_qualified_name_channel_catches_the_using_free_form`（限定名两半必须抓）与
  `test_qualified_name_channel_does_not_report_lookalikes`（**7 条 FP 一条都不许进**：
  `a.Repository.X` / `MyRepository.X` / `_repositoryFactory` / 字符串 / 注释 / `BusinessEntity` /
  裸 `Business.X`）。`R3_TABLE` 里那两条 `Eagle.Repository` 反例也一并**改回正例**，并补 8 条限定名探针。
- **护盾 112 → 120**（`M119`–`M126`）：八条各自只咬一条锁 —— 作用域三条（`M119` R2 / `M121` R5 /
  `M122` R6）、判据三条（`M120` R3 全名 vs 段名 / `M123` R9 正则 / `M124` R3 的 `Repository` 归属）、
  限定名通道两条（`M125` 裸段名那半 / `M126` 带 `Eagle.` 前缀那半）。
  ★★ **`M124` 的方向在 10-02 反转了**：原先是「把 `Repository` **加回** `forbidden` ⇒ 必须红」，
  在当下契约里这条**恒绿**（它已经是加回去的状态）⇒ 已改成「**拿掉**它 ⇒ 必须红」。**同一份
  `why` 里同时留着两个方向的理由**，否则后来人只看得见一个方向、不知道哪个才是当下契约。
  ★ `M125`/`M126` **必须分成两条**：它们是**同一条判据的两半**，合并只能证明「其中一半还在」
  （同 `M51`/`M52`/`M53`、`M121`/`M122` 的口径）。⚠ **有意不守的那一面**：`_BARE_NS_SEGMENTS`
  若被改成把 `Business` 也当裸段名，`M125` **不会**红（那是**放宽**不是收窄）—— 由行为锁
  `test_qualified_name_channel_does_not_report_lookalikes` 里那条「裸 `Business.X` 不报」兜住
  （判据落在行为上，不是某一行源码）。同理 `M126` 若把 `alts` 清空，`bare` 那支仍会 append，
  **不会**变成 `re.compile("")`（那会匹配一切 —— 本批最怕的反向事故）。
  ★ `M121`/`M122` **必须分开登记**：两条是**独立的 `dirs` 声明**，合并成一条只能证明其中一处还在；
  且两条锚点都带上各自的 `pattern` 行才互不干扰（谁把这两个块改成逐字相同，会先在「锚点命中 2 次」
  上炸出来）。★ `M120` 的 `why` 本次**订正过两处过期断言**（两次都留着当教材）：初版把
  「顺带收下 `Eagle.Repository`」写成想要的副作用；10-01 又据被证伪的量法改成「`Repository` 已
  从章程摘出」；10-02 复量推翻后者 ⇒ 现在写的是第三版（病理是**穷举** + 那段订正史）。

### Fixed（2026-09-30：锚点假绿 / 钩子路径抽取 —— 两处「机制在、判据不在」）

- **「锚点尾行坏掉」曾被当成「还没有锚点」**（`agentguard/cli.py` + `harness/coverage.py`，
  两个各自独立的读侧副本）：读侧原先把「**没有**锚点文件」与「有文件但尾行解析不了」合并成同一个
  `None`。于是保留链合法的前缀、砍掉台账尾巴（**前缀保留的截断，行内链自己看不出来** ——
  每一行都还是合法的）、再往锚点尾行追加**一个字节**，`gate log --verify` 就退回「尚无锚点」并
  **rc=0**。而抓截断/重写正是锚点唯一的存在理由 —— 报绿的恰好是它唯一的职责。
  改为三态 `ok` / `absent` / `corrupt`：`corrupt` 时 `--verify` rc=1 且点名
  「锚点文件尾行无法解析……本命令**不能**判台账完整」，`--anchor` **拒绝**在坏尾行之上追加
  （否则新锚点的 `prev` 会被记成"无前锚"，断链接续不留痕）；`absent`（文件不在 / 空文件）**照旧
  rc=0 +「尚无锚点」**——反向也锁死，免得修成"一律报红"把首次锚定前的正常流程变成红灯。
- **shell 重定向目标被当成「被编辑的文件」**（`integrations/hooks/agentguard-gate.py`）：
  `target_file()` 的命令兜底把重定向目标当文件，于是 `… 2>/dev/null`、`… > /tmp/t.log` 这类
  **只读**命令抽出 `/dev/null` / `/tmp/t.log` 交给 `--only` ⇒ auto-gate 一个都匹配不上 ⇒
  rc=2 fail-closed ⇒ **每一条带重定向的命令**都往模型回灌「harness 门禁调用出错 rc=2
  （多半是配置问题）」。代价不在这一次，而在**注入通道自己报假警**：模型被反复教"门禁坏了"，
  真毛病却在**提取**这一步。两道**独立**防线：① 重定向目标只给 Codex（它的写入就是 shell 命令
  且此前已过 `WRITE_HINTS`；ZCode 的写入走 Write/Edit，**只复用输出契约、不复用命令判据** ——
  与本仓 2026-09-13「故意不设 matcher」那条结论同向）；② 仓外目标一律按「取不到文件」处理，
  交回合末 Stop 全仓兜底（不是放松，是别报假警）。★ 同批修掉同一段正则里的**第二个**缺陷：
  `(?:>|>>)` 且字符类没排除 `>` ⇒ `echo x >> src/a.vue` 抽出 `'>'` ⇒ `--only <仓>/>`
  （实测三例全中：`>>` / `Add-Content >>` / `2>>`）。
- 回归锁 **+12**：`tests/test_gate_log_anchor.py` 新增 `TestAnchorCorruptTail` 6 条
  （含完整利用链「截断 + 坏锚点」，以及反向锁「还没锚仍是绿」），`tests/test_entry_level.py`
  新增 `TestHookPathExtraction` 6 条（含反向锁「Codex 仓内重定向仍照查」与 `in_tree` 的
  「路径包含」vs「字符串前缀」之分）。
- **护盾 97 → 103**（`M104`–`M109`）：六条常驻变异各锁一处**独立**代码路径。注入证伪实测
  **10 条全红、恢复后逐文件 sha256 与原件一致**（MA1–MA4 / MB1–MB6），其中 6 条提升为常驻。
  ★ 两条独立防线的证据：`M107`（放开重定向给所有客户端）与 `M109`（撤掉仓外守卫）**单独**撤时
  端到端那条主锁**都不红**（另一道还在兜），只有两道同时撤才红 —— 所以「哪一处坏了」只能靠
  各自那条单元锁分辨，两条都得在。
- 顺带补掉一个**此前就存在的洞**：`tests/test_gate_log_anchor.py`（09-29 那批新增）**没登记进
  `tests/lock-inventory.json`**（登记 total 1034 vs 实测 1052，差 18 恰好是它）—— 未登记的模块
  **被整文件删掉也不会有任何检查发现**，正是这个看守存在的理由；本次一并登记（24 条）。
  ⚠ 该模块在本批是**未跟踪**文件，而冒烟的副本按 `git ls-files` 造（只含已跟踪文件）：未跟踪的
  锁模块在副本里**一条都不会跑**（冒烟按「锁模块一条用例都没跑」计失败）。这不是缺陷，是**设计**
  —— 它把「忘了提交锁模块」从静默变成一条红。

### Changed（2026-09-30：注入通道不再教「行内豁免」—— 语法只住可信文档）

- **两处命中文案不再复述豁免语法**，`harness-ok` 的写法改为**只住在工程根 `AGENTS.md`**
  的「行内豁免」一节（它本来就在 agent 的上下文里，正常用法一点没少）：
  ① `integrations/hooks/agentguard-gate.py` 的 `msg`（**每一次失败/出错都出现、7 家客户端
  通用**的那一句）；② `checks/h5_ruleset.py::UploadSignRule.name`（命中行的说明文案）。
- **为什么两处都要删、少一处不行**：它们**同属一条通道**。hook 的 `msg` 是正文，而规则命中的
  `name` 会经 `Finding` → gate 的 stdout **逐字**回灌 —— 只删正文等于「正文不教了、命中行旁边照教」。
- **威胁模型**（用户 2026-09-30 提出）：注入通道若可被伪造，攻击者借门禁**自己的口吻**就能诱导
  agent 把**任意**命中行合法豁免 —— 伪造者不必自己编话术，门禁的原文就是话术。
- **判据是「教」不是「提到」—— 这条线画错会造出假阳性机器**：
  - 老锁 `tests/test_checks.py` 写的是 `assertIn("harness-ok: H5-UPLOAD", fnd[0].message)`
    「文案里必须给出出口」，**把缺陷锁成了要求**；本批反转成 `assertNotIn`，并补上
    「`AGENTS.md` 里必须写着出口」—— 两半一起：只删不给出路＝把人堵死，不是把路改道。
  - hook 侧**不能**图省事写 `assertNotIn("harness-ok", ctx)`：`waiver_summary()`
    (`checks/rule_base.py`) 在**真的用掉豁免**时会打一行
    `⚠ 本次用掉 N 条行内豁免（harness-ok）：`，那行是**故意**要可见的审计
    （`tests/test_review_v13_batch2.py:370` 与 `tests/test_entry_level.py` 的 N7 锁都**正面要求**
    它出现，理由是"豁免是唯一合法的绕过出口，越看不见越危险"）。**2026-09-30 当场探针验过**：
    构造一个「一行带标记豁免 + 一行未豁免」的文件跑真 hook，注入正文里确实带着那行审计 ——
    照 `assertNotIn` 写就会把这个**正常**文件判红。故只锁**只有教学形态才命中**的两点：
    模板占位符 `<规则ID>`、位置指令「紧邻上一行」；外加一条兜底 —— 注入里凡出现 `harness-ok`，
    就必须同时是那行审计（`本次用掉`），除审计外的任何提及都算回归。
- 回归锁 **+2**：`tests/test_entry_level.py::TestHookPathExtraction::test_injected_message_does_not_teach_the_waiver`
  （走**真 hook 子进程**、按 Codex 的写操作重定向形态取回灌，与同批主锁同款）、
  `tests/test_checks.py::TestH5Ruleset::test_upload_sign_rule_message_does_not_teach_the_waiver`。
  护盾 **103 → 105**（`M110` 注入正文又把教学句加回去 / `M111` 规则命中文案又把教学句加回去），
  两条各自对应**一条独立通道**，实测对照绿 + 双 RED 且失败清单里就是各自那条锁。

### Fixed（2026-09-30：`in_tree` 的平台忠实判据 —— 一条「只在 Linux 上红」的假绿）

- **现场**：`4fa05de` 的 harness-ci **两个 Python 版本（3.11 / 3.12）都红在同一条锁上** ——
  `tests/test_entry_level.py::TestHookPathExtraction::test_in_tree_is_path_aware_not_string_prefix`，
  `AssertionError: Windows 路径大小写不敏感`；而**本机（Windows）全量 1064 例全绿**。
  同一次 CI 里「变异冒烟」照过（它跑在本机同款判定上），所以红只在**自检**那一格 ——
  与 M92 同族：**锁自己依赖了"哪台机器在跑它"**，而这种红在开发机上永远看不见。
- **成因**：`in_tree` 靠 `os.path.normcase` 折叠大小写，而 **`normcase` 在 POSIX 上是恒等变换**。
  末段那句写成了**无条件**断言「大小写不敏感」⇒ Linux 上 `D:/Repo/SRC/a.vue` 与 `d:/repo`
  被 abspath 到 cwd 之下后是两个大小写不同的真路径，`startswith` 为假。
- **修法不是删掉那一句**（删了就丢了 Windows 侧的判据），而是**平台忠实**：
  用 `os.path.normcase("A") == "a"` **现探**平台（**不是** `os.name`）—— 判据直接跟着被测函数
  所用的那套语义走，`in_tree` 换实现时这条锁会跟着说话。POSIX 半边断言的是**否定**：
  大小写不同的两个目录**不许**被当成同一个（若为真，说明有人把 `normcase` 换成了写死的
  `.lower()` —— 在这台机器上永远看不出来）。
- **两半的护盾边界不对称，这点要记住**：「整个丢掉 `normcase`」**有**护盾（`M114`，它在 Windows
  上也红 —— `D:\Repo\…` 与 `d:\repo\` 首字符就对不上）；「换成写死的 `.lower()`」在本机（Windows）
  变不出红来。★ **本段原先接着写的是「那一半没有、也不该有护盾」—— 这句话在当天晚些时候被推翻了**
  （`90b4b58` 的 CI 就红在这条判断上，见下面「平台盲区」一节）：正确的说法不是"不该有护盾"，
  而是"每条护盾都得说清自己**在哪个平台上可见**"。`M115` 现在**有**护盾，条件是声明
  `blind_on: ["nt"]`。**那一半的第二个平台是 CI 的 Linux runner** 这半句仍然成立。
- **POSIX 半边的期望已用模拟核实**：临时把 `os.path.normcase` 换成恒等 / `.lower()` 两向各跑一遍，
  四句旧断言在 POSIX 语义下仍全部成立、大小写那一句按预期取反。
- 锁数不变（是把一条**已存在**的锁改对），护盾 **107 → 108**（`M114`）。

### Changed（2026-09-30：变异冒烟提速 —— 每条变异只跑 `expect` 点名的那条）

- **病根不是机器慢，是跑得比判据需要的多**。判据本来就只认一条用例（`main()` 里
  `failed_names(out)` 与 `expect` 的比对），命令却写的是 `-p <锁模块>` **整跑整个模块** ——
  105 条变异里 **51 条**挤在同一个 `tests/test_review_2026_09_23.py` 上，等于把那个模块整跑了 51 遍。
  实测该模块：整跑 `Ran 79 tests in 22.905s`，`-k <expect>` 点名跑 `Ran 1 test in 0.009s`
  （真实耗时 23.553s → 0.445s，**53×**）。
- **改法**：新增 `_lock_cmd(lock, expect)`，`expect` 在场时追加 `-k <expect>`；旧行为保留在
  `--full-module-lock`（**排查用的退路**，不是日常档：万一某条变异的失败依赖同模块其它用例
  留下的状态，用它对 A/B）。
- **全量冒烟 2102s → 200s**（103→107 条变异都在内），且**结论集不变**：107 个变异命中 107、
  失败/跳过 0、对照绿 —— 与提速前的全 RED 参照集一致。
- ⚠ **这不是纯性能改动，它改了"跑什么"，所以两向都上锁**：
  - ① 同名模块里的**兄弟用例不许被执行** —— 否则"优化"没生效、悄悄退回整跑，而这**从输出上
    完全看不出来**；
  - ② `--full-module-lock` 必须**真的**退回整跑 —— 若它是个摆设，将来排查"变异依赖同模块残留
    状态"时，人以为在做 A/B，其实两次跑的是同一件事。
  - 两向都靠**同一个可观测副作用**判（合成仓里的兄弟用例写一个副本之外的标记文件），
    不是读源码字面；另加三条**形状**断言：空/`None` 的 `expect` 一律**不许**退化成 `-k ""`
    （那会匹配到全部用例、看起来"也跑了"，实际把「这条变异没登记期望锁」这件事藏起来），
    且三种入参都必须仍带着 `discover -s tests -p <锁模块>` 的骨架。
- **`_ran_nothing` 的提示语补上第二种成因**：`-k` 过滤到 0 条同样是 `Ran 0 tests … OK` + `rc=0`，
  与「锁模块不在副本里」（未跟踪/未提交，2026-09-23 那次误诊的现场）**同一个观感**。两种成因
  都必须判红（宁可假失败不可假绿），提示语现在把两种都说全，免得又把人送去改锁。
- **改法不会把任何一条变异变成空转**（静态交叉核实 105/105）：每条变异的 `expect` 都确实定义在
  它自己的 `lock` 模块里；`-k` 的通配语义下只有 `M10` 的 `test_table` 会匹配到 2 条同名用例
  （不同类里），无害 —— 判据仍要求期望的名字出现在**失败清单**里。
- 回归锁 **+1**（`tests/test_lock_inventory.py::test_lock_run_targets_only_the_named_case`），
  护盾 **105 → 107**（`M112` 退回整跑锁模块 / `M113` 让 `--full-module-lock` 变成摆设）。

### Fixed（2026-09-30：变异冒烟的「平台盲区」—— 一条护盾只在单个平台上可见）

- **现场**：`90b4b58` 推上去后，CI 的「自检」两格**都绿了**（上一节那条 Linux 假绿确实修好了），
  红的是**变异冒烟**，而且是**我自己刚引入的**：
  `FAIL M114-in-tree-drops-normcase 变异后仍然绿 → 锁被弱化/没打在判据上`，
  同一次运行的 `自检` 却全过。**判据与结论对不上**，正是本仓最忌讳的形态。
- **成因：不是锁弱了，是这条护盾在 Linux 上根本不可见。**`M114` 撤掉 `in_tree` 的
  `os.path.normcase`，而 **`normcase` 在 POSIX 上是恒等变换** ⇒ 撤掉它**等于什么都没改**，
  变异体与原体语义相同，锁**没有理由**红。冒烟跑在 Linux，只认「变异后必须红」，
  于是把"本平台看不见"诊断成了"锁被弱化"—— **方向指反**。
  这与上一节的锁是**同一个病的镜像**：判据依赖了「哪台机器在跑」。
- **修法不是删护盾**（删掉就是把"让它闭嘴"当成"修好了"，本仓管这叫假绿，而且护盾在 Windows 上
  确实咬得住 —— 本机单跑 `M114` 是 `RED ✓`）。修法是让冒烟**认得平台**：变异条目可选
  `blind_on`（⊂ `nt`/`posix`）+ `blind_why`（非空）+ `covered_by`；命中盲区时**不跑**（跑了只有绿，
  那个绿既不能说明有牙也不能说明没牙），打印 `BLIND` 行、汇总里**单列计数**、
  **不计命中也不计失败**。汇总行现在是
  `N 个变异：命中 a，平台盲区 b（…），失败/跳过 c`。
- **顺带把另一半也收进来了**：`M115`（把 `normcase` 换成写死的 `.lower()`）**在 POSIX 上红 /
  在 Windows 上不可见**，与 `M114` **恰好互补**。上一节写的「这一半没有、也不该有护盾」**已作废** ——
  它当时是按"没有平台概念"推的；有了盲区声明，**两条合起来覆盖两个平台**，
  比"一条管一个平台、另一半靠 CI 兜着"结实（`M114` 守 Windows 半边，`M115` 守 POSIX 半边）。
- **两平台的结论现在是同一句话**：Windows 上 `命中 108 / 盲区 1(M115) / 失败 0`，
  Linux CI 上 `命中 108 / 盲区 1(M114) / 失败 0`。都不是"全红"，也都**明说了哪条没验**。
- **POSIX 半边先证伪再动手**（本机不能真跑 Linux，故用**语义模拟**）：把 `os.path.normcase` 换成
  恒等（= POSIX 语义），对「原状 / M114 / `.lower()`」三份源码各跑一遍锁的两个半边。
  结果两行互补：`M114` = Windows `RED` / POSIX `绿`，`.lower()` = Windows `绿` / POSIX `RED`。
  ★ 探针**自证**过：同一张表里「原状」在 POSIX 语义下**红** —— 那正是 `4fa05de` 那次 CI 红的
  复现，说明模拟确实落在了那条判据上，不是自说自话。（探针第一版有 bug：补丁在 `exec` 时就撤了，
  而 `in_tree` 是**调用时**读 `normcase`，两行输出一模一样才被发现 —— 别信"跑过了"的观感。）
- ⚠ **`blind_on` 是个逃生门，所以它自己被上锁**
  （`tests/test_lock_inventory.py::test_platform_scoped_shields_are_paired_and_inventoried`）：
  ① 盲区名单**逐个列举**（多一条、少一条、改个平台都红 —— 加盲区是要人点头的动作，
  故意与 `MUTATIONS` 那条"只增不减"的纪律相反，那条防的是**护盾被悄悄删**，这条防的是
  **盲区被悄悄加**）；② `blind_why` 非空；③ **互补性**：`covered_by` 必须存在、必须在**同一个
  锁模块**里、必须**互相指**、两条盲区并集**恰好是 {nt, posix}** ——
  **没有任何一条护盾可以两个平台都瞎**。没有③，`blind_on` 就只是一句自我声明。
- 回归锁 **+1**（该模块 13→14），护盾 **108 → 109**（`M115`）。四向注入证伪全红、还原后逐字节相同。

### Fixed（2026-09-30：注入通道不再搬运「豁免理由」里的字 —— 与「不再教语法」是同一通道的两个面）

- **`waiver_summary()` 把源文件注释里的自由文本原样搬进了模型上下文**（`checks/rule_base.py`）。
  它原先打的是「规则 ID + 行号 + **理由原文**」三样，而这一行的去向是 gate stdout → hook 的
  `out` → **模型上下文**。理由是从注释里**逐字**抄来的（`// harness-ok: <规则ID> <理由>`），
  于是威胁模型里那个前提不必靠伪造实现：**谁能让一行注释进仓，谁的文字就穿着门禁自己的审计
  口吻进模型上下文** —— 连话术都不必编，门禁的原文就是话术。`Stop` 钩子还会**扫全仓**，
  所以污染一行注释等于对**此后每一次**会话生效。
  这与上面「不再教豁免语法」那批是**同一条通道的第二个面**：那批删的是「**教**怎么用」（hook
  的 `msg`、H5 规则的 `name`），这批守的是「**别搬运**用过的字」。两件事刻意分开命名，
  失败信息才分得开。
  **处置是「第三个字段只入账、不回显」**（`for rid, lno, _reason in WAIVED_DETAILS`。
  一行只打 `· <规则ID> L<行号>`）。理由的家在 **`diff`** —— 这一点是文档自己写的
  （`AGENTS.md`：「理由写在标记后面：标记会留在 diff 里，人工复核看得见」），复核的人读的是
  diff，不是这行注入。⚠ **「要求写理由」与「把理由回显出来」是两件事**：前者**保留**
  （没理由不豁免，有锁钉着；第三个字段照旧入账，只是不再打印）。
- ★ **一条锁把缺陷锁成了要求，必须反转而不是删掉。** 老 N7
  （`test_entry_level.py::TestPcInlineWaiver::test_waivers_used_are_reported_on_pc`）里写的是
  `assertIn("演示必须临时打印", r.stdout)`，理由是「豁免汇总必须带上理由 —— 理由才是复核的
  依据」—— 它**正面要求了那个缺陷**。这是**第二例**，第一例是上一批 ③ 里的
  `test_checks.py`（老锁写 `assertIn("harness-ok: H5-UPLOAD", …)`，把「文案里必须给出出口」锁成
  了要求）。已就地**反转**为 `assertNotIn`，并**补一条正向**断言 `assertIn("PC-CONSOLE L3", …)`
  —— 没有它，这条锁会从「别回显理由」退化成「什么都别打印」，是**反向的假绿**。
  ★ N7 的**实质没有被削弱**：它要的是「豁免看得见」（豁免是唯一合法的绕过出口，越看不见越
  危险），而那两条判据（审计行 + 规则 ID）原样保留，去掉的只是**第三个字段的回显**。
- **新锁判到「注入正文」那一层**：
  `TestHookPathExtraction::test_injected_context_carries_no_waiver_reason_text` —— 造一个
  「**既用掉豁免、又另有真违规**」的文件（这一点要紧：只用豁免时门禁 `rc=0` **不回灌**，
  锁会在空跑中静默变绿），理由里塞一个独一无二的标记串，断言 ① 审计行还在、② 标记串
  **不在**注入正文里、③ 规则 ID + 行号**在**。
  ⚠ 造样本时当场踩到一个坑，已写进测试注释：豁免标记的作用域是「命中行的本行**或紧邻上一
  行**」，两行 `console.log` **挨着写**时前一行的标记会顺带豁免后一行 ⇒ 门禁 `rc=0` 不回灌
  （实测那一刻 gate 打的是「本次用掉 **2** 条」）。**隔一个空行**才让"另有真违规"真的存在。
- 验证：注入证伪 2/2 —— 把 `— %s` 注回去，**两条锁都红**，且第二条的失败原文以
  「harness 门禁未通过…」开头，直接证明那个标记串**确实落进了注入正文**（不是 gate stdout 的
  旁证）；还原后逐字节相同。真冒烟 `--only=M116-waiver-summary-echoes-reason` = RED ✓。
  护盾 **109 → 110**（`M116-waiver-summary-echoes-reason`，**不在平台盲区里**）；
  该模块 62 → 63 例、总例数 1068 → 1069。

### Fixed（2026-09-30：套件内的真树写入竞态 —— 探针还原不再清空文件）

- **一条「随负载出现、与本次提交无关」的假红**：`tests/test_hooks_chain.py::TestSentinelBlocksDirtyEngine`
  为了造出「脏工程仓」会把一行探针注释追加进 `checks/pc_code_check.py`，tearDown 里再用
  `open(…, "wb")` 还原 —— **先截断后写**。而并发的 `tests/test_entry_level.py` 要读**同一份文件**
  断言 `warn_node_missing_once` 的接线：它会落进那个窗口读到 **0 字节**，于是并行跑全量时
  随机红在一个**根本没有被本次改动碰过**的模块上。实测（同夹具同机器、四个读者线程）：
  **修前 470 次读里 169 次 0 字节（≈36%），修后 1888 次读 0 次**。
- ★★ **第一版设计被实测否掉**：原计划是「同目录临时文件 + `os.replace()` 原子替换」，理由是
  「读者只见旧或新、不会见 0 字节」。**Windows 上不成立** —— 读者持有句柄时 `os.replace` 抛
  `PermissionError [WinError 5]`（实测 104 次 0 字节读 + **1965 次读失败**，单次最久重试 2.873s），
  且替换那一瞬间读者会拿到 `FileNotFoundError`。「原子替换」这条保证**只在 POSIX 上成立**。
- **修法是让那个中间态不存在**：① 弄脏改**二进制追加**（`open(…, "ab")`），与还原**共用同一个
  `PROBE_LINE` 字节常量** —— 文本模式会把 LF 翻成 CRLF，而还原是按**原始字节长度**截回，
  两边不同源就永远截不回原样；② 还原改 `open(…, "r+b").truncate(n)`（n = 原始字节长度），
  **永不把文件清成 0 字节**，读者只可能看到「原始」或「原始 + `PROBE_LINE`」两种形态；
  ③ 截断前先核对尾巴确实**恰好**是本类写的那一行，不是就**一个字节都不动**、只往 stderr 告警
  （保住 v13 严重 2 那条非破坏性规则：宁可不还原，不覆盖别人的改动）。
- **同一批的另一个真树写入者**：`test_self_check_catches_missing_guard_call_site` 造反面样本时，
  写的是**真工程仓里那份共享分发器** `harness/hooks/pre-commit.py`。全量跑期间真树被写过的文件
  实测**只有两个**（`checks/pc_code_check.py` 与 `harness/hooks/pre-commit.py` —— mtime + 大小
  前后扫描，718s 得出，跑完两者都回到原大小），本批把后者也消掉。
- **改法不在 `checks/` 侧，`checks/harness_self_check.py` 本批一个字节没动**（不在提交里）：
  `check_guard_call_site()` 本来就从**模块级** `ROOT` 派生 `pc_path`，所以测试只要 monkeypatch
  `sc.ROOT` 指向临时目录就行，真文件零触碰（顺带删掉两条 `skipTest`「文件已不存在就跳过」的
  退路 —— **跳过＝不验**）。但「ROOT 可被调用方覆盖」正是这条改法的**前提**：前提没了，那条
  反向锁就只能回去改写真树。前提由护盾 `M118-guard-call-site-ignores-the-root-it-is-given`
  守着（把 `pc_path` 改成从 `HERE` 现算 —— 值与 `ROOT` 相同、**但不受覆盖影响**），
  它守的**不是 ⑥ 本身**，是「反向锁可以不改写真实文件」这件事。
- 回归锁 **+4**（该模块 25 → 29 例、总例数 1069 → 1073）：
  `TestProbeRestoreNeverExposesEmptyFile` 借真实对象起四个读者线程，断言
  「0 字节 0 次 / 短于原始长度 0 次 / 读失败 0 次」并**要求至少有一次合法读**（防空转）；
  `TestGuardCallSiteReadsTheRootItIsGiven` 把自检的三态（文件不存在 / 有坑要报 / 合规不报）
  在临时目录里逐个钉住。
- **护盾 110 → 112**（`M117`/`M118`）：分别钉住这两处改法的回退。`M117` 的注入证伪 **RED 3/3**，
  失败原文是 `AssertionError: 41 != 0 : 并发读者读到了 **0 字节**（41 次）` —— 红的是**并发判据本身**
  而不是末态断言，证明这条锁打的是那个真实窗口、不空转；两处还原均按 sha256 核对**逐字节一致**。
  官方入口 `--only=M117…,M118…` 实测：对照绿、两条 RED、失败/跳过 0（73s；对着最终字节复跑 110s）。
- ⚠ **一处订正**：起初把 `M118` 的期望锁名写成了 `test_case3_…`，而 case ③ 走的是临时文件、
  真正被它打中的是 `test_case2_…`（保证「确实会报」的那条）—— 名字对不上就等于拿一条
  **两边都绿**的用例当牙齿，已按失败原文改正。
- ★ **同批自己踩的坑，由全量套件当场抓出**：上面那段往 stderr 写的中文告警让
  `tests/test_hooks_chain.py` 从「不是输出型入口」变成**是**（`sandbox/list_entries.py` 的判据是
  「会输出 **且** 含非 ASCII」），而它没有 `reconfigure` 兜底 ⇒ 自检 ① 直接阻断（rc=1），
  连带 `test_review_v6::test_runs_clean_under_werror`（真跑 `list_entries.py --check`，期望 rc=0）
  与 `test_review_v8_batch3` 的两条（合规反锁期望 rc=0）**一起红 —— 一条根因、三个症状**。
  按仓里标准写法补上那 6 行兜底，入口清单 58 → 59。★ 顺带堵掉一个真隐患：GBK 控制台下那句中文
  会抛 `UnicodeEncodeError`，而它跑在 **tearDown** 里 —— 异常直接漏出 tearDown，还原被中途打断。
- ★ 有意**不**给 `test_coverage` / `test_stdlib_only` 那两处非递归列目录的遍历补容错：
  它们扫的是 `tests/` 自己，本批没有任何东西会在那里出现又消失（真树写入者已清到零）；
  凭空补容错会把「真的少了一个文件」也吞掉。

### Added（2026-09-29：台账防篡改三件套 —— 月度分片 / 行内链 / 锚点）

- **月度分片**（`harness/run_gate.py`）：无 `HARNESS_LOG_FILE` 时写当月片
  `reports/gate_log-YYYY-MM.jsonl`，读侧 `_ledger_files()` 把旧单文件 `gate_log.jsonl`
  （若有）认作最早一片按时间序拼全集 —— 单文件只涨从写入侧封顶；设了
  `HARNESS_LOG_FILE`（测试密闭）行为与旧版逐字一致。`--summary` / `coverage` /
  壳侧 `gate log` 三个读者全部改为跨片读（判据三处同源，tests 对拍锁咬住）。
- **行内链哈希 `h`**：每行 `h = sha256(上一行h + 本行规范化JSON)` 截 16 hex，
  跨片首行接上一片链尾（分片不断链）；片首行自描述 `sv: 1`。「取链尾 → 算 h →
  追加」在文件锁窗口内完成，抢锁失败降级为不加锁尽力写（可能分叉，校验侧点名 ——
  可见的诚实例外，台账绝不因锁反向拦门禁）。改/删中间任何一行，其后所有 h 全对不上。
- **`gate log --anchor` / `--verify`**（`agentguard/cli.py`）：锚点记录（n / 首尾 ts /
  链尾 h / 整本 sha256 / 自上一锚点以来的 rollup）追加到 `reports/gate_log_anchor.jsonl`
  并**入库**（`.gitignore` 放行这一份，台账本体继续 git 忽略），打印现成 git 命令、
  **不自动 commit**（人类掌舵）；`--verify` 重算链并与最后一条锚点对账：吻合 /
  未锚定新增 / 链断（行号）/ 锚定段失配 / 截断 / 坏行数变化，逐项点名。
- **`coverage --quick`**：读最新锚点的 rollup 出「规则从未触发」盲区速览，
  不穿全量台账（月度 rollup 的消费口；窗口口径在输出里点明，不冒充全窗口）。
- 回归锁：`tests/test_gate_log_anchor.py`（链相接 / 篡改点名 / 截断点名 / 跨片不断链 /
  三份读侧判据对拍 / rollup 形状 / 密闭模式行为不变）。

### Fixed（四批，按时间先后）

- **`a3bea26` v0.1.7-dev 的三处红**（`8746dd7` 推上去后 harness-release 与 harness-ci 双双转红）：
  ① `tests/test_review_v8_batch2.py` 的夹具写死 `D:/repo/...` —— POSIX 上 `D:/repo/.git`
  **不是绝对路径**（没有前导 `/`），于是 `hook_dirs.resolve()` 里那句「common 是相对路径就按
  repo 绝对化」被触发，拼出 `D:/repo/D:/repo/.git/hooks`：本机绿、Linux 红。夹具改用
  `os.path.abspath()` 造**当前平台上的真绝对路径**，判定语义一字未改。
  ★ 这不是产品缺陷：产品拿到的路径永远来自本机 git；错的是夹具把**开发机的路径形状**当成了
  普适形状。② `mutation_smoke.py` 的对照支**次序**：**3.12 起 `unittest discover` 在一条
  用例都没跑时改判 rc=5**（3.11 是 rc=0 + `OK`），而对照支先判退出码、后判 `_ran_nothing`
  ⇒ 在 3.12 上把「副本本身没跑成」诊断成**相反的方向**（该去查名单/提交，却被指去查副本）。
  把更具体的那条挪到前面；判据一条没变，3.11 上行为逐字不变。③ `tests/test_coverage.py`
  第四轮 N-2 那条锁的判据收窄（本次唯一的**假警**，两平台同红）：它原先 grep 源码里的字面
  `run_gate`，比它自己声称的那件事（「会**调** run_gate」）宽了一档 —— **docstring 里提一句
  文件名不是调用**。⇒ 判据改为读**语法树**（`Name` / `Attribute` / `Import` / `ImportFrom`，
  或整串就是那条模块路径的字符串常量）；真调用一定落在其中 ⇒ **只少报假警、不漏真调用**。
  新增 `test_the_run_gate_criterion_is_not_vacuous`（5 个「算调用」形态 + 4 个「纯散文」形态）。
  ⚠ 界限写进注释（有意留）：写成**带参数的整串**命令行不在判据内 —— 本仓现有写法都是把路径
  单独成串。⚠ 字符类用 ASCII 而非 `\w`：str 模式里 `\w` 连中文都算，不带空格的中文串会被
  读成「像路径」。★ 一处根因**两个红**：断言消息会把**整份源码**打出来，里面那句
  `-W error::ResourceWarning` 又让严格句柄 grep 命中 —— **别当成句柄泄漏去查**。

- **`a3bea26` M88 锚点失效**（护盾静默减护，同族第二次：`M44` → `M88`）：本批第⑦条**删掉的
  正是 M88 自己的锚点句**（那条的内容就是「删掉那段说明」）⇒ 锚点在产品文件里命中 **0 次**
  ⇒ 变异**根本施加不上** ⇒ 冒烟把它按失败计（判据本身是对的）⇒ harness-ci 的冒烟 job 红。
  危害在于**没有任何测试会红**：产品全绿、名单照旧、台账照旧，唯一没发生的事是**注入本身**
  —— 属「已登记」冒充「已验证」，而且只有真跑冒烟（本机十几分钟）才发现。
  ⇒ **证伪方向随状态翻转**：当前态是「代码已修 + 说明已删」，能把它弄红的动作从「删说明」
  变成「把旧口径说明搬回来」；id 随之改名 `M88-...-note-restored`，台账同批更新。

- **`0f89f1c` M92 在 CI(Linux) 上假绿**（`c2b6e1b`「夹具写死了开发机状态」那一族 ——
  本仓反复咬人的一条）：CI 冒烟 94/95，唯一 FAIL 是 `M92-l6-git-cannot-start-reports-success`
  （「变异后仍然绿」），同一份夹具本机 Windows 全红。根因是两个 `127` 走**不同的 `except`
  分支**，而分流靠 `subprocess` 对坏 cwd 抛什么异常 —— 那是**按平台选**的（两平台实测）：
  `cwd=不存在的目录` → Windows `NotADirectoryError`（第二支）/ Linux `FileNotFoundError`
  （第一支）；`cwd=一个文件` → 两平台都 `NotADirectoryError`（第二支）。夹具原先用前者 ⇒
  Linux 上跑的是**第一支**，被它守护的第二支没有任何路径到达 ⇒ 把第二支的 `return 127`
  改成 0 也仍然绿。⇒ 夹具改用「文件当 cwd」并**断言文案**（`assertIn("起不来", err)`），
  把「走的到底是哪一支」也变成判据；`mutation_smoke.py` 里那句只记了 Windows 半张的注释
  同步订正成两平台对照表（**它正是这次写死开发机状态的源头**）。
  ★ 顺带（同一处入口）：`--only=<短 id>` 原先**静默通过** —— 过滤器是全等匹配，匹配不上就
  空转一圈打印「0 个变异：命中 0」并**返回 0**，与「跑过了、全绿」在观感与退出码上都没有
  区别。补上第四处同类守卫：选中为 0 时 rc=1，且在 `mkdtemp` **之前**返回（不留临时目录）——
  前三条守卫挡的是「没跑成」，这条挡的是「没让你跑」。

- **`a92fcab` `--only` 的第二个入口**：模块头的用法行写的是空格写法，而解析器只认 `--only=`
  ⇒ 照用法行敲会被**静默忽略**、跑成全量（当时 95 条，约四十分钟）—— 与上一条的假绿
  **方向相反**（一个假绿、一个跑过头），病根同一个：**过滤器不可信，而且它不告诉你**。
  ⇒ 两种写法**都收**；`--only` 落在末尾、后面没有值时不**静默退回全量**，而是造一个空过滤
  交给上一条守卫报错；用法行订正为 `[--only=<id>[,<id>…]]`，并把两条边界写进模块头。
  顺手修掉上一条自己造的回显瑕疵：空格写法报错却回显**带等号**的形态 —— 回显了一个用户
  没敲的形态，正属本仓忌讳的「诊断指错方向」那一类；改为只回显**值**，空值显示「（空值）」。
  ★ 纯增量：仓内既有引用（`reports/` 证据件、`reports/_falsify_u1b.py`）**全是等号写法**，
  没有一处现有调用的行为被改变。

### Added

- **`a3bea26` 锚点看守 2 条（防复发）**：名单「只增不减」挡的是「变异被删/改名」，
  **挡不住**「变异还在、锚点却没了」。① `test_every_mutation_anchor_still_exists` —— 判据与
  `apply_mutation` **同源**（三种 kind 各照抄它那一行，刻意不另立一套），把「锚点还在不在」
  提前到每次跑套件（秒级，不依赖冒烟）；② `test_the_anchor_check_is_not_vacuous` —— 用合成
  文本两向验判据本身不是空转。⚠ 边界写进锁里：它只证「锚点**还在**」，**不证**「施加后锁
  会红」—— 后者仍只有真跑冒烟算数。
- **`83f0cf1` `--only` 四向行为锁 + 冒烟 `--dry-run` 观察窗（护盾 95 → 97）**：新锁
  `tests/test_lock_inventory.py::test_only_filter_is_never_silently_ignored_or_widened`
  走**真入口**（沿用同文件 `test_missing_lock_module…` 的先例：喂真 argv 给真 `main()`），
  判据是**行为**而不是字面 —— ① 空格写法 → rc=0 且**恰好**选中那一条；② 等号写法 → 与①
  **逐字相同**（本次故障正是二者不一致）；③ 短 id → rc=1 且**不许**出现「选中 0 个变异」；
  ④ 末尾裸 `--only` → rc=1 且**不许**出现任何「选中…」；⑤ 对照：不带 `--only` → 选中**全量**
  （少了这一向，①～④ 可能被「永远报错」的实现蒙混过关）。末段再把**用法行本身**拉进判据：
  docstring 示范的每一种写法都必须真跑通。⚠ 扫描只认 `--only=` 与 `--only `（空格）两种
  分隔符，散文里 `--only（…）` 这类跟随字符**刻意不扫**（误报的代价是下一次真漂移没人信）。
  为把四向验成毫秒级（真跑四向要小十分钟，一条变异的对照段就是 ~84s）给冒烟加 `--dry-run`：
  只打印**选中了哪几条**就退出。★ 位置有判据：它在**空过滤守卫之后、`mkdtemp` 之前** ——
  摆在守卫前面的话，`--only <短 id>` 会打印「选中 0 个变异」并返回 0，等于给干跑开后门；
  锁的夹具把 `mkdtemp` 换成会炸的假件，干跑若「不干」，这条锁自己先红。
  护盾 **+2**：`M102`（解析退回只认等号）/ `M103`（撤掉空过滤守卫）——两半分**开**登记，
  因为观感与排查方向完全相反（一个去查「为什么跑了这么久」，一个去查「为什么这么快就绿了」）。

### 这四批共用的验证口径

- **动过「被变异瞄着的行」就必须跑全量冒烟**：`a3bea26` 的 M88 与 `83f0cf1` 的 `--dry-run`
  都是这个情形 —— 锚点被改废时**任何测试都不会红**，唯一能发现的是冒烟把那一条按失败计
  （它**拒绝把 SKIP 计入「已验证」**）。`83f0cf1` 的全量：对照绿 / **97 条全 RED** / 命中 97 /
  失败与跳过 0（2075s）；`a3bea26` 的全量：对照绿 / 95 条全 RED / 0 SKIP / 0 FAIL（2393s）。
- **发布面**：`sandbox/mutation_smoke.py` 与 `tests/` **都不进发布包**（`pack_release.py` 实际
  `copy2` 的 sandbox 工具只有 bootstrap / fix_paths / uninstall / upgrade 四支）⇒ 这四批都
  **不碰整包指纹**、不需要与版本号同批改、也不向客户仓库出货。

## [0.1.7-dev] — 2026-09-23

> 本版的主线是「**报告里指出来的问题，一条条落地，而且每条都留下能证伪的锁**」。
> 外部评审（AutoClaw，2026-09-23）报了 33 项（2 高 / 12 中 / 19 低），逐条有交代：
> 该改的改、属业务判断的**明确不改并写明理由**（见「仍未做」）。
> 最重的一条是**旗标注入**：`run t <文件> --mode loose <坏文件>` 能把一次 PC-COMP 拦截
> （rc=1）翻成放行（rc=0）—— 三个入口把调用方给的 files **原样拼在 gate 名之后**，
> 于是一个内容为 `--mode` 的「文件名」就能让**被检方自己放宽门禁**。
> 另有若干条是同一个形状：**判据宽了一格，于是它问的问题不是它想问的**
> （R1 只认一条 `RMC/Eagle.RMC.Controller` 前缀、`\boverride\b` 能吃 `overrideEnabled`、
> `"const" in line` 能吃字符串里的 const、注释里的「代码」被当真代码）。
> 纪律：改判据的每一条都拿**规则本体**在受管仓上复量过（不是 grep），锁一律**注入证伪**过；
> 凡是**会改变拦截行为**的放宽，一条未动。

### Fixed

- **H1 旗标注入（高危）**：`run` / `auto-gate` / MCP 三个入口的 `files` 现在**先过形态闸**——
  旗标形态（`-x` / `--x`）的位置参数一律 **rc=2 fail-closed**，错误文案逐字打出是哪一项；
  `--` 之后全归位置参数。并断言**拒绝时门禁根本没被跑过**（否则等于换个口径的真判定）。
  `auto-gate` 的 `--only`（值来自编辑器宿主 payload）同理。
  另：`--mode` 真被调用方覆盖时，台账记 `mode_override`，并把 **loose 方向**标进 `weakened`
  ——把「收紧」也记成 weakened 是说假话。
- **H2 `cli.py setup` 不再吞子步骤返回码**：任一步失败 → 最终 rc 非零 + 打印失败清单；
  `setup --repo <不存在的目录>` 不再以 `NotADirectoryError` 裸回溯。
- **M2 README 与其守护锁互相印证了过时事实**（「后端没有 harness-ok」，实际早已实现且生效）。
- **M3 hook 目录按 git common-dir 解析**（新增 `harness/hook_dirs.py`）：worktree 专属 gitdir 下的
  `hooks/` **git 从不执行**。`sandbox/doctor.sh` 曾因此报出「hooksPath 已接管但原 hook 未备份」❌，
  外加一条**对该文件永远不成立**的修复指引。
- **M4 `using` 的三种语义等价形态**（`using static` / 别名 / `global using`）不再绕过 R2/R3 分层规则
  （实测存量 0 次 ⇒ 只关未来的口子）。
- **M5 R1/R2/R3 统一走 `_code_lines`**：块注释里的「代码」不再当真代码。R1 恒 info（只减噪声），
  R2/R3 是 ERROR（属误报面收窄）。
- **M6 `\boverride\b` 收紧**（原先 `override` 子串：`overrideEnabled`、乃至字符串里的 override
  都能放行整行）＋ 删掉被完全遮蔽、**永远不会生效**的死判据 `override_pat`。
- **M7 `\bconst\b` 收紧**（原先 `"const" in line`）。★ 命中面放宽那半边**未动**（见「仍未做」）。
- **M8/M9 Trae `hooks.example.json` 与 Qoder `settings.example.json`**：命令双重转义、`_契约` 块为
  已作废口径、与自家体检器三向矛盾（**照抄必坏**）。
- **M10 PreToolUse 若被接入，新建文件会被「全拒」(rc=2)**，且文案误导向「配置问题」。
- **M11 opencode 插件把 cwd 设为被编辑文件父目录**，与 `--only` 的 cwd 相对判定失配。
- **M12 传感器失效的 fail-open 残留**：新增 `rule_base.git_sensor_failed`，缺省档恒 False
  （与改前**逐字等价**）；`HARNESS_GIT_REQUIRED=1` 下「读不到 diff」不再被当成「未改动的存量」。
- **L1–L13、L16–L19 逐条订正**：ENC 三态口径；后端补 `waived_legacy` 统计；**PC/H5 行内豁免必须
  落在注释里**（原先 `console.log("harness-ok: PC-CONSOLE …")` 这种**字符串自豁免**也生效）；
  MCP 入口类型校验 → `-32602`；`_read_jsonl` 的 `last=0` 不再返回全部（`-0 == 0`）；
  `upgrade/uninstall` 的入口作用域判据与端脚本同源（`is_full = "--all" or 无位置文件`）；
  `bypass` 的定性写明**它是声明留痕入口、不是权限闸**（不拦提交、也不改变判定）。
- **L14 `.cmd`/`.bat` 不再先经 `cmd /c`**：那层 shell 会让 cmd **再解析一遍**引号 ——
  实测**路径与参数都含空格**时 rc=1 且输出为空。实测依据不是「碰巧能跑」：直接调
  `CreateProcessW`（绕开 subprocess）也能跑 `.cmd` ⇒ 是**操作系统本人**认它。
  三份实现现在口径一致（`check_contracts` / `upgrade` / `uninstall`；`mcp.py` 本来就没有那一支）。
  ⚠ 后两处**随安装包分发**，与发布包**逐字节**耦合（`verify_release` ⑧）⇒ 只能随版本号一起动，
  本版即那一版。**判据与锁同源，两处各有一条**。
- **L15 演练（`upgrade.py` 不带 `--yes`）的退出码不再骗人**：演练支原先把自己刚攒下的失败清单
  **丢掉、一律 `return 0`** —— 同一份清单在 `--yes` 支判 rc=1、演练支判 rc=0，脚本化调用
  （`if rc==0 then …`）会把「预演有 ✗」读成「演练通过」。现在两支**同判据**。
  活体实测（同一夹具、同一台机器，只换脚本版本）：缺 `marketplace/` 的包 **before rc=0 → after rc=1**。
  同批 `uninstall.py` 六处步骤分母 `/5`→`/6`（文件头与步骤表本来就是 `[x/6]`）。
- **L5 两半 + L18**：F3/F6 从「锚首个命中行」改为**报全部命中行**（往存量文件里新加第二处不再被
  静默漏掉）；**R1 作用域从一条前缀放到整个 Controller 族**（后缀判定 `Eagle.<组>.Controller`，
  实测受管仓真实存在的恰四条：RMC/RMCHY/Module/Support），实测 **+15 文件/+15 行**，
  且 R1 恒 info ⇒ **只多观察、不改任何拦截裁决**。★ **否决了报告建议的
  `*/Eagle.*.Controller`**：实测它会把 `Eagle.RMCEx.ApiController` 与 `Eagle.RMC.ApiController`
  一并吞进作用域（`.*` 能吃 `RMCEx.Api`）—— 那是另一套防腐层。
  范式库里的本机知识库路径实量 **7 处 / 5 文件**，且**写法有三种**（大写盘符 `D:/…`、
  git-bash 形 `/d/…`、**裸名字 `KnowledgeVault/…`**）—— 前导既不是盘符也不是 `/d/`，
  两种「按形态扫」的正则**都不命中** ⇒ 判据改成**按名字扫**；6 处活指针改 `<VAULT>`。

### Added

- **`tests/test_review_2026_09_23.py`（79 条）**：本批的边界探针表 + 逐项回归锁。
  新增的锁**一律注入证伪过**；其中 L5 那两条登记为持久变异 **M100/M101**
  （「退回单前缀 → 3 条全红」与「换成报告那个通配写法 → 恰好只红『不该进』那一条」）。
- **`harness/hook_dirs.py`**（M3）、**`rule_base.git_sensor_failed`**（M12）、
  `sandbox/check_client_configs.py` 与 `install_shim.py --check` 的扩项。
- **护盾（变异冒烟）95 条**，并首次**全量真验**：全量复验当场查出 **M44 的锚点被本批 H1 改废**
  —— 锚点在产品文件里命中 **0 次**、变异**根本施加不上**。
  ★★ **这类失效没有任何测试会红**（产品全绿、名单照旧，唯一没发生的事是**注入本身**），
  属「已登记」冒充「已验证」；冒烟器把它**按失败计**并拒绝计入已验证。锚点已现读重接。
  ⚠ 教训：**改产品里被变异瞄着的行之后，必须跑全量冒烟**，不能只跑本批新增的那几条。

### Changed（另外三批：**早已并入 main，却从未进过 CHANGELOG**，本次一并补记）

- **知识库根不再随产物出货**（`D:/KnowledgeVault` → `<VAULT>`）；修掉一类被漏数的本机路径
  （2026-09-22 首次落地、2026-09-23 补完）。起因是**真出过**：`target_form` 早先把**引擎仓**路径
  换成了 `<HARNESS>`，却漏了**知识库根**那一类 ⇒ **22 份产物全部**带着 `D:/KnowledgeVault/concepts/…`
  出货 —— 那是**维护机**上的目录，同事 clone 客户仓后这一行指向不存在的地方，**而屏幕上一切正常**。
  ★ 为什么漏的是这一类：审计清单**只数了「harness 路径」一个名字**。「28/28 都是本机盘符」这个结论
  本身没错，错的是它让人以为已经数完了。
  ★ 教训：**清点「某类东西还剩几份」之前，先数「这类东西有哪些名字」**；少列一个名字，
  扫得再仔细，那一类也是零。
  ⚠ 初版曾按反引号把路径抠出来再填，把 `（工单必读 `00_工单必读.md`）` 那半句**静默丢了**
  —— 那才是真正的内容损失；现在只碰路径那一段，括号那句逐字留下（§六①「逐字保留」）。
  用户拍板：**仓级写占位、定义放用户级**（定义落在 `~/.agentguard…/GLOBAL_AGENTS.md` 的哨兵区**之外**）。
  ⚠ 顺带查出**既有的那条锁是空的**：`test_every_kept_line_is_in_the_product` 用的夹具路径
  **不在 `VAULT_ROOTS` 里**，压根走不到 `portable()` —— 看着有覆盖、实际零覆盖；新类的 `setUp`
  因此当场断言「夹具必须踩在 `VAULT_ROOTS` 上」：**判据一旦空转，它自己先红**。
- **`keji_v3` 后端线纳管（34 条业务 gate / 12 个客户前缀）**（用户 2026-09-23 裁决；
  `ntrx-class-meeting-video` 是基于 ntrx 的**临时功能分支 → 不纳管**）。
  ★ 登记走 `checks/registry.py` 的 **`OVERRIDES`** 而**不是** `CUSTOMER_PREFIXES`：后者按
  `{前缀}_{端}` 三端展开，塞进去会**凭空长出** `keji_v3_pc` / `keji_v3_h5` 两条没有对应仓的登记。
  ★ 顺带查清：`D:/MyProject/aiauto/` 下那些目录**都是 `agentguard_back_net` 一个仓的 git worktree**，
  共用 `.git` 配置 ⇒ `core.hooksPath` 是共享的。所以 `keji_v3` **一直有钩子**，只是跑到时按
  `rc=3 非受管`跳过 —— 又一处「看着有、其实没在管」。实测改后同一条路径 **rc=0**，
  且 `cli.py check` 在真实 `BaseApiController.cs` 上跑出 `gate=keji_v3_backend`（**规则真跑**，
  不是「认得出仓」）。文档活数 33/34 → **34/35**（只改活数，**带日期的历史件一律不改**）。
- **CodeBuddy 定调「两件套」（`AGENTS.md` + `CLAUDE.md`）**，并订正一句写过的**过头话**：
  09-20 那句「33 个受管仓零残留」按字面读是假的 —— 那次清的名字只有三个、**清单里漏了
  `.codebuddy/`**（而且 `.codebuddy/` 实际有 **4** 个不是 2 个：另两个是用户自己的计划草稿、
  已入库、**差点连坐**）。依据是 PC 两仓那份 `RULE.mdc`（两份 sha256 相同）**不是指路薄壳**，
  是**规则正文的第二份副本**：把维护机绝对路径写死 3 处，其中一处是让 AI 去执行一个死路径脚本；
  且全仓**无生成者、无读取者**。`README.md` 的 node 那行同时补齐**两档**行为
  （编辑期只 skip / **提交期 fail-closed**）—— 原先只说「skip 而非拦」是半句真话。

### Added（同上：补记这三批带来的文档与锁）

- **`docs/新同事接入指南.md`**（从零接入导览 8 节，每节标出权威源）；README「详细文档」表已登记。
- **`tests/test_docs_consistency.py::TestEndRepoFileShapeIsTwoFiles`**（3 条）：判据只判
  **列表项/表格行**、只认**仓级**形态（`~/` 用户级配置合法）。★ 两条都是探针当场纠正的：
  ±N 行窗会造出**影子区**（第一版真漏了）、最初那版把 `~/.trae-cn/hooks.json` **误报**成违规。
  已真注入证伪（把当年那条违规行塞回去 → 锁点名报红；还原后 sha256 一致、复绿）。
- **`TestVaultRootNeverShipsAsAMachinePath`（7 条）+ `TestBodyCarriesNoMachinePath`（3 条）**：
  真的知识库根必须出货成占位、括号那半句必须逐字留、`portable` 只认知识库根、
  产物不许**新增**本机路径（按**集合关系**判，不是「必须空」）、两个形态判据不许空转。
  注入证伪 4 条，逐条还原。

### 仍未做（**有意为之**，非遗漏）

- **R2 / R5 / R6 / R9 的作用域偏窄**（与 R1 同族，但**性质不同**）：R2 的 `dirs` 只列三个
  `Eagle.*.Business`（`Module/`、`Support/` 两族在 12/12 仓都有）；R5/R6/R9 是**组前缀**，
  带 `.cs` 的组里 Core/Module/Support/DataReport/Audit/EntityGenerator **六个整片不在作用域**。
  这四条**不是 info**（R2 恒 ERROR；R5/R6/R9 对改动文件升 ERROR）⇒ 放宽会**改变拦截行为**，
  属业务判断，**只量不动**。⚠ 命中数**尚未量出来**（量数脚本在本机内存紧张时被回收），
  **没量出来就不写数**。
- **M7 的命中面放宽**（`\w*Status`，609 处候选）、**M4 残余**（RMCEx/RMCHY 族）、
  是否撤 R6 的 `base.GetPageData(` 豁免（903 处全是正解裸转调，**建议保留**）、
  是否把 `_strip_strings` 装到别处。
- **22 份瘦身 `AGENTS.md`** 仍在 `D:/Rain/agents_scaffold_out/`，**未落盘进任何客户仓**（等用户点头）。

> **2026-10-01 状态更新**（就地标注，上面原文保留 —— 它记的是当时真实的判断）：
> ① **上面那段「R5/R6/R9 是组前缀」只有 R5/R6 成立，R9 那半句作废**：
> `R9MagicStatusRule` **没有覆写** `applicable()`/`in_scope()`，而基类
> `Rule.applicable`（`checks/rule_base.py:105-107`）恒返回 `True`、`_BackendRule` 也不定义它
> ⇒ **R9 早就是全仓生效**，它的 `dirs` 是**死代码**、扩它等于空操作（运行期实证：gxgk 后端仓
> R9 的命中里 `Core/` 5 处、`Module/` 4 处 —— 而 `dirs` 里根本没有这两个组）。
> ② 另一处订正：R5/R6 的 `["RMC","RMCEx"]` 里 **`RMCHY/` 是被裸 `startswith("RMC")`
> 顺带蹭进来的**，不是显式覆盖。③ **命中数已量出**（不是 grep，是跑规则本体；见
> `[Unreleased]` 里 2026-10-01 那节）。④ 四条作用域与 M7 的命中面**均已按用户拍板落地**；
> ⑤ **22 份 `AGENTS.md` 本轮一并落盘**（逐仓本地提交、**不推**）—— 顺序不可颠倒：
> `harness_guard` 会在**门禁文件有未提交改动**时拦下任何业务仓提交（本轮实测被它拦过一次，
> 它是对的），所以落盘必须排在**本次 harness 提交之后**。
>
> **2026-10-02 再更新（②的续集，同一次提交内）**：⑥ 10-01 那条「R3 **章程收窄**、`forbidden`
> 里不放 `Repository`」**已被逐条复量推翻并当场反转** —— 那 225 条不是「全是死 using、零真实
> 依赖」，而是 **177 条死 using + 48 条真类型依赖**（`StaticManager`，声明在 `namespace
> Eagle.Repository`，多为实体属性里的运行时调用）；错因是**拿 `Repository` 去 grep 正文**，
> 而在用的类型名 `StaticManager` **不含这个子串** ⇒「grep 不到」被读成「不存在」。
> 同批还查出**第二个盲区**：判据只看 `using` 行，而 12 个仓各有一份实体文件用**裸
> `Repository.StaticManager`**（靠 enclosing namespace 解析、无需 using）。处置见
> `[Unreleased]` 里那节的 R3 两条 —— `Repository` 复位、加限定名通道、噪音改从**客户仓侧**治。
> ⚠ 这段（③「命中数已量出」）现在**读数已补齐**：`[Unreleased]` 那节的表是 12/12 仓的
> `before`/`after` 两跑，不再是「7/12 仓」的残表。

### 本版之前累积（原 `[Unreleased]` 段，2026-09-20 → 09-23）

（下面这几批早已并入 main，只是此前一直没发出去；逐段保留原文，数字/口径均带当日日期。）

### Changed
- **R12 的 F2 / F7 收窄（2026-09-21 用户拍板，不是"再订正文案"而是真改判据）**。
  两条噪声同一个形状：**拿整个项目的范围去问只该问一类文件的问题**。

  · **F7**（ApiController 项目内不得内嵌非 Controller 类）→ 加**文件名闸门**，
    只查 `ctx.path.stem.endswith("Controller")`。立法意图是本函数 fix 正文自己写的
    「**Controller 类里**不得内嵌实体/DTO/参数类」，而实现问的是"ApiController **项目**里
    有没有非 Controller 类"。实测 11 个受管后端仓 **24 → 8**；放行的 16 条无一例外是
    这个错位：`Common/MessageHandlers/CustomMessageHandler{,_Events}.cs`（partial 的第二片，
    6 个仓各 2 条 = 12）、`Services/*Service.cs`（4 条）。留下 8 条全是控制器文件里
    真内嵌的 Dto/Request。命中措辞同步改为「Controller 文件内嵌非 Controller 类」
    （原文案「ApiController 项目内嵌…」已不是事实）。
    ⚠ **残余盲区写进 docstring 了**：把内嵌类拆到 `XxxController.<别的名字>.cs` 会漏
    （存量实测 0 例）。要堵就换成"本文件声明了 Controller 类"的判据 ——
    实测**同样 24 → 8，两个口径的保留集逐个相同**，本次选了更简单的那个。

  · **F2**（Table/ 下文件缺 ORM 表特性）→ 只对**像实体的类**发问。新增
    `_R12_NON_ENTITY_SUFFIXES`（`ViewModel`/`Mapping`/`Dto`/`DTO`/`Request`/`Response`/
    `Param`/`Input`）+ `Vm*` 前缀判据，且**只认 `class`/`record`/`struct`**（不再认
    `interface`/`enum`）。实测 11 个受管后端仓 **443 → 260**（拿**规则本体**在旧命中集合上重跑
    得到的数，不是 grep），放行的 183 条按**首个命中的形状**分桶 = Vm*/ViewModel 148、
    *Mapping 11、只有 interface/enum 11、*Dto 7、*Request 3、*Response 3
    （分桶互斥、按优先级取首个，所以带 Dto 后缀的 `VmXxxDto` 计入 Vm* 那一档）。
    留下的 260 = `*Entity` 收尾 243 + 无后缀普通类 17（`JTSetOption` / `SpecialOption` /
    `CostFile`）。原判据 `_r12_declares_a_type` 改名 `_r12_declares_entity_like_class`，
    并**与 F7 共用** `_R12_TYPE_DEF`（原先那条"故意比 F7 宽、别合并"的注释随之作废，
    见下方 Fixed 段的订正）。`_R12_DECLARED_TYPE` 那条正则已删。

  · 两向都加锁：`test_f2_narrowed_to_entity_like_classes`（8 种"不像实体"的形状逐条静默 +
  4 种"像实体"的照报 + `Vm` 前缀不能放宽成"以 Vm 开头"）、
  `test_f7_only_checks_controller_files`（**同一个内嵌类**换文件名必须静默、换回
  `*Controller.cs` 必须报——只验静默那一侧的话，判据被改成 `return []` 也能过）。
  两条锁都做过真注入证伪：把闸门改成 `if False:` → F7 那条红；把 `_r12_looks_like_entity`
  改成 `return True` → F2 那两条红（改回即绿）。
  F2 收窄同时反转了 `test_f2_skips_files_that_declare_nothing` 里 interface/enum 的两条断言
  （原为"必须照报"）——**代价写在测试 docstring 里**：`interface ICommonAccCaseEntity`
  （Table/ 下 11 个受管仓各一份）现在真的不报了。

- **12 个老仓补齐 `legacy_baseline: true`（配置一致性）**。
  受影响：`gxgk_pc / gxgk_h5 / gxgk_backend`、`hy_pc / hy_h5 / hy_backend`、
  `hy_base_pc / hy_base_h5 / hy_base_backend`、`hy_hzy_pc / hy_hzy_h5 / hy_hzy_backend`，
  与 21 个新仓（`zhongxin / ntrx / zhongdj / keji / c712 / c712v3 / hanbogaoxin` × 三端）一致。
  本批的目的是把**存量违规进台账可观测**这条链路打通：原来 12 老仓的存量命中
  因按行过滤被直接 dropped，**永远不进 `gate_log.jsonl`**，「清理存量后改 false」**没有数据基础**。
  现在 `--all` 全量扫描时，存量违规从"丢弃"变"info 上台账"（仅限 `changed_lines=None` 的口径，
  提交期仍按行过滤主导，**行级规则**与改前一致）。

  ⚠ **订正（2026-09-21）**：本段原先写「**不**改 gate 拦截行为」，理由是「按行过滤主防线未动」——
  **那句话只对行级规则成立，对整文件规则不成立**。`WholeFileRule` 的 Finding 没有行号
  （结论是"这个文件含 CRLF""这个文件引用了死组件"这类**整个文件的属性**），它 `file_level = True`，
  **永远不参与按行过滤**（`checks/rule_base.py::filter_findings_by_lines` 见 `file_level` 直接放行），
  所以"按行过滤"这道防线本来就管不到它们。实测（2026-09-21，探针枚举全部 26 个规则类后逐个判定）：
  开 `legacy_baseline` 后，**4 条 ERROR 级整文件规则**对**非新建文件**从"拦截"降为"只观察" ——
  `VAuthRule` / `VForKeyRule` / `ComponentRule` / `CrlfRule`（PC 端 3 条、H5 端 3 条，去重 4 个类），
  另有 `ElDenyWarnRule` 本就是 WARN、只是不再告警；**20 条行级规则 0 条降级**。
  即：对这 12 个老仓的**存量文件**，这 4 类违规改前会拦提交、改后只进 INFO 台账；
  **新建文件照拦**（`is_new_file` 为真时不降级）、**行级规则照拦**。
  用户 2026-09-21 拍板：**配置维持现状**（`legacy_baseline: true` 不动，不回溯改客户仓行为），
  只把说法改对。

### Added
- **`tests/test_legacy_baseline_consistency.py`**（配置锁，机械护栏）：
  ① 所有非 `harness_self` 的 gate 必须有显式 `legacy_baseline` 字段；
  ② 值必须是 yaml bool 字面量（`true` / `false`），不能是字符串
   （`rule_base.legacy_baseline()` 走 `bool(...)`，字符串 `"false"` 为 True，与声明方向相反）；
  ③ `harness_self` 不强制此字段（它不是客户仓）。
- **`tests/_tmpclean.py`**（测试临时目录强删助手）：`rmtree_force` / `mkdtemp_cleanup`。
  根因与实测存量见 Fixed 段最后一条。
- **R12 新增 4 条锁**（`tests/test_entity_standards.py`）：`test_f3_is_anchored_to_the_table_attribute_line`、
  `test_f6_is_anchored_to_the_namespace_line`、`test_comments_are_not_treated_as_code`（F2/F3/F6
  三个方向各一条）、`test_f2_skips_files_that_declare_nothing`（含 interface/enum 反向对照）。
  另把 `test_normal_entity_passes_filename` 从**空洞断言**改成可证伪的：原文断言的是
  "命中里不含这两种措辞"（`assertFalse(any("禁用" in … or "*" in …))`），而 F1 换个措辞照样过 ——
  实测把"F5 永远开火"注入进去，35 条用例**全绿**；现改为直接断言命中列表为空。

### Fixed
- **`_notify_throttled` 的 read-modify-write 加跨进程文件锁**
  （并发抢同一 gate 拦截通知时，状态文件 `notify_last.json` 可能被竞争覆盖 → 节流失效 → 重复发飞书通知）。
  新增 `_file_lock_ctx` helper（Windows `msvcrt.locking` / POSIX `fcntl.flock`，非阻塞，
  哨兵文件 `<state>.lock` 与目标文件解耦）。锁失败降级为"未节流"（宁可重发不漏拦截信息——
  项目原则"出问题先看台账"），**不影响门禁判定、gate_log、--summary 任何主路径**。
  风险场景：CI 多 job 并发 / 多 worktree / 多 agent 同时触发拦截。此前实测（2026-09-21）：
  单进程 8 线程并发抢锁只 1 个赢家，其余全走降级；跨进程 4 个子进程并发均正常退出不抛。
  回归锁：`tests/test_notify_throttle_concurrent.py`（8 项，含跨进程 subprocess 场景）。
- **`tests/test_review_v9_batch1.py::TestL7AcceptanceSyntaxProbe` 的夹具不再依赖机器布局**
  （这就是 v0.1.6-dev 的 Known gaps 里那条"WSL 全量唯一红"）。旧夹具为造"一条没有 node 的 PATH"，
  把 `shutil.which("git"/…)` 的**所在目录整条**塞进去 —— 在本机碰巧成立（git-bash 的 node 与 git
  不同目录），在 Linux/WSL 上必假：那台机 git / node / python3 全在 `/usr/bin`，"保留 git 所在目录"
  就等于把 node 一起保留了。实测：WSL 上旧写法得到 `PATH=/usr/bin` → `which node` 命中
  `/usr/bin/node`，夹具自检当场红。现改为 `_node_free_path()` 按**文件**建镜像，只把 node 一族
  （node/nodejs/npm/npx/corepack/yarn/pnpm）挡在外面 —— 前提从"这台机器的 node 恰好不在这些目录里"
  变成"node 这个名字不进我的 PATH"。`os.symlink` 建不了时退回按目录，此时由原有断言出声、不静默。
  **被测判据一字未改**；带 `@_slow`，所以此前只走快跑档时从没执行过。
- **`README_推广接入.md` 的「方言薄壳…已全量清除（29 仓）」**：29 是当时扫过的仓数，写在"已清除"
  这个结论里会被读成当前事实。改为「受管仓全量（2026-09-20 在维护机逐仓复核 33 个受管仓，零残留）」
  —— 日期把它钉成历史读数，而不是一个必然过期的实时数（同 `RUNBOOK` 那条"不许写死仓数"的锁同源）。
- **R12（Entities 分层）三处「凭原文判代码」+ 两处「出货文案里的假话」**（2026-09-21 全面复量）。
  判据面：F2/F3/F6 原先直接吃 `ctx.content`（原文），注释里的东西被当真代码 ——
  实测三个方向都错：`// [Table]`（Visual/ 下）**误报**、`/* [Table] */`（Table/ 下）**漏报**、
  块注释里的 `namespace X.Table.Y` **误报**。现统一走 R5/R9/R10/R11 一直在用的
  `_code_lines` + `_strip_strings` 口径。F3/F6 的锚点同时从 `line=1` 改成**真实行号**
  （提交期按行过滤下，锚 1 会让"往 Visual/ 文件里新写一个 `[Table(`"整条被静默丢弃 ——
  实测存活 0 条，而这正是 R12 最想拦的场景）。**未加 `file_level`**：加上它会让每个存量
  违规文件（仅 F1 一类就 243 个）在改任何一行时都拦提交。
  文案面（数字写进出货文案就会过期，本仓复发过的老毛病）：
  ① F1 的「实测 Eagle.RMC.Entities 全仓 **0 个**这种文件」→ 实测 **243 个**（13 仓
  `*Entities/{Table,Visual,Api}/` 下 37,223 个 .cs）；直接删掉那半句，不换新数字。
  ② F7 的「基线是全仓 ApiController 内非 Controller 类**仅 2 个**…**严判据零误伤**」→
  13 仓 7,403 个 ApiController .cs 实测 **29 条命中**，其中真违规 9 条、**误报 20 条**
  （16 条 = `CustomMessageHandler` 这个 partial 类拆在 2 个文件里、两片各报一次；4 条 =
  Service 实现类放在 ApiController 项目里），差一个数量级；改成如实说明误报的**形状**
  （哪种形态会被报出来），并注明**本次只订正文案、未改判据**。

  ⚠ **订正（2026-09-21 晚）**：①②里那套 `13 仓 / 37,223 / 243 / 7,403 / 29 / 20` 的**分母是错的**
  —— 当时把未受管的模板仓（`agentguard_back_net`、`*_base_new`、`_backup` 之类）也算进了"客户仓"，
  而 gate 从来不在那些仓上跑。按 `config/harness.yaml` 真管的 **11** 个后端仓复量：
  `*Entities/{Table,Visual,Api}/` 下 .cs = **30,045**、F1 命中 = **195**；
  ApiController .cs = **6,078**、F7 命中 = **24**（真违规 8、误报 16）。上面的历史数字
  按当时的扫法保留（日期钉住读数），但**引用时请用 11 仓这套**。
  ⚠ 也别再把"13 个客户仓"当成"13 家客户"：那 13 个目录里 2 个是模板仓、1 个是备份。
- **R12 F2 的一条连带误报（剥注释那次改动自己带出来的）**：判据改读"正文"之后，
  「整份被注释掉的死文件」与「只剩 `using`、类定义那几行被注释」的文件全都冒出来报
  "缺 ORM 表特性"。13 仓 885 条 F2 命中按 `(原文里有 [Table]?, 正文里有 [Table]?)`
  正好分两档：`(False,False)` 539 条（HEAD 起就在报）、`(True,False)` **346 条 = 新增误报**。
  新增 `_r12_declares_a_type()` 前置判据（正文里真有 class/record/struct/**interface**/**enum**
  才判）——F2 问的是"这个**实体**该不该带表特性"，文件里根本没有实体时无从问起。
  复量后 F2 **回到 HEAD 的 539 条且是同一批文件**（逐文件跑过：346 条静默、539 条照旧开火，
  两向各 0 例外）；对**活**文件的剥注释语义仍然有效（有活 class、`[Table]` 只在注释里 → 报）。
  该判据的类型集合**故意比 F7 的 `_R12_TYPE_DEF` 宽**（多 interface/enum）：合并成窄的那份会让
  `interface ICommonAccCaseEntity`（Table/ 下 13 仓各一份）从"诊断说反了"静默变成"连报都不报"。

  ⚠ **订正（2026-09-21 晚）——上面最后一句已被用户拍板推翻**：`_r12_declares_a_type` 已改名
  `_r12_declares_entity_like_class`，类型集合**改成与 F7 同宽**（只认 class/record/struct），
  `interface ICommonAccCaseEntity` 现在**确实不报了**。理由与实测见 Changed 段第一条。
  那段历史结论按"当时为什么那么写"保留，但**不要再拿它当现行判据的依据**。
- **`_R12_TABLE_ATTR` 的第三个分支 `EntityTypeConfiguration<` 是死代码**：模式尾部要求紧跟 `\(`，
  于是它只在 `<` 与 `(` 字面相邻时命中 —— 正常 C# 永远写不成那样。存量实测 `[EntityTypeConfiguration<`
  出现 **0 次**（所以只算潜在坑、没造成过漏报），已删；删前两向验过 6 个样本串判定逐个不变。
  将来若真要连 `[EntityTypeConfiguration<T>(…)]` 一起认，写法是 `EntityTypeConfiguration\s*<[^>]*>`，
  但那是**扩判据**，得先确认后端确实这么写。
- **`checks/rule_base.py` 的 `GIT_OBJECT_DATABASE` 拼错**：环境变量名应是 `GIT_OBJECT_DIRECTORY`
  （同类清理行另外三处本来就写对了）。拼错意味着那个变量名进不了 git 子进程的环境 —— 在依赖
  `GIT_OBJECT_DIRECTORY` 的场景下会读不到对象库。
- **测试临时目录泄漏（同一根因复发多处，2026-09-21 系统性收口）**：Windows 上 git 把
  `.git/objects/**` 标成只读，`shutil.rmtree(path, ignore_errors=True)` 撞上 PermissionError
  会**静默吞掉** —— "清理失败"与"清理成功"外观完全一样。新增 `tests/_tmpclean.py`
  （`rmtree_force` 先摘只读位再删；`mkdtemp_cleanup` 给模块级目录挂进程退出清理），
  把 `tests/` 下 **59 处** `shutil.rmtree(..., ignore_errors=True)` 与 **4 处**模块级
  `atexit.register(shutil.rmtree, …)` 全部收敛到它（用 AST 定位真调用节点，散文里提到旧写法的
  注释一处未动）。修前在维护机 %TEMP% 实测存量垃圾 **13,588 个目录 / 500.6 MB**。

  ⚠ **第一次收口漏了一整类写法**（2026-09-21 二轮复量发现，靠"跑完套件再数一遍目录"才暴露）：
  除 `shutil.rmtree(p, ignore_errors=True)` 外还有第三种形态
  `self.addCleanup(shutil.rmtree, p, True)` —— 这里 `shutil.rmtree` 是**当回调传出去的裸引用**，
  不是 `Call` 节点，所以"找 func 是 shutil.rmtree 的调用"这种扫法**结构性**看不见它。
  实测漏掉 **9 个文件 / 12 处**，且其中两处的目录正好是复量时唯一还在增长的两笔
  （`v13b2_` +15、`v13b3_` +14）—— 两个方向的证据对上了。12 处已全部收敛（残留写法 grep 为空，
  9 个模块真 import 全过）。
  ⚠ **"改完就不涨了"这条尚未实测**：对照复量（跑同一批 9 个模块、前后各数一次）跑到一半
  被宿主以"系统内存吃紧"为由停掉，只拿到"前"没拿到"后"。机制本身已由对照探针证实
  （同一只真 git 仓：`shutil.rmtree(p, True)` 目录还在、`_rmtree_force(p)` 没了），
  但**增量归零这个数还没拿到**，不写进文档。
  另外三处模块级 `setdefault("HARNESS_LOG_FILE", os.path.join(_mkdtemp(...), ...))` 改为
  **先判再建**：Python 实参在调用前求值，即便 `setdefault` 最后没采纳（同进程里
  `_engine_copy` 已经设过这个变量），目录也已经建出来了；进程被 kill 时 `atexit` 不跑，
  就白留一份（这正是"每次 +2"的来源）。

## [0.1.6-dev] — 2026-09-19

> 本版的主线是「**范式里教你的写法，在你自己的仓里根本不存在**」。上一版治的是同一份代码在
> 两台机器上得到两种结论，本版治另一半 —— **文档与真代码不一致**，而它更隐蔽：门禁不会报错、
> 屏幕上全是 ✓，**照着抄的人才会写错**。13 个 PC 仓全量实测后，清掉一整套凭空捏造的组件与 API
> （`eagle-drawer` / `useUnsavedGuard` / `closeForce` / `venue/list.vue` / 「130+ 全局组件」）；
> 另把三份端级模板都指着的**全局守则接进了安装流程**（此前照《安装说明》做完它根本不存在）。
> **无破坏性变更**，配置结构一字未改，可直接覆盖安装。

### Added
- **全局守则（`~/.agentguard/GLOBAL_AGENTS.md`）的生成者**（`sandbox/global_agents.py`，只读体检
  加 `--check`）。此前三份端级模板（`templates/AGENTS_{pc,h5,backend}_slim.md`）都指向它，而它
  **没有生成者** —— 只有 `sandbox/bootstrap_agent_env.sh` 会写，那支脚本不在安装流程里、也没有
  任何调用方，于是照《安装说明》做完，"端级模板指着的那个文件"根本不存在。改由 `install_shim.py`
  （装编辑期 hook 那一步，必然会跑到）顺带渲染。**哨兵分区**：`<!-- HARNESS:BEGIN -->` 到
  `<!-- HARNESS:END -->` 之间每次重渲染、手改会被覆盖；**区外属用户，harness 永不触碰**。
- **`tests/test_global_agents.py`（守则正文只许住一处）**，以及 `sandbox/agents_audit.py`
  （客户仓 `AGENTS.md` 瘦身体检，**只读**，按「这一行有没有归宿」判定、不按端）。
- **`golden-patterns/pc/pc_dev_sop.md`**（8 步开发 SOP，净新增）+
  `integrations/eagle-rmc-pc-page-dev-skill/SKILL.md`（薄路由，frontmatter 的 `name`/`description`
  逐字沿用原技能 —— 触发词变了技能就不再被激活）。
- **`tests/test_agents_audit.py` / `tests/test_hooks_chain.py`** 等新锁。

### Changed
- **PC 范式：一整套幻影 API 就地改写**（不删小节，`pc_page_patterns.md` §6.3 等）。实测 13 个 PC 仓：
  `<eagle-drawer>` 标签 **0 处**、`src/components/` 无此文件、`registerGlobComp.ts` 无此注册；
  `useUnsavedGuard` / `closeForce` / `isFullScreen`、`src/composables/`、`venue/` 同样 0 命中。
  抽屉改用 `el-drawer`（Element Plus 原生，同期每仓实有 81~346 处）；「未保存提醒」改成真有的
  `closeConfim`（只弹框问一句、**不检测内容改动**）；出处 `venue/list.vue` → `accidentReport/tabs/list.vue`。
  ⚠ 复量时**别按子串 grep** —— `eagle-drawer` 会被 `<eagle-drawer-container>`（另一个组件）命中。
- **「130+ 个全局注册组件」→ 86**（口径脚注一并补上）。口径：`registerGlobComp.ts` 里**未注释**的
  `app.component("eagle-*")`，单双引号两种写法都要数（只数单引号会把 gxgk / ntrx / 712_v3 数成 0~1）。
  基线仓 86；712_v3 / gxgk / ntrx = 86、ZhongXin / keji = 85、HanBoGaoXin = 82、712 = 67、
  zhongdj = 66、hy 系三仓 = 57。「130+」约等于 `src/components/` 的 `.vue` 文件数（157 个，
  其中 `eagle-*` 123 个）—— **目录文件数 ≠ 全局注册数**。
- **`checks/components_allowlist.yaml` 的 `el_deny` 移除 `el-drawer`**：原 warn 指向一个不存在的
  封装组件，拿假规则骚扰真代码只会训练使用者忽略它。`el-dialog` 保留，并专门验过它不是同一类
  错误（`eagle-model` 确有封装，`registerGlobComp.ts:72`，各仓 630~988 处；`<el-dialog>` 仅 29~41 处）。
- **PC 页面开发技能并入 `golden-patterns/pc/`**（原先住在 `~/.skills-manager/`，那不是 git 仓、
  不随 harness 分发，客户仓那条「先加载技能」的规则因此没有归宿）。正文按主题并入 9 份，
  合并前先做的覆盖审计抓到 **1 处真丢**（「新增组件说明模板」整节蒸发，12 个落点逐字查 0 命中），
  已补回 `pc_components_request.md` §10.6。401 双传那条从"正确写法"降级为 ❌ 反例（会拼出
  `?id=1&id=1`，后端签名解析成 `"1,1"`）。
- **归宿表加组**：11 组 / 19 条 `[行]`、**0 条指空**；② 可删 **159** / 搬迁 22 / 结构 14 / 待迁 **127**
  （`agents_audit.py` 一行未改，变的只是手写的表）。**本工具没有任何写入路径，不动客户仓。**
- **`bootstrap_agent_env.sh` 三处静默失效**（「屏幕上打着 ✓，实际却没做」）：① 薄壳正文写成
  git-bash 形态路径，而它们正是 **Windows 上的 AI 工具**读它的入口路由 —— 实测 Windows Python
  开 `/c/...` 直接 `FileNotFoundError`，等于死指针却一直打 ✓；② 备份守卫判"含不含某串"而那串
  在正文里本来就有 ⇒ 守卫恒假 ⇒ 每次都无条件截断重写（真机活证据：三份 `.bak-20260919` 与现行
  文件**逐字相同**）；③ `ln -s` 对**文件**也返回 0 却做一次拷贝，会在
  `~/.config/opencode/AGENTS.md` 这种"工具会自动加载"的位置留下一份**再也不会刷新**的副本
  —— 专打新机器。判据一律落在**结果**（`[ -L ]` 回验 / 内容逐字比对），不落在退出码。
- **`docs/packaging/INSTALL.md` 补全全局守则这一节**（含它的卸载/升级语义 —— 与另外两样不同，
  **升级时要重跑才更新**，因为它渲染出来的是正文而不是指针）。
- **文档订正**：`README_推广接入.md` 不再写「✅ N 仓已有」（原写"6 仓已有"，实测 33 个受管仓中
  `AGENTS.md` 29 / `CLAUDE.md` 30 / `RULE.mdc` 2 —— 它是每机事实，写死必然有一天是假的），
  改成"不写数、指向命令"；`README.md` / `README_推广接入.md` / `AGENTS.md` 三处"路径示例"说法
  随共享 `harness.yaml` 只留键名一并订正。

### Fixed
- **三条夹具「写死了开发机状态」第三次复发，并首次在 CI 上真跑过。** main 自 09-16 起一直没推过
  远程，09-16 之后加进来的用例**从没在 CI 上跑过** —— 首次推远程才是第一次真验收，`harness-ci`
  当场红（822 例 / failures=6）。① 夹具用 `shutil.which("sh")` 跑脚本，而 Ubuntu 的 `/bin/sh`
  是 dash ⇒ `${BASH_SOURCE[0]}` `Bad substitution`，脚本压根没跑起来（本机 git-bash 的 `sh`
  恰是 bash 马甲 ⇒ **本机绿、CI 红**）；② `_same_path` 靠按平台选的 `normpath` ⇒ Linux 上
  `D:\x ≠ D:/x`，对每台 Windows 机器误报。教训：**写夹具前先问「这台机器上没有的东西，
  这条断言还成立吗」。**
- `pc_page_patterns.md` §6.3 的 `<el-drawer>` 每仓读数由不可复现的 `80~307` 改为实测 `81~346`
  并附复量命令（客户仓天天在改，当快照看）。

### Known gaps
- **PC 端 `el-drawer` 的"每仓多少处"是活数**（客户仓天天在改），文档里只能给快照 + 命令，
  给不了稳定值。
- `golden-patterns/pc/` 仍**没有任何锁覆盖**（`CODE_DIRS` 只扫 `.py`/`.sh` + `templates/*.md`）
  —— 这批改动改错了没有东西会响，所以合并前做的是**逐条覆盖审计**，不是靠锁。
- WSL 上非快跑档仍有一条红：`tests/test_review_v9_batch1.py::test_syntax_probe_is_false_without_node`
  —— 该机 `git`/`node`/`python3` 全在 `/usr/bin`，「造一条没有 node 的 PATH」这个前提不成立。
  带 `@_slow`，此前 WSL 复验走的是快跑档所以从没执行过。**未处置**（三个选项待拍板）。

## [0.1.5-dev] — 2026-09-18

> 本版的主线是「**同一份代码在两台机器上得到两种结论**」，两个面都治了。
> **判定面**：期望行尾是**维护机当年检出时的快照**，于是本机 `gxgk_h5` 拿一个**未曾改动**的
> 文件去跑都被判「行尾异常」（台账 **2026-09-12** 那两行 `gxgk_h5` 全量扫描里 `H5-CRLF` 各记
> 1489 条，全是误报 —— 这是个**带日期的读数**，不是"台账里现在有多少条"），而同事在
> WSL 上 clone 之后 PC 端**每个文件**都会被判错；33 条机器路径写在**共享**配置里，换台机器就
> 全部指空 —— 而配置看起来是「配好的」，症状不是报错而是**静默什么都不做**。
> **出货面**：同一份提交，Windows 上打一份、Linux 上打一份，解开后逐成员对下来**实测 8 处**不同
> ——5 处纯粹是行尾，2 处是构建机的 setuptools 版本，1 处是被前面两类带跑的派生文件。
> 出货这三层分开治：**源头**（工作树 / 索引的口径）、**出货点**（每个复制点按同一口径读）、
> **判据**（指纹不看平台噪音）。**无破坏性变更**，可直接覆盖安装。

### Added
- **`sandbox/eol_text.py`（行尾归一 + 指纹口径，本仓唯一的定义处）**：两个函数各回答一个问题。
  `align_to_index` 回答"出货**源码**该按什么行尾写"——以 **git 索引**为准：索引侧是 `lf`
  而工作树是 CRLF → 折成 LF，其余（`crlf` / `mixed` / `none` / 不在索引里）**一律原样**。
  只认 `lf` 一个标记，因为只有它才有"提交时 git 会归一成 LF"这个确定含义——折它是**复现**
  git 会做的事，不是发明一条转换规则；`crlf` 是"这个人就要 CRLF"、`mixed`/`none` 是"说不清"，
  两种都不该由打包器替他决定（宁可少折，不可猜错）。`canon_bytes` 回答"**指纹**里一个成员
  按什么算"——文本折 LF、二进制原样，二进制判据刻意与 `.gitattributes` 的 `text=auto` 一致
  （同样看前 8KB 有没有 NUL），免得两处口径各说各话。`index_eol_map()` 读 `git ls-files --eol`
  而**不是自己扫文件**：要的答案是"git 认为它该是什么"，扫文件只能拿到"它现在是什么"——
  那正是要修的东西。**任何失败都返回 `{}` 且绝不抛**：没有 git 就退回逐字节照搬（本模块出现
  之前的行为），出货件的可用性不挂在 git 上。
- **`tests/test_pack_eol.py`（22 例）**：五层，每层钉一个**具体的错法** —— ①对齐规则（写成
  `!= "crlf"` 会把 `mixed`/二进制一起卷进来 = 改内容）；②**真 git 临时仓库**（索引 LF /
  工作树 CRLF，真起一个仓库验它读的是索引侧而非盘上那份）；③指纹口径；④**两个支路同口径**；
  ⑤第 ⑧ 条**不假红**。③④ 每条"必须相同"的断言都配了一条**阴性对照**（差一个字符、换一支
  脚本必须仍然报）——只断言"相同"的话，一个恒返回同一值的实现照样全绿。⑤ 是本批最容易
  被忽略的一处：校验器若按**工作树字节**比，会在 Windows 上把一份**本来正确**的包判成坏的，
  而假红与假绿一样磨损可信度。**无 git 则 skip，不假绿。**
- **突变 M39–M42（各自独立，不合并）**：M39 对齐规则失效（`align_to_index` 退化为原样）、
  M40 指纹退回原始字节、M41 把 `RECORD` 收进指纹、M42 把 `Generator:` 行收回来。
  四个是**四个不同的根因**，合并成一个就再也分不清是哪一层漏了。
- **突变 M43–M45（各自出一条，理由同 M39–M42）**：M43 把 gate 键粘回上一行的末尾、M44 断掉
  「子进程 → 父进程」那条回传通道、M45 把解析失败伪装成端默认。M43 治的是**编辑动作**而不是代码
  （只要以后还有人在 `harness.yaml` 里批量删行它就会复发）；M44/M45 见下面「行尾来源留痕」那条。
- **`harness/_eol_policy.py`（期望行尾三态，引擎层新增）**：把「期望什么行尾」从**共享配置里的
  常量**改成**按本机本仓的检出事实判定**。三态：显式 `CRLF` / `LF`（语义与接线前**逐字节相同**，
  优先级最高，且**不探不打** —— 不跑 git 子进程）、`auto`（= 不写这一行，**新默认**）、端默认
  PC=CRLF / H5=LF。判定顺序每一步都有实测支撑：① `.gitattributes` 最优先（本仓自己就是反例：
  `autocrlf=true` 而 attr 写了 `eol=lf`，先看 autocrlf 会**当场判错**）→ ② 否则读
  `core.autocrlf`（`true`→CRLF / `input`→LF，这两个值是 git 的**保证**不是近似）→ ③ 否则
  （`false` / 未设）仍走 `ls-files` —— 实测 `eagle-rmc-mobile-net-hy` 是 `autocrlf=false` 而
  工作树 **1072 w/crlf / 0 w/lf**，计划里「`false` → LF」的捷径在它身上必错。判据是 **`w/`**
  （工作树侧）不是 `i/`：行尾门禁读的是磁盘字节。第 0 步「空仓不作任何断言」防的是**夹具随机器
  变**（`git init` 但从不 `git add` 的仓里 `autocrlf` 照样返回 true）；90% 门槛防的是批量转换
  （`git add --renormalize .`）让门禁**静默翻转**。出声判据收成「结论 != 端默认」+ `no-majority` /
  `probe-failed` 两条「探不出来」—— 每提交一行永不改变的话换来的不是「知道」而是**不再读**。
- **`tests/test_review_v13_batch9.py`（29 例）**：真临时仓锁三态语义（含那条**逼出顺序①的反例仓**），
  外加 5 例端到端读**台账**的（见下面「行尾来源留痕」）。
- **`tests/test_interpreter_convention.py`（11 例）**：四件事各钉一种复发形态 —— 一次定义 /
  标记即事实 / 不留裸 `python` 调用 / 行为阴阳两向。
- **`tests/test_repo_key_registry.py`（11 例）**：钉住「仓库**键名**是团队策略、**值**是每机事实」
  这条分界。②③ 两支各自**起两次真引擎**（`harness_self` 与 `--validate`，用 `HARNESS_CONFIG`
  喂夹具配置），而不是单测 `_config` 里的函数 —— ⑤ 跑在子进程里、事故现场就是它，只测函数
  只能证明「引擎知道这个键没填」，证明不了「谁读了它、得出的结论是什么」。这与批次 3 的 M44
  教的是同一件事（两半各自绿、中间是断的）。
- **突变 M46–M48（各自出一条，理由同 M39–M45）**：M46 把 `_machine_keys` 退回「数键在不在」
  的口径、M47 让自检把「键在、本机没填值」也判 ❌、M48 让 `--validate` 把 ⊘ 退回阻断项。
  **M47/M48 是同一条判据的两半张脸**（一个管提交链最前面的自检，一个管 CI 用的体检）：
  只修一边，就是「同事的提交过了而 CI 红」或反过来。

### Changed
- **`.gitattributes` 扩展为全仓**：`* text=auto eol=lf`，另加 `*.zip` / `*.whl`
  `-text -diff`（二进制不该被当文本处理）。此前这份文件只覆盖发送包装器那一小块，
  于是同一个仓库里"有的路径会归一、有的不会"——**同一类文件在仓库里有两套行为**，
  这本身就是下一次误诊的种子。安全性已实测：`git ls-files --eol` 全仓**无一例外全是
  `i/lf`**，零个 `i/crlf` / `i/mixed` —— 没有任何文本文件是以 CRLF 提交的，所以 `eol=lf`
  只改**检出**形态，**从不改已提交的字节**。（计数会随提交长：量的时候 232 个 `i/lf`，
  写下这一行的那次提交又新增 2 个；2026-09-18 在一个**全新 Linux 检出**上复核 = 234 个
  `i/lf` + 7 个 `i/-text` = 241 个文件，与 Windows 侧逐项相同。**判据是"有没有一个 crlf"，
  不是"有几个 lf"** —— 前者是结论，后者只是快照。）
- **工作树规范化（67 个 CRLF 文件按索引重写）**：此前 `w/crlf` 有 67 个，现在
  `{('i/lf','w/lf'): 232, ('i/-text','w/-text'): 7}`，**0 个 `w/crlf`**（这个 232 同理：
  规范化当时快照，不含本次新增的 2 个）。写法是逐文件从
  **git 索引 blob**（`git cat-file blob :<path>`）取字节重写，**不是** `git checkout -- .`
  ——后者会丢弃未提交的改动，而索引 blob 是"git 认定的那份"，非破坏性。事后
  `git add --renormalize .` 只暂存到 `.gitattributes` 本身、**没有产生任何新的内容变更**，
  这正是"已提交内容未被触碰"的证明（中途出现的 67 条幻影 ` M` 是 stat 缓存过期，
  `git diff --quiet` 本就是 0）。
- **`sandbox/pack_release.py` 的每个复制点都改走 `eol_text.copy_shipped`**：此前用
  `shutil.copy2` 照搬**工作树字节**——这正是根因 ①。6 个文本工具/文档改走归一读法，
  **2 个轮子仍是 `shutil.copy2`**（二进制，归一它就是改内容）。新增 `content_sha1()` 与原有
  `sha1_file()` **并列而非替换**：`SHA1SUMS.txt` 是**下载完整性清单**，必须逐字节精确；
  `content_sha1` 才是**指纹**口径。两个哈希各有各的用途，混成一个会在某一天让"下载校验"
  与"版本比对"中的一方说谎。
- **`pack_files()` 的两个支路必须同口径**（磁盘支路算"本次这份"、zip 支路算"上一次那份"）。
  只归一一边的话，比对的两个数仍来自两套口径，等于没修——而 zip 支路要算的"上一次那份"
  在 CI 形态下**正是别人（Linux）打出来的**。已由 `TestBothBranchesAgree` 单独钉住。
- **`integrations/plugins/build_plugin.py`** 的 `SKILL.md` 复制点同样改走归一读法
  （它也是那 8 处差异里的 1 处）。
- **`sandbox/verify_release.py` 第 ⑧ 条按"该出货的字节"读仓库那份**：与打包器同一个读法，
  这样"包内工具 vs 仓库工具"比的是同一件事，而不是拿 LF 的产去比 CRLF 的工作树（见 ⑤）。
- **解释器探测统一成「**真的能跑起来**」，9 个入口逐字节相同**（`harness/hooks-dir/pre-commit`
  正本 + 8 支 `sandbox/` 脚本）：症状在 WSL/Ubuntu 上是**每一次提交都 fail-closed**，而报错看不懂
  —— 钩子调的是裸 `python`，而 Ubuntu 上只有 `python3`。反方向也有洞：Windows 的 `python3`
  常是 Microsoft Store 的占位别名（`command -v` 能命中、一跑就失败）。所以约定**不是**
  「命令在不在」，而是「`--version` 退出码 0 且输出 `Python 3`」；候选顺序
  `$HARNESS_PYTHON` → `python3` → `python` → `py -3`，一个都没有就出声道明原因并 fail-closed。
  候选顺序只写在标记行 `# HARNESS-PY-CANDIDATES:` 里。**为什么必须内联、不抽成被 source 的
  `_python.sh`**：两条锁搭的是「只拷单支分发器」的最小布局真跑，同级新文件不会被拷过去 ——
  抽出去那些锁当场失效（技术上的不可行，不是取舍）。
- **33 条机器路径从共享配置请出去**：`config/harness.yaml` 里写着 33 条
  `D:/MyProject/aiauto/agentguard_*` —— 那是**维护机上的事实**，不是团队策略。现在路径一律下沉到
  gitignored 的 `config/harness.local.yaml`（各人一份），共享文件只留 `gates:` 段那 334 行策略。
  **连带面比预想大，且都是实测踩到的、不是推演**：① `checks/harness_self_check.py` 的 ⑤ 把
  `harness_self` 误报成「引用未定义的 repo」→ rc=1，而它跑在**每次提交链的最前面**（原先判断
  「删那行死值零风险」只对了一半：`_config` 的覆盖只发生在 `gate()` / `all_gates()`，而 ⑤ 读的是
  `load()`）；② `--validate` 的「repo 未配置」原本是**阻断项**，共享 repos 一空 → CI（干净容器，
  本来就没有本机路径）直接变红，所以本版必须同时引入「判得出本机有没有路径」这条区分；
  ③ `sandbox/acceptance_check.py` 的探针只写 gates 覆盖层，而 `HARNESS_CONFIG` 是**替换**
  local.yaml 的 → repos 整段为空 → 它造的「违规」在新语义下合法 → **假红**；④ `--list` 补一处
  会喊的静默：路径**配了**但本机不存在时逐行打 33 个 `@ D:/MyProject/...`，没有一行是真的，
  而每一行看起来都正常。
  ★ **2026-09-18 修正（跨平台批次 7，同日）**：这一批**顺手把键名一起删了**，方向对但越界了。
  详见下面「仓库键名表还给共享配置」那条 —— 它是一次**发布阻断级**的回归，且**存活了 5 个批次
  没人发现**，因为维护机上 33 个键全在、同事机上此前几轮 WSL 跑的都是「一条都没配」那个形态。
- **22 处 `line_ending:` 改为不写（= `auto`）**，`--validate` 同时补它的**值域**校验
  （此前什么校验都没有，写 `auto` 会被静默接受、拼错同样静默）。**施工事故留档**：删那 22 行时
  替换把该行与下一行之间的换行一起吞了，21 个 gate 键粘到上一行末尾 ⇒ 在 YAML 里整行成注释，
  **该 gate 从配置里整个消失**，而症状不报错、只是「看起来配好了」（`--validate --structure-only`
  从 34 掉到 13 且 `script:` 全部串位）。已逐 gate 比对 HEAD 修复并补三条锁 + 变异 M43 钉住。
  ★ 其中「严格行扫描 == 加载集」那条**咬不住**（粘连同时骗过行扫描与加载器，两边一起少、集合
  仍相等，实测注入后它是绿的）——真正起作用的是粘连正则与 `script` 后缀后果锁。
- **`sandbox/bootstrap_agent_env.sh` 生成的全局引导去个人化**：原先写着一个**维护机目录通配**
  （`在 D:/MyProject/aiauto/agentguard_* 、eagle-rmc-* 任何客户仓库改码前`），同事的仓根本不长在
  那儿；更早一处还把 `$HARNESS_ROOT` 留成了字面量（单引号 heredoc 不展开），读者照着敲等于敲了
  个未定义变量。「该管哪些仓」现在不再靠目录猜，改为指向 `run_gate.py --list`（唯一权威来源）。
  另两处 `/d/MyProject/...` 通配循环是同类里更隐蔽的一种：**零匹配时「检查过了」与
  「什么都没检查」在屏幕上长得一模一样**。
- **`integrations/` 三处去个人化**：`zcode/opencode-go-proxy.py` 的 `ENV_PATH` 原写死维护机的
  `C:/Users/HP/AppData/Local/hermes/.env`，改成三个候选落点依次试，判据是「**这个文件真的在**」
  而不是「目录在」（空目录会让 `open()` 抛得更晚、更难懂）；`trae-cn/README.md` 示例命令里印着
  维护者的 `python.exe` 绝对路径（这一段是全文档最容易被直接抄走的，照抄必然跑不起来）；
  `opencode/agentguard-gate.ts` 的 harness 目录查找改成按候选推导。
- **`sandbox/run_acceptance_all.py` 修上面的连带回归**（不是去个人化）：工程根原先写死，
  且「仓→路径」是用正则从 `config/harness.yaml` 刮 `repos:` 段 —— repos 段下沉后共享文件里只剩
  注释，实测（在 `10487ec` 那个提交的状态上跑旧算法）repos 段共 1 条、识别出 **0** 条，紧接着的
  `assert len(gates) >= 10` 当场炸。★ **当时全量套件是绿的**（748 例 / skipped=3 / rc=0），
  没有任何一条锁盖住这个脚本 ——「套件全绿」与「这一批真的没把别处弄坏」是两件事，
  这次是后者的一个实例。现在改为从**生效配置**读，工程根由**本文件位置**推导。
- **行尾来源落进台账（`eol_source` / `eol_value`）**：`resolve()` 一直返回 `(值, 来源)`，
  而两个调用点都只取 `[0]` —— 来源算了又扔。本版只是把那个被丢掉的第二返回值接住，经**既有**的
  `HARNESS_STATS_FILE` 通道回传，由 `_log_run` 落成两个**平铺**键（平铺而非嵌对象，为的是
  `grep eol_source reports/gate_log.jsonl`，与 `weakened` 同一种读法）。**代价：0 次新增 git 调用。**
  **不落键是硬的，不是省事**：非行尾 gate（backend / `harness_self`）、被本机关掉的 gate
  （子进程压根没说话）一律不落键 —— 每行都挂一个「来源」等于没有来源；5 条锁里有 2 条就是这两条
  阴性对照，且都先断言「这一行确实落了」。**`resolve-failed` 与 `endpoint-default` 必须分开**：
  半残部署（`_eol_policy.py` 不在，老装机就是这形态）原先走裸 `except: pass` → 门禁按模块默认跑、
  台账里看不出任何异样；现在留痕，但**绝不写 `endpoint-default`** —— 那等于把「没解析出来」和
  「解析出来正好是端默认」写成同一句话，而 `no-repo`（本机没配这个仓）是**正常**状态。
  **为什么这几条锁必须端到端**：这条通道有两半（子进程写临时文件 / 父进程 `_read_stats()` +
  `_log_run()` 落盘），单测 `emit_stats()` 只覆盖前一半 —— M44 断的正是**后一半**，
  实测单测全绿、只有端到端读台账那几条红。
- **丢锁清单首次与实测对齐**：`test_fix_paths.py` 3→4、补登 `test_bootstrap.py`=8，
  **47 个模块 = 47 个 `test_*.py`，total = 逐模块之和 = 磁盘实测**。这不是本版引入的：
  前者登记值低于实测 1 条，后者**根本没进登记表** —— 按清单自己的注解，未登记的模块
  「删光锁不会被任何检查发现」。看守的 `test_total_not_shrunk` 用的是 `>=` 且**只看总数不看归属**，
  所以它一直没红 —— 不是它漏了，是它按设计管不了「这 9 条差额是谁的」。
- **`tests/test_review_v6.py` 的 doctor 预算 300→600s**：这是**测量修正**不是性能断言 ——
  实测 doctor 单跑 65~85s，而在全量套件里跑到 **370s**（同一份脚本，5× 膨胀，成本几乎全在
  Windows 进程创建上）。红的样子很费解、值得记一笔：`TimeoutExpired: … timed out after -70.65
  seconds` —— **负的**，因为 `Popen.communicate` 只在管道空闲超时时才查预算，而它一直在吐输出。
- **仓库键名表还给共享配置（批次 2 的回归，发布阻断级）**：`config/harness.yaml` 的 `repos:` 段
  重新声明全部 33 个键、**值一律留空**。批次 2 把键名连值一起请出去，于是「这个键压根没声明过」
  与「我这台机器没有这个仓」**塌成了同一件事**（都是「键不在 repos 段里」）；而剩下的判据
  （`_machine`：本机有没有机器路径）是按**维护机形态**校准的（那里 33 个键全在），所以它一旦
  成立就真是拼错。**实测的同事机形态是另一个**，而且是**最常见**的那个：`docs/packaging/INSTALL.md`
  明写「只写你机器上确实存在的那几个仓即可」、「场景 A」模板也只给 3 个键。复现（只填 1 条）：
  `harness_self` 打 32 行 `❌ ⑤ gate <X> 引用未定义的 repo` → rc=1，提交钩子再把它转成
  **fail-closed rc=2** —— **该同事每次提交都被拦，而他自己的代码一个字节都没被检查过**
  （拦在自检这一步，根本走不到他的 `.vue`）。同一次输出里还自相矛盾：`⚠ … 路径本机不存在 ——
  不阻断` 就打在 `❌ … 引用未定义的 repo` 上面，而现实里这两句说的是同一件事。
  **为什么 5 个批次没人发现**：维护机上 33 个键全在（看不见）；同事机上此前几轮 WSL 复验跑的都是
  「一条都没配」那个形态（走汇总 ⚠ → 全绿），是批次 6 的 WSL 复验第一次造出「配了 1 条」这个
  中间形态才当场红的。**修法不是把判据反过来**：两根判据各归各位 —— 键不在 repos 段 =
  真配置错（拼错 / 新 gate 漏登记），**任何机器上都阻断**；键在、值空或路径不存在 =「本机没这个
  仓」，常态，**不阻断但仍然逐条看得见**（⊘ 列出 + 末尾汇总）。检测力那一半由 M43 与新增的
  M47/M48 分别守住。附带修掉一个**同源**的复发：`_sanity_check` 里逐 gate 打的「已启用但仓库
  路径不可用」在**零配路径**的机器上是 33 行，把批次 2 专门为它做的汇总又冲掉了 —— 同一夹具
  同一命令下 stderr 非空行 **38 → 5**（钩子的 `_compact` 只有 40 行回灌窗口，33 行噪音足以把
  真正要改的那一行挤出去）。两处前后对照与复量方法见 `reports/_batch7_before_after.txt`。

### Known gaps
- **「本机覆盖的留痕」（`line_ending` 的本机覆盖 / 台账 `local_overrides`）仍未做**，且是**有意**
  留到下一批：它与本版做的「行尾**来源**留痕」不是同一件东西 —— `local_overrides` 记的是
  「本机**覆盖**了」（削弱族，干净跑不出现），本版记的是「期望行尾**从哪来**」（干净跑照样有）。
  之所以另起一批：要动受保护文件 + 台账 schema + `test_review_v13_batch8.py` 那套 `weakened` 锁，
  混批会让「行尾到底修好了没有」不可判。**本版刻意把粒度封顶在 `declared` 一档**，不细分
  「声明值来自 base 还是 local」—— 那正是下一批的地盘。
- **本版治的是"指纹"，不是"轮子字节"**：轮子里的 `dist-info/METADATA` 由 setuptools 在
  **本平台以文本模式**写出（Windows 上连**头字段**都带 `\r`，而它读的 `README.md` 是纯 LF
  —— 911 行 0 个 CRLF），这一处**不是源码行尾造成的**，钉 setuptools 版本也治不好。
  本批只保证**指纹**跨平台一致（G3 告警的判据），**不**保证轮子文件字节跨平台一致；
  后者要么把构建收敛到单一平台、要么事后重写产物，两件事都不该由一个归一模块偷偷做掉。
- **实测验收（两个时点，都留在案）**：
  ① **重打包前**（只读复算，不依赖重打包成功）：CI 那份
  `docs/releases/assets/agentguard-v0.1.4-dev.zip` 与本机那份在新口径下**重算得到同一个
  指纹 `e0a930520487`、14 个成员 diff 0 处** —— 两份都是**改动之前**打出来的，所以这条证明的是
  "口径真的把平台差异挡掉了"，而不是"新打包器自己跟自己一致"。
  ② **重打包后**：本机那份的 `PACK-INFO.json` 由 `066cd5b0136f` 变为 `e0a930520487`，
  与 ① 的复算值逐字符相同；**同机连打两次指纹不动**（时间戳确实在指纹之外）；
  最关键的一条 —— 重打包时**没有**触发 G3「同版本号、内容不同」告警：`dist/` 里那份旧的
  Windows 包与刚打出来这份**指纹撞上了**。那正是本批要的效果。
  CI 那份文件里记录的 `f38503b01e4a` 是历史，**不改写**（它是别的平台的记录，且已被 CI 用掉）。

## [0.1.4-dev] — 2026-09-18

> 「包内**不含**引擎」这件事此前只写在《安装说明》先决条件的**括号里**（不是一步），
> 于是首次接入最常见的第一个坑是：跑到第 0 步路径适配被 fail-fast 拦下，报「没找到 mcp.py」，
> 而真正缺的是「还没 clone」。本版把这一步变成**可执行的一步**，并把两处误诊拆开。
> **无破坏性变更**，可直接覆盖安装。

### Added
- **`bootstrap.py`（引擎引导，随包分发）**：一条命令把引擎仓 clone 到本机
  （`--dir` 换位置 / `--url` 换地址或走 SSH / `--check` 只体检）。跑完会把**后面两步的命令
  直接打出来**（路径适配 + 生成覆盖层），不要求读者回去翻文档。三条刻意的"不做"：
  不覆盖已存在的目录、不更新已有的引擎仓、没有 `--yes`（动作是纯增量的）——
  因此它**不在 `uninstall.py` 的五项清理里**：clone 出来的仓库是你自己的仓库。
- **《安装说明》新增「零、先 clone 引擎仓」一节**：讲清包内为什么永远装不出引擎
  （钩子 / MCP 注册 / shim 全都指向仓库，引擎更新即生效 —— 这是**有意**的取舍），
  给出可复制的命令与「没 clone 会怎样」的对照；末尾有与「二」的**第 0 步**的区分（包外 vs 包内）。
- **`tests/test_bootstrap.py`（8 例，**真 git / 真 clone**，不用替身）**：正路 / 幂等 /
  拒绝占着的目录 / 没有 git / clone 报错 / 克隆到的不是引擎仓 / `--check` 不动手，
  外加一条**跨工具判据锁**——bootstrap 说"就位"了，`fix_paths.py` 就必须不再拦
  （两处判据一旦分叉，就会长出"上一步说好了、下一步照样 ✗"的鬼故事）。5 处突变验证。

### Changed
- **`fix_paths.py` 的 fail-fast 不再把两种情形合成一句**：目录**根本不存在** → 这是"还没
  clone 引擎仓"，直接给可复制的 clone 命令 + 指向《安装说明》「零」；目录在、只是没有
  `mcp.py` → 那是指错了仓 / 路径拼错，**不提 clone**。合成一句时，拿到包的人会去反复核对拼写，
  而问题根本不在拼写上。（配套锁：`tests/test_fix_paths.py` 两条，含一条负对照。）
- **清掉 5 处写死的「三支工具」计数**：随包工具从三支变四支，于是 `pack_release.py` /
  `verify_release.py` / `test_release_artifact.py` / `test_preflight.py` 里那几处
  注释与文档一律改为**以常量或产物为准**（`SHIPPED_TOOLS` / `TOOLS` / `ENTRIES`）。
  写死数量的注释必然在下一次加工具时烂掉 —— 本版就是"下一次"。
- **修 `test_release_artifact.py` 里一条因名单过期而假红的锁**：`test_identical_repack_stays_quiet`
  造"上一次那份包"时是**手写文件名单**，加 `bootstrap.py` 时漏了项，于是这条"内容相同就不许响"
  的锁在一次完全正常的重打包上红了 —— 报出来的形状与"打包器有问题"一模一样。改为从产物派生
  （`_restage_identically`；只排除两个自指文件，轮子重新打时间戳）。

### Known gaps
- （无新增未闭合项。下列两条自 0.1.3-dev 结转。）
- **「组件引用」环节未做（明确后补，2026-09-18）**：含路径形态的引用计数已留档
  `reports/_comp_path_scan.txt`（`reports/` 整个被 gitignore，不进版本控制），
  但 **DEAD/DENY 名单尚未据此重判、README 那一节也标着「未完成（待补）」**。
  次序已定：先修引用计数 → 重判名单 → 才谈接线。**本轮不接线、不动名单。**
- **「几家客户端」的口径还没定死**：文档 6 处写「**7 家客户端**的编辑期门禁」
  （`cli.py:351`、`sandbox/check_client_configs.py:99`、`sandbox/install_shim.py`、`sandbox/uninstall.py`、
  `sandbox/upgrade.py`），而 `check_client_configs.py` 的 `CLIENTS` 只有 **6** 个
  （claude / codebuddy / codex / qoder / zcode / trae）。
  **2026-09-18 找到一条硬证据**：`tests/contracts/baseline/` 下正好有 **7** 份契约基线
  （上述 6 个 + `opencode.json`），`integrations/` 也有对应的 `opencode`（**有真实钩子实现**
  `agentguard-gate.ts`）；`integrations/hermes/` **只有 README**、无契约基线。
  → 文档里的「7 家」几乎可以确定就是这个集合，缺口在 `check_client_configs.py` 漏登记 `opencode`。
  **但本轮不改它**（要对齐的是「检查器该不该收 opencode」这个决定，不是数字）。
  **对外引用这个数之前先定死是哪几家。**

## [0.1.3-dev] — 2026-09-18

> 本段 = 2026-09-18 发版时把 `[Unreleased]` **整段迁移**过来的全部内容（含更早日期写下的条目，
> 它们此前从未被归入任何版本段）+ v0.1.2-dev 之后新增的部分。为什么整段迁移而不是只挑本版的：
> 约定是「发版时把 `[Unreleased]` 里属于本版的内容移进版本段」，而这些条目当时**都还没有归属**
> （`## [0.1.2-dev]` 那段只记了当版新增的 `uninstall.py`）—— 再留在 `[Unreleased]` 里，
> 下一次发版就更分不清是哪一版出的，正是本文件开头那条约定要解决的问题。
> 各条自带的日期是**写下它的日期**，不是发版日期。

### Added
- **README 补三节：「能带来什么」/「升级」/「卸载」（2026-09-18，+117 行）**：此前 README 讲了「是什么 / 怎么装」，
  却漏了同事最要紧的三问 —— 用它对我有什么好处、装完怎么升级、不想要了怎么退干净。三节按**读者要做的事**
  组织（不按内部模块名），升级一节写清两种布局下的动作差异（路径引用型 / 副本型），卸载一节对齐包内
  `uninstall.py` 的五项撤回。
- **《门禁安装包-升级链路剖析》U-1…V-2 全部落地（2026-09-17）**：按报告批次逐条修 ——
  **U-1** 出货件与机器无关（占位符 / `PACK-INFO.json` 指纹 / 校验器三道锁）+ 修复变异冒烟空转；
  **U-2** 补升级入口 `sandbox/upgrade.py` 与安装矩阵 `cli.py installed`（补上「装」与「卸」之间那一段）；
  **U-3** 卸载面 G5 + G8（编辑期 shim 纳入 INSTALL.md 与 `uninstall.py`；skill 归档 `.disabled-*` 的残留、重名与占用误诊）；
  **U-4** 升级面文档（定义「原地升级」/ 修下载渠道旧说法 / CHANGELOG 版本段）；
  **U-5** 升级路径端到端锁（装 A → 装 B 四条通道）+ 出货件校验器第 ⑧ 项（包内工具与仓库同源）；
  **V-1** pip 同版本号时**直接代跑** `--force-reinstall` 再对账（原先静默）；
  **V-2** 出货件指纹**真扩到整包** —— 且轮子按**内容**算（zip 条目时间戳随构建时刻变，按字节算的话
  「同版本号、内容不同」这句会逢包必响，人就把它训练成噪音，与漏报同一种失败）。
- **测试套件「慢用例接线」批 1–6（2026-09-17）**：`--fast` 快跑档从 **214.5s 降到约 29.8s**，
  全量 678 例不变（`lock-inventory.json` 全程未动）。判据在两轮复量后被改写两次 —— 从「文件名义耗时」
  改成「**这些秒在不在快跑档的关键路径上**」，再加一条「**这条 ≥1s 的例是不是某条变异的唯一判据**」。
  两个反例都已写进 `tests/test_coverage.py` 的名单锁 docstring：批 5 收益实测为 0（含同跑内重放自证），
  批 4 差点把 M19 的唯一牙齿当「≥1s 的例」削掉。**接线规则的可执行判据 = `tests/test_coverage.py::must_wire`。**
- **锁清单再基线 + 给计数看守补上第一条变异（2026-09-17）**：`tests/lock-inventory.json` 从
  35 个模块 / 506 条 → **43 个模块 / 677 条**（按 `_count()` 实测值贴齐，补登 `test_review_v13_batch1..8.py`）。
  新增变异 **M38**：把一条锁改名成 `_disabled_*`，模块自己全绿，**只有计数看守看得见** —— 此前名单里
  一条打它的变异都没有。
- **代码评审 v13 全量落地（2026-09-16）**：严重 1–5 / 高 A·B·6·7 / 中 9–20 / 低 18·24·25 逐条修，
  分五批提交（接线类 → 静默通过类 → 语义类 → 高 6·低 25·中 17 → 收尾），每批带回归锁。
  其中两条是「看着在守、其实守不住」：保护哨兵在真实提交链路上**恒返回 0**、`harness_self` 验收探针
  会写进业务仓文件（现改为密闭夹具）。
- **台账新增 `skipped` / `weakened` 两个可区分字段（2026-09-17，评审 v13 复查遗留项②）**：此前「本机把 gate 关了」（`harness.local.yaml` 的 `enabled: false`，设计上允许）与「跑了且通过」都是 `rc=0`；「`HARNESS_ALLOW_POLICY_OVERRIDE=1` 放行策略覆盖」那一行更是一个字段都不多 —— 实测整本 `gate_log.jsonl` 里 `enabled`/`override`/`policy`/`local` 四个关键词**一个都搜不到**。现在两条削弱路径各留痕（`local_disabled` / `policy_override:gates.<gate>.<字段>`），干净跑不写字段；`cli.py log` 加 `mark` 列与 CSV 列、`run_gate --summary` 单列「跳过 N 次 / 被削弱 N 次（哪些 gate）」；同一条策略告警同一命令只打一遍（含 run_gate 起的检查子进程）；回归锁 `tests/test_review_v13_batch8.py`（18 例，7 处突变验证）。
- **MCP/CLI 持久化回归测试入库（2026-09-16）**：`tests/test_agentguard.py`（10 例）补上 —— 覆盖协议 fixtures / 工具行为 / 三入口一致性，闭合旧「Known gaps」（该缺口此前长期以运行时证据 + 契约锁代偿）。
- **维护导览上线（2026-09-15）**：README 新增「维护导览」（改规则／造范式／接端／改引擎／发版／排障 的入口与验证矩阵）；分享 PPT 新增第 16 页「怎么一起维护它（维护者导览）」（17→18 页）；PPT gate 计数口径 33→34 与 config 同步（含工程自检 gate）；第 16 页补「改动 → 生效路径」说明（何时 pull、何时重装、何时需重新发包）。
- **发布流水线上线（2026-09-15）**：新增 `.github/workflows/release.yml`——推 `v*` tag → 全量自检 → 构建 wheel + 内部安装包 → 自动创建 GitHub Release（策展说明 `docs/releases/<tag>.md` + CI 自动追加校验行（SHA1/大小/文件数）与 Full Changelog 对比链接 + ASCII 附件；tag 与 VERSION 一致性校验；`workflow_dispatch` 空跑构建校验）；新增 `docs/releases/` 发布说明约定（README + 模板 + v0.1.1 / v0.1.2 回填）；README「打包与分发」发布流程更新为 2 步。
- 文档升级（2026-09-14）：README 新增「AGENTS.md 单守 vs harness 门禁优缺点对照」「三种接入方式：技能 / hooks / 插件」「支持的 AI Agent 工具清单」三节；`docs/harness分享PPT.html` 扩至 15 页（新增 3 页）；`docs/packaging/INSTALL.md`（安装说明单一来源，打包脚本渲染）。
- `sandbox/pack_release.py`：内部安装包一键重建（wheel + marketplace + OPENCLAW.md / LICENSE / 安装说明 + SHA1SUMS + zip；幂等可重复）。
- 打包路线开工（分支 `feat/packaging`）：M0 基线 = `VERSION` / `CHANGELOG` / `docs/adr/0001-packaging-principles.md` / `tests/test_packaging_contract.py`（壳契约锁骨架）。
- **推荐版交付（2026-09-14）**：
  - `agentguard/`：MCP 薄服务 `mcp.py`（单文件、零第三方依赖、6 工具、stdio JSON-RPC）+ CLI `cli.py`（run/check/log/ops/hooks/skill/bypass/setup/quickstart）。
  - 引擎台账字段：`entry`（来源入口 hook|cli|mcp）与 `ver`（VERSION 单一来源）；提交钩子链自动标记 `hook`。
  - 技能包升级（MCP/CLI/台账/留痕四段新增）；`list_entries` ENTRIES 扩展至 23 个入口。
  - Claude 插件原型：`integrations/plugins/`（构建器 + 端到端自证 + `claude plugin validate --strict` 0 错误 0 警告）。
  - 文档：`docs/packaging/RUNBOOK.md` / `docs/packaging/COMPAT.md`。
- 文档：`docs/packaging/OPENCLAW.md`（OpenClaw 接入完整步骤）。
- 法务：新增 `LICENSE`（内部专有；对外边界=完全内部，见《决策记录》2026-09-14）。

### Changed
- **R5 出货提示里去掉写死的存量数（2026-09-18，`checks/backend_ruleset.py`）**：原文案写着「gxgk 存量 975 处」，
  而这个数在任何合理口径下**都复现不出来**（同日用本规则自身的 `check()` 全量实扫 gxgk 后端仓：
  **260 处 / 114 个文件**）。要命的是它写在**出货代码**里 —— 每次 R5 命中都会把这句打给 11 个客户仓的开发者，
  文档里的「975」只是它的回声。**修法不是换个新数，是把数字从文案里删掉**（改为「存量数百处」）：
  存量数随仓库变动，写死在文案里既没人复量、又会让企业级的数扩散成常识 —— 一个没人复量的数，不如没有数。
- **README「组件引用路径写法」一节更正：前提一半是错的，结论反过来了（2026-09-18）**：原文断言
  「组件收敛规则看不见路径写法 → 53–55 处逃逸」，实测（`reports/_comp_path_scan.py`，纯只读）后：
  ① `registerGlobComp.ts` 这类文件今天**就是红的**（注册文件必然写裸形态，规则已抓到）；
  ② 真缺口在 `views/layout/*.vue` + `views/modules/**`，H5 侧净新增为 **0**（范围收窄到 PC）；
  ③ ★ 但真正的发现是**名单本身是错的** —— 命中的组件里，`eagle-menu-project` 这类被 allowlist 标成
  「零引用死组件」的，实际被 **37 个业务文件**用着（与 `eagle-choose-tag` 是同一个 bug 的复发：
  引用计数的数据源看不见路径写法，于是把「用着的」判成了「死的」）。
  → 正确顺序是**先修引用计数 → 据此重判 DEAD/DENY 名单 → 才接线**；直接接线 = 拿假前提拦真代码。
  本轮**不接线、不动名单**（「组件引用」这一环待补），改写内容与复量更正一并落进 README 与锁它的
  `tests/test_review_v13_batch4.py`（只动散文，不动断言）。
- 分享 PPT 文案精修（2026-09-14 晚）：第 5 页标题与卡内两处「单守」统一改「纯 AGENTS.md」；第 6 页重构为「本场景需要、它们给不了的三件事」（多仓统一治理 / AI 行为闭环 / 全栈轻量覆盖）+ 左右卡定位句；第 9 / 11 页导语精简（auto-gate 通用入口、三型 导航 / 主力 / 兜底）。
- 整改 C1（2026-09-14）：新增 `sandbox/run_tests.py`（文件级并行，标准库；`--fast` / `--profile` / `-j N`）与回归锁；README 测试章节更新为「并行档 + 串行两档」；实测全量墙钟 517s→127s（4.1×，本机带负载）。
- 整改 A1（2026-09-14）：新增本机对账工具 `sandbox/audit_reconcile.py`（提交 × 台账四分类 + 钩子体检 + 配置时间佐证）与回归锁（7 用例）；RUNBOOK §2 增补对账用法；首跑 33 仓（✅10 ｜ ◻12 ｜ ⚠5 ｜ ⛔0）。
- 分享 PPT 第 6 页讲稿化改写（2026-09-14）：术语改白话（一处配置 / 存量豁免 / 回灌环等）+ 末行孤字修正；第 15 页「E1 配置保护」→「配置防漂移」。
- 分享 PPT 外部评审收编 + 复审修正（2026-09-14）：5 / 6 页合并，外部评审新增「为什么不直接用现成工具」「已知边界与下一步」两页（现 17 页）入库；复审修正第 6 / 15 页表述精度、去除内部标签、页码与《推广接入》索引同步。
- 分享 PPT 去重（2026-09-14）：「三种接入模式」与「三型怎么用」两页合并为一页（步骤并入模式卡，17→16 页）；页码、分页注释与《推广接入》索引同步。
- 分享 PPT 全量复审与丰富（2026-09-14）：新增「保护循环」「耗时实测」两页（现 17 页）；数字口径校正——可评判规则 26 → 29（PC 10→9 / H5 8→7；口径 = 23 条规则 + 6 类 loader 级检查）；架构页补 agentguard 打包壳；对比 / 规则页增补审计留痕与豁免机制；总结页文档指针更新。
- 文档一致性（2026-09-14 晚）：接入模式统一升级为三型（自觉 / 工具 / 强制）——分享 PPT 第 10 / 11 页改版（工具型入列 + 步骤扩充）、README 与《推广接入指南》同步；清理「两种接入模式」残留。
- 仓库卫生：`.gitignore` 增加 `dist/` / `build/` / `*.egg-info/`（打包与构建产物）。
- `run_gate.py`：未登记 gate 名 → **rc=2 明确报错**（2026-09-14 采纳评审观察项方案 B，fail-closed）——
  此前与「已禁用」同路径静默跳过 rc=0，拼错/改名会成假绿；已登记但 `enabled:false` 仍 rc=0 跳过；
  配套回归锁：`tests/test_hooks_chain.py`。
- 源码卫生：修复 `tests/test_hooks_chain.py` 头部注释的 invalid escape 告警（Python 3.12+ SyntaxWarning），新增全仓扫描锁 `tests/test_source_hygiene.py`。

### Known gaps
- （无未闭合项。上一条已知缺口 `tests/test_agentguard.py` 已于 2026-09-16 入库，见 Added。）
- **「组件引用」环节未做（明确后补，2026-09-18）**：含路径形态的引用计数已留档
  `reports/_comp_path_scan.txt`（`reports/` 整个被 gitignore，不进版本控制），
  但 **DEAD/DENY 名单尚未据此重判、README 那一节也标着「未完成（待补）」**。
  次序已定：先修引用计数 → 重判名单 → 才谈接线。**本轮不接线、不动名单。**
- **「几家客户端」的口径还没定死**：文档 6 处写「**7 家客户端**的编辑期门禁」
  （`cli.py:351`、`sandbox/check_client_configs.py:99`、`sandbox/install_shim.py`、`sandbox/uninstall.py`、
  `sandbox/upgrade.py`），而 `check_client_configs.py` 的 `CLIENTS` 只有 **6** 个
  （claude / codebuddy / codex / qoder / zcode / trae）。
  **2026-09-18 找到一条硬证据**：`tests/contracts/baseline/` 下正好有 **7** 份契约基线
  （上述 6 个 + `opencode.json`），`integrations/` 也有对应的 `opencode`（**有真实钩子实现**
  `agentguard-gate.ts`）；`integrations/hermes/` **只有 README**、无契约基线。
  → 文档里的「7 家」几乎可以确定就是这个集合，缺口在 `check_client_configs.py` 漏登记 `opencode`。
  **但本轮不改它**（要对齐的是「检查器该不该收 opencode」这个决定，不是数字）。
  **对外引用这个数之前先定死是哪几家。**

## [0.1.2-dev] — 2026-09-14

### Added
- **一键卸载上线 v0.1.2-dev**（2026-09-14）：`VERSION` 0.1.1-dev → 0.1.2-dev；内部安装包新增 `uninstall.py`（整机退出五项清理：技能副本归档 / 提交期钩子撤回 / Claude Code 插件 / OpenClaw 扩展 / pip CLI；默认预演、`--yes` 执行；防误删特征校验 + 台账留痕）与回归锁 `tests/test_uninstall.py`（6 例）；安装说明「四、卸载」重写为逐项对照表；OPENCLAW.md / RUNBOOK / README 同步。

## [0.1.1-dev] — 2026-09-14

### Added
- **发布 v0.1.1-dev**（2026-09-14）：`VERSION` 0.1.0-dev → 0.1.1-dev；内部安装包新增 `fix_paths.py`（换机器路径适配：一键改写 `.mcp.json` 的 python / 引擎路径）与回归锁 `tests/test_fix_paths.py`；安装说明新增「第 0 步·路径适配」与升级附注；README「三种接入方式」补最短路径 + 三通道逐步安装，「打包与分发」补 Release 下载与「发布新版本（3 步）」。
