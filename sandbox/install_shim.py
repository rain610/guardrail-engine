#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装/刷新本机钩子 shim（把占位符换成当前工程仓路径，写进 ~/.eagle-agents/hooks/）。

为什么需要它：客户端（Claude Code / CodeBuddy / Qoder / ZCode / Trae / Codex）的钩子配置里写的是
**本机路径**，而实现只应该有一份（仓内的 `integrations/hooks/eaglermc-harness-gate.py`）。
本脚本负责把「本机薄转发器」生成到位，并写好指针文件 `~/.eagle-agents/harness.path`。

用法：
    python sandbox/install_shim.py              # 生成/刷新 shim + 指针 + 全局守则
    python sandbox/install_shim.py --check      # 只体检（不写文件）：shim 是否存在、能否解析到目标
    python sandbox/install_shim.py --uninstall  # 撤回：shim 与指针两支改名归档（可逆，不删文件）
                                                #   ⚠ 全局守则**不在撤回清单里**，理由见 uninstall()

背景（2026-09-13 外部评审 G2）：原先 shim 只在本机手工存在、仓内无模板无安装器，
且目标缺失时**完全静默** exit 0 → 换机器/挪目录后 7 家客户端的编辑期门禁会整体无声失效。

--uninstall 的由来（2026-09-17 剖析报告 G5）：本脚本产出的两支文件此前**没有任何撤回入口**
—— 安装说明里没有它们、`uninstall.py` 的五项清理里也没有，于是"整机退出"之后
`~/.eagle-agents/` 下会原样留下 shim 与指针，而指针还指着别人的引擎仓。

