#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路径适配：把「离线安装包」里按打包机打戳的 marketplace 配置改到你自己的机器。

背景：包内 marketplace/plugins/guardrail-engine/.mcp.json 在打包时写入了打包机的
python 解释器路径与 guardrail-engine 引擎路径（插件机制要求绝对路径）；换机器
使用前跑一次本脚本即可完成适配（幂等，可重复执行）。

用法：

    python fix_paths.py --harness <你的 guardrail-engine 路径>
    python fix_paths.py --mcp-json <任意 .mcp.json 路径> --harness <你的 guardrail-engine 路径>

默认：--python = 当前解释器；--marketplace = 本脚本所在目录下的 marketplace/（包内布局）。
退出码：0 = 成功；1 = 失败（路径不存在 / 未找到配置等）。
"""
import argparse
import io
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def norm(p):
    return os.path.abspath(p).replace(os.sep, "/")


def find_mcp_json(marketplace):
    cands = [
        os.path.join(marketplace, "plugins", "guardrail-engine", ".mcp.json"),
        os.path.join(marketplace, "plugins", "agentguard", ".mcp.json"),   # 旧包布局
        os.path.join(marketplace, "guardrail-engine", ".mcp.json"),
        os.path.join(marketplace, "agentguard", ".mcp.json"),
        os.path.join(marketplace, ".mcp.json"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _require_python(vi=None):
    """前置检查：本工程需要 **Python 3.11+**（与《安装说明》和包元数据一致）。

    为什么要有：包元数据曾写 >=3.8，而引擎用到 3.9+ 的 API（`pathlib.is_relative_to`），
    3.11 以下从未测试过 —— 版本不够时应当**人话报错并退出**，而不是让用户撞
    AttributeError / 语法错（那种报错没人看得懂，还会被当成"包坏了"）。
    参数 vi 仅供测试注入（默认取 sys.version_info）。
    """
    import sys
    v = vi if vi is not None else sys.version_info
    if tuple(v[:2]) < (3, 11):
        sys.stderr.write(
            "✗ 需要 Python 3.11 或更高版本（当前 %d.%d）。\n"
            "  · 安装：https://www.python.org/downloads/（Windows 勾选 Add python.exe to PATH）\n"
            "  · 装好后重跑本命令；3.8/3.9/3.10 不受支持（引擎用到 3.9+ 的 API，且 3.11 以下从未测试）\n"
            % (v[0], v[1]))
        raise SystemExit(2)


def main(argv=None):
    _require_python()   # 前置检查（Python 3.11+）
    ap = argparse.ArgumentParser(description="把安装包内的 .mcp.json 适配到本机（python / 引擎路径）")
    ap.add_argument("--harness", default=None,
                    help="你的 guardrail-engine 仓库路径（缺省读 GUARDRAIL_ENGINE_DIR / AGENTGUARD_DIR / HARNESS_DIR）")
    ap.add_argument("--python", default=None, help="python 解释器路径（默认：当前解释器）")
    ap.add_argument("--marketplace", default=None,
                    help="marketplace 目录（默认：本脚本所在目录下的 marketplace/）")
    ap.add_argument("--mcp-json", default=None,
                    help="直接指定要改写的 .mcp.json（已安装副本等特殊场景）")
    a = ap.parse_args(argv)

    harness = (a.harness or os.environ.get("GUARDRAIL_ENGINE_DIR")
               or os.environ.get("AGENTGUARD_DIR")
               or os.environ.get("HARNESS_DIR") or "").strip()
    if not harness:
        print("✗ 请用 --harness 指定 guardrail-engine 路径（或设置 GUARDRAIL_ENGINE_DIR）")
        return 1
    harness = norm(harness)
    mcp_rel = None
    for rel in ("guardrail_engine/mcp.py", "agentguard/mcp.py"):
        if os.path.isfile(os.path.join(harness, *rel.split("/"))):
            mcp_rel = rel
            break
    if not mcp_rel:
        print("✗ 在 %s 下没找到 guardrail_engine/mcp.py —— --harness 要指向你 clone 的 Engine 仓库根" % harness)
        # 两种情形要给不同的下一步：
        #   · 目录**根本不存在** → 十有八九是"还没 clone 引擎仓"。这是首次接入最常见的第一个坑
        #     （包内**不含**引擎，是有意的：钩子 / MCP / shim 都指向仓库，引擎更新即生效），
        #     而《安装说明》原先把「已 clone 引擎仓」塞在括号里当"前置条件"；
        #   · 目录在、只是没有 mcp.py → 那是指错了仓 / 路径拼错，别把 clone 那套倒给用户。
        # 两种报同一句话的话，拿到包的人会去反复核对拼写，而问题根本不在拼写上。
        if not os.path.isdir(harness):
            print("  · 这个目录还不存在 —— 安装包内**不含**门禁引擎，需要先 clone 一次：")
            print("      git clone https://github.com/rain610/guardrail-engine.git")
            print("  · 详见包内《安装说明.md》「零、先 clone 引擎仓」")
        return 1
    mcp_py = harness + "/" + mcp_rel

    python = norm(a.python or sys.executable)
    if not os.path.isfile(python):
        print("✗ python 解释器不存在：%s" % python)
        return 1

    if a.mcp_json:
        mj = os.path.abspath(a.mcp_json)
        if not os.path.isfile(mj):
            print("✗ 未找到 .mcp.json：%s" % mj)
            return 1
    else:
        marketplace = os.path.abspath(
            a.marketplace or os.path.join(os.path.dirname(os.path.abspath(__file__)), "marketplace"))
        if not os.path.isdir(marketplace):
            print("✗ marketplace 目录不存在：%s（用 --marketplace 指定）" % marketplace)
            return 1
        mj = find_mcp_json(marketplace)
        if not mj:
            print("✗ 在 %s 里没找到 plugins/guardrail-engine/.mcp.json（用 --mcp-json 直接指定）" % marketplace)
            return 1

    try:
        with io.open(mj, encoding="utf-8") as f:
            data = json.loads(f.read())
        servers = data["mcpServers"]
        key = "guardrail-engine" if "guardrail-engine" in servers else "agentguard"
        srv = servers[key]
    except Exception as ex:
        print("✗ 无法解析 %s：%r" % (mj, ex))
        return 1

    old_cmd = srv.get("command")
    old_arg0 = (srv.get("args") or [None])[0]
    env = srv.setdefault("env", {})
    old_env = env.get("GUARDRAIL_ENGINE_DIR") or env.get("AGENTGUARD_DIR")

    srv["command"] = python
    if srv.get("args"):
        srv["args"][0] = mcp_py
    else:
        srv["args"] = [mcp_py]
    env["GUARDRAIL_ENGINE_DIR"] = harness
    env.pop("AGENTGUARD_DIR", None)      # 旧名 Migration：统一写新名，旧引擎仍认 AGENTGUARD_DIR


    with io.open(mj, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    print("适配文件：%s" % mj)
    print("  command                  : %s  →  %s" % (old_cmd, python))
    print("  args[0]                  : %s  →  %s" % (old_arg0, mcp_py))
    print("  env.GUARDRAIL_ENGINE_DIR : %s  →  %s" % (old_env, harness))
    print("✅ 适配完成。下一步：claude plugin marketplace add <本包目录>/marketplace（OpenClaw 见 OPENCLAW.md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
