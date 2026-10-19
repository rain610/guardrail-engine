# -*- coding: utf-8 -*-
"""套件并行跑：测试文件级并行（标准库实现，零第三方依赖）。

动机（整改 C1，2026-09-14）：
    全量套件本机串行数分钟级（评审环境曾 300s 超时）；CI（ubuntu）约 1 分钟。
    本工具按测试文件切分、以子进程并行分发，把本机墙钟压到分钟级以内。

用法：
    python sandbox/run_tests.py --fast          # 日常推荐：HARNESS_FAST=1 + 并行
    python sandbox/run_tests.py                 # 并行跑全量（严格句柄，同 CI 口径）
    python sandbox/run_tests.py -j 4            # 指定并行度（默认 min(6, CPU)）
    python sandbox/run_tests.py --profile       # 串行逐文件计时（找慢文件）
    python sandbox/run_tests.py --tests-dir DIR [-p "test_*.py"]

约定：
    * 每个文件一个子进程：python -W error::ResourceWarning -m unittest discover -s <dir> -p <file> -v
    * 以结果一致为准：汇总 Ran / OK / FAILED / skipped；任一文件失败 → 退出码 1
    * **句柄泄漏同样算失败**（记号 `⚠句柄`）：判据与 CI 逐字一致 ——
      `-W error::ResourceWarning` 的警告落在 GC 阶段，只打印、不改退出码，
      所以必须另判一次，否则本地会报「✅ 全部通过」而 CI 红（2026-09-21 实测栽过）
    * 只准标准库；pytest-xdist 明确排除（违反零依赖承诺，永久排除）

退出码：0=全绿；1=有失败；2=参数错误。
"""
import argparse
import concurrent.futures as cf
import fnmatch
import io
import os
import re
import subprocess
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 句柄泄漏判据 —— 与 CI **逐字一致**（`.github/workflows/ci.yml:87` 的
# `grep -qE "ResourceWarning|Exception ignored in"`）。
#
# 为什么单靠 `-W error::ResourceWarning` 不够：GC 阶段抛出的 ResourceWarning 只**打印**
# 「ResourceWarning: unclosed file」/「Exception ignored in: <_io.FileIO …>」，
# 套件照样报 `OK`、子进程照样退出码 0 —— 于是这个 runner 也照样打「✅ 全部通过」。
# 2026-09-15 CI 为这个原因补了 grep（注释就写在 workflow 里）；**本文件当时没跟着补**，
# 于是本地这一档长期是「看着同 CI 口径、实际不判红」。2026-09-21 因此又栽一次：
# `tests/test_agents_scaffold.py` 漏了 28 个句柄，这里报「✅ 全部通过」、CI 红。
# 判据必须与 CI 逐字一样 —— 两地判据一旦分叉，「本机绿 CI 红」就会再回来。
LEAK_RE = re.compile(r"ResourceWarning|Exception ignored in")


def discover_files(tests_dir, pattern):
    try:
        names = os.listdir(tests_dir)
    except OSError as ex:
        print("[run_tests] ✗ 测试目录不可用：%s（%s）" % (tests_dir, ex))
        sys.exit(2)
    return sorted(n for n in fnmatch.filter(names, pattern) if n.endswith(".py"))


def parse_output(text):
    """解析单个 worker（unittest -v）输出：Ran / skipped / OK|FAILED。"""
    ran = 0
    skipped = 0
    ok = False
    failinfo = ""
    m = re.search(r"Ran (\d+) tests?", text)
    if m:
        ran = int(m.group(1))
    m = re.search(r"skipped=(\d+)", text)
    if m:
        skipped = int(m.group(1))
    for line in text.splitlines():
        s = line.strip()
        if s == "OK" or s.startswith("OK ("):
            ok = True
        m = re.match(r"FAILED \(([^)]*)\)", s)
        if m:
            failinfo = m.group(1)
    return {"ran": ran, "skipped": skipped, "ok": ok, "failinfo": failinfo,
            "leaks": len(LEAK_RE.findall(text))}


def verdict(r):
    """一个文件的判定 —— `rc != 0` 或**有句柄泄漏**都算不通过（同 CI 口径）。

    泄漏单列一个记号：它不改退出码、也不是断言失败，混在 `✗` 里看报告会以为是用例红了。
    """
    if r["rc"] != 0:
        return "✗"
    return "⚠句柄" if r["leaks"] else "✓"


