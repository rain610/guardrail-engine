# -*- coding: utf-8 -*-
"""门禁现场演示（Python 版，2026-10-25）—— PowerShell / cmd 不需要 Git Bash。

与 `demo_gate.sh` 同一场戏、同一组判据，只是不依赖 `sh`：
  1. 临时目录写一个含 console.log 的 .js → 跑 example gate → 必须 rc=1 且点名 NO-CONSOLE；
  2. 改成合规写法 → 再跑 → 必须 rc=0；
  3. 删除临时目录（不碰任何真实代码）。

用法（任意目录）：
    python <HARNESS>/sandbox/demo_gate.py

退出码：0=演示通过；1=两向断言失败（门禁没按预期拦/放）；2=用法或环境错误。
演示固定走 example gate：唯一开箱即用的规则集；用 --repo 指向临时目录，
因此不依赖 config/harness.local.toml 里配过任何受管仓。
"""
import os
import shutil
import subprocess
import sys
import tempfile

# 中文输出在 GBK/ASCII 宿主管道下会崩或乱码；与其它入口同一兜底（sandbox/list_entries.py 会查）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = "example"
OFFENDING = '// 演示：调试残留，提交前应被门禁拦下\nconsole.log("debug");\n'
CLEAN = "const answer = 42;\n"


def _run_gate(target, repo):
    env = dict(os.environ)
    # 与 .sh 版一致：台账里记 entry=demo，能区分「演示」与真实提交判定。
    env["HARNESS_ENTRY"] = "demo"
    cmd = [sys.executable, os.path.join(ROOT, "harness", "run_gate.py"),
           GATE, "--repo", repo, target]
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def main(argv):
    if len(argv) > 1 and argv[1] != GATE:
        print("✗ 本演示的探针内容只对应 example gate 的规则（console.log）。")
        print("  想看其它 gate 的现场拦截，请在对应业务仓里按 README 的接入流程跑。")
        return 2

    tmp = tempfile.mkdtemp(prefix="guardrail-engine-demo.")
    target = os.path.join(tmp, "demo.js")
    try:
        print("=" * 60)
        print(" guardrail-engine 门禁演示（gate=%s，临时仓=%s）" % (GATE, tmp))
        print("=" * 60)

        print("\n【第 1 步】写一个含 console.log 的临时文件（模拟调试代码忘了删）…")
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(OFFENDING)
        print("已写入: %s" % target)
        print("\n---- 运行门禁（期望 rc=1，点名 NO-CONSOLE）----")
        r = _run_gate(target, tmp)
        out = (r.stdout or "") + (r.stderr or "")
        print(out, end="" if out.endswith("\n") else "\n")
        if r.returncode != 1:
            print("❌ 演示失败：期望 rc=1（拦截），实得 rc=%s" % r.returncode)
            return 1
        if "[NO-CONSOLE]" not in out:
            print("❌ 演示失败：rc=1 但输出里没有 [NO-CONSOLE]（拦的可能不是这条规则）")
            return 1
        print("✅ 拦截成功：这条改动提交不上去。")

        print("\n【第 2 步】删掉调试日志，改成合规写法…")
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(CLEAN)
        print("\n---- 再次运行门禁（期望 rc=0）----")
        r = _run_gate(target, tmp)
        out = (r.stdout or "") + (r.stderr or "")
        print(out, end="" if out.endswith("\n") else "\n")
        if r.returncode != 0:
            print("❌ 演示失败：修复后期望 rc=0（放行），实得 rc=%s" % r.returncode)
            return 1
        print("✅ 放行成功：同样的门禁，改对了就能过。")

        print("\n【第 3 步】清理临时文件…")
        print("已删除 %s" % tmp)
        print("\n" + "=" * 60)
        print(" 演示结束。结论：门禁 = 提交前的硬性检查，AI 记不记得住不重要，")
        print(" 门禁一定记得。")
        print("=" * 60)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
