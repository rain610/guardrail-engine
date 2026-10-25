#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`~/.guardrail-engine/GLOBAL_AGENTS.md` 的**唯一**渲染器（正文 + 哨兵分区拼装）。

为什么是「唯一」（2026-09-19）
--------------------------------------------------------------------------------
这份文件有**两个写入方**：
  ① `sandbox/bootstrap_agent_env.sh` —— 整机装配第 1 步；
  ② `sandbox/install_shim.py`      —— 《安装说明》「二、Agent 接入」里**同事必然跑到**的那一步。
原先只有 ①：正文（heredoc）与拼装（内联 python）都长在那个 shell 脚本里。要让 ② 也会写，
最直觉的做法是「再抄一份」——**那样会漂，而且漂得很难看**：

  哨兵行只要差一个字符（多一个空格、换个全/半角），先写的那份就会被后写的那份判成
  「没有哨兵的老文件」→ 走迁移分支，把整份旧内容**追加**到哨兵区之后；两个写入方交替跑
  （装 shim → 跑 bootstrap → 再装 shim…），文件每跑一次涨一倍，而屏幕上每次都打印绿色 ✓。

所以正文与拼装都收进本模块，两个写入方都调它 —— **没有第二份可漂**。
`tests/test_global_agents.py` 里有一条锁直接盯这件事：正文标志行在 `sandbox/` 下只许出现在
本文件里（判据来自本文档 §一「正文只有一份」）。

顺带修掉一个潜在 bug：原写法是 `sed -i "s|__HARNESS__|$HARNESS_WIN|g"`，工程路径里含
`|` 或 `&` 时会被 sed 当元字符吃掉/改写（Linux 上把仓放在 `~/a&b/` 下就是活例）。
这里是 `str.replace`，不解释元字符。

三个动作（与旧实现逐字同口径 —— 换实现不改行为，本机那份现存文件重跑后必须仍是
`rerendered` 且逐字节不变）
--------------------------------------------------------------------------------
  created     目标不存在：新建，哨兵区 + 区外为空
  rerendered  哨兵在：**只换哨兵之间**，区外一字不动
  migrated    老版本生成的文件（无哨兵）：先备份 `<文件>.pre-sentinel-<日期>`，
              **旧内容一律原样保留在哨兵区之后** —— 无法机械判断哪些是用户自己加的，
              宁可不删（静默丢掉别人写的规矩是不可接受的）

体检看什么（`--check`）
--------------------------------------------------------------------------------
  ① 文件在不在、哨兵齐不齐、哨兵区内有没有残留占位符（渲染自己坏没坏）—— **只有这一项判红**
  ② 哨兵区**外**的残留占位符：只提醒（那是刻意保留的旧内容，不能自动删，见 leftover_placeholder）
  ③ 哨兵区里**烘的引擎仓路径还是不是当前这个仓**：只提醒（见 stale_note）。
     挪过仓 / 本机有两个克隆时，守则会让 AI 去一个不是这里的路径敲命令 —— 此前这一路
     完全不设防（`--check` 一路 ✅），而它正是"看起来装好了其实没有"的同形失效。

用法
--------------------------------------------------------------------------------
    python sandbox/global_agents.py <HARNESS 根路径>   # 渲染并写入；**stdout 只打印动作词**
    python sandbox/global_agents.py --check            # 只体检，不写文件（人看的报告）
                                                       #   比对基准=本文件所在的引擎仓
                                                       #   想比别的克隆：--check <那个根>

退出码：0=成功 / 体检通过    1=渲染失败（**原文件未被改动**）/ 体检不通过    2=参数错

stdout 为什么只放动作词：`bootstrap_agent_env.sh` 用 `$( ... )` 捕获它再 `case` 分派人类
文案（那段文案留在 shell 里，与各步骤的措辞保持一致）。stdout 多一句话，捕获值就整体不匹配，
`case` 直接落进「未知状态」分支 —— 而屏幕上看起来只是"文案变了"，没人会想到是管道契约破裂。
所以：**结论走 stdout（机读），解释走 stderr（人读）**，与本仓 auto-gate 同一条约定。
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import time

