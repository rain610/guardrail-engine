// agentguard 门禁插件（OpenCode）
// =====================================================================================
// 目的：把 OpenCode 从「自觉制」（模型读 AGENTS.md 才跑门禁）升级为「编辑期自动闭环」——
//       与 Claude Code 的 PostToolUse 钩子同级：AI 写完文件后自动跑门禁，把结论回灌给模型。
//
// 设计原则（与 harness 其它接入保持一致）：
//   1. **单一实现源**：不在插件里重写门禁逻辑，而是调用仓里的正本
//      `integrations/hooks/agentguard-gate.py`（与 Claude/Codex 用的是同一份）。
//   2. **找不到 harness 就静默放行**：宁可某台机器不过门禁，也绝不卡住同事的编辑器。
//   3. **只拦"真会改文件"的工具**：读目录/搜索不触发（否则一个会话白等几十秒）。
//   4. **结论回灌模型**：写进 tool 的 output，模型看得到并应据此修复（与 Claude exit 2 同效）。
//
// 配置（都是可选的，默认值适用于本机）：
//   AGENTGUARD_DIR        harness 工程根（主名；旧名 HARNESS_DIR 同样可用。不设则按自身位置/指针文件/常见位置找）
//   HARNESS_PYTHON               python 可执行文件（不设则按候选序真试：python3 → python → py -3）
//   HARNESS_OPENCODE_IDLE_CHECK  =1 时，回合结束（session.idle）再兜底跑一次全量改动检查
//   HARNESS_OPENCODE_LOG         日志路径（默认 %TEMP%/agentguard-gate.log）
// =====================================================================================

import { spawnSync } from "node:child_process"
import * as fs from "node:fs"
import * as os from "node:os"
import * as path from "node:path"
import { fileURLToPath } from "node:url"

const POINTERS = [
  path.join(os.homedir(), ".agentguard", "harness.path"),
  path.join(os.homedir(), ".agentguard.path"),
]

/**
 * 自身位置派生的候选：本文件住在 `<harness>/integrations/opencode/` 下，**上溯 2 层**即工程根。
 *
 * 与 Python 钩子**层数不同**，别照抄：那个在 `integrations/hooks/` 下，是**上溯 3 层**
 * （见 integrations/hooks/agentguard-gate.py 的 `_resolve_harness`）。两处都是"文件搬家就
 * 得跟着改"的地方，所以各自的锁分开钉。
 *
 * ESM 没有 `__dirname`；用 `import.meta.url`。用户按 README 写一行 re-export 引用正本时，
 * `import.meta.url` 仍指向正本 → 推导成立。取不到就返回空数组，退到后面的候选。
 */