def run_one(tests_dir, fname, env):
    t0 = time.time()
    r = subprocess.run([sys.executable, "-W", "error::ResourceWarning", "-m", "unittest",
                        "discover", "-s", tests_dir, "-p", fname, "-v"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    dt = time.time() - t0
    combined = (r.stdout or "") + (r.stderr or "")
    info = parse_output(combined)
    # 句柄泄漏也要留证据（它不改退出码，只落在 stdout/stderr 里，所以要主动截）
    info.update({"file": fname, "rc": r.returncode, "seconds": dt,
                 "tail": combined[-1500:] if (r.returncode != 0 or info["leaks"]) else ""})
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description="套件并行跑（文件级，标准库）")
    ap.add_argument("-j", "--jobs", type=int, default=0, help="并行度（默认 min(6, CPU)）")
    ap.add_argument("--profile", action="store_true", help="串行逐文件计时")
    ap.add_argument("--fast", action="store_true", help="设 HARNESS_FAST=1")
    ap.add_argument("--full", action="store_true", help="清除 HARNESS_FAST（全量口径）")
    ap.add_argument("--tests-dir", default=os.path.join(ROOT, "tests"), help="测试目录")
    ap.add_argument("-p", "--pattern", default="test_*.py", help="文件匹配（默认 test_*.py）")
    args = ap.parse_args(argv)

    files = discover_files(args.tests_dir, args.pattern)
    if not files:
        print("[run_tests] ✗ 未发现测试文件（%s / %s）" % (args.tests_dir, args.pattern))
        return 2

    env = dict(os.environ)
    mode = "passthrough"
    if args.fast:
        env["HARNESS_FAST"] = "1"
        mode = "fast"
    elif args.full:
        env.pop("HARNESS_FAST", None)
        mode = "full"

    jobs = args.jobs or min(6, os.cpu_count() or 4)
    if args.profile:
        jobs = 1
    jobs = max(1, min(jobs, len(files)))

    print("[run_tests] %d 个文件 ｜ 并行度 %d ｜ 模式 %s ｜ %s"
          % (len(files), jobs, mode, args.tests_dir))
    t0 = time.time()
    results = []
    if jobs == 1:
        for f in files:
            r = run_one(args.tests_dir, f, env)
            results.append(r)
            print("  %s %-38s %5.1fs  (Ran %d%s)" % (
                verdict(r), f, r["seconds"], r["ran"],
                ", skipped %d" % r["skipped"] if r["skipped"] else ""), flush=True)
    else:
        with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(run_one, args.tests_dir, f, env): f for f in files}
            for fut in cf.as_completed(futs):
                r = fut.result()
                results.append(r)
                print("  %s %-38s %5.1fs  (Ran %d%s)" % (
                    verdict(r), r["file"], r["seconds"], r["ran"],
                    ", skipped %d" % r["skipped"] if r["skipped"] else ""), flush=True)

    total_s = time.time() - t0
    ran = sum(r["ran"] for r in results)
    skipped = sum(r["skipped"] for r in results)
    failed = [r for r in results if r["rc"] != 0 or r["leaks"]]
    seq = sum(r["seconds"] for r in results)
    if args.profile:
        print("")
        print("  ---- 最慢在前 ----")
        for r in sorted(results, key=lambda x: x["seconds"], reverse=True):
            print("  %6.1fs  %-38s (Ran %d)" % (r["seconds"], r["file"], r["ran"]))
    print("")
    print("[run_tests] 合计 %d 例（skipped %d）｜ 墙钟 %.1fs ｜ 串行累计 %.1fs ｜ 加速 %.1fx"
          % (ran, skipped, total_s, seq, (seq / total_s) if total_s else 0))
    if failed:
        print("[run_tests] ✗ 失败 %d 个文件：" % len(failed))
        for r in failed:
            why = r["failinfo"] or "rc=%s" % r["rc"]
            if r["leaks"]:
                why += "；句柄泄漏 %d 条（GC 阶段 ResourceWarning，不改退出码 —— CI 会判红）" % r["leaks"]
            print("  ---- %s（%s）----" % (r["file"], why))
            if r["tail"]:
                print(r["tail"])
        return 1
    print("[run_tests] ✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