# UTF-8 兜底（宿主管道可能是 GBK/ASCII 控制台，打印中文会抛 UnicodeEncodeError）。
# 锁：tests/test_entry_level.py::TestAllEntriesHaveUtf8Fallback（清单由 sandbox/list_entries.py
# 机器推导 —— 本文件在 sandbox/*.py 的候选范围内，会打印且含非 ASCII，故必须有这段）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PLACEHOLDER = "__HARNESS__"
BEGIN = "<!-- HARNESS:BEGIN 本区由 harness 渲染，请勿手改（手改下次运行会被覆盖） -->"
END = "<!-- HARNESS:END -->"

# 本文件所在的引擎仓根（`<HARNESS>/sandbox/global_agents.py` → `<HARNESS>`）。只给 `--check`
# 当比对基准用：体检时"当前引擎仓"是谁，从本文件落在哪推出来最省事（与 install_shim 同一算法）。
ENGINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 正文（全仓唯一一份）。普通 Python 字符串：里面的 `$`、反引号、`>` 全是字面量。
# 本机真实路径写成 PLACEHOLDER，渲染时烘进去 —— 本文件住在 ~/.agentguard/，本来就是每机一份。
# ⚠ 改这里的措辞前先想一句话：**别的机器上的人怎么收到这次改动？** 答不上来就别写正文，
#   改成指路（见 golden-patterns/index.md 的仓库结构约定）。
# ---------------------------------------------------------------------------
BODY = """# 全局 AI Agent 统一守则（所有工具共用一份）

> 本文件是用户级全局引导的唯一权威源：各 AI 工具的全局薄壳
> （`~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md`、`~/.config/opencode/AGENTS.md`）都指向本文件
> —— **改哨兵区即全部生效**（这正是它跟"各自抄一份"的关键差别）。
> **本文件只负责「指路」，不装规则正文** —— 正文在 harness 工程里：机械规则在 `checks/`，
> 范式与端级模板在 `golden-patterns/`，入口见
> `__HARNESS__/golden-patterns/index.md`。
> ⚠ 下面哨兵区由 harness 每次运行重渲染，手改会被覆盖；**哨兵区之外是你自己的，harness 永不触碰**。

## AgentGuard 受管仓库守则（最高优先级）
在**已接入门禁的任何受管仓库**改码前（受管仓清单：python __HARNESS__/harness/run_gate.py --list）：
1. 先读该仓库根的 AGENTS.md（仓库级指针：它指向端级模板与范式）
2. 改完必须跑统一门禁（仓库根执行）：
   python3 __HARNESS__/harness/agents/auto-gate.py
   ↑ 解释器名按本机写：Ubuntu / WSL 上通常只有 python3，Windows 上多为 python。
     也可以先 `export HARNESS_PYTHON=python3`（pre-commit 钩子与 sandbox 各脚本都认这个变量）。
   退出码：0=通过 / 1=拦截修复 / 2=配置错 / 3=非受管仓库
3. 被拦截按门禁输出修复，禁止 --no-verify（紧急除外，须说明）
4. pre-commit 钩子会再拦一遍
5. 多仓 worktree 并行时：只动自己任务的文件

## 受管仓 AGENTS.md 里的 `<VAULT>`（多数人用不上）
受管仓根 `AGENTS.md` 的 `## 项目知识库` 一节写的是 `<VAULT>/concepts/projects/<项目>/`。
`<VAULT>` = **团队知识库根目录，只有负责人本机上有**。
**你这台机器上没有这个目录，就忽略这一节** —— 它不参与门禁、不影响你改代码。
⚠ 不要为了"把它填上"去改受管仓的 `AGENTS.md`：那文件随仓分发给所有人，
里面只该出现占位符，不该出现任何人的本机路径。

## 通用编码准则（Karpathy 四准则）
1. Think Before Coding：先梳理约束/边界；有歧义先问，不擅自假设业务逻辑
2. Simplicity First：YAGNI，不过度设计
3. Surgical Changes：只改与任务强相关的行，不顺手改无关代码
4. Goal-Driven：拆解为可自测的验收标准，分阶段迭代
"""