function selfCandidates(): string[] {
  try {
    return [path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..")]
  } catch {
    return []
  }
}

// 与 integrations/hooks/agentguard-gate.py 的 WRITE_HINTS 保持同源语义：
// 工具名不是白名单时就按"命令特征"判断这条命令是否真会写文件。
const WRITE_TOOLS = new Set(["write", "edit", "patch", "multiedit", "create", "apply_patch", "notebookedit"])
const WRITE_HINTS = [
  "Begin Patch", "apply_patch", ">>", "> ", "Set-Content", "Add-Content", "Out-File", "New-Item",
  "Write-", "@'", "tee ", "sed -i", "python -c", "node -e", "git apply", "Remove-Item", "Copy-Item",
  "Move-Item", "ni ", "[System.IO.File]", "WriteAll", "AppendAll", "WriteAllText", "::WriteAllLines",
  "New-Object System.IO", "Clear-Content", "Set-Item", "Rename-Item",
]

/**
 * 「**真的是**工程根」的判据 —— 与 Python 钩子**同款**：能跑的门禁正本在，才算数。
 *
 * 2026-09-18 批次 5 收紧（原先是 `isDirectory`）：一个**恰好叫 agentguard 的空目录**
 * 以前会被当成工程根 —— 于是"找到了"却什么都跑不了，而调用方以为门禁在生效。
 * 判据从"目录存在"改成"`harness/agents/auto-gate.py` 是可读文件"，与
 * integrations/hooks/agentguard-gate.py 的 `_resolve_harness` 逐字同款。
 */
function isHarness(p: string | undefined | null): boolean {
  try {
    return !!p && fs.statSync(path.join(p, "harness", "agents", "auto-gate.py")).isFile()
  } catch {
    return false
  }
}

/** 「像工程根、但半残」（auto-gate.py 不在）—— 对应 Python 钩子的 K8 分支。 */
function looksLikeHarness(p: string | undefined | null): boolean {
  try {
    return !!p && fs.statSync(path.join(p, "integrations", "hooks", "agentguard-gate.py")).isFile()
  } catch {
    return false
  }
}

function log(msg: string) {
  const file = process.env.HARNESS_OPENCODE_LOG || path.join(os.tmpdir(), "agentguard-gate.log")
  try {
    fs.appendFileSync(file, `[${new Date().toISOString()}] ${msg}\n`)
  } catch {
    /* 日志失败绝不影响会话 */
  }
}

/** 半残只报一次（与 Python 钩子的 `_RESOLVE_WARNED` 同理：每次事件都喊 = 没人再看）。 */
let partialLogged = false

/**
 * 定位 harness 工程根：**自身位置 → AGENTGUARD_DIR（旧名 HARNESS_DIR）→ 指针文件 → 常见位置 → 从 cwd 上溯**；
 * 全找不到返回 null（→ 静默放行，见开头设计原则 2，本批不动它）。
 *
 * 2026-09-18 批次 5：原先第一顺位是写死的 `D:/Rain/project/agentguard`（维护者本机）。
 * 同事的仓不长在那儿 —— 那条候选在他们机器上恒假，且把"本机路径"糊进了要分享的文件里。
 * 换成自身位置推导（零配置、换 clone 目录也对），顺位与 Python 钩子对齐。
 */
function findHarness(): string | null {
  const cands: string[] = []
  for (const c of selfCandidates()) cands.push(c)
  const fromEnv = (process.env.AGENTGUARD_DIR || process.env.HARNESS_DIR || "").trim()
  if (fromEnv) cands.push(fromEnv)
  for (const ptr of POINTERS) {
    try {
      const v = fs.readFileSync(ptr, "utf8").trim()
      if (v) cands.push(v.replace(/\\/g, "/"))
    } catch {
      /* 没有指针文件是正常情况 */
    }
  }
  cands.push(path.join(os.homedir(), "agentguard"))
  cands.push(path.join(os.homedir(), "projects", "agentguard"))   // WSL 常见落点
  let d = process.cwd()
  for (let i = 0; i < 6; i++) {
    cands.push(path.join(d, "agentguard"))
    cands.push(d)
    d = path.dirname(d)
  }
  let partial: string | null = null
  for (const c of cands) {
    if (isHarness(c)) return c.replace(/\\/g, "/")
    if (!partial && looksLikeHarness(c)) partial = c
  }
  // 只**记一行日志**，不往 stderr 喊、更不拦：这条是 `:11` 写死的设计原则
  // 「宁可某台机器不过门禁，也绝不卡住同事的编辑器」。但"半残却无人知晓"不能继续 ——
  // 日志里说清"工程根看着在、门禁其实没生效"，排查时看得到。
  if (partial && !partialLogged) {
    partialLogged = true
    log(`工程根看起来是 ${partial}，但缺 harness/agents/auto-gate.py：本次放行不阻塞，`
        + `但门禁**没有生效**，请检查工程仓完整性`)
  }
  return null
}

/**
 * 解释器解析（2026-09-18 批次 5）。判据不是「命令在不在」，而是「**真能跑起来**」：
 * `--version` 退出码 0 且输出 **Python 3.11+**（评审 2026-11-04 P2-4：3.10 会让引擎 import
 * 期失败）。候选顺序与 sandbox 各脚本的
 * `HARNESS-PY-CANDIDATES:` 标记行同源：HARNESS_PYTHON → python3 → python → py -3。
 *
 * 同时挡掉三种真实情形：Ubuntu/WSL 上常常只有 `python3`（裸 `python` 直接 ENOENT）/
 * Windows 的 Microsoft Store `python3.exe` 别名（PATH 找得到、一跑就失败）/ 系统里是 Python 2。
 *
 * 原先两处都是 `process.env.HARNESS_PYTHON || "python"` —— 在 WSL 上 spawnSync 直接失败，
 * 而调用处写的是 `r.status ?? 0`，把"压根没跑起来"读成了 **rc=0 静默放行**：
 * 与 Python 钩子同族的洞（门禁没生效，却看起来一切正常）。所以除了换解析，
 * 调用处还要把 `r.error` 单独拎出来记一笔，不再与"门禁通过"混为一谈。
 */
let pyMemo: string[] | null = null

function pickPython(): string[] {
  if (pyMemo) return pyMemo
  const cands: string[][] = []
  const env = (process.env.HARNESS_PYTHON || "").trim()
  if (env) cands.push([env])
  cands.push(["python3"], ["python"], ["py", "-3"])
  for (const c of cands) {
    try {
      const r = spawnSync(c[0], [...c.slice(1), "--version"],
        { encoding: "utf8", timeout: 20_000, windowsHide: true })
      // 判据是 **3.11+**，不是「任意 Python 3」（评审 2026-11-04 P2-4，与 sh 入口的 _py_ok 同源）：
      // 3.10 会被选中 → 引擎 import 期失败。取 major.minor 数值比较，只有 3.11+ 才采用。
      const out = `${r.stdout || ""}${r.stderr || ""}`
      const m = /[Pp]ython (\d+)\.(\d+)/.exec(out)
      if (r.status === 0 && m && Number(m[1]) === 3 && Number(m[2]) >= 11) {
        pyMemo = c
        return c
      }
    } catch {
      /* 试下一个 */
    }
  }
  log("找不到能跑的 Python 3.11+（已试：HARNESS_PYTHON / python3 / python / py -3）")
  pyMemo = ["python3"]     // 退回最可能的名字：让调用处照常失败，并在日志里**如实**记下
  return pyMemo
}

/** 从工具调用参数里尽力取出被写入的文件路径。 */
function targetFile(args: any): string {
  if (!args || typeof args !== "object") return ""
  for (const k of ["filePath", "file_path", "filepath", "path", "target"]) {
    const v = (args as any)[k]
    if (typeof v === "string" && v) return v
  }
  return ""
}

function looksLikeWrite(tool: string, args: any): boolean {
  const t = (tool || "").toLowerCase()
  if (WRITE_TOOLS.has(t)) return true
  // 其余工具（bash/shell/...）按命令特征判断——读目录、grep 之类直接放行
  const cmd = typeof args?.command === "string" ? args.command : ""
  if (!cmd) return false
  return WRITE_HINTS.some((h) => cmd.includes(h))
}

/** 跑门禁正本（与 Claude/Codex 同一份实现）。返回 {rc, msg}。 */
function runHook(repoDir: string, filePath: string): { rc: number; msg: string } {
  const harness = findHarness()
  if (!harness) return { rc: 0, msg: "" } // 静默放行
  const hook = path.join(harness, "integrations", "hooks", "agentguard-gate.py")
  if (!fs.existsSync(hook)) {
    log(`hook 不存在：${hook}（跳过）`)
    return { rc: 0, msg: "" }
  }
  const payload = JSON.stringify({
    cwd: repoDir,
    tool_name: "Write",
    tool_input: { file_path: filePath },
  })
  const py = pickPython()
  try {
    const r = spawnSync(py[0], [...py.slice(1), hook, "opencode", "PostToolUse"], {
      input: payload,
      encoding: "utf8",
      timeout: 200_000,
      windowsHide: true,
    })
    if (r.error) {
      // 「压根没跑起来」≠「门禁通过」—— 原先 `r.status ?? 0` 把前者读成后者（rc=0 静默放行）。
      // 仍然放行（不卡编辑器，设计原则 2），但日志里留一笔：排查时这两种 0 必须分得开。
      log(`解释器起不来（${py.join(" ")}）：${r.error.message} —— 本次放行，门禁**未生效**`)
      return { rc: 0, msg: "" }
    }
    const raw = `${r.stdout || ""}${r.stderr || ""}`.trim()
    // hook 的退出码恒为 2（表"阻断"），分不清"门禁未通过"与"调用出错" →
    // 用 client=opencode 契约里回传的 gate_rc（没有 JSON 时退回 hook 退出码）。
    let rc = r.status ?? 0
    let msg = raw
    try {
      const parsed = JSON.parse(raw)
      const h = parsed?.agentguard
      if (h && typeof h.gate_rc === "number") {
        rc = h.gate_rc
        msg = String(h.message || "")
      }
    } catch {
      /* 不是 JSON（老版本 hook / 异常输出）→ 用原文与退出码兜底 */
    }
    log(`gate_rc=${rc} hook_exit=${r.status} repo=${repoDir} file=${filePath}`)
    return { rc, msg }
  } catch (e: any) {
    log(`调用异常（放行）：${e?.message || e}`)
    return { rc: 0, msg: "" } // 异常一律放行——hook 不拦路
  }
}

const AgentGuardGate = async (_ctx: any, _options: Record<string, unknown> = {}) => {
  return {
    // 编辑期闭环：AI 每次真正写文件后自动跑门禁，结论回灌进 tool output（模型可见）
    "tool.execute.after": async (input: any, output: any) => {
      const tool: string = input?.tool || ""
      const args: any = input?.args
      if (!looksLikeWrite(tool, args)) return

      const file = targetFile(args)
      // 评审 2026-09-23 M11：交给钩子/引擎的必须是**绝对路径**。宿主给的是原始字符串，
      // 可能是相对的；而钩子侧按下发的 cwd 解析它，本插件恰好把 cwd 改成了"被编辑文件的
      // 父目录" —— 于是 `src/a.vue` 会变成 `<仓>/src/src/a.vue`：auto-gate 一个都匹配不上
      // → rc=2，编辑期门禁整条失效，还被回灌成"多半是配置问题"（实测复现）。
      // 归一只能在这一侧做：只有本进程知道"相对谁"（path.resolve 按本进程 cwd，即宿主
      // 启动目录）。归一之后下面那条 cwd（被编辑文件的父目录）只用来让 auto-gate 认 gate，
      // 不再参与文件定位。
      const abs = file ? path.resolve(file) : ""
      const repoDir = abs ? path.dirname(abs) : process.cwd()
      const { rc, msg } = runHook(repoDir, abs)
      if (rc === 0 || rc === 3) return // 0=通过 / 3=非受管仓库 → 不打扰
      // hook 的 message 里已经带了标题（JSON 契约路径）；只有走"退出码兜底"时才补标题，
      // 否则模型会看到两行一样的标题（实测踩过）。
      const head = rc === 1
        ? "harness 门禁未通过（自动跑于你本次改动之后）"
        : `harness 门禁调用出错 rc=${rc}（多半是 harness 配置/环境问题，不是业务错）`
      const body = /门禁(未通过|调用出错)/.test(msg) ? msg : `${head}\n${msg}`
      const text = `\n\n---\n${body}\n`
      try {
        output.output = `${output.output || ""}${text}`
      } catch {
        /* 回灌失败不影响会话 */
      }
      log(`已回灌模型：rc=${rc}`)
    },

    // 兜底（默认关）：有的改动可能绕过了"写文件类工具"的判据（如没命中的 shell 写法），
    // 回合结束时再跑一次 auto-gate（它收集仓内全部未提交改动）。慢 → 用 env 显式开启。
    event: async ({ event }: any) => {
      if (process.env.HARNESS_OPENCODE_IDLE_CHECK !== "1") return
      if (event?.type !== "session.idle") return
      const harness = findHarness()
      if (!harness) return
      try {
        const py = pickPython()
        const r = spawnSync(py[0],
          [...py.slice(1), path.join(harness, "harness", "agents", "auto-gate.py")],
          { cwd: process.cwd(), encoding: "utf8", timeout: 200_000, windowsHide: true })
        if (r.error) {
          log(`idle 兜底：解释器起不来（${py.join(" ")}）：${r.error.message} —— 本次跳过`)
          return
        }
        if ((r.status ?? 0) === 1) {
          console.log("[agentguard] 回合结束兜底检查未通过：\n" + `${r.stdout || ""}${r.stderr || ""}`.trim())
        }
      } catch (e: any) {
        log(`idle 兜底异常（忽略）：${e?.message || e}`)
      }
    },
  }
}

export default {
  id: "agentguard-gate",
  server: AgentGuardGate,
}
