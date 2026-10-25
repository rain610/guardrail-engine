#!/usr/bin/env python3
"""为 30 个业务仓 AGENTS.md 注入指针写法（P1#3）

在标题行后插入路径解析说明，让 AI 知道如何把 <HARNESS> 解析成真实路径。
解析链：$GUARDRAIL_ENGINE_DIR（旧名 $AGENTGUARD_DIR / $HARNESS_DIR 兼用）环境变量
→ ~/.guardrail-engine/harness.path（旧 ~/.agentguard/harness.path 同样可读）文件内容。

幂等：已含 POINTER_MARKER 的文件跳过。
"""

import os
import sys

# UTF-8 兜底（与其它入口一致：Windows GBK 控制台防崩溃）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
import _config  # noqa: E402  （零依赖 mini-yaml + 本机覆盖层，禁止 import yaml）

POINTER_MARKER = "<!-- harness-pointer-injected -->"
POINTER_NOTE = r"""
> **路径约定**：下文 `<HARNESS>` 指本机 harness 工程根目录，按序取：
> 1. 环境变量 `$GUARDRAIL_ENGINE_DIR`（旧名 `$AGENTGUARD_DIR` / `$HARNESS_DIR` 同样可用）
> 2. `~/.guardrail-engine/harness.path` 文件内容（接入脚本生成；旧 `~/.agentguard/harness.path`
>    同样可读；Windows 上同一路径写作 `%USERPROFILE%\.guardrail-engine\harness.path`）
> 3. 都没有 → 本机没跑过接入脚本。**别猜路径**：请让用户重跑一次接入脚本
>    （`sandbox/setup_machine.sh`），跑完上面第 2 条就有了。
>
> 拿到工程根后，可用 `config/harness.local.toml` 的 `harness:` 键交叉验证；两者不一致时
> 以 `harness:` 键为准（接入脚本每次都会校正它）。
"""

def load_config():
    """加载 harness 配置，返回 repos 字典。

    走 harness 自带的零依赖 mini-yaml（含本机覆盖层合并），不引第三方 yaml。
    """
    cfg = _config.load() or {}
    repos = cfg.get("repos") or {}
    if not repos:
        print("ERROR: 本机未配 repos 段（检查 config/harness.local.toml）")
        sys.exit(1)
    return repos

def _derive_note_head(note):
    """从 POINTER_NOTE 自动推导块首（第一个非空行），绝不手写常量。

    手写常量的后果：改了 POINTER_NOTE 措辞却忘了同步常量 → remove_pointer 匹配不到旧块 →
    重注时新旧并存，而脚本仍打印「注入: 34」并退出 0（报平安）。自动推导让两者永不脱钩。
    """
    for line in note.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


NOTE_HEAD = _derive_note_head(POINTER_NOTE)


def remove_pointer(lines):
    """删掉所有「路径约定」块（marker 行 / 引用正文 / 前后空行），返回 (新 lines, 是否删过)。

    给 --force 用：POINTER_NOTE 改了必须整块换掉，只靠 marker 幂等跳过则文本更新永远进不去。

    **按内容识别、不只认 marker**：marker 行与正文之间还夹着空行（NOTE 以换行开头），
    只从 marker 往下扫会在第一个空行就停住，把正文整段留下 —— 那样再注入就会出现新旧两份并存。
    同时这样也能清掉历史遗留的「无 marker 孤立块」。
    """
    out = []
    i = 0
    removed = False
    while i < len(lines):
        line = lines[i]
        if POINTER_MARKER in line or line.lstrip().startswith(NOTE_HEAD):
            # 回退刚收下的前置空行
            if out and out[-1].strip() == "":
                out.pop()
            i += 1
            # 跳过块首之后的空行
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            # 吃掉引用块本体（> 行，允许中间夹空行）
            while i < len(lines) and (lines[i].lstrip().startswith(">") or lines[i].strip() == ""):
                i += 1
            removed = True
            continue
        out.append(line)
        i += 1
    return (out, removed)