# migrated 分支插在旧内容之前的那段说明。挪出正文之外（它不属于守则正文，
# 只是一次性的搬迁提示），但仍是渲染器的一部分，所以与正文同住一个文件。
_MIGRATE_NOTE = (
    "<!-- ↓↓↓ 以下是你这台机器上这份文件此前的全部内容，harness 原样保留\n"
    "     （无法判断哪些是你自己加的，故不删）。逐段确认后，不需要的整段删掉即可 ——\n"
    "     哨兵区内由 harness 维护，区外永不被覆盖。 -->\n"
)


def target():
    """默认目标：`~/.guardrail-engine/GLOBAL_AGENTS.md`（已有旧 `~/.agentguard/` 时留在旧目录）。"""
    home = os.path.expanduser("~")
    new = os.path.join(home, ".guardrail-engine")
    old = os.path.join(home, ".agentguard")
    base = new if (os.path.isdir(new) or not os.path.isdir(old)) else old
    return os.path.join(base, "GLOBAL_AGENTS.md")


def looks_like_msys(root):
    """`/d/dev/harness` 这种 git-bash 形态的路径 —— 烘进正文里 Windows 侧读不到。

    为什么专门查这个（2026-09-19）：本文档让同事手敲 `global_agents.py <HARNESS>`，而他在
    git-bash 里的直觉写法就是 `/d/...`。**它作为 argv 交给 python.exe 是能开的**（MSYS 会转换，
    实测过）—— 于是命令**成功**、文件**写成了**、屏幕上**全是 ✓**，只有烘进去的那个路径是坏的，
    而且坏在别人机器上（本机 git-bash 里打开看着还挺对）。这与本仓反复在治的失效模式同形：
    看起来成功了，其实没有。所以只提醒、不拦（Linux 上 `/a/b` 这种单字母顶层目录合法存在）。
    """
    import re
    return bool(re.match(r"^/[A-Za-z]/", root or ""))


def render(root):
    """正文 + 本机路径。正斜杠、去尾斜杠 —— 写进去的是**喂给 Windows python 的形态**。"""
    return BODY.replace(PLACEHOLDER, root.replace("\\", "/").rstrip("/"))


# 反推时用的锚点窗口：取 BODY 里每处占位符左右各这么多个字符当"夹子"。
# 够长就不会跟正文别处撞车（三处占位符彼此相距上百字符），够短则正文局部改措辞也不失效。
_ANCHOR = 24


def baked_roots(block):
    """**哨兵区正文**里烘进去的引擎仓根路径（去重后的列表）—— `render()` 的逆运算。

    为什么不直接"拿当前 root 比整块正文"：那会把两件事混成一件 —— **路径变了**（该重跑）
    与**正文变了**（引擎仓更新了，正常）。所以这里逐处占位符反向夹出当初填进去的那个值。

    夹不出来（正文改过、或哨兵区被人手改坏）→ 那一处跳过、不猜：本函数只"看见什么报什么"，
    推不出来的情况由调用方保持沉默。**宁可漏报，不可误报** —— 体检命令一旦开始误报，
    人就不看它了，那比不报更糟。
    """
    out = []
    parts = BODY.split(PLACEHOLDER)
    if len(parts) < 2:
        return out
    cursor = 0
    for i in range(len(parts) - 1):
        pre = parts[i][-_ANCHOR:]          # 用 parts 切片而非 BODY 偏移：锚点里绝不会含占位符
        post = parts[i + 1][:_ANCHOR]
        at = block.find(pre, cursor)
        if at < 0:
            continue
        start = at + len(pre)
        end = block.find(post, start)
        if end < 0:
            continue
        cursor = end
        got = block[start:end]
        # 夹出来的必须像个路径：带换行/占位符就说明锚点撞车了，宁可丢这一处
        if got and "\n" not in got and PLACEHOLDER not in got and got not in out:
            out.append(got)
    return out