顺带渲染全局守则的由来（2026-09-19）：`~/.eagle-agents/GLOBAL_AGENTS.md` 是各 AI 工具
**自动加载**的入口路由（三份端级模板 `AGENTS_{pc,h5,backend}_slim.md` 都指着它），
而此前只有 `sandbox/bootstrap_agent_env.sh` 会生成它 —— 那支脚本**没有任何调用方**
（不在安装包里、CI 不跑、`setup_machine.sh` 也不跑），于是同事照《安装说明》「二、Agent 接入」
做完，这个文件**根本不存在**，三份端级模板一起指向空气。本脚本正是那一步里同事必然跑到的
地方，所以由它顺带渲染。正文与拼装**不写在这里**：见 `sandbox/global_agents.py`
（唯一一份；理由在那个模块头 —— 两份正文会让哨兵互不认账，交替运行把文件越写越大）。
"""
from __future__ import annotations

import pathlib

import argparse
import os
import sys
import time

# UTF-8 兜底（2026-09-13 评审 v3 H1 的系统性修法）：宿主管道可能是 GBK/ASCII 控制台，
# 打印中文/✗ 会抛 UnicodeEncodeError；errors=replace 保证最坏也只是个别字符变问号。
# 锁：tests/test_entry_level.py::TestAllEntriesHaveUtf8Fallback（机器推导清单，别手删）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 同目录的全局守则渲染器（2026-09-19）。**正文只有它那一份** —— 绝不要为了省一次 import
# 在本文件里再抄一份：两份正文会让哨兵互不认账（见 global_agents 模块头）。
# 显式补一次 sys.path：`python <HARNESS>/sandbox/install_shim.py` 时 sys.path[0] 本就是本目录，
# 这一行是给"别的 cwd 下按路径加载本文件"兜底的。
# 用 try 包住：引擎仓被剪裁过（只留了本文件）时 --check / --uninstall 仍要能用 ——
# 那两支文件是**别人机器上的既有安装**，不该因为少一个模块就退不掉。
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import global_agents
except ImportError:
    global_agents = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "integrations", "hooks", "eaglermc-harness-gate-shim.py")
AGENTS = os.path.join(os.path.expanduser("~"), ".eagle-agents")
SHIM = os.path.join(AGENTS, "hooks", "eaglermc-harness-gate.py")
POINTER = os.path.join(AGENTS, "harness.path")
# shim 转发目标在候选目录下的**相对路径** —— 必须与模板里的 `REL` 逐字一致，否则体检的判据
# 与运行时的判据分叉（L13：判据必须与产品同源）。锁：tests/test_install_shim.py::
# TestCheckMatchesRuntimeChain::test_rel_matches_the_shim_template。
REL = os.path.join("integrations", "hooks", "eaglermc-harness-gate.py")
PLACEHOLDER = "__HARNESS__"
ARCHIVE_SUFFIX = ".disabled-"


def archive_name(target):
    """挑一个没被占用的归档名：`<target>.disabled-<戳>`，撞车就加序号 `-2`、`-3`…

    **跨文件同口径**：eaglermc_gate/cli.py 与 sandbox/uninstall.py 各有一份镜像
    （本脚本随包/随仓分发，不能 import 包内模块）。改动任一侧都要同步，
    tests/test_uninstall.py::TestArchiveNameDoesNotDrift 拿同一输入喂三份实现做对拍。

    秒级戳不够用：脚本化的「装 → 卸 → 装 → 卸」会落在同一秒，Windows 上 os.rename
    撞到已存在的目录直接抛 OSError（剖析报告 G8 的根因修法：让撞车根本不发生）。
    """
    base = "%s%s%s" % (target, ARCHIVE_SUFFIX, time.strftime("%Y%m%d-%H%M%S"))
    if not os.path.exists(base):
        return base
    n = 2
    while os.path.exists("%s-%d" % (base, n)):
        n += 1
    return "%s-%d" % (base, n)


def _is_ours():
    """shim 是不是本脚本生成的（特征校验，防误删）：模板渲染出来的必然带 GEN_PATH。"""
    try:
        return "GEN_PATH" in pathlib.Path(SHIM).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def uninstall():
    """撤回本机 shim 与指针：**改名归档，不删任何文件**（可逆）。

    只动本脚本生成的那**两支**文件。⚠ 这"两支"**不含 `GLOBAL_AGENTS.md`**：虽然 2026-09-19
    起安装时也由本脚本渲染它，但撤回时**不碰**。理由不是"它不算本脚本的"，而是
    **它的哨兵区之外属于用户** —— 那里可能写着同事自己加的规矩，归档走就等于把那些一起拿走，
    而"撤回"本该只退掉 harness 装的东西。所以它留在原地，退出后照常可用（哨兵区继续由
    harness 维护，下次装回来即是最新）。
    `~/.eagle-agents/` 下、`hooks/` 下还可能有别的东西：**目录本身一律不碰**，
    也绝不做 rmtree/remove（tests/test_install_shim.py 有 AST 锁盯着）。
    """
    labels = ((SHIM, "shim"), (POINTER, "指针"))
    if os.path.isfile(SHIM) and not _is_ours():
        print("[uninstall] ✗ 拒绝：%s 里找不到 GEN_PATH —— 不是本脚本生成的，请人工确认后再处理" % SHIM)
        print("[uninstall]   两支文件都**未动**（要么一起退，要么一起留）")
        return 1
    did = 0
    for path, label in labels:
        if not os.path.isfile(path):
            print("[uninstall] · %s 未装（%s）：无需撤回" % (label, path))
            continue
        dst = archive_name(path)
        try:
            os.rename(path, dst)
        except OSError as e:
            print("[uninstall] ✗ %s 归档失败（**未移动**，仍在原处）：%r" % (label, e))
            return 1
        print("[uninstall] ✅ %s 已归档（可逆）：%s → %s" % (label, path, dst))
        did += 1
    if did:
        # "退出"不是"停用"：客户端 hook 配置里写的就是这个路径，shim 走了它们就指向空气。
        print("[uninstall] ⚠️ 各客户端的 hook 配置里仍写着这个路径 —— 请到宿主配置里删掉该 hook")
        print("            （见《安装说明》四 / RUNBOOK「4. 卸载与回滚」）；只删文件不改配置，"
              "编辑期门禁会**静默失效**（与装的时候同一个失效模式）。")
        print("[uninstall]    留空目录 %s（本脚本不删目录，也不动里面别人的东西）。"
              % os.path.dirname(SHIM))
    return 0


def render(root):
    src = pathlib.Path(TEMPLATE).read_text(encoding="utf-8")
    return src.replace(PLACEHOLDER, root.replace("\\", "/").rstrip("/"))


def check():
    """自检本机 shim / 指针 / GEN_PATH 这条**运行时解析链**还能不能落到实现上。

    判据 = **整链是否至少有一跳可达**（评审 2026-09-23 L13）。原先的判据是「GEN_PATH 可达
    **且** 指针可达」—— 两跳必须同时好 —— 与运行时语义不符：模板的 `resolve()` 是
    「EAGLERMC_HARNESS_DIR（旧名 HARNESS_DIR）→ 指针 → GEN_PATH 逐个试，**取第一个 `REL` 存在的**」。于是
    「指针有效、GEN_PATH 因挪仓/换克隆而失效」这种**运行时完全正常**的状态被判 ✗ ——
    偏严的假红。而假红与假绿同害：它会把人训练成"这条自检的输出不用看"，那时真红也没人信了。
    现在：任一跳可达 = ✅（并点名**实际会被用到的那一跳**）；"陈旧跳"与"指针 ≠ GEN_PATH
    （双仓并存）"降成 ⚠ 照报 —— 值得知道，但不该让自检变红。整链全不达才是 ✗（那才是真的
    走了 shim 里那条"找不到门禁实现"的分支）。
    """
    if not os.path.isfile(SHIM):
        print("[check] ✗ 未安装 shim：%s" % SHIM)
        return False
    src = pathlib.Path(SHIM).read_text(encoding="utf-8", errors="replace")
    import re
    m = re.search(r"GEN_PATH\s*=\s*r?[\"']([^\"']+)[\"']", src)
    if not m:
        print("[check] ✗ shim 里找不到 GEN_PATH（是不是被手改过？重跑本脚本刷新）")
        return False
    gen = m.group(1)
    ptr_val = None
    if os.path.isfile(POINTER):
        ptr_val = pathlib.Path(POINTER).read_text(encoding="utf-8").strip()
    env = (os.environ.get("EAGLERMC_HARNESS_DIR") or os.environ.get("HARNESS_DIR") or "").strip()

    # 与模板 resolve() **同一判据**：候选逐个试，取第一个 `REL` 存在的。别在这里另写一套
    # "看着差不多"的规则（例：只判目录存在）—— 两套判据必然分叉成"自检说好、运行时说坏"。
    def _reaches(root):
        return bool(root) and os.path.isfile(os.path.join(root, REL))

    links = (("环境变量", env), ("指针", ptr_val), ("GEN_PATH", gen))
    for label, root in links:
        if label == "指针" and not os.path.isfile(POINTER):
            print("[check] ✗ 缺指针文件 %s" % POINTER)
            continue
        if not root:
            print("[check] · %s：%s" % (label, "(未设)" if label != "指针" else "(空)"))
            continue
        print("[check] %s %s：%s" % ("✅" if _reaches(root) else "✗", label, root))
    live = [(label, root) for label, root in links if _reaches(root)]
    if not live:
        # 这是唯一该判红的形态：shim 的 resolve() 会返回 None，编辑期门禁在**这一跳都不达**时
        # 被跳过（模板里那条出口：不阻塞、但出声 + 落 shim-missing.log）。
        print("[check] ✗ 解析链**整条失效**：环境变量 / 指针 / GEN_PATH 都到不了 %s ——"
              "该机器上的编辑期门禁会被跳过（不阻塞，但 shim 会打一次提示并落 shim-missing.log）。"
              "修法：重跑本脚本，或设 EAGLERMC_HARNESS_DIR（旧名 HARNESS_DIR 兼容）。" % REL)
        return False
    print("[check] ✅ 运行时实际会用到的：%s → %s"
          % (live[0][0], os.path.join(live[0][1], REL)))
    for label, root in links:
        if root and not _reaches(root):
            print("[check] ⚠️ %s 这一跳已失效（%s）——当前不影响运行（前面那跳先命中），"
                  "但它是个陈迹：重跑本脚本可对齐。" % (label, root))
    # H4（评审 v3）："指针 ≠ GEN_PATH"（双仓并存）时告警 —— 运行链以指针优先，
    # 用户可能"更新了仓 B、实际跑的是旧仓 A"。★ 降成 ⚠ 而不是红：两个克隆并存时
    # 指着另一个仓未必是错，是谁的错不该由体检裁决（同"挪仓只提醒"那条口径）。
    if ptr_val and os.path.normcase(os.path.normpath(ptr_val)) != os.path.normcase(os.path.normpath(gen)):
        print("[check] ⚠️ 指针与 GEN_PATH **不一致**：运行链以指针优先（%s），"
              "你以为的仓（%s）不会被用到；重跑本脚本可对齐。" % (ptr_val, gen))
    print("[check] 运行时解析链：环境变量=%s → 指针=%s → GEN_PATH=%s"
          % (env or "(未设)", ptr_val or "(无)", gen))
    # 全局守则（2026-09-19 起本脚本顺带渲染它）：这里**只报状态、不参与判红**。
    # 它不属"shim 装没装好"的判据 —— 有人可能刻意不要全局守则（只给自己仓配钩子），
    # 那是他的选择，不该判红。"装的时候没写成功"由 install() 自己返回 False，不会静默。
    # 摘要来自 global_agents.inspect() —— 不在这里另写一套判据（两套判据必然漂）。
    if global_agents is None:
        print("[check] · 全局守则：跳过（引擎仓里找不到 sandbox/global_agents.py）")
    else:
        # 传 ROOT：让守则那一路也能比出"哨兵区烘的路径还是不是这个仓"（挪过仓 / 换过克隆）。
        # 两条提醒都印出来 —— 摘要里写着"见下"，下文必须真的有东西（此前它们是空的）。
        print("[check] · 全局守则：%s" % global_agents.brief(root=ROOT))
        for note in (global_agents.leftover_placeholder(), global_agents.stale_note(root=ROOT)):
            if note:
                print("[check]   " + note)
    # 走到这里 = 解析链至少一跳可达（上面 `not live` 那支已经 return False 了）。
    # ★ 原先这里 `return ok`，而 `ok` 只在"指针或 GEN_PATH 单跳失效"时被置 False —— 正是 L13
    #   要修掉的那条偏严判据。现在返回常量：**判据只有一个**（整链是否可达），没有第二个
    #   半隐形的状态位；留一个恒真的 `ok` 变量只会让下一个读代码的人以为还有别的判据。
    return True


def install_global_agents():
    """顺带渲染 `~/.eagle-agents/GLOBAL_AGENTS.md`（2026-09-19）；成功返回 True。

    为什么归这一步、为什么正文不在这里，见模块 docstring「顺带渲染全局守则的由来」。

    ⚠ **撤回时不退它**（见 uninstall()）：哨兵区之外属于用户，退掉就等于把那些一起拿走。

    返回值参与 install() 的成败：文档承诺这一步产出"shim + 指针 + 全局守则"三样，
    少了一样却打绿灯，就是本仓反复在治的那种"看起来装好了其实没有"。
    """
    if global_agents is None:
        print("✗ 找不到同目录的 global_agents.py —— 全局守则的正文与拼装都在它里面，"
              "本脚本不再自带一份（引擎仓不完整？重新 clone 或 git pull 后再跑）")
        return False
    try:
        action, backup = global_agents.write(ROOT)
    except OSError as e:
        print("✗ 全局守则渲染失败（**原文件未被改动**）：%r" % (e,))
        return False
    print("✅ %s 全局守则：%s"
          % ({"created": "已创建", "rerendered": "已重渲染哨兵区",
              "migrated": "已迁移为哨兵分区"}.get(action, "已更新"), global_agents.target()))
    if backup:
        print("    旧内容原样保留在哨兵区之后；另外备份 → %s" % os.path.basename(backup))
    warn = global_agents.leftover_placeholder()
    if warn:
        print("    " + warn)
    return True


def _update_harness_local(root):
    """更新 config/harness.local.yaml 的 `harness:` 键（统一解析链第 ② 环）。

    幂等：键已存在且值对就不改；值不对或没键就补/修。
    本函数只写文件，不判红（写失败由调用方决定怎么处理）。
    """
    # 找引擎仓根（从本文件位置向上找，直到找到有 config/ 目录的）
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        cfg_dir = os.path.join(d, "config")
        if os.path.isdir(cfg_dir):
            cfg = os.path.join(cfg_dir, "harness.local.yaml")
            break
        d = os.path.dirname(d)
    else:
        return  # 找不到 config 目录，跳过

    root_norm = root.replace("\\", "/").rstrip("/")

    if not os.path.isfile(cfg):
        return  # 配置文件不存在，跳过（setup_machine.sh 会创建）

    try:
        with open(cfg, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return

    # 找 harness: 键
    harness_line_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("harness:") and not line.strip().startswith("#"):
            harness_line_idx = i
            break

    if harness_line_idx is not None:
        # 键已存在：检查值
        old_val = lines[harness_line_idx].split(":", 1)[1].strip().strip("'\"")
        if old_val == root_norm:
            return  # 值已对，不用改
        # 值不对：更新
        lines[harness_line_idx] = "harness: %s\n" % root_norm
        action = "已更新"
    else:
        # 键不存在：在 repos: 之前插入
        repos_idx = None
        for i, line in enumerate(lines):
            if line.strip().startswith("repos:"):
                repos_idx = i
                break
        if repos_idx is not None:
            lines.insert(repos_idx, "harness: %s\n" % root_norm)
        else:
            # 连 repos: 都没有：追加到末尾
            lines.append("\nharness: %s\n" % root_norm)
        action = "已添加"

    try:
        with open(cfg, "w", encoding="utf-8", newline="") as f:
            f.writelines(lines)
        print("✅ %s harness.local.yaml 的 harness: 键 → %s" % (action, root_norm))
    except OSError as e:
        print("  harness.local.yaml 写入失败：%r" % (e,))


def install():
    body = render(ROOT)
    os.makedirs(os.path.dirname(SHIM), exist_ok=True)
    with open(SHIM, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    with open(POINTER, "w", encoding="utf-8", newline="") as fh:
        fh.write(ROOT.replace("\\", "/") + "\n")
    print("✅ 已生成 shim：%s" % SHIM)
    print("✅ 已写指针：%s → %s" % (POINTER, ROOT))
    _update_harness_local(ROOT)  # 统一解析链第 ② 环
    if not install_global_agents():
        return False
    return check()


def main():
    ap = argparse.ArgumentParser(description="安装/刷新本机 harness 钩子 shim")
    ap.add_argument("--check", action="store_true", help="只体检，不写文件")
    ap.add_argument("--uninstall", action="store_true",
                    help="撤回 shim 与指针（改名归档，不删文件）")
    a = ap.parse_args()
    if a.uninstall:
        return uninstall()
    ok = check() if a.check else install()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