def inject_pointer(agents_md_path, force=False):
    """注入指针说明，返回 (changed: bool, detail: str)"""
    if not os.path.isfile(agents_md_path):
        return (False, "missing")

    with open(agents_md_path, encoding="utf-8") as f:
        lines = f.readlines()

    if force:
        lines, _ = remove_pointer(lines)

    # 幂等检查
    for line in lines:
        if POINTER_MARKER in line:
            return (False, "already-injected")

    # 找标题行（第一个 # 开头的行）
    title_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title_idx = i
            break

    if title_idx < 0:
        return (False, "no-title")

    # 在标题行后插入指针说明
    # 保留原有空行结构：标题行 → 空行 → 指针说明 → 空行 → 原内容
    insert_lines = [
        "\n",
        POINTER_MARKER + "\n",
        POINTER_NOTE,
        "\n",
    ]

    new_lines = lines[:title_idx + 1] + insert_lines + lines[title_idx + 1:]

    with open(agents_md_path, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(new_lines)

    return (True, "injected")

def verify_pointers(repos):
    """校验每仓恰好 1 份指针，返回异常仓列表 [(key, 详情)]。

    marker 与块首**都要**计数：只数 marker 会漏掉「无 marker 的孤立块」（历史遗留或手改过），
    那种情形 marker 是 1、实际却两份并存 —— 照样报平安。
    """
    bad = []
    for key, path in sorted(repos.items()):
        agents_md = os.path.join(path, "AGENTS.md")
        if not os.path.isfile(agents_md):
            bad.append((key, "AGENTS.md 不存在"))
            continue
        with open(agents_md, encoding="utf-8") as f:
            s = f.read()
        n_marker = s.count(POINTER_MARKER)
        n_head = s.count(NOTE_HEAD)
        if n_marker != 1 or n_head != 1:
            bad.append((key, f"marker x{n_marker} / 块首 x{n_head}（均应为 1）"))
    return bad


def main():
    force = "--force" in sys.argv[1:]
    repos = load_config()
    stats = {"injected": 0, "skipped": 0, "missing": 0, "no-title": 0}
    details = []

    for key, repo_path in sorted(repos.items()):
        agents_md = os.path.join(repo_path, "AGENTS.md")
        changed, detail = inject_pointer(agents_md, force=force)

        if changed:
            stats["injected"] += 1
            details.append(f"  ✅ {key}: injected")
        elif detail == "already-injected":
            stats["skipped"] += 1
            details.append(f"  ⏭️  {key}: already injected")
        elif detail == "missing":
            stats["missing"] += 1
            details.append(f"  ❌ {key}: AGENTS.md not found")
        elif detail == "no-title":
            stats["no-title"] += 1
            details.append(f"  ⚠️  {key}: no title line found")

    print(f"\n指针写法注入完成{'（--force：已注入的仓整块替换为当前文本）' if force else ''}：")
    print(f"  注入: {stats['injected']}")
    print(f"  跳过（已注入）: {stats['skipped']}")
    print(f"  缺失: {stats['missing']}")
    print(f"  异常（无标题行）: {stats['no-title']}")
    print()
    for d in details:
        print(d)

    if stats["missing"] > 0:
        print(f"\n⚠️  {stats['missing']} 个仓库缺少 AGENTS.md，需手工创建")
        sys.exit(1)

    # 注入后自检：每仓必须恰好 1 份。
    # 缺了这步，remove_pointer 一旦失效（块首措辞变了 / marker 被手删 / 历史孤立块），
    # 新旧就会并存，而脚本照样打印「注入: N」退出 0 —— 损坏的 AGENTS.md 被当成功分发。
    bad = verify_pointers(repos)
    if bad:
        print("\n❌ 指针份数校验失败（每仓应恰好 1 份）：")
        for key, why in bad:
            print(f"  {key}: {why}")
        sys.exit(1)
    print(f"\n✅ 校验通过：{len(repos)} 个仓，每仓恰好 1 份指针")

if __name__ == "__main__":
    main()
