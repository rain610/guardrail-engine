#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""opencode-go 本地中转代理（纯标准库）—— 给"只会说 OpenAI 协议、且不能自定义请求头"的客户端用。

存在理由（2026-09-13 实测）：
  · opencode-go 网关**必须**带请求头 `x-opencode-session: <任意串>`，否则
    400 {"type":"MissingSessionID"}；不带 User-Agent 会被 Cloudflare 拦 403 code 1010。
  · 有些客户端（如 ZCode CLI 的 openai 内置 kind）只认 `OPENAI_BASE_URL`，无处配置自定义请求头。
  → 本代理收下它们的请求，补上必要请求头与真密钥，再转发给 opencode-go。

密钥：运行时从 Hermes 的 .env 读 `OPENCODE_GO_API_KEY`，**不落文件、不打印**。

用法：
    python opencode-go-proxy.py [--port 8791]
然后客户端侧：
    OPENAI_BASE_URL=http://127.0.0.1:8791/v1
    OPENAI_API_KEY=dummy            # 真密钥在代理里注入
"""
from __future__ import print_function

import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://opencode.ai/zen/go/v1"
KEYNAME = "OPENCODE_GO_API_KEY"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) opencode-go-proxy"

# Hermes `.env` 的候选位置（2026-09-18 批次 5）：原先写死 `C:/Users/<用户名>/AppData/Local/hermes/.env`
# —— 维护者本机的用户名与布局，同事机器上必然找不到，而且报错里连"该往哪儿放"都看不出来。
# 判据是"**这个文件真的在**"而不是"目录在"：空目录会让 open() 抛得更晚、更难懂。
_ENV_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env"),  # Windows 常见落点
    os.path.join(os.path.expanduser("~"), ".config", "hermes", ".env"),           # XDG 风格
    os.path.join(os.path.expanduser("~"), ".hermes", ".env"),                     # 家目录直下
]


def env_candidates():
    """→ 依次要试的 `.env` 路径。设了 `HERMES_ENV` 就**只试它**（显式指定优先，且不悄悄绕开）。"""
    explicit = (os.environ.get("HERMES_ENV") or "").strip()
    return [explicit] if explicit else list(_ENV_CANDIDATES)


def find_env():
    """→ 第一个**存在**的 `.env` 路径；一个都不在就返回 None（不猜、不回退）。"""
    for p in env_candidates():
        if os.path.isfile(p):
            return p
    return None


def load_key():
    """从 Hermes 的 .env 取密钥（找不到就抛，**绝不回退到硬编码**）。"""
    path = find_env()
    if not path:
        raise RuntimeError("找不到 Hermes 的 .env（已试：%s）；可用 HERMES_ENV=<路径> 显式指定"
                           % "、".join(env_candidates()))
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        txt = f.read()
    m = re.search(r'^\s*%s\s*=\s*["\']?([^"\'\r\n]+)' % KEYNAME, txt, re.M)
    if not m:
        raise RuntimeError("在 %s 里找不到 %s" % (path, KEYNAME))
    return m.group(1).strip()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "opencode-go-proxy/1.0"

    def log_message(self, fmt, *args):          # 精简日志（别把 body 打出来）
        sys.stderr.write("[proxy] %s %s\n" % (self.command, self.path))
        sys.stderr.flush()

    def _relay(self, body):
        url = UPSTREAM + self.path[len("/v1"):] if self.path.startswith("/v1") else UPSTREAM + self.path
        headers = {
            "Authorization": "Bearer " + load_key(),
            "Content-Type": "application/json",
            "x-opencode-session": "zcode-cli-proxy",   # ← 关键：补上这个头
            "User-Agent": UA,
            "Accept": self.headers.get("Accept", "application/json"),
        }
        req = urllib.request.Request(url, data=body, method=self.command, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "application/json")
                self.send_response(r.status)
        except urllib.error.HTTPError as e:
            data = e.read()
            ctype = e.headers.get("Content-Type", "application/json")
            self.send_response(e.code)
        except Exception as e:                       # 上游不可达等
            data = json.dumps({"error": {"type": "proxy_error", "message": str(e)[:300]}}).encode()
            ctype = "application/json"
            self.send_response(502)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._relay(self.rfile.read(n) if n else b"")

    def do_GET(self):
        self._relay(None) if False else self._relay(b"")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--check", action="store_true", help="只检查密钥能否取到，不起服务")
    a = ap.parse_args()
    if a.check:
        k = load_key()
        print("密钥可取：%d 字符（不打印）" % len(k))
        return 0
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print("opencode-go 代理已启动：http://127.0.0.1:%d/v1  →  %s" % (a.port, UPSTREAM))
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