def _same_path(a, b):
    """同一条路径的两种写法算相等 —— 口径与 install_shim 里"指针 vs GEN_PATH"完全一致。

    必须归一化：`render()` 写出去的是正斜杠形态，而 install_shim 传进来的 `ROOT` 在 Windows
    上是**反斜杠**形态 —— 直接比字符串会对每一台 Windows 机器误报。

    ⚠ 2026-09-19 CI 实测：**分隔符要自己换，不能指望 `normpath`**。本函数原先只靠
    `os.path.normpath`，而它是**按平台**选的实现 —— Windows 上 `ntpath.normpath("D:/x")`
    会折成 `D:\\x`，所以本机绿；Linux 上 `posixpath.normpath("D:\\x")` 把反斜杠当**普通字符**
    原样留着，于是 `D:\\x` ≠ `D:/x` → 对每一台 Windows 机器误报（`TestStaleRootIsReported`
    那条锁在 CI 上必红）。先统一成正斜杠，两个平台才走同一条路。
    （`normcase` 保持平台语义不动：Linux 上大小写**是**有区别的，不该在这儿抹平。）
    """
    a = (a or "").replace("\\", "/")
    b = (b or "").replace("\\", "/")
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _read(path):
    """newline="" 读原样，再统一成 \\n —— 免得 CRLF 混进来把哨兵行比不中（本仓踩过）。"""
    with io.open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read().replace("\r\n", "\n")


def splice(old, block, date_tag):
    """纯函数：老内容 + 本次渲染的 block → `(新内容, 动作, 是否需要备份)`。

    抽成纯函数是为了三个分支能单测对拍（写文件的副作用在 write() 里）。
    `old=None` 表示目标不存在。
    """
    if old is None:
        return BEGIN + "\n" + block + END + "\n", "created", False
    if BEGIN in old and END in old:
        # 哨兵在 → 只换中间那一区，前后一字不动
        head = old.split(BEGIN)[0]
        tail = old.split(END, 1)[1]
        return head + BEGIN + "\n" + block + END + tail, "rerendered", False
    return (BEGIN + "\n" + block + END + "\n\n" + _MIGRATE_NOTE + old), "migrated", True


def write(root, path=None, date_tag=None):
    """渲染并写入；返回 `(动作, 备份路径或 None)`。

    失败**不做半截事**：备份先于写入，写入是单次整文件覆盖（`shutil.copy2` 失败会先抛，
    此时目标文件还没被碰）。调用方负责把异常报成人话。
    """
    path = path or target()
    date_tag = date_tag or time.strftime("%Y%m%d")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    old = _read(path) if os.path.isfile(path) else None
    out, action, need_backup = splice(old, render(root), date_tag)
    backup = None
    if need_backup:
        backup = "%s.pre-sentinel-%s" % (path, date_tag)
        shutil.copy2(path, backup)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)
    return action, backup


def split(txt):
    """`(哨兵前, 哨兵区, 哨兵后)`；没有哨兵时返回 None（调用方据此判"不是本渲染器写的"）。"""
    if BEGIN not in txt or END not in txt:
        return None
    return (txt.split(BEGIN)[0], txt.split(BEGIN)[1].split(END)[0], txt.split(END, 1)[1])


def leftover_placeholder(path=None):
    """哨兵区**之外**还有未渲染占位符 → 返回提示句；没有则 None。

    区外的 `__HARNESS__` 只可能来自 migrated 分支保留的旧内容：**不能自动删**（那里面
    可能混着用户自己写的规矩），只能提醒。这句话两个写入方（bootstrap / install_shim）
    都要出现，所以归模块 —— 否则一边提醒、一边不提醒，又是一处"看运气"。
    """
    path = path or target()
    if not os.path.isfile(path):
        return None
    txt = _read(path)
    parts = split(txt)
    outside = (parts[0] + parts[2]) if parts else txt
    if PLACEHOLDER not in outside:
        return None
    return ("⚠ %s 的哨兵区之外仍有未渲染的 %s 占位符（多半在保留的旧内容段里）——"
            "确认后把那一整段删掉；删掉后重跑，哨兵区会自动补成最新。" % (path, PLACEHOLDER))


