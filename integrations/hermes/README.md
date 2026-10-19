# Hermes 接入 eaglermc_harness

Hermes 用本工程的两条通路：`AGENTS.md` 地图 + skill（`eaglermc-harness`，已分发进各 agent skills/）。

## 1. 地图：仓库根 AGENTS.md（已就位）

`<HARNESS>/AGENTS.md` —— 任何 agent 打开本工程即可读；在端仓库改码时
Hermes 靠 `auto-gate.py` 的「改码后跑门禁」约定执行。

## 2. skill：eaglermc-harness（已分发给 Hermes skills）

由 `skills-manager` 分发到 Hermes 的 skills/ 目录，Hermes 在进入 gxgk/hy/hy-hzy 改码场景时
自动加载，按其中「跑门禁」规则执行。

## 3. 手动触发（Hermes 会话内）

直接在端仓库根跑以下命令过门禁并看退出码（0=通过 / 1=拦截 / 3=非受管跳过）：
```
python <HARNESS>/harness/agents/auto-gate.py --repo D:/MyProject/aiauto/eaglermc_pc_net-gxgk
```

## 4. Hermes 专属强绑定（必做，不是可选）

Hermes 没有 pre-edit / post-edit 编辑器钩子，编辑期唯一的强制点就是约定本身 —— 所以下面两句
**必须**写进 Hermes 的 SOUL / profile 规则（让它成为硬默认，不靠 skill 提醒的自觉）：

1. 「在 gxgk / hy / hy-hzy / 712 / ntrx 等受管仓改码，改完先跑
   `python <HARNESS>/harness/agents/auto-gate.py`，退出码 0 才算写完」
2. 「任务收尾用 `python <HARNESS>/eaglermc_gate/cli.py done --repo <仓>` 收口（跑门禁 + 打凭证行）；
   把凭证行粘进回复；没跑门禁的任务不算完成」

## 5. 写码前先读黄金范式（同 skill 约定）

进仓改码前先读端级范式（PC / H5 / 后端各自的 `AGENTS_*_slim.md` 指路），
照已验收样板写 —— 被门禁拦下时，命中输出里带 `📖 See:` 指针，顺着它读对应范式文档。