# -*- coding: utf-8 -*-
"""插件原型端到端验证：读取构建产物 `.mcp.json` 的声明 → 起 MCP 服务 → 调门禁工具 → 断言拦截。

用法：
  python integrations/plugins/verify_plugin.py --plugin <BUILT_PLUGIN_DIR>

验证内容（"能实际跑起来并完成一次端到端门禁调用"）：
  1. 从插件声明的 .mcp.json 读出 MCP server 命令（不读任何额外文档）
  2. 按声明拉起服务，完成 initialize / tools/list 握手（工具数 ≥ 6）
  3. 造一个应拦截的夹具文件，调 harness_run_gate → 必须得到 rc=1 / BLOCKED
  4. 调 harness_check_file 自动识别 gate → 必须得到同一裁决（入口一致性初查）
  5. stdout 协议纯净性检查

本脚本只读/只写构建产物与临时夹具，不做任何删除。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def read_manifest(plugin_dir):
    p = os.path.join(plugin_dir, ".mcp.json")
    if not os.path.isfile(p):
        print("✗ 找不到 %s" % p)
        return None
    with io.open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    servers = cfg.get("mcpServers") or {}
    if "eaglermc-gate" not in servers:
        print("✗ .mcp.json 里没有 eaglermc-gate server：%s" % list(servers))
        return None
    s = servers["eaglermc-gate"]
    cmd = [s["command"]] + list(s.get("args") or [])
    return cmd, dict(s.get("env") or {})


def mk_fixture():
    d = tempfile.mkdtemp(prefix="plugver-")
    repo = os.path.join(d, "repo")
    os.makedirs(os.path.join(repo, "src"))
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"], ["git", "config", "user.name", "t"]):
        subprocess.run(c, cwd=repo, capture_output=True)
    cfg = os.path.join(d, "cfg.yaml")
    io.open(cfg, "w", encoding="utf-8", newline="").write(
        "repos:\n  t: %s\ngates:\n  t:\n    enabled: true\n    repo: t\n"
        "    script: checks/pc_code_check.py\n    run_on: [pre_commit, post_edit]\n" % repo.replace("\\", "/"))
    bad = os.path.join(repo, "src", "bad.vue")
    io.open(bad, "w", encoding="utf-8", newline="").write(
        "<template>\r\n  <div>h</div>\r\n</template>\r\n<script setup>\r\nconsole.log(1);\r\n</script>\r\n")
    log = os.path.join(d, "gate_log.jsonl")
    return d, repo, bad, cfg, log


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", required=True)
    a = ap.parse_args(argv)
    plugin_dir = os.path.abspath(a.plugin)

    got = read_manifest(plugin_dir)
    if not got:
        return 2
    cmd, base_env = got
    print("== 插件端到端验证 ==")
    print("plugin : %s" % plugin_dir)
    print("声明命令: %s" % " ".join(cmd))

    d, repo, bad, cfg, log = mk_fixture()
    env = dict(os.environ)
    env.update(base_env)
    env.update({"HARNESS_CONFIG": cfg, "HARNESS_LOG_FILE": log, "PYTHONUTF8": "1"})

    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env, cwd=plugin_dir)
    mid = [0]

    def req(method, params=None, timeout=90):
        mid[0] += 1
        p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": mid[0], "method": method,
                                   "params": params or {}}) + "\n").encode("utf-8"))
        p.stdin.flush()
        box = {}

        def rd():
            box["l"] = p.stdout.readline()
        t = threading.Thread(target=rd, daemon=True)
        t.start()
        t.join(timeout)
        assert not t.is_alive(), "协议超时"
        return json.loads(box["l"].decode("utf-8"))

    r0 = req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "verify-plugin", "version": "0"}})
    ok_init = r0.get("result", {}).get("serverInfo", {}).get("name") == "eaglermc-gate"
    print("initialize: serverInfo=%s" % r0.get("result", {}).get("serverInfo"))

    p.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
    p.stdin.flush()

    r1 = req("tools/list")
    tools = [t["name"] for t in r1.get("result", {}).get("tools", [])]
    print("tools(%d): %s" % (len(tools), tools))

    r2 = req("tools/call", {"name": "harness_run_gate", "arguments": {"gate": "t", "files": [bad]}})
    t2 = r2["result"]["content"][0]["text"]
    run_ok = ("rc=1" in t2) and ("BLOCKED" in t2)
    print("run_gate(bad): rc=1&BLOCKED -> %s" % run_ok)

    r3 = req("tools/call", {"name": "harness_check_file", "arguments": {"path": bad}})
    t3 = r3["result"]["content"][0]["text"]
    chk_ok = ("gate=t" in t3) and ("rc=1" in t3)
    print("check_file(bad): gate=t&rc=1 -> %s" % chk_ok)

    p.stdin.close()
    rest = p.stdout.read()
    extra = len([l for l in rest.split(b"\n") if l.strip()])
    p.wait(timeout=30)
    print("stdout 纯净性: extra=%d | server rc=%s" % (extra, p.returncode))

    overall = ok_init and len(tools) >= 6 and run_ok and chk_ok and extra == 0
    print("PLUGIN E2E", "PASS" if overall else "FAIL")
    if overall:
        print("证据: 声明命令可直接跑通 MCP 握手，并以 rc=1/BLOCKED 完成一次端到端门禁判定。")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