def stale_note(path=None, root=None):
    """哨兵区里烘的引擎仓路径 ≠ `root`（当前引擎仓）→ 返回提醒句；否则 None。

    补的是这么一个洞（2026-09-19）：守则正文里的三处绝对路径是**渲染那一刻**烘进去的，
    之后没人再看一眼。于是「clone 到 A → 装 shim → 后来把仓挪到 B」这条路上，
    `--check` 一路 ✅ —— 而守则正让 AI 去 A 敲命令，那个路径已经没了。
    挪仓不重跑的人通常在 shim 那关先撞上（`--check` 有 GEN_PATH 目标不存在的判定），
    但守则这一路此前**完全不设防**。

    **只提醒、不判红**（与 leftover_placeholder 同一条线）：两个克隆并存时守则指着另一个仓
    未必是错，"是谁的错"也不该由体检裁决 —— 重跑一次就对齐，所以只出声、不拦。

    `root` 没给（不知道当前仓是谁）→ None。宁可沉默也不猜。
    """
    if not root:
        return None
    path = path or target()
    if not os.path.isfile(path):
        return None
    parts = split(_read(path))
    if parts is None:
        return None
    wrong = [b for b in baked_roots(parts[1]) if not _same_path(b, root)]
    if not wrong:
        return None
    return ("⚠ 哨兵区里烘的引擎仓路径是 %s，当前引擎仓在 %s —— 挪过仓 / 换过克隆？"
            "跑一次 `python <HARNESS>/sandbox/install_shim.py` 即对齐"
            "（哨兵区整段重渲染，哨兵区之外不受影响）。"
            % ("、".join(wrong), root.replace("\\", "/").rstrip("/")))


def inspect(path=None, root=None):
    """体检 → `(ok, 人类可读的行列表, 一句话摘要)`。

    ⚠ **哨兵区之外**的 `__HARNESS__` 不算错（见 leftover_placeholder）：那是刻意保留的
    旧内容，只提醒、不判红。判红的只有哨兵区**内**的残留（那说明渲染自己坏了）。

    `root` = 当前引擎仓（给了才做"烘进去的路径还对不对"这一项，见 stale_note；同样只提醒、
    不判红）。没给就跳过那一项 —— 这也是 `root` 必须是**可选**参数的原因：`bootstrap` 那条
    路只想知道"文件写没写对"。
    """
    path = path or target()
    if not os.path.isfile(path):
        return (False,
                ["✗ 未生成：%s（跑 `python sandbox/global_agents.py <HARNESS>` 生成）" % path],
                "未生成")
    txt = _read(path)
    parts = split(txt)
    if parts is None:
        return (False,
                ["✗ 缺哨兵（BEGIN/END）—— 不是本渲染器写的，或被人手改坏了；"
                 "重跑会走 migrated 分支（旧内容原样保留，不丢）"],
                "存在但缺哨兵（不是本渲染器写的）")
    head, block, tail = parts
    lines = ["✅ 已生成：%s" % path,
             "✅ 哨兵齐：区外 %d 字（你的，harness 永不触碰）" % (len(head) + len(tail))]
    if PLACEHOLDER in block:
        lines.append("✗ 哨兵区里有未渲染的 %s（不该发生：哨兵区每次整段重渲染）" % PLACEHOLDER)
        return False, lines, "哨兵区内有残留占位符（渲染坏了？）"
    lines.append("✅ 哨兵区已渲染（%d 字，无残留占位符）" % len(block))
    # 两条提醒都不参与 ok（见各自 docstring）。摘要里只放短标记，具体内容在 lines 里 ——
    # install_shim 的 `--check` 两条都会印，所以"见下"确实有下文。
    warn = leftover_placeholder(path)
    stale = stale_note(path, root)
    if warn:
        lines.append(warn)
    if stale:
        lines.append(stale)
    if warn and stale:
        return True, lines, "已生成（哨兵区外有未渲染占位符 + 路径与本仓不一致，见下）"
    if warn:
        return True, lines, "已生成（哨兵区外另有未渲染占位符，见下）"
    if stale:
        return True, lines, "已生成（哨兵区路径与本仓不一致，见下）"
    return True, lines, "已生成"


