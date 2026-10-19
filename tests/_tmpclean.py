# -*- coding: utf-8 -*-
"""测试临时目录的**强删**助手（2026-09-21）。

为什么要单开一个模块：Windows 上 git 把对象文件（`.git/objects/**`）标成只读，
`shutil.rmtree(path, ignore_errors=True)` 撞上 PermissionError 会**静默吞掉**——
于是"清理失败"与"清理成功"在外观上完全一样，代价是每跑一次套件就在 %TEMP% 堆一份
完整副本（有的还带整个 .git）。同一根因在本仓已复发过多次：

    2026-09-21 实测存量：engine-copy-* 1515 份、bootstrap-* 599 份、
                        engine-copy-logs-* 12 份、lockinv_copytree_* 157 份
    （lockinv 那份已先单独修过，见 tests/test_lock_inventory.py 的 finally）

先把只读位摘掉再删。不依赖 `rmtree` 的 `onerror` 回调参数——3.11 / 3.12 行为一致。
"""
import atexit
import os
import shutil
import tempfile


def rmtree_force(path):
    """删掉 path：先摘只读位，失败也不抛。

    清理失败**不该**让用例报红（那不是被锁住的判据），但也不能继续静默堆垃圾——
    所以"摘只读位"这步尽了力，剩下的交给 `ignore_errors`。
    """
    if not path or not os.path.exists(path):
        return
    for root, dirs, files in os.walk(path):
        for nm in dirs + files:
            try:
                os.chmod(os.path.join(root, nm), 0o700)
            except OSError:
                pass            # 摘不掉就交给下面 ignore_errors（不能因清理失败而报红）
    shutil.rmtree(path, ignore_errors=True)


def mkdtemp_cleanup(prefix, **kw):
    """建一个临时目录并登记**进程退出时**强删 —— 给模块级（进程活多久它活多久）目录用。

    模块级 `mkdtemp` 没有 setUp/tearDown 可挂，漏了这一步就是每跑一次留一个：实测
    `harness-v9log-` 219 份 / `harness-v9b2log-` 201 份 / `harness-v9b3log-` 272 份。
    """
    d = tempfile.mkdtemp(prefix=prefix, **kw)
    atexit.register(rmtree_force, d)
    return d
