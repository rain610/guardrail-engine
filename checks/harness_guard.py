# -*- coding: utf-8 -*-
"""
harness/checks/harness_guard.py —— 保护循环（防篡改哨兵）
============================================================
「保护 hook」核心：修代码的 agent 不能弱化验证它的检查。

职责（2026-09-03 Plan A 改造）：
  检查 harness 工程仓库自身是否存在「未提交」的受保护文件改动
  （checks/*.py / components_allowlist.yaml / config/harness.yaml）。
  有 → 拦截当前提交。

  旧版端仓库模式检查「端仓库 diff 里是否出现 checks/... 路径」——但受保护文件
  在独立工程仓库里，端仓库 diff 永远不会出现它们，该检查形同虚设，已移除。
  真实攻击路径是：改弱门禁脚本 → 不提交 → 回端仓库提交业务代码 → 弱化版门禁放行。
  本改造直接堵住它：门禁文件有未提交改动时，任何端仓库 commit 都先被拦。

放行场景：
  1. --self 模式 + 提交消息含维护标记（[harness] / harness-maintenance / HARNESS:）
  2. harness 工程仓库没有未提交的受保护文件改动（普通业务工单）

用法：
  python harness/checks/harness_guard.py <gate名>                      # 端仓库 pre-commit 模式（默认）
  python harness/checks/harness_guard.py <gate名> --repo <被提交仓>     # 同上，但"谁在提交"由调用方指定
  python harness/checks/harness_guard.py --self [提交消息]              # 工程仓自身模式
退出码：0=放行，1=拦截（工程仓存在未提交的受保护改动），
        2=**读不出**工程仓的改动（git 坏/工程仓非工作区）→ fail-closed 阻断，见 _not_a_worktree_hint

GBK 兼容（2026-09-07 实测踩坑）：GitHub Desktop 等 GUI 触发 hook 时控制台是 GBK 编码，
print ❌/✅ 等 Unicode 符号会 UnicodeEncodeError 崩溃 → 门禁异常退出误拦所有提交。
修复：①输出统一用 ASCII 前缀 [X]/[OK]；②文件头强制 stdout/stderr 为 UTF-8 + errors=replace 兜底。
"""
import sys, os, io

# stdout/stderr 强制 UTF-8（errors=replace 兜底：即使编码器仍不认识某字符也不会崩）
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
elif hasattr(sys.stdout, "buffer"):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))   # checks/
ROOT = os.path.dirname(HERE)                         # 工程根 agentguard/
LIB = os.path.join(ROOT, "harness")                  # _config.py 所在目录

# 显式维护标记（提交消息含任一即放行；仅 --self 模式生效）
MAINT_TAGS = ["[harness]", "harness-maintenance", "HARNESS:"]


def _in_protected(rel):
    """相对工程根的路径是否属于受保护文件。checks/ 下任何 .py 都受保护（新增检查脚本也算）。
    2026-09-12 扩展（外部评审 P0-2）：原清单只覆盖门禁"内容"，未覆盖"调度层"——
    run_gate.py/hooks/agents/_config.py 改弱不提交时 guard 零反应，是防篡改模型唯一漏档。"""
    r = rel.replace("\\", "/")
    if r.startswith("checks/") and r.endswith(".py"):
        return True
    # 调度层（hooks-dir 分发器也保护——它是全部接入仓 pre-commit 的实体）
    if r in ("harness/run_gate.py", "harness/_config.py",
             "harness/hooks-dir/pre-commit",
             # N5 修复（第二轮评审）：commit-msg 同属共享分发器实体，漏保护=可被静默改弱
             "harness/hooks-dir/commit-msg"):
        return True
    if r.startswith("harness/hooks/") and r.endswith(".py"):
        return True
    # 第三轮 N-δ：Claude/Codex 的自动门禁钩子与 harness/** 同等对待——它决定"AI 改完代码
    # 谁来跑门禁"，改弱它等于让门禁在编辑期静默消失（提交期仍有 pre-commit 兜底）。
    if r.startswith("integrations/hooks/") and r.endswith(".py"):
        return True
    if r.startswith("harness/agents/") and r.endswith(".py"):
        return True
    return r in ("checks/components_allowlist.yaml",
                 "checks/components_allowlist_h5.yaml",
                 "config/harness.yaml")


def _commit_msg():
    """提交消息来源（2026-09-11 重构，修 P0 挂死）：
    - 旧版在 stdin 非 tty 时无条件 sys.stdin.read()——GUI/CI 触发 hook 时 stdin 常是
      保持打开的管道 → 永久阻塞，commit 静默卡死（外部评审实测复现）；
      且 git 从不给 pre-commit 传提交消息（那是 commit-msg 的职责），读了也是死代码。
    - 现版：消息一律来自 argv（commit-msg hook 传 $1 文件路径，读文件零风险；
      手动调用可 --self <消息> 直传）。**绝不读 stdin。**"""
    return ""