def check(path=None, root=None):
    """`--check` 的人读报告：打印并返回 rc 用的布尔值。"""
    ok, lines, _ = inspect(path, root)
    for ln in lines:
        print(ln)
    return ok


def brief(path=None, root=None):
    """一句话状态，给安装器自己的 `--check` 用（那边输出已经够长，不宜再贴三行）。

    摘要由 inspect() 一并给出 —— 不另写一套判据（两套判据必然漂，这正是本模块存在的理由）。
    """
    return inspect(path, root)[2]


def main():
    ap = argparse.ArgumentParser(description="渲染 ~/.guardrail-engine/GLOBAL_AGENTS.md（守则正文的唯一来源）")
    ap.add_argument("root", nargs="?",
                    help="HARNESS 工程根（Windows 形态正斜杠路径；渲染时烘进正文；缺省走统一解析链）")
    ap.add_argument("--check", action="store_true",
                    help="只体检，不写文件（比对本文件所在的引擎仓；也可显式传一个根去比）")
    a = ap.parse_args()

    # 统一解析链：argv > env > config > harness.path > cwd
    root = a.root
    if not root:
        # 尝试从统一解析链获取
        try:
            sys.path.insert(0, os.path.join(ENGINE_ROOT, "guardrail_engine"))
            from _shell import find_root
            root = find_root()
        except Exception:
            root = None

    if a.check:
        # 基准默认取本文件所在的引擎仓（与 install_shim 同一算法）；显式传根则比那个
        # —— 例：`--check D:/别的克隆`，想知道手上这份守则指的是不是它。
        return 0 if check(root=root or ENGINE_ROOT) else 1
    if not root:
        print("✗ 找不到引擎根：设 GUARDRAIL_ENGINE_DIR 环境变量（旧名 AGENTGUARD_DIR / HARNESS_DIR 也可），"
              "或在 config/harness.local.toml 里写 `harness:` 键，"
              "或 `python sandbox/global_agents.py <HARNESS>` 显式传", file=sys.stderr)
        return 2
    if looks_like_msys(root):
        print("⚠ 这个路径看着是 git-bash 形态（%s）—— MSYS 会让你这条命令**跑成功**，"
              "但它是**原样**烘进守则正文的，Windows 上的 AI 工具按它找不到引擎仓。\n"
              "  请换成盘符形态重跑一次（哨兵区每次整段重渲染，直接覆盖，不用先清理）：\n"
              "    sh sandbox/where.sh --win        # 打印本机形态，例如 D:/dev/guardrail-engine"
              % root, file=sys.stderr)
    try:
        action, backup = write(root)
    except OSError as e:
        # 人话走 stderr（stdout 是机读的动作词通道，见模块头）
        print("✗ 渲染失败（**原文件未被改动**）：%r" % (e,), file=sys.stderr)
        return 1
    if backup:
        print("     旧内容原样保留在哨兵区之后；另外备份 → %s" % os.path.basename(backup),
              file=sys.stderr)
    warn = leftover_placeholder()
    if warn:
        print("     " + warn, file=sys.stderr)
    # 动作词单独一行、且是 stdout 上**唯一**的东西（bootstrap 用 $( ) 捕获后 case 分派）
    print(action)
    return 0


if __name__ == "__main__":
    sys.exit(main())
