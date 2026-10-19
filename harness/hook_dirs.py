# -*- coding: utf-8 -*-
"""hook 目录解析：**git 实际会用的那个目录**与安装脚本落档备份的那个目录。

为什么单独成模块（2026-09-23 外部评审 M3）：`sandbox/doctor.sh` 的"接管 hooksPath 后原 hook
有没有被我们吞掉"这一项，原先按 `git rev-parse --absolute-git-dir` 拼 `<gitdir>/hooks` 去扫。
对 **worktree** 这是错的 —— git 的 hook 查找走 **common-dir**，worktree 专属 gitdir 下的 `hooks/`
**git 从来不执行**（本机实测见下）。于是：

  · 扫到了一个 git 永远不看、我们也没接管过的目录；
  · 报出来的是 ❌「hooksPath 已接管但以下原 hook 未备份」，还把修复指引写成"重跑
    install_hooks.sh 会备份" —— 而安装脚本按 COMMON 解析（`sandbox/install_hooks.sh` L112-118），
    它**枚举不到** worktree 专属 gitdir，那句承诺对该文件永远不成立。

★ 判据必须与产品同源：`install_hooks.sh` 的 REAL_HOOKS / BACKUP_BASE 规则（无自定义 hooksPath 时）
    REAL_HOOKS = $COMMON/hooks   BACKUP_BASE = ${COMMON:-$GITDIR}
本模块就实现这一条，doctor 与锁共用，避免两处各算一遍再漂移。

本机实测（2026-09-23，临时仓 + worktree 探针）：
    git -C <主仓> rev-parse --git-path hooks  → <主仓>/.git/hooks
    git -C <worktree> rev-parse --git-path hooks  → <主仓>/.git/hooks      ← 不是 worktree gitdir
    git -C <worktree> rev-parse --git-common-dir  → <主仓>/.git
    在 <worktree-gitdir>/hooks/ 放可执行的 pre-commit 后提交 → **该 hook 没有运行**（rc=0、无输出）
"""
import os


def resolve(gitdir, common, repo=None):
    """返回 (git 实际使用的 hooks 目录, 安装脚本落档备份的目录)。

    - `gitdir` = `git rev-parse --absolute-git-dir`（worktree 时是**专属** gitdir）
    - `common` = `git rev-parse --git-common-dir`（可能是相对路径，此时按 `repo` 绝对化）
    - 两者都取不到 → 调用方自己决定跳过（本函数不抛异常，返回 (None, None) 之外不做判断）
    """
    if common and not os.path.isabs(common) and repo:
        common = os.path.normpath(os.path.join(repo, common))
    base = common or gitdir
    if not base:
        return None, None
    return os.path.join(base, "hooks"), os.path.join(base, "agentguard-original-hooks")