def _parse_args(argv):
    """解析命令行 → (self_mode, gate_name, commit_msg)。
    抽成纯函数以便单测（P3-5 修复的回归锁；原实现 self 模式把 args[0] 吃成 gate 名，
    单 token 消息丢失 → [harness] 维护提交被白拦）。

    语义：
      --self            工程仓自身维护模式（无 gate 概念，标签 "self"）
      --msg-file <路径> 从文件读提交消息（commit-msg 场景），最优先
      其余位置参数        self 模式 = 全部是消息；非 self = args[0] 是 gate 名，其余是消息
    绝不读 stdin（P0：管道不 EOF 时 sys.stdin.read() 永久阻塞，GUI/CI commit 静默挂死）。
    """
    args = list(argv)
    self_mode = "--self" in args
    args = [a for a in args if a != "--self"]
    commit_msg = ""
    if "--msg-file" in args:
        i = args.index("--msg-file")
        if i + 1 < len(args) and os.path.isfile(args[i + 1]):
            try:
                with open(args[i + 1], encoding="utf-8", errors="replace") as f:
                    commit_msg = f.read()
            except Exception:
                commit_msg = ""
            args = args[:i] + args[i + 2:]
    if self_mode:
        return True, "self", commit_msg or " ".join(args)
    return False, (args[0] if args else "gxgk_pc"), commit_msg or " ".join(args[1:])


def _take_repo_arg(argv):
    """取出 `--repo <路径>`，返回 (剩余 argv, 路径或 None)。

    评审 v13 严重 4：本模块判 self 模式原先**只靠 cwd 推断**，而 cwd 是调用方随手能覆盖的
    环境信号。`harness/hooks/pre-commit.py::_run_guard` 当年就写死 `cwd=ROOT` 调过来 ——
    于是**每一次**端仓提交都被判成"本仓提交"，再撞上"pre-commit 阶段读不到提交消息 → 放行"，
    哨兵在真实提交链路上恒返回 0。它的坏法不是偶尔漏拦，而是**保护从未生效过一次**却一直
    打印着"保护循环：检查…"，看起来完全正常（这正是本项目反复强调的"倾向报平安"）。

    修法：把事实交给调用方。调用方本来就知道自己在提交哪个仓（pre-commit.py 解析出的
    target_repo），用 `--repo` 明说；给了就**以它为准，不再看 cwd**。
    不给（dispatcher / 手工调用 / 旧装法的钩子）时仍沿用 cwd 推断，行为不变。
    """
    args = list(argv)
    repo = None
    if "--repo" in args:
        i = args.index("--repo")
        if i + 1 < len(args):
            repo = args[i + 1]
        del args[i:i + 2]
    return args, repo


def _not_a_worktree_hint():
    """工程仓"根本不是 git 工作区"时，给一句可执行的修复指引（clone 半残 / .git 被误清理）。

    判定用 `git rev-parse --is-inside-work-tree` 的**退出码 + 输出**，不去匹配 git 的报错文本：
    那些文本随 locale 变化（中文 git 输出「不是一个 git 仓库」），按字符串匹配等于给自己埋一个
    只在英文环境下才有效的提示 —— 而这句提示恰恰是出问题时唯一的指路牌。
    返回 "" = 不是这种情形（或探不出来），调用方按通用提示走。
    """
    try:
        import subprocess as _sp
        r = _sp.run(["git", "-C", ROOT, "rev-parse", "--is-inside-work-tree"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=30)
        if r.returncode != 0 or (r.stdout or "").strip() != "true":
            return ("看起来工程仓不是 git 工作区（clone 半残 / .git 被误清理）→ "
                    "重跑 `git clone` 恢复工程仓。")
    except Exception:
        pass
    return ""


def main():
    argv, commit_repo = _take_repo_arg(sys.argv[1:])
    self_mode, gate_name, commit_msg = _parse_args(argv)
    # 2026-09-14（Codex 评审 v8 第 3 批）：harness 仓注册自检 gate 后，本仓提交会经 core.hooksPath
    # 走到这里 —— 于是出现**自指死结**：正在提交的就是这批门禁文件，而 guard 说"门禁脚本未提交"，
    # 本仓提交被自己的哨兵永久挡住。处置：本次提交带维护标记（[harness] 等）＝有意改动门禁文件，
    # 放行并出声（真正的防护是"改门禁必须显式标记 + 人看着"，不是"永远不许改"）。
    # ---- (a) 修复（2026-09-14）：自指死结的根因是 self 模式判据只看 `--self` 参数 ------------
    # 本仓注册自检 gate 后，提交经 core.hooksPath → pre-commit.py → 本哨兵（带 gate 名、无 --self），
    # 于是"正在提交的就是这批门禁文件"被当成"有人要用弱化门禁放行"→ 永久阻断。
    # 判据改成**看事实**：hook 场景 cwd = 被提交仓根，若它就是工程根 → 本仓提交 → self 模式。
    if not self_mode and commit_repo is not None:
        # 调用方显式指定了"正在提交哪个仓" → 它就是权威，**不叠加 cwd 推断**
        # （cwd 可被调用方覆盖 —— 严重 4 的成因正是"覆盖了 cwd 还照样推断"）。
        try:
            if os.path.normcase(os.path.abspath(commit_repo)) == os.path.normcase(os.path.abspath(ROOT)):
                self_mode = True
                print("[guard] 本仓提交（调用方指定 --repo=工程根）→ 按 self 模式把关")
        except Exception:
            pass        # 路径畸形 → 保持非 self：走下方真实检查（宁可多查一次，不可少查）
    elif not self_mode:
        try:
            import subprocess as _sp     # 本模块按惯例只在函数内局部导入（别用模块级名字，会 NameError）
            _top = _sp.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                           text=True, timeout=30).stdout.strip()
            if _top and os.path.normcase(os.path.abspath(_top)) == os.path.normcase(os.path.abspath(ROOT)):
                self_mode = True
                print("[guard] 本仓提交（cwd=工程根）→ 按 self 模式把关")
        except Exception:
            pass

    # ---- (a) 的另一半：pre-commit 阶段 git 还没写 COMMIT_EDITMSG（-F 亦然），消息读不到 --------
    # 此时**延后把关**：本阶段放行，[harness] 标记由 commit-msg 钩子强制（它拿得到消息文件，
    # 缺标记即 rc=1 阻断）。注意这不会开新口子：--no-verify 本来就同时跳过两个钩子。
    if self_mode and not (commit_msg or "").strip():
        print("[harness_guard] 本仓提交：pre-commit 阶段读不到提交消息 → 本阶段放行，"
              "标记把关交给 commit-msg 钩子（缺 [harness] 会让提交失败）。")
        return 0

    # 维护标记放行：**仅限工程仓自身提交**（self 模式）。端仓库提交消息里碰巧带 [harness]
    # 与 harness 维护无关，不放行（防绕过）。
    # 中 12（评审 v13）：这里原先还有一份**极性相反**的同类分支（`if not self_mode and commit_msg
    # and any(MAINT_TAGS): return 0`，在下方注释之前）——即"端仓库提交只要消息里带 [harness]
    # 就放行"，与紧随其后的注释（"不放行，防绕过"）正好相反，等于把一个现成的绕过口子
    # 埋在最显眼的位置（只差调用方给它传消息）。当前 hook 不传消息所以没被触发，但分支是活的。
    # 已删除：放行只在 self 模式成立，下面这一个分支就是全部。
    sys.path.insert(0, LIB)
    from _config import changed_files_ex

    if self_mode and any(t.lower() in commit_msg.lower() for t in MAINT_TAGS):
        print("[harness_guard] 含维护标记，放行工程仓库 harness 文件修改。")
        return 0

    # 两种模式共用同一防护：检查 harness 工程仓未提交的受保护文件改动。
    # 2026-09-16（评审 v13 复查遗留项，用户拍板）：本行原先用宽容包装 `changed_files(ROOT)`。
    # 那个函数自己在 harness/_config.py 里写着「查不了时返回 []，需要 fail-closed 语义的调用方
    # 请用 changed_files_ex()」—— 而这里恰恰是最需要 fail-closed 的调用方：查不了 → 空清单 →
    # 下面 touched 为空 → return 0。**工程仓 git 一坏（.git 被清理 / git 不可用 / 查询超时 /
    # 权限不足），防篡改哨兵静默失效，且输出里一个字都不说**；同一时刻分发器、pre-commit
    # 读暂存区各处却都是 fail-closed（rc=2），只有这一处相反 —— 与本项目反复强调的
    # 「宁可报错、不可假装查过」正好背道而驰。
    # 代价已知并接受（先例：2026-09-15 分发器先跑 harness_self_check，坏了就拦全部 33 个端仓）：
    # 工程仓不健康时端仓提交会被一并拦下。这是**工程仓/环境**问题而不是业务代码问题，
    # 提示里必须说清，免得人去改业务代码。
    files, err = changed_files_ex(ROOT)
    if err:
        print("[harness_guard] [X] 读不出工程仓的未提交改动 → 无法验证门禁文件是否被弱化，"
              "按 fail-closed 阻断本次提交（rc=2）。")
        print("    原因：%s" % err)
        _hint = _not_a_worktree_hint()
        if _hint:
            print("    %s" % _hint)
        print("    这是工程仓/环境问题，**不是业务代码问题**；修好工程仓后重试。")
        print("    确需跳过：git commit --no-verify（跳过=本次提交没有门禁）。")
        return 2
    touched = [os.path.relpath(f, ROOT) for f in files]
    touched = [t for t in touched if _in_protected(t)]
    if touched:
        if self_mode:
            print("[harness_guard] [X] 独立工程仓库改动触碰受保护文件，已拦截：")
        else:
            print(f"[harness_guard] [X] harness 工程仓存在未提交的门禁文件改动（gate={gate_name}），已拦截：")
        for t in touched:
            print(f"    {t}")
        print("    门禁脚本/配置未提交 = 可能已被弱化，用它放行业务提交不安全。")
        print("    请先在 agentguard 仓库处理：")
        print("      提交：git commit -m \"[harness] <说明>\"")
        print("      还原：git checkout -- <文件>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
