# -*- coding: utf-8 -*-
"""变异冒烟（评审 v11 §3-改进方案 1）：把「锁真的有牙齿」变成**可执行断言**。

为什么需要它：`tests/test_lock_inventory.py` 只回答「锁在不在」；它挡不住
「方法在、但断言被弱化/方法体空转」——评审 v11 §3 现场演示过（弱化 L7 + M7 变异下，
看守与套件同时全绿）。本脚本回答「锁还咬不咬」。

做法（安全、可重复）：
  1. 把工程**按 git ls-files 复制到临时目录**，并当场补成一份**干净的 git 工作区**
     （init + add + 一次提交；不补的话 fail-closed 的 harness_guard 会让对照恒红，见
     `_make_work_tree` —— 这是 2026-09-17 实测到的"机制空转"）；
  2. 先跑一次**对照**（未变异必须绿）——防止"副本本身是坏的"造成假红；
  3. 逐个变异：施加 → 跑目标锁 → 期望 **RED** → 还原；
  4. 判据（fail-closed）：
       · 锚点找不到（count != 1 或正则 0 命中）→ **SKIP = 失败**（等于没测，不能算通过）
       · 本平台是这条变异声明的**盲区**（`blind_on`，见下）→ **BLIND，不计命中也不计失败**
       · 变异后仍绿 → **失败**（锁被弱化 / 变异没打在判据上）
       · 红了但不是 `expect` 那条 → **失败**（判据取**失败清单**，不取"这串字出现过"，见 failed_names）
       · 对照不绿 → **失败**
  退出码 0 = 除盲区外全部命中；1 = 有失败/SKIP（CI 可直接用）。

★ **平台盲区（`blind_on`，2026-09-30 新增）** —— 为什么必须有这个概念：
   一条变异的可见性**可能只存在于一个平台上**。现场：`M114` 撤掉 `in_tree` 的
   `os.path.normcase`，而 **`normcase` 在 POSIX 上是恒等变换** ⇒ 撤掉它等于什么都没改，
   变异体与原体语义相同、**在 Linux 上变不出红来**。而 CI 的 `变异冒烟` 跑在 Linux 上，
   于是它报「变异后仍然绿 → 锁被弱化」—— **诊断方向正好指反了**：锁没弱，是这条护盾
   在那个平台上不可见。（同族：`4fa05de` 的「只在 Linux 上红」的锁；这是它的镜像。）
   处置**不是**删护盾（删掉就是把"让它闭嘴"当成"修好了"，本仓管这叫假绿），而是让冒烟
   认得「本平台看不见」并**明说**：声明要写进变异条目（`blind_on` + `blind_why` + `covered_by`），
   运行时打印 `BLIND` 行、汇总里单列计数、**不计入命中也不计入失败**。
   ⚠ 这是个**逃生门**，所以它自己被上锁：`tests/test_lock_inventory.py::
   test_platform_scoped_shields_are_paired_and_inventoried` 钉死三件事 ——
   ① 声明集合是**逐个列举**的（增删都要动锁，不能悄悄多一条）；
   ② 每条必须有非空 `blind_why`；
   ③ **互补性**：`covered_by` 指的必须是同一锁模块里**在相反平台上可见**的另一条变异，
      两条合起来必须覆盖 {nt, posix} —— 即"盲"只在单个平台上被允许，
      **没有任何一个平台可以两只眼睛都瞎**。

用法：python sandbox/mutation_smoke.py [--only=<id>[,<id>…]] [--dry-run] [--keep] [--full-module-lock]
      ★ `--only=` 与 `--only ` 两种写法**都收**（2026-09-24：原先只认等号，而本文写的是空格写法
        ⇒ 照本文敲会被静默忽略、跑成全量）。过滤器是**全等**匹配，要写**完整 id**
        （如 `M92-l6-git-cannot-start-reports-success`；写 `M92` 会一条都匹配不上并直接报错）。
      ★ `--dry-run`：只打印**选中了哪几条**就退出 —— 不建副本、不跑任何变异。
        全量一趟从 2026-09-30 起约几分钟（此前约四十分钟），敲下命令后先看一眼
        "选中的是不是我要的"仍然划算。
      ★ `--full-module-lock`：**排查用的退路**，不是日常档。默认每条变异只跑 `expect`
        点名的那条用例（`-k`，见 `_lock_cmd`）；怀疑某条变异依赖同模块其它用例的残留
        状态时，用它退回整跑整个锁模块做 A/B。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 本平台（`blind_on` 用的词表，只有这两个）。★ 用 os.name 而不是测 normcase：
# 前者是"我在哪台机器上"，正是盲区声明要问的问题；后者是"被测函数此刻怎么表现"，
# 那是**锁**该问的问题（见 test_entry_level.py 的 in_tree 锁）—— 两个问题别混。
PLATFORM = "nt" if os.name == "nt" else "posix"

# 变异清单：id / 文件 / 施加方式 / 期望被拉红的锁模块
MUTATIONS = [
    {"id": "M5-repo-wide", "file": "checks/file_types.py", "kind": "after_def",
     "anchor": "def is_repo_wide(gate_name):", "inject": "    return False  # mutation-smoke",
     "lock": "test_review_v9_batch1.py",
     "expect": "test_harness_self_runs_on_staged_py",
     "why": "仓库级 gate 又按扩展名过滤 → 提交期 harness_self 被过滤掉（评审 v9 V9-1）"},
    {"id": "M7-syntax-probe", "file": "sandbox/acceptance_check.py", "kind": "regex",
     "pattern": r'(res\["syntax"\]\s*=\s*)', "repl": r'\1True or ',
     "lock": "test_review_v9_batch1.py",
     "expect": "test_syntax_probe_is_false_without_node",
     "why": "acceptance 语法探针恒真 → 盲区（评审 v9 V9-3 / v10 M7）"},
    {"id": "M3-rc-flatten", "file": "harness/hooks-dir/pre-commit", "kind": "exact",
     "old": 'exit "$rc"', "new": 'exit 1  # mutation-smoke',
     "lock": "test_review_v9_batch1.py",
     "expect": "test_dispatcher_passes_hook_rc_through",
     "why": "分发器把 rc=2 压成 1 → 配置错误被当成业务拦截（Codex v4 P2-1）"},
    {"id": "M11-compact-tail", "file": "integrations/hooks/agentguard-gate.py", "kind": "exact",
     "old": ('        head_n = MAX_LINES // 2                  # 省略标记占 1 行，其余头尾对半\n'
             '        tail_n = MAX_LINES - 1 - head_n\n'
             '        elided = len(lines) - head_n - tail_n\n'
             '        lines = lines[:head_n] + ["…（省略中间 %d 行）…" % elided] + lines[-tail_n:]'),
     "new": '        lines = ["…（省略中间 %d 行）…" % (len(lines) - MAX_LINES)] + lines[-MAX_LINES:]  # mutation-smoke',
     "lock": "test_entry_level.py",
     "expect": "test_compact_keeps_findings_not_only_tail",
     "why": "回灌内容只留末尾 → 诊断洪水把最前面的检查结论整段挤掉，模型只看到"
            "「门禁未通过」看不到要改什么（2026-09-15 CI 红：4 条钩子契约锁同时红）"},
    {"id": "M12-warn-flood", "file": "harness/_config.py", "kind": "exact",
     "old": ('        elif not os.path.isdir(rp):\n'
             '            missing.append((name, rp))'),
     "new": ('        elif not os.path.isdir(rp):\n'
             '            warn(f"gate {name} 仓库路径不存在: {rp}")  # mutation-smoke'),
     "lock": "test_entry_level.py",
     "expect": "test_diagnostic_noise_does_not_flood_hook_context",
     "why": "诊断退回逐 gate 各一条 → 同事机器上 30+ 条灌满 40 行回灌窗口，"
            "模型看不到要改哪一行（与 M11 同源的噪声侧）"},
    {"id": "M13-version-fork", "file": "agentguard/mcp.py", "kind": "exact",
     "old": '    return _shell.read_version(ROOT)',
     "new": ('    if not ROOT:  # mutation-smoke\n'
             '        return "0.0.0-dev"\n'
             '    try:\n'
             '        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:\n'
             '            return f.read().strip() or "0.0.0-dev"\n'
             '    except OSError:\n'
             '        return "0.0.0-dev"'),
     "lock": "test_agentguard.py",
     "expect": "test_all_entries_agree_without_version_file",
     "why": "MCP 壳退回「只读 VERSION、缺失即 0.0.0-dev」的自备实现 → 轮子安装形态下"
            "同一个包报出两个版本号（剖析报告 N1：ADR 铁律 3「版本单一来源」在安装形态下失效）"},
    {"id": "M14-version-nofallback", "file": "agentguard/_shell.py", "kind": "exact",
     "old": ('    try:  # 轮子安装形态（无 VERSION 文件）：回退到包元数据\n'
             '        from importlib import metadata\n'
             '        return metadata.version("agentguard")\n'
             '    except Exception:\n'
             '        return fallback\n'),
     "new": '    return fallback  # mutation-smoke',
     "lock": "test_agentguard.py",
     "expect": "test_all_entries_agree_without_version_file",
     "why": "公用件摘掉包元数据回退 → 「三处一致」变成「三处一致地错」（都退化成写死的 0.0.0-dev）。"
            "M13 防分叉、M14 防集体退化，两条一起才说明这条锁比的是**真值**而不是自洽"},
    {"id": "M18-override-silent", "file": "harness/_config.py", "kind": "exact",
     "old": ('                _policy_notice_once(\n'
             '                    ("override", local, tuple(sorted(bad))),\n'
             '                    "[config-policy] ⚠ 已按 HARNESS_ALLOW_POLICY_OVERRIDE=1 放行策略字段覆盖"\n'
             '                    "（仅调试用，别进共享配置）；本次放行 %d 条：%s\\n"\n'
             '                    % (len(bad), ", ".join(sorted(bad)[:12]) or "无（本次未改动任何策略字段）"))\n'
             '                # 放行模式下**没有任何字段被退回** —— 不能把 bad 记进「被退回」那个信号，\n'
             '                # 否则 --validate 会打出一句「…已被忽略」，与事实正好相反。\n'
             '                _POLICY_REJECTED[:] = []\n'
             '                _POLICY_OVERRIDDEN[:] = list(bad)\n'),
     "new": ('                _policy_notice_once(("override", local, ()),\n'
             '                                    "[config-policy] ⚠ 已按 HARNESS_ALLOW_POLICY_OVERRIDE=1 "\n'
             '                                    "放行策略字段覆盖（仅调试用）\\n")  # mutation-smoke\n'),
     "lock": "test_review_v8_batch2.py",
     "expect": "test_escape_hatch_reports_what_it_let_through",
     "why": "放行开关退回**一句泛泛的提示**（剖析报告六节「豁免出口缺少审计」的原文形态）："
            "看不出动了哪几个策略字段，last_policy_override() 也永远是空的 → 排查"
            "「这台机器上某 gate 怎么突然变松了」等于没线索。这条变异同时证明那条锁不是"
            "恒真 —— 它要求字段名逐个出现在输出里、且两个信号互斥。"
            "★ 2026-09-17 重新对锚：62811cc 给这条提示加了去重（_policy_notice_once），"
            "锚点因此失配、冒烟报 SKIP —— 而冒烟当时正因副本没有 .git 而空转，"
            "SKIP 没人看见。锚点跟着新写法重对，判据（泛泛提示 / 信号为空）一字未改。"},
    {"id": "M17-runon-ghost-trigger", "file": "harness/run_gate.py", "kind": "exact",
     "old": '        _RUN_ON_OK = {"pre_commit", "post_edit"}',
     "new": '        _RUN_ON_OK = {"pre_commit", "post_edit", "stop", "auto"}  # mutation-smoke',
     "lock": "test_review_v8_batch3.py",
     "expect": "test_run_on_whitelist_equals_actually_consumed_triggers",
     "why": "白名单里塞回没有消费方的 stop/auto → 写进配置能过体检、gate 却永远不在任何时机触发"
            "（剖析报告六节「半成品痕迹」：写进配置等于没写）。这条变异同时验证新锁**不是恒真**"
            "—— 它从 run_gate.py 取白名单字面量、从两个触发点实现取消费方字面量，两边不等才红"},
    {"id": "M19-accept-fixture-in-tree", "file": "sandbox/acceptance_check.py", "kind": "exact",
     "old": '                cfg = os.path.join(d, "overlay.yaml")',
     "new": ('                cfg = os.path.join(ROOT, "sandbox",\n'
             '                                   "_accept_probe_cfg_%d.yaml" % os.getpid())  # mutation-smoke'),
     "lock": "test_review_v8_batch3.py",
     "expect": "test_self_gate_probe_writes_nothing_into_the_engineering_tree",
     "why": "把 harness_self 的密闭夹具落回**工程树**（历史形态就是如此：建一个 in-tree 探针、跑完删掉）"
            "→ 承诺「不向工程树写任何文件」当场作废；被 Ctrl-C / 超时打断时残留文件会让此后"
            "每次提交都红。★ 这条变异刻意只改**路径怎么算**（改回 ROOT 底下），不改其它："
            "旧锁若写成「跑完看树里多了什么」，因为旧实现 try/finally 当场删掉，回退后照样绿 —— "
            "本仓 M15 栽过同款假绿。新锁在写入那一刻记账，路径形状怎么变都拦得住。"},
    {"id": "M20-pack-stamps-machine", "file": "sandbox/pack_release.py", "kind": "exact",
     "old": '"--python", a.python, "--keep-placeholders"], cwd=ROOT)',
     "new": '"--python", a.python], cwd=ROOT)  # mutation-smoke',
     "lock": "test_release_artifact.py",
     "expect": "test_real_pack_verifies_and_has_no_problems",
     "why": "打包器又按**本机**打戳（出货件烧进打包机的 python / 引擎绝对路径）→ 同事拿到包，"
            "里面写的是别人机器的路径。这是 G1 的原文形态：占位符不留，装机时无从填充；"
            "而 `claude plugin validate` 照样通过，所以这条只有在**真产物**上跑校验器才拦得住。"},
    {"id": "M21-ship-machine-paths", "file": "sandbox/verify_release.py", "kind": "exact",
     "old": '    for mj in _mcp_jsons(stage):\n        _check_machine_independent(mj, p)\n',
     "new": '    for mj in _mcp_jsons(stage):\n        pass  # mutation-smoke\n',
     "lock": "test_release_artifact.py",
     "expect": "test_pack_ships_placeholders_not_packer_paths",
     "why": "校验器不再检查「出货件与机器无关」→ M20 那种包会被放行到同事手上。"
            "与 M20 成对：一个管「别打戳」，一个管「打戳了要拦住」，只留一个都是漏的。"},
    {"id": "M22-plugin-version-drift", "file": "sandbox/verify_release.py", "kind": "exact",
     "old": '        if _pep440(got) != _pep440(version):',
     "new": '        if False:  # mutation-smoke',
     "lock": "test_release_artifact.py",
     "expect": "test_version_mismatch_is_reported",
     "why": "插件清单 version 不再与 VERSION 对账（G7）→ 市场里那一份的版本号与实际出货件脱钩，"
            "`claude plugin update` 的「同版本即无事可做」判断建立在错数字上：出问题时会往"
            "「工具没生效」的方向排查，而真相是版本号根本没跟上。"},
    {"id": "M23-packinfo-unchecked", "file": "sandbox/verify_release.py", "kind": "exact",
     "old": 'def _check_pack_info(stage, p, version):\n',
     "new": 'def _check_pack_info(stage, p, version):\n    return  # mutation-smoke\n',
     "lock": "test_release_artifact.py",
     "expect": "test_missing_pack_info_is_reported",
     "why": "PACK-INFO.json 不再验（G3）→ 包的**身份**与内容脱钩：同版本号下内容被换过、"
            "指纹对不上，校验器一概不响；同事手上那份到底是哪一版无从判定。"},
    {"id": "M24-fingerprint-blind", "file": "sandbox/pack_release.py", "kind": "exact",
     "old": '                out[rel] = wheel_content_sha1(fp) if rel.endswith(".whl") else content_sha1(fp)',
     "new": '                out[rel] = "0" * 40  # mutation-smoke',
     "lock": "test_release_artifact.py",
     "expect": "test_any_marketplace_change_moves_the_fingerprint",
     "why": "指纹只留文件名、内容哈希换成常量 → **覆盖范围不全的指纹照样自洽**："
            "改了 marketplace 里任何一个字节，指纹纹丝不动。「字段能对上」这种自证式的锁"
            "看不出这个 —— 必须有一条「改一个字节就该动」的行为锁。"
            "★ 变异刻意写成 `\"0\"*40` 而不是删掉哈希：保留 `{相对路径: 哈希}` 这个字典形状，"
            "否则 `fingerprint()` 与 `diff_files()` 照样跑得下去，红灯就该来自语义而不是崩溃。"
            "★ 2026-09-17 扩指纹时锚点跟着搬到了 `pack_files()` 的构件支路（此前那版在另一处 "
            "walk 循环里）：变异打的是**清单怎么造的**，不是某一支调用方读没读它。"},
    {"id": "M25-samever-silent", "file": "sandbox/pack_release.py", "kind": "exact",
     "old": '    elif prev_files is not None and fingerprint(prev_files) != pack_fp:',
     "new": '    elif False and prev_files is not None and fingerprint(prev_files) != pack_fp:  # mutation-smoke',
     "lock": "test_release_artifact.py",
     "expect": "test_same_version_different_content_warns_but_still_succeeds",
     "why": "同版本号、不同内容的重打包不再出声 → 同事拿到新包，`claude plugin update` 打印"
            "「已是最新」并**什么都不做**，而他以为已经升级了（G3 的现场形态）。"
            "这条只保证「吵醒人」，不阻断出货 —— 出货不该因为一句提醒被拦，但必须被看见。"
            "★ 2026-09-17 扩指纹时这条判定的**取数方式**换了（从「读上一个 zip 的 "
            "marketplace_sha1 字段」改成「把上一个 zip 现算成清单再比」），判定形状没变："
            "仍只在「与上一次不同」时出声 —— 所以这条变异照旧有牙。"},
    {"id": "M26-openclaw-no-overwrite", "file": "sandbox/upgrade.py", "kind": "exact",
     "old": ('        for fn in fns:\n'
             '            try:\n'
             '                shutil.copy2(os.path.join(dp, fn), os.path.join(tdir, fn))\n'),
     "new": ('        for fn in fns:\n'
             '            if os.path.isfile(os.path.join(tdir, fn)):  # mutation-smoke\n'
             '                continue\n'
             '            try:\n'
             '                shutil.copy2(os.path.join(dp, fn), os.path.join(tdir, fn))\n'),
     "lock": "test_upgrade_path.py",
     "expect": "test_openclaw_extension_gets_b_content_and_keeps_the_orphan",
     "why": "就地覆盖退回「已存在的不动」（看着像保守，实则是升级路上最典型的假成功）→ "
            "扩展目录里那份共享文件永远停在上一个版本，而脚本照样报「已覆盖」、rc=0。"
            "这条变异同时钉住那条锁的**两个方向**：只断言「B 的新文件在」是不够的 —— "
            "新文件照抄进来、旧文件原地不动，那句话照样成立。判据必须是**共享文件的内容**。"},
    {"id": "M27-skill-copy-no-overwrite", "file": "agentguard/cli.py", "kind": "exact",
     "old": ('                    if old == data:\n'
             '                        same += 1\n'
             '                        continue\n'),
     "new": ('                    if True:  # mutation-smoke\n'
             '                        same += 1\n'
             '                        continue\n'),
     "lock": "test_upgrade_path.py",
     "expect": "test_skill_copy_gets_b_content_and_the_matrix_notices",
     "why": "技能副本的幂等复制退化成「已存在即算一致」→ 副本永远停在上一次装的内容，"
            "而 created/updated/same 三个计数自洽、调用方读不出任何异常。"
            "剖析报告的原话是四条通道一条都没覆盖「装 A → 装 B」：这条变异正是把技能副本"
            "那一条打回原形 —— 只测「装」的时候它必然全绿，因为那时目标目录是空的。"},
    {"id": "M28-pip-no-install", "file": "sandbox/upgrade.py", "kind": "exact",
     "old": '    rc, out = run_cmd([a.python, "-m", "pip", "install", flag, wheel], timeout=900)',
     "new": '    rc, out, tail = 0, "", ""  # mutation-smoke',
     "lock": "test_upgrade_path.py",
     "expect": "test_pip_channel_refreshes_a_to_b_and_says_so",
     "why": "pip 那一步只假装跑了（rc 直接给 0、一个文件都不装）→ 这条变异是「rc 不是判据」"
            "那条锁的反证：若锁只看 rc / 只看输出里有没有「成功」，一个字节都没搬的升级照样绿。"
            "本仓在插件通道栽过同款形状（G3），判据才改成读产物 —— 这条把同一个要求钉在 pip 上。"
            "★ 打的是 `pip_install()` 这个共用入口，`-U` 与后补的 `--force-reinstall` 一起失效："
            "两条命令只要有一条没真跑，产物就对不上，锁必须红。"},
    {"id": "M29-pip-rc-as-criterion", "file": "sandbox/upgrade.py", "kind": "exact",
     "old": '    if now == want:',
     "new": '    if rc == 0:  # mutation-smoke',
     "lock": "test_upgrade_path.py",
     "expect": "test_pip_same_version_noop_is_healed_by_an_automatic_force_reinstall",
     "why": "判据从「产物与本包逐字节一致」退回「看 pip 的 rc」—— 这是 2026-09-17 修掉的"
            "那个缺陷的**原文形态**：同版本号、内容不同时 pip 是 rc=0 且一个字节都不换"
            "（隔离 venv 实测原话：is already installed with the same version…），"
            "于是脚本打印「✅ 升级完成」，机器上跑的却还是旧内容。"
            "★ 这条与 M28 互补、不能互相替代：M28 证明「产物必须真的换」，M29 证明"
            "「换了没换不能靠 rc 判」—— 只做 M28 的话，把判据挂回 rc 仍然全绿。"},
    {"id": "M30-plugin-version-excuse", "file": "sandbox/upgrade.py", "kind": "exact",
     "old": '        if new_ver == want_ver:',
     "new": '        if True:  # mutation-smoke',
     "lock": "test_upgrade_path.py",
     "expect": "test_plugin_copy_stale_across_versions_is_not_excused_by_the_version",
     "why": "内容没刷新时**一律**甩锅给「版本号相同」→ 版本号明明从 0.1.2 升到了 0.1.3 也会这么打印。"
            "真实事故里更常见的恰恰是这一种（宿主没重启 / 市场指错 / 缓存没落）：读的人于是去升"
            "一个**已经升过**的号，然后困惑为什么还是没换。假警报比漏报更磨损可信度 —— "
            "诊断错方向等于没诊断。（这条只改诊断、不改裁决，所以只能由锁的那句"
            "「不许拿错的理由解释」来拦 —— 这正是它必须单独存在的原因。）"},
    {"id": "M31-skill-matrix-blind", "file": "agentguard/cli.py", "kind": "exact",
     "old": '            if a == b:',
     "new": '            if True:  # mutation-smoke',
     "lock": "test_upgrade_path.py",
     "expect": "test_skill_copy_gets_b_content_and_the_matrix_notices",
     "why": "G4 的安装矩阵对技能副本恒报「与引擎仓一致」→ 那行字退回纯装饰：该报落后的时候不报，"
            "「这台机器上到底是哪一版」这个视图就只剩下好看。与 M27 成对、不可互替："
            "一个管「内容有没有换」，一个管「换没换读得出来」—— 报告 G4 的原话正是"
            "「升级工具读不出装出来的副本是哪一版」。"},
    {"id": "M32-pack-tools-drift", "file": "sandbox/verify_release.py", "kind": "exact",
     "old": 'def _check_shipped_tools(stage, p):\n',
     "new": 'def _check_shipped_tools(stage, p):\n    return  # mutation-smoke\n',
     "lock": "test_release_artifact.py",
     "expect": "test_stale_tool_in_pack_is_reported",
     "why": "校验器不再比对「包内工具 === 仓库那份」→ **改了升级脚本、忘了重打包**这个形态"
            "四处全绿：存在性检查（包里有没有 upgrade.py）绿、包内自洽的 SHA1SUMS 绿、"
            "只覆盖 marketplace 的指纹绿、打包器只在同版本重打包时比指纹而工具不在指纹里 → "
            "同事照文档跑的是**上一版的升级脚本**，且没有任何一步会报错。"
            "★ 这条不是假设：U-5 当场就是它 —— 改完 sandbox/upgrade.py 之后 dist/ 里那份仍是旧的，"
            "而 `python sandbox/verify_release.py` 照样 rc=0。锁分两层：打包器搬对了"
            "（test_shipped_tools_are_the_repo_copies）与校验器发现得了没搬对（本条的 expect），"
            "删掉任一层，另一层都拦不住这个形态。"},
    {"id": "M33-pip-back-to-advice-only", "file": "sandbox/upgrade.py", "kind": "exact",
     "old": '    rc2, _o2, tail2 = pip_install(a, wheel, "--force-reinstall")',
     "new": '    rc2, _o2, tail2 = 1, "", "mutation-smoke：只提示、不代跑"  # mutation-smoke',
     "lock": "test_upgrade_path.py",
     "expect": "test_pip_same_version_noop_is_healed_by_an_automatic_force_reinstall",
     "why": "同版本号那条路退回「只打印一句 --force-reinstall 让人自己去跑」—— 这正是本仓"
            "2026-09-17 之前的形状，也正是用户 2026-09-17 拍板要改掉的形状：提示在多台机器上"
            "等于没人跑，而脚本仍然报的是别的结论。判据落在**产物字节**上，所以"
            "「打印了那句提示」不算数 —— 这条变异考的正是这一点。"
            "★ 与 M29 成对但不可互替：M29 管「拿什么判」，本条管「判出没换之后做不做事」；"
            "只做 M29 的话，把代跑删掉、改回提示仍然全绿。"},
    {"id": "M34-pack-fingerprint-back-to-market-only", "file": "sandbox/pack_release.py",
     "kind": "exact",
     "old": '    return fingerprint(pack_files(stage))',
     "new": '    return market_fingerprint(stage)  # mutation-smoke',
     "lock": "test_release_artifact.py",
     "expect": "test_a_tool_change_moves_the_pack_fingerprint",
     "why": "整包指纹退回**只覆盖 marketplace/**（2026-09-17 之前的原文形态）→ 改了工具或文档、"
            "忘了重打包时指纹纹丝不动，「同一个版本号下已经存在过一份内容不同的包」不响。"
            "★ 这条是用户 2026-09-17 拍板「真扩」的**阴性对照**：只把覆盖范围改回窄口径，"
            "其余一切不变 —— 若锁只验「字段与实算对得上」（那种自证式写法），退回去照样全绿。"
            "有牙的是 test_a_tool_change_moves_the_pack_fingerprint（动的必须是市场子树**之外**的"
            "文件，改 market 的话窄口径也会响，那条就证明不了扩没扩）与 "
            "test_repacked_wheel_with_new_timestamps_keeps_the_fingerprint。"},
    {"id": "M35-pack-sha1-not-checked", "file": "sandbox/verify_release.py", "kind": "exact",
     "old": '    elif info.get("pack_sha1") != want_pack:',
     "new": '    elif False:  # mutation-smoke',
     "lock": "test_release_artifact.py",
     "expect": "test_tampered_pack_fingerprint_is_reported",
     "why": "校验器**记了整包指纹却不再核它** → `pack_sha1` 退回纯装饰：字段在、格式对、"
            "看起来是一份新格式的 PACK-INFO，唯独没有人拿它和包内容对过账。这比压根没有这个字段"
            "更坏 —— 读的人看到字段就以为身份已核（与 M23「整个 PACK-INFO 不验」是两回事："
            "M23 连文件都不看，本条是**只核一半**）。"},
    {"id": "M36-oldformat-silently-passed-as-mismatch", "file": "sandbox/verify_release.py",
     "kind": "exact",
     "old": '    if "pack_sha1" not in info:',
     "new": '    if False and "pack_sha1" not in info:  # mutation-smoke',
     "lock": "test_release_artifact.py",
     "expect": "test_old_format_pack_info_is_not_silently_passed",
     "why": "旧格式（没有 pack_sha1）不再单独点名，而是落进下面那条「指纹不符」里 → 报出来的"
            "话是「记录 None，实算 78c8…」这种，读的人理解成**包被改过**，于是去找谁动了包；"
            "真相是这份 PACK-INFO 压根没有整包字段、该重新生成。诊断指错方向等于没诊断"
            "（与 M30 同一条立场），而旧包是真会遇到的（同事手里、dist/ 里都有）。"},
    {"id": "M37-wheel-back-to-byte-hash", "file": "sandbox/pack_release.py", "kind": "exact",
     "old": '                out[rel] = wheel_content_sha1(fp) if rel.endswith(".whl") else content_sha1(fp)',
     "new": '                out[rel] = sha1_file(fp)  # mutation-smoke',
     "lock": "test_release_artifact.py",
     "expect": "test_repacked_wheel_with_new_timestamps_keeps_the_fingerprint",
     "why": "轮子退回**按文件字节**入指纹（2026-09-17 扩指纹时的初版形态，当时就是这么写的）→ "
            "wheel 是 zip、条目时间戳由构建时刻决定，**构建本身不可复现**（实测 f7b78f70… / "
            "6ddd8029…，内容一模一样），于是每次重打包 `pack_sha1` 都变：那条「同版本号、内容不同」"
            "的提醒变成**逢包必响**，人很快学会无视它，真正该看见的那次也就看不见了 —— "
            "漏报与噪音是同一种失败。★ 与 M34 互补：M34 管「覆盖得够不够宽」，本条管"
            "「进来的东西按什么算」。两条都会让 test_identical_repack_stays_quiet 变红。"},
    {"id": "M38-lock-renamed-out-of-collection", "file": "tests/test_review_v13_batch1.py",
     "kind": "exact",
     "old": '    def test_no_newline_marker_does_not_shift_lines(self):',
     "new": '    def _disabled_no_newline_marker_does_not_shift_lines(self):  # mutation-smoke',
     "lock": "test_lock_inventory.py",
     "expect": "test_no_module_lost_tests",
     "why": "一条锁被**改名成不是 test 的样子**（停用/改名合并里最省事的做法）→ unittest 不再收集它，"
            "模块自己跑起来全绿、套件全绿、CI 全绿，**只有计数看守看得见**。"
            "这正是 2026-09-17 之前看守漏掉的那半个盲区：本清单当时只登记 35 个模块而 tests/ 下有 43 个，"
            "test_review_v13_batch1..8.py 这 8 个**整个模块**不在看守视野里 —— 那个形状下，本条变异"
            "乃至把整个模块的文件删掉，都不会有任何检查出声。本条钉住的是它现在的**覆盖面**："
            "登记表已按实测值再基线，少一条就红。"
            "★ 与 M19 那类不同：本条不改任何产品代码，只改**守卫名单所看守的对象** —— 而看守的弱点"
            "恰恰只在「被看守的对象悄悄变少」这一侧，所以这条阴性对照必须打在名单上，不能打在别处。"},
    {"id": "M39-eol-not-aligned-to-index", "file": "sandbox/eol_text.py", "kind": "exact",
     "old": '    return align_to_index(data, eol_map.get(rel))',
     "new": '    return data  # mutation-smoke',
     "lock": "test_pack_eol.py",
     "expect": "test_read_shipped_gives_lf_not_worktree_bytes",
     "why": "随包源码退回**照搬工作树字节**（= 本批之前 `shutil.copy2` 的原文形态）→ "
            "本机 `core.autocrlf=true` 时打出的包与 CI 的包内容不同（实测外层 3 个成员 + "
            "轮子内 2 个成员），而「改了没改」在包里看不出来：两份都自洽，SHA1SUMS 都对得上。"
            "★ 与 M40 成对、不可互替：本条管「搬进包里的字节」，M40 管「这些字节怎么入指纹」。"
            "只做 M39 的话包是干净了但指纹仍随平台漂；只做 M40 的话指纹稳了而包里躺的仍是 CRLF。"
            "★ 判据刻意落在**真 git 仓库**上（索引 LF + 工作树 CRLF），不用替身："
            "这条要断言的正是「git 说该是什么」与「盘上是什么」不一致时听谁的。"},
    {"id": "M40-fingerprint-back-to-raw-bytes", "file": "sandbox/eol_text.py", "kind": "exact",
     "old": '    return data if looks_binary(data) else data.replace(b"\\r\\n", b"\\n")',
     "new": '    return data  # mutation-smoke',
     "lock": "test_pack_eol.py",
     "expect": "test_eol_only_difference_is_invisible",
     "why": "指纹口径退回**逐字节** → 只差行尾的两份内容算出的指纹不同，于是 G3 那条"
            "「同一个版本号下已经存在过一份内容不同的包」**逢包必响**。危害与漏报同源："
            "人被训练成无视它之后，真正该看见的那一次（同事装了新包却还在跑旧包的唯一前兆）"
            "也就看不见了。★ 这条同时打中两个支路：磁盘支路与 zip 支路都经 `canon_bytes` —— "
            "这正是 M-E「只归一一边」那个错法的反面（那种错法由 "
            "test_disk_and_zip_yield_the_same_inventory 单独钉住）。"},
    {"id": "M41-wheel-record-back-into-fingerprint", "file": "sandbox/eol_text.py", "kind": "exact",
     "old": '    if norm.endswith(".dist-info/" + WHEEL_DERIVED[0]):\n        return None\n',
     "new": '    pass  # mutation-smoke\n',
     "lock": "test_pack_eol.py",
     "expect": "test_record_is_excluded",
     "why": "`dist-info/RECORD` 重新进指纹 → 它是**别的成员的字节哈希**，别的成员一变它必然跟着变。"
            "留着它不会带来任何新信息，只会把同一个根因在指纹里算两遍 —— 而代价落在诊断上："
            "轮子内一处平台差异会让**两个**成员同时变，读的人以为有两处问题。"
            "（严重度低于 M40/M39，但它是「看着更严谨、其实只增加噪音」那一类，正是本仓反复栽的形状。）"},
    {"id": "M42-wheel-generator-line-back", "file": "sandbox/eol_text.py", "kind": "exact",
     "old": '    if norm.endswith(".dist-info/WHEEL"):\n        data = b"\\n".join(ln for ln in data.split(b"\\n")\n                          if not ln.startswith(_GENERATOR_PREFIX))\n',
     "new": '    pass  # mutation-smoke\n',
     "lock": "test_pack_eol.py",
     "expect": "test_toolchain_generator_line_is_ignored",
     "why": "`Generator:` 行重新进指纹 → 它写的是**构建机的 setuptools 版本**（实测 79.0.1 vs 84.0.0），"
            "而 setuptools 版本不在这份源码的管辖内：同一份源码在 79 与 84 上打出的轮子功能上无从区分。"
            "留着它 = 把「构建机装了什么」混进「这份包是不是我做的」这个判断 —— 而这恰恰是"
            "本批要治的那个病的**另一种形态**（平台/环境噪音冒充内容变化）。"
            "★ 与 M40 的区别：M40 是行尾，本条是工具链版本。两条都治好了「本机 vs CI」才真的对得上 —— "
            "实测那一对包里 8 处差异中，5 处行尾、2 处工具链、1 处派生件，正好 M39/M40 + M42 + M41。"},
    {"id": "M43-gate-key-glued-into-the-previous-line", "file": "config/harness.toml", "kind": "exact",
     "old": 'run_on = ["pre_commit", "post_edit"]\n\n# 示例 gate（展示配置结构，按需修改或删除）\n[gates.example]\n',
     "new": 'run_on = ["pre_commit", "post_edit"] [gates.example]\n',
     "lock": "test_review_v13_batch9.py",
     "expect": "test_no_gate_key_is_glued_onto_the_previous_line",
     "why": "把 gate 表头 `[gates.example]` 粘回上一行末尾 —— 复现 2026-09-18 施工中**真出过**的那次事故："
            "批量删行时替换把该行与下一行之间的换行一起吞了。YAML 时代该键成了上一行值的一部分，"
            "**这个 gate 从配置里整个消失**，而症状不是报错，是「看起来配好了」。"
            "迁到 TOML 后同一形态会让整份配置解析失败（症状更吵），但病根没变：仍是「表头必须独立成行」。"
            "★ 严格行扫描（只认 `^[gates.key]$`）会**漏掉**粘在值行后面的表头 —— 咬得住的是粘连正则。"
            "★ 这条变异治的是**编辑动作**而不是代码：只要以后还有人在 `harness.toml` 里批量删行，它就会复发。"},
    {"id": "M46-machine-keys-counts-empty-values", "file": "harness/_config.py", "kind": "exact",
     "old": '    return sorted(k for k in repos if k != "harness_self" and repos[k])',
     "new": '    return sorted(k for k in repos if k != "harness_self")',
     "lock": "test_repo_key_registry.py",
     "expect": "test_empty_value_is_not_a_machine_path",
     "why": "把 `_machine_keys` 退回批次 7 之前的口径（数「键在不在」而不是「值非空」）。"
            "这正是那次事故的**机制本身**：键名回到共享配置之后，「键在不在」恒为真 —— "
            "一份只填了 1 条的 local.yaml 会被判成「本机有机器路径」，于是另外 32 个留空的键"
            "被当成**拼错**而阻断，同事每次提交都被 fail-closed 拦住。"
            "★ 两条锁各咬一半，正好证明这个口径有两个面：单测那条问「空值算不算路径」，"
            "汇总那条问「『本机已填 N 条』里的 N 对不对」—— 后者是这条变异在**输出文本**上的痕迹。"},
    {"id": "M47-self-check-blocks-on-unfilled-key", "file": "checks/harness_self_check.py", "kind": "exact",
     "old": '        if repo_key not in repos:\n'
            '            _undeclared.append(name)',
     "new": '        if repo_key not in repos or not repos[repo_key]:\n'
            '            _undeclared.append(name)',
     "lock": "test_repo_key_registry.py",
     "expect": "test_self_check_passes",
     "why": "把「键在、本机没填值」也塞进 ❌ 那一支 —— 也就是把批次 2 的判据（本机有没有"
            "机器路径）换个写法搬回来。它看起来更严、更安全，实测后果是：**只配了自己那个仓的"
            "同事机上，另外 32 个 gate 全判 ❌ → harness_self rc=1 → 提交链 fail-closed rc=2，"
            "而他自己的代码一个字节都没被检查过**（拦在自检这一步）。"
            "★ 为什么这条锁必须端到端：⑤ 跑在子进程里，事故现场就是它；只测 `_config` 的函数"
            "只能证明「引擎知道这个键没填」，证明不了「谁读了它、得出的结论是什么」。"},
    {"id": "M48-validate-blocks-on-unfilled-key", "file": "harness/run_gate.py", "kind": "exact",
     "old": '        elif not rp:\n            norepo = True',
     "new": '        elif not rp:\n            issues.append("repo 未配置")   # mutation-smoke',
     "lock": "test_repo_key_registry.py",
     "expect": "test_validate_passes_and_still_shows_the_gap",
     "why": "把 `--validate` 的 ⊘（本机没配路径，不阻断）退回成 `repo 未配置` 阻断项 —— "
            "与 M47 是**同一条判据的另外半张脸**（一个管提交链最前面的自检，一个管配置体检，"
            "而 CI 用的是后者）。两者必须同时修对：只修 ⑤ 的话，同事的 CI 照样红。"
            "★ 它同时咬住「看得见」那一半：⊘ 是**不阻断但不静默**的唯一痕迹，退回阻断项之后，"
            "「未配的 gate 逐条列出来」这件事就没了（静默放行是本仓反复记过的形状）。"},
    {"id": "M56-stale-pyc-reuse", "file": "sandbox/mutation_smoke.py", "kind": "exact",
     "old": "    _purge_pycache(path)\n    return None",
     "new": "    return None  # mutation-smoke: 故意不清 __pycache__",
     "lock": "test_lock_inventory.py",
     "expect": "test_stale_bytecode_cannot_survive_a_mutation",
     "why": "删掉 `apply_mutation` 末尾那次 `__pycache__` 清理（2026-09-23 新加的接线）。"
            "★ 这条变异**改的是冒烟自己**，与 M49…M55 改规则本体不同：它咬的不是某条规则，"
            "而是**整份名单的可信度** —— 一旦上一条变异的字节码被复用，名单里同名的连号变异"
            "（M51/M52/M53 都期望 `test_r2_cases`）会**假绿**，而「全绿」看上去和真绿一模一样。"
            "★ 它是那条新锁的**阴性对照**：锁的第一向（绕过 `apply_mutation` 直接写 ⇒ 读到旧内容）"
            "证明坑真实存在，第二向（经 `apply_mutation` ⇒ 读到新内容）才是这条变异能咬到的接线。"
            "只有两向都在，这条锁才既不是空转、也不只是「记录了一个历史事实」。"},
    {"id": "M57-pretooluse-newfile-denied", "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '            if event == "PreToolUse" and not os.path.isfile(tf):\n',
     "new": '            if False and not os.path.isfile(tf):\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_new_file_is_allowed_through_pretooluse",
     "why": "评审 2026-09-23 M10：撤掉「PreToolUse + 文件不存在 → 放行」（新建场景）这一支。"
            "被编辑的文件在写之前当然不存在 → `--only` 一个都匹配不上 → auto-gate rc=2 → "
            "qoder / trae / codebuddy 的 PreToolUse deny 分支把**新建任何文件**都拒掉，"
            "理由还写成「门禁调用出错 rc=2（多半是配置问题，别当业务错）」。"
            "★ 这条咬的不是某条规则，而是**「新建文件」这个动作本身** —— 存量 0，真实语料里等不到，"
            "只能靠夹具构造出「文件尚不存在」这个状态（这正是它必须进名单的原因）。"
            "★ 它和 M58 是一对**反向钉**：M57 防「放行不够」（把新建全拒掉），"
            "M58 防「放行过头」（连 PostToolUse 的吵也吞掉）。两条共用同一个锚点，"
            "各钉一个方向 —— 只钉一向等于把「改宽还是改窄」交给运气。"
            "★ 三个客户端都实测表现一致（都 deny），所以锁写成 DENY_CLIENTS 循环而非只挑一家："
            "契约是三家各自读的，只钉一家等于替另两家背书。"},
    {"id": "M58-newfile-allowance-swallows-postedit",
     "file": "integrations/hooks/agentguard-gate.py", "kind": "exact",
     "old": '            if event == "PreToolUse" and not os.path.isfile(tf):\n',
     "new": '            if not os.path.isfile(tf):\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_missing_file_is_still_loud_after_the_edit",
     "why": "与 M57 同锚点、反方向：把放行从 `event == PreToolUse and not isfile(tf)` 扩成"
            "「任何事件下文件不存在都放行」。后果是 PostToolUse 撞到不存在的文件（M11 那种"
            "「路径形态不对」的典型症状）不再出 rc=2 —— 编辑期门禁从**响的故障**变成**哑的故障**，"
            "模型与人都再也看不到任何东西，而表面与「通过」完全一样。"
            "★ 注入实测：这条只让 PostToolUse 那条用例红，M10 的两条 PreToolUse 用例**照样绿**"
            "（放行本身没被撤）。这不是锁没打上，是这条变异的受害面本来就只在那里 —— "
            "正因为如此才需要 M57/M58 两条分钉，而不是合成一条。"},
    {"id": "M59-only-relative-path-not-normalized",
     "file": "integrations/hooks/agentguard-gate.py", "kind": "exact",
     "old": '            if not os.path.isabs(tf):\n                tf = os.path.join(cwd, tf)\n',
     "new": '            if False:\n                tf = os.path.join(cwd, tf)\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_existing_bad_file_still_denied_with_relative_path",
     "why": "评审 2026-09-23 M11 的钩子侧一半：撤掉 `--only` 的绝对化。`tf` 是宿主给的原始字符串，"
            "可能相对；此后**存在性判据**在钩子进程的 cwd（宿主启动目录）上求值，"
            "而引擎侧解析 `--only` 用的是 payload cwd —— 两边基准不同。"
            "★ 最坏形态不是「报错」而是**静默放行**：`os.path.isfile(tf)` 为假 → 走 M10 的"
            "PreToolUse 放行 → 违规文件既不查也不报，观感与「合规」一模一样（比不判更坏）。"
            "★ 夹具必须让两个 cwd 分家（`hook(cwd=ROOT)`），这条变异才有可观测差别："
            "两边 cwd 相同时它是**绿的**（判据等价，不是锁弱）—— 所以锁写的是"
            "「相对路径 + 钩子进程 cwd ≠ 被编辑仓」，而不是随便给个相对路径。"},

    {"id": "M60-enc-touch-truthiness",
     "file": "checks/encoding_ruleset.py", "kind": "exact",
     "old": 'getattr(ctx, "changed_lines", None) is not None',
     "new": 'bool(getattr(ctx, "changed_lines", None))',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_touch_is_three_state_not_truthiness",
     "why": "评审 2026-09-23 L1：把 ENC 的 touched 判据退回**真值**写法。三态里 `frozenset()` "
            "表「本次动过、只是没有新增行」（纯删除型改动），真值写法把它读成「没动过」→ "
            "legacy 仓走存量豁免报 INFO、rc=0 放行。★ 而这恰恰是编码事故最常见的载体："
            "编辑器保存是**整文件重写**，删一行也能顺手把整个文件存成 GBK —— 正是 ENC 立规时"
            "要拦的那件事。★ 变异只动一行、不带续行反斜杠，锚点原文对着源码抄即可。"},

    {"id": "M61-changed-lines-advance-on-failed-git",
     "file": "checks/rule_base.py", "kind": "exact",
     "old": '                    cur += 1                  # 上下文行推进（-U0 下罕见）\n'
            '        except FileNotFoundError:\n',
     "new": '                    cur += 1                  # 上下文行推进（-U0 下罕见）\n'
            '            got_info = True\n'
            '            if (r.stdout or "").strip():\n'
            '                saw_diff = True\n'
            '        except FileNotFoundError:\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_nonzero_rc_is_never_read_as_diff_information",
     "why": "评审 2026-09-23 L2 的**原缺陷形态**：`got_info`/`saw_diff` 在 try 末尾（rc 判断"
            "**之前**）也进位一次 —— 于是「git 失败但 stdout 里有东西」被读成「读到了 diff」，"
            "还把失败正文当 diff 解析进 `lines`；正文若是普通文本（无 `+` 行）则得到 "
            "`frozenset()`＝「本次一行没新增」⇒ 该文件存量违规**整套豁免生效**"
            "（错得比多报更隐蔽）。★ 变异是**加回**那两行（不是删），所以 old 是插入点、"
            "new 是插入后的样子。"},


    {"id": "M63-cli-last-zero-reads-everything",
     "file": "agentguard/cli.py", "kind": "exact",
     "old": '    if last is None or last <= 0:\n',
     "new": '    if False:\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_zero_and_negative_last_yield_no_records",
     "why": "评审 2026-09-23 L10：摘掉 `_read_jsonl` 的 N≤0 守卫 → `lines[-last:]` 里 "
            "`-0 == 0` ⇒ `--last 0` 退化成 `lines[0:]`，把**整本台账**倒出来；负值更离谱"
            "（`--last -5` → `lines[5:]`，丢掉开头 5 条、其余全返回）。台账本机可以几千条，"
            "这种「参数写 0、行为是全量」的错位在排查现场极难认出来（输出看着就是一份正常台账）。"},




    {"id": "M67-comment-spans-whole-line",
     "file": "checks/rule_base.py", "kind": "exact",
     "old": '    if not line:\n        return []\n',
     "new": '    if not line:\n        return []\n    return [(0, len(line))]\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_comment_spans_matrix",
     "why": "L4 判据的**地基**本身退化：`comment_spans` 一律「整行即注释」→ 三个站点同时回到"
            "原缺陷（字符串里的标记全部生效）。与 M64/M65/M66 是两路：那三条证明「站点没接上"
            "会被抓住」，这条证明「地基塌了」会被抓住 —— 判据的判据也要有牙齿。"},

    # ---------------------------------------------------------------- L11（评审 2026-09-23）
    # L11 是「**静默失效**」那一族里最隐蔽的一条：钩子命令起不来时，客户端那侧连一行日志都没有，
    # 而体检器当时是**全绿**的。修法分三处：体检器学会看**解释器**（L11-a）、钩子不许被写坏的
    # 环境变量弄崩（L11-b）、也不许把会话卡在"等宿主写完 stdin"上（L11-c）。下面五条是这五处
    # 判据的**牙齿** —— 每条都问"把这段改回去，有没有锁真的红"。
    {"id": "M68-interpreter-not-checked",
     "file": "sandbox/check_client_configs.py", "kind": "exact",
     "old": '        ifg = _interpreter_finding(c)\n'
            '        if ifg:\n'
            '            (problems if ifg[0] == "bad" else warns).append("%s %s" % (where, ifg[1]))\n',
     "new": '        ifg = None\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_missing_interpreter_is_bad",
     "why": "L11 的主判据整个脱钩（函数还在、判据不接了 —— 本仓栽过多次的「机制在、判据不在」）："
            "解释器消失了体检器照样报 ✅，而钩子其实根本起不来。★ 这条**只能**用"
            "`test_missing_interpreter_is_bad` 当 expect，不能用 `..._is_ok` 那一族："
            "脱钩后「正常配置」的结论仍是 ok，拿它当牙齿等于挑了一条**两边都绿**的用例。"},
    {"id": "M69-naive-tokenizer",
     "file": "sandbox/check_client_configs.py", "kind": "exact",
     "old": '    out, cur, q = [], "", False\n'
            '    for ch in cmd:\n'
            '        if ch == \'"\':\n'
            '            q = not q\n'
            '            continue\n'
            '        if ch.isspace() and not q:\n'
            '            if cur:\n'
            '                out.append(cur)\n'
            '                cur = ""\n'
            '            continue\n'
            '        cur += ch\n'
            '    if cur:\n'
            '        out.append(cur)\n'
            '    return out\n',
     "new": '    return cmd.replace(chr(34), " ").split()\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_quoted_path_with_space_is_not_a_false_alarm",
     "why": "切词退回**朴素**写法（`_tokens` 就是为这个才引入的）：路径含空格时"
            "`\"C:/Program Files/…/python.exe\"` 被切成两半，判据于是把一个**存在**的解释器判成"
            "不存在 —— 假红。★ 期望那条锁必须断言 `lvl == \"ok\"`：若写成 `assertNotIn(\"bad\", …)`，"
            "朴素切法只会把它退成 ⚠（不是 ❌），**这条变异就滑过去了**（假红被写在断言里的漏洞）。"},
    {"id": "M70-split-command-args",
     "file": "sandbox/check_client_configs.py", "kind": "exact",
     "old": '        cmd, args = node.get("command"), node.get("args")\n'
            '        combined = isinstance(cmd, str) and isinstance(args, list)\n'
            '        if combined:\n'
            '            out.append((path + "/command+args",\n'
            '                        cmd + " " + " ".join(str(x) for x in args)))\n'
            '        for k, v in node.items():\n'
            '            if combined and k in ("command", "args"):\n'
            '                continue\n',
     "new": '        for k, v in node.items():\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_split_command_and_args_is_judged",
     "why": "ZCode 那种「命令被拆成 `command`=解释器 + `args`=[脚本,客户端,事件]」的形态不再合成："
            "两半各自都判不出「这条命令整体能不能起来」（args 那半看着就是脚本路径、command 那半又不含"
            "门禁关键字而进不了判据）→ **整整一家客户端的判据静默消失**。这正是 L11 修复前 zcode 的"
            "真实状态：这条变异复现的就是原缺陷本身。"},
    {"id": "M71-env-int-crash",
     "file": "integrations/hooks/agentguard-gate.py", "kind": "exact",
     "old": 'TIMEOUT = _env_positive_int("HARNESS_HOOK_TIMEOUT", 180)\n',
     "new": 'TIMEOUT = int(os.environ.get("HARNESS_HOOK_TIMEOUT", "180"))\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_garbage_timeout_does_not_crash_the_hook",
     "why": "回到 L11 修复前的**模块级裸 int()**：`HARNESS_HOOK_TIMEOUT=3m` 这类手滑会让钩子在"
            "import 期就抛 ValueError。后果不对称得可怕 —— 不是「超时配错了」，而是**七个客户端的"
            "编辑期门禁全部失效**，用户看到的现象只是「改了下配置，门禁就不响了」。"},
    {"id": "M72-no-stdin-watchdog",
     "file": "integrations/hooks/agentguard-gate.py", "kind": "exact",
     "old": '    done = _stdin_watchdog(STDIN_TIMEOUT)\n'
            '    try:\n'
            '        return sys.stdin.read() or ""\n'
            '    except Exception:\n'
            '        return ""\n'
            '    finally:\n'
            '        done.set()               # 读完了就撤掉看门狗（后面跑门禁有自己的 TIMEOUT）\n',
     "new": '    try:\n'
            '        return sys.stdin.read() or ""\n'
            '    except Exception:\n'
            '        return ""\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_stdin_left_open_does_not_hang",
     "why": "`_read_stdin_text` 退回裸 `sys.stdin.read()`：宿主把管道留在打开状态时（GUI 客户端"
            "异常退出 / Trae 那层 PowerShell 包装 / 有人手跑）**整个会话就卡死在这里**，而 TIMEOUT"
            "只包得住 subprocess 那一段、管不到读 stdin。★ 与本文件既有的 subprocess 超时锁是两回事，"
            "别混：那条证明「跑门禁不会拖死会话」，这条证明「**等宿主写 JSON** 也不会拖死会话」。"},
    {"id": "M73-watchdog-never-cancelled",
     "file": "integrations/hooks/agentguard-gate.py", "kind": "exact",
     "old": '    finally:\n'
            '        done.set()               # 读完了就撤掉看门狗（后面跑门禁有自己的 TIMEOUT）\n',
     "new": '    finally:\n        pass                     # 变异：不取消看门狗\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_watchdog_is_disarmed_after_a_normal_read",
     "why": "M72 的**正对面**，也是这一族里更阴的一半：stdin 正常写完而看门狗不撤 → 钩子跑完了、"
            "结论还没打印就被自己的看门狗 `os._exit(0)` 杀掉 → 客户端收到 exit 0 当成**放行**，"
            "用户看到的只是「门禁忽然不管用了」。卡死至少看得见，这个看不见。"
            "★ 这条是**反方向注入量出来的缺口**、不是推演：2026-09-23 把这一行改成 `pass` 之后跑"
            "全严格档，M72 那三条锁**一条都没红**（它们全在问「没看门狗会不会卡死」，没有一条问"
            "「看门狗撤没撤」）。补上 `test_watchdog_is_disarmed_after_a_normal_read` 之后，"
            "同一注入恰好红在那一条上（rc=1、失败清单只此一条）。这正是「双向」纪律要的："
            "只证「回退成原缺陷会被抓住」不够 —— 判据**收过头**同样是坏，而且是更不容易被发现的那种。"},

    # ---------------------------------------------------------------- L5（评审 2026-09-23）
    # L5 的 F3/F6 那一半：R12 的两个 helper 原先返回「**首个**命中行」。危险之处在于它和
    # 按行豁免**叠乘**：存量文件里已有一处命中（存量、不在 changed_lines 里），开发时又添第二处
    # → 判据仍锚第一处 → 整条结论被按行豁免丢掉 → **新写的那处静默通过**。而「往 Visual/ 文件里
    # 新写一个 [Table(」正是 R12 最想拦的那一幕（见 test_entity_standards 里 F3 锚点那条锁）。
    # ★ 这里原本还登记了 `M76-f3-line-fallback-to-1`（把原实现无命中时兜底 `return 1` 请回来，
    # 期望 `test_no_occurrence_reports_nothing` 红）。**真跑冒烟时被判 FAIL「变异后仍然绿」**
    # —— 冒烟是对的，这条变异**今天打不中任何锁**：F3 分支先过 `has_table_attr` 闸门，而
    # `_r12_table_attr_lines` 与 `_r12_has_table_attribute` 同用一条正则、同扫正文（该正则不含
    # 空白/换行，跨行与否等价）⇒「闸门为真但命中行为空」是不可达组合，`or [1]` 那半永远不执行。
    # 于是**删掉这条**而不是留着：一条打不中的变异留在「牙齿名单」里，只会把这份名单训成噪音
    # （正是 `run()` 的 docstring 在防的那种）。那条锁本身保留 —— 它守的是 helper 的**空列表契约**
    # （谁把无命中改回"兜底锚第 1 行"，会同一次改动动到闸门才会显形），锁的 docstring 里已写明
    # 它当前**不可被单点变异证伪**，别再把它读成"已证有牙齿"。

    # ------------------------------------------------- L7/L9（评审 2026-09-23，MCP 壳层）
    # 这两条的主题是**错误归因**：坏输入被壳层兜成「服务端自己的意外」(-32603)，
    # 或干脆不做契约校验（`repo` 只验 isdir）—— 门禁把错因说得越像自己的毛病，
    # 越会被当成环境噪声绕过去。五条变异覆盖四个判据点 + 一个"判据收过头"的方向。
    {"id": "M77-l7-name-no-typecheck",
     "file": "agentguard/mcp.py", "kind": "exact",
     "old": '    if name is not None and not isinstance(name, str):\n',
     "new": '    if False:  # 变异：撤掉 name 类型校验（回到 L7 原缺陷）\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_non_string_name_is_invalid_params",
     "why": "L7 的原缺陷形态：`name: [1,2]` 让 `name not in _HANDLERS` 抛 `TypeError: unhashable "
            "type: 'list'`，被 `handle_tools_call` 外层的兜底 except 收成 **-32603 Internal error**"
            "（=「服务端自己出了意外」，宿主的合理反应是重连/停用这个 server），而真相是宿主自己"
            "传了个列表 ⇒ 该报 **-32602**。★ 期望的那条锁必须断言**错误码**，不能只断言「有错」："
            "两种归因下 isError 都是真，只有码能分开。"},
    {"id": "M78-l7-params-no-typecheck",
     "file": "agentguard/mcp.py", "kind": "exact",
     "old": '    if not isinstance(params, dict):\n',
     "new": '    if False:  # 变异：撤掉 params 类型校验（回到 L7 原缺陷）\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_non_dict_params_is_invalid_params",
     "why": "同族：`params` 是列表/标量时，`params.get` 直接 `AttributeError` —— 连 -32603 都"
            "不是「渲染出来的」，而是靠外层兜底接住的偶然而已。"},
    {"id": "M79-l7-arguments-no-typecheck",
     "file": "agentguard/mcp.py", "kind": "exact",
     "old": '    elif not isinstance(args, dict):\n',
     "new": '    elif False:  # 变异：撤掉 arguments 类型校验（回到 L7 原缺陷）\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_non_dict_arguments_is_invalid_params",
     "why": "第三个判据点，**形状与前两个不同**（值得单列）：`arguments: \"abc\"` 崩在 **handler 里**"
            "（`a.get(...)`），被 `handle_tools_call` 的 `except Exception` 接成 **正文**"
            "（`isError=true` 的一段文字），**连 JSON-RPC 错误对象都没有** —— 调用方拿到的是一段"
            "人话，程序判不出「我参数传错了」。锁因此断言的是返回结构（`__rpc_error__` 的码），"
            "不是文案。"},
    {"id": "M80-l9-repo-any-directory",
     "file": "agentguard/mcp.py", "kind": "exact",
     "old": '        if not os.path.exists(os.path.join(rp, ".git")):\n',
     "new": '        if False:  # 变异：撤掉 L9 的「得像仓库」判据\n',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_plain_directory_is_rejected_without_spawning_the_engine",
     "why": "L9 原缺陷：`repo` 支只验 `isdir` ⇒ 任意目录（`D:/`、别的项目的根）都能被交给引擎"
            "去走一遍，而 inputSchema 里写的契约是「a repository directory」。锁判两件事："
            "① 拒绝了；② **没起子进程** —— 只判 ① 的话，一个「先起进程再拒绝」的实现照样全绿，"
            "而扫目录的成本已经付掉了。"},
    {"id": "M81-l9-dot-git-must-be-a-dir",
     "file": "agentguard/mcp.py", "kind": "exact",
     "old": 'os.path.exists(os.path.join(rp, ".git"))',
     "new": 'os.path.isdir(os.path.join(rp, ".git"))',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_worktree_style_dot_git_file_still_passes",
     "why": "**反方向**（判据收过头）：M80 只证明「撤掉判据会被抓住」，证明不了判据本身不误伤。"
            "worktree 的 `.git` 是**文件**（本机受管仓里就有 12 个这种 worktree、外加 submodule），"
            "`exists`→`isdir` 一字之差就会把它们整批误拒 —— 这种假红比原缺陷更难被发现，"
            "因为它长得像「这条安全校验在正常工作」。"},

    # ------------------------------------------------- M12（评审 2026-09-23，git 传感器 fail-open）
    # 主题：同一个 `None` 底下藏着两件相反的事（"文件本次没动" / "git 读不到"），
    # 而 legacy 降级判据只看"None ⇒ 存量" ⇒ 传感器故障被读成"整文件都是存量"，门**静默放宽**。
    # 五条变异：两条撤判据（故障位丢了 / 档位不认了）、一条把两态并回去、
    # 两条**反方向**（故障位恒生效 / 把"不是仓库"也当故障）—— 后两条守的是"别修成假红"。
    {"id": "M82-sensor-failclosed-switch-ignored",
     "file": "checks/rule_base.py", "kind": "exact",
     "old": '    return (os.environ.get("HARNESS_GIT_REQUIRED") or "").strip() in ("1", "true", "yes", "on")',
     "new": '    return False  # 变异：撤掉 fail-closed 档（档开了也当没开）',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_fail_closed_tier_stops_the_downgrade",
     "why": "M12 原缺陷的半边：档位形同虚设 ⇒ `HARNESS_GIT_REQUIRED=1` 也拦不住降级 —— "
            "用户以为拧紧了，实际还是 fail-open。★ 这一条**只**打这一个点："
            "`git_required()` 返回 False 之后，缺省档那三条断言照旧通过（它们本就期望降级）。"},
    {"id": "M83-sensor-flag-always-on",
     "file": "checks/rule_base.py", "kind": "exact",
     "old": '    return git_required() and bool(getattr(ctx, "git_sensor_failed", False))',
     "new": '    return bool(getattr(ctx, "git_sensor_failed", False))  # 变异：故障位恒生效（档位失效）',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_default_tier_keeps_every_downgrade_site_unchanged",
     "why": "**反方向**（修过头，且是这次最容易踩的一个）：把故障位做成恒生效，"
            "**所有机器**（不只开了档的）都会在 git 抽风时被存量违规拦死 —— 借一次修复偷偷"
            "改掉所有人的判定。★ 这条变异跑下来其它 M12 锁**全绿**（都期望「更严」），"
            "只有这条 companion 会红；没有它，这种越权修改在测试里完全看不出来。"},
    {"id": "M84-untouched-file-read-as-failure",
     "file": "checks/rule_base.py", "kind": "exact",
     "old": '    if not saw_diff:\n        return None, False',
     "new": '    if not saw_diff:\n        return None, True  # 变异：把「文件没动」也报成传感器故障',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_untouched_file_in_a_real_repo_is_not_a_sensor_failure",
     "why": "把三态收口处**重新并成一态**（这正是 M12 修之前的样子，只是换了个位置）。"
            "后果不是「少报」而是**假红海**：`cli.py check <某文件>` 是存量体检，"
            "被体检的文件当然没有 diff —— 若那也算故障，一开档整个仓的历史违规全变 ❌。"},
    {"id": "M85-probe-failure-swallowed",
     "file": "checks/rule_base.py", "kind": "exact",
     "old": '        return False, True\n    ok = r.returncode == 0',
     "new": '        return False, False  # 变异：探针故障混进「这里不是 git 仓」\n    ok = r.returncode == 0',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_git_binary_missing_is_a_sensor_failure_not_a_plain_directory",
     "why": "比 M12 正文描述的那条**更静默**的形态（本次实测发现的同族洞）：git 不在 PATH 时"
            "`_is_git_worktree` 的 except 把探针故障吞成 False，于是 `compute_changed_lines` "
            "连 `_warn_git_once` 都不会走 —— 用户连「告警」这一档都拿不到。"
            "★ 判据要点：`rc != 0` 是 git **明确回答「不是工作树」**（合法输入），"
            "异常才是「读不到」；两者必须分档。"},
    {"id": "M86-not-a-repo-counted-as-failure",
     "file": "checks/rule_base.py", "kind": "exact",
     "old": '    return ok, False\n\n\ndef _is_git_worktree(repo: str) -> bool:',
     "new": '    return ok, True  # 变异：非 git 目录也算故障\n\n\ndef _is_git_worktree(repo: str) -> bool:',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_a_plain_directory_is_still_legitimate_input",
     "why": "**反方向**（与 M85 成对）：把「明确不是仓库」也当成传感器故障 ⇒ "
            "`cli.py check <任意目录>`（**合法**用法：对非受管目录体检）在开档后全变假红。"
            "M85 与 M86 各守一端，中间的判据线就是「谁在回答」—— git 回答了 / git 没答上来。"},
    # ★ 本组是语料里**第一条改 `.md` 的变异**（其余 78 条都是 `.py`）：`apply_mutation` 只做
    #   读-替换-写、不认后缀，所以机制上本来就支持；登记它是因为 L18 那一类**复发过三次**。
    # L15 的两半（代码 + 那段临时说明）已在 `0.1.7-dev` **同一次提交里一起落地**，锁盯着它们同真同假：
    # 《安装说明》§5.1 的 ⚠ 与「演练支还丢不丢 `fails`」。
    # ⚠ 2026-09-23 夜（踩到自己第三次的老病）：**本变异原先的 `old` 就是被第⑦条删掉的那句话**
    # —— 第⑦条写的就是「删掉这段说明」，删完锚点在产品文件里命中 0 次 ⇒ **变异根本施加不上**
    # （冒烟按失败计：等于没测）。所以**证伪方向随状态翻了**：现在的当前态是「代码已修 + 说明已删」，
    # 能把它变红的动作从「删说明」变成「把旧口径说明搬回来」。
    {"id": "M88-l15-install-rehearsal-note-restored",
     "file": "docs/packaging/INSTALL.md", "kind": "exact",
     "old": "> **演练的退出码现在可信了**（2026-09-23 修，评审 L15）：演练支与 `--yes` 支**同一判据** ——",
     "new": '> ⚠ **演练（不带 `--yes`）只告诉你"将做什么"，<u>不</u>告诉你"能不能成"**'
            '（2026-09-23 实测，评审 L15）：',
     "lock": "test_review_2026_09_23.py",
     "expect": "test_the_code_and_the_install_note_agree",
     "why": "L15：`sandbox/upgrade.py::main` 的演练支原先把 `fails` 丢了（同一份失败清单，演练 rc=0、"
            "`--yes` rc=1），而它随包分发、与仓库逐字节对拍，当时改不了 ⇒ 改成在《安装说明》里"
            "挂一段临时 ⚠ 说清「演练只看 ✗ 行、别看退出码」。代码与说明已在 `0.1.7-dev` 同批改完"
            "（说明按清单第⑦条整段删掉），本变异把**那段说明的首行按旧口径搬回来** —— 文档又在描述"
            "一个已经不存在的毛病，锁此时必须红（`discards=False` 而 `note=True`，正是 `assertFalse` 那支）。"
            "★ 只换首行即可：锁认的是**哨兵句这个子串**在不在文档里，那正是「口径退回去了」的最小可观测形态。"},
    # L19 的定性句：它是"用法处的一句话"，而缺席的后果是**误导**（把人引到"没声明就跑不了"
    # 的错觉上）。本变异把 cli 输出里那句抹掉 —— README / RUNBOOK 里还有，所以只有
    # "命令输出"那一条锁该红，正是要证明三处是**各量各的**。
    {"id": "M89-l19-bypass-gate-wording-dropped",
     "file": "agentguard/cli.py", "kind": "exact",
     "old": "，不是权限闸 —— 它不拦任何提交",
     "new": " —— 它不拦任何提交",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_it_says_so_where_people_read_it",
     "why": "L19：`bypass` 是**声明留痕入口**，不是权限闸 —— 这句定性原先只活在实现里、"
            "没写在用法处。评审的定性建议没有缺陷可修（实现与 RUNBOOK 一致、从无虚假承诺），"
            "缺的就是这句话，而它的缺席是有害的：`--no-verify` 本来就跑得通，"
            "缺了这句反而会让人以为「没声明就跑不了」（真相是「跑了没人知道」）。"},
    # ── L6 / L8 / L12：这三条**落地时没有配锁**，是事后清点时补的 ──────────────────────
    # （其余低项都随修随锁；这三条只有代码/文档面上的改动，`tests/` 里一条锁都没有 ——
    #  「已登记」≠「已验证」的现场：修复是真的，但没有任何东西拦着它复发。）
    # L6 的四条变异各打一支分支。★ M92/M93 分两条不是凑数：`_git_out` 里那两个 127 走的是
    # **不同的 except 分支**。第一版只写了一条用例，注入把 `return 127` 改成 0 时**全绿**
    # —— 打中的是一条永不执行的行。
    # ★★ 2026-09-24（M92 在 CI 上变绿、本机全红）：这里原先记的是「cwd 坏掉抛
    #   `NotADirectoryError`（Windows 实测）」—— 那**只是一半**，而且正是"夹具写死了开发机
    #   状态"的源头。两平台实测：
    #       cwd=不存在的目录 → Windows `NotADirectoryError`（第二支）／Linux `FileNotFoundError`（第一支）
    #       cwd=一个文件     → 两平台都 `NotADirectoryError`（第二支）
    #   于是那条用例在 Linux 上跑的是**第一支** ⇒ 第二支没有任何路径到达 ⇒ 本变异后仍然绿。
    #   夹具已改用「文件当 cwd」并顺带断言文案（见
    #   `test_review_2026_09_23.py::test_git_that_cannot_start_is_127_not_a_crash`）。
    #   教训与 `c2b6e1b` 同族：**夹具依赖的运行时事实，先问一句「这在另一个平台成立吗」。**
    {"id": "M90-l6-timeout-labeled-as-a-block",
     "file": "agentguard/cli.py", "kind": "exact",
     "old": "        return (2, \"\", \"✗ 子进程超时",
     "new": "        return (1, \"\", \"✗ 子进程超时",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_a_timed_out_child_is_rc2_not_a_crash",
     "why": "L6：超时归 1 = 与「门禁拦截」同码 —— 一个卡住的钩子会被读成「代码不合规」，"
            "而 `if rc==1 then 修代码` 是所有人的第一反应。基础设施故障不许冒充业务结论。"},
    {"id": "M91-l6-bad-cwd-labeled-as-a-block",
     "file": "agentguard/cli.py", "kind": "exact",
     "old": "        return (2, \"\", \"✗ 起不了子进程",
     "new": "        return (1, \"\", \"✗ 起不了子进程",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_a_missing_cwd_is_rc2_not_a_crash",
     "why": "L6：cwd 不存在时归 1 —— 同一种归错码（环境问题冒充门禁拦截）。"},
    {"id": "M92-l6-git-cannot-start-reports-success",
     "file": "agentguard/cli.py", "kind": "exact",
     "old": "        return 127, \"\", \"git 起不来",
     "new": "        return 0, \"\", \"git 起不来",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_git_that_cannot_start_is_127_not_a_crash",
     "why": "L6：git 起不来却报 rc=0 —— 调用方（hooks status/install/uninstall、"
            "read_prev_hookspath）全按 `rc != 0` 分支理解结果，rc=0 等于**谎报「查到了」**。"},
    {"id": "M93-l6-git-missing-reports-success",
     "file": "agentguard/cli.py", "kind": "exact",
     "old": "        return 127, \"\", \"找不到 git",
     "new": "        return 0, \"\", \"找不到 git",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_git_absent_from_path_is_127_not_a_crash",
     "why": "L6：`FileNotFoundError` 那一支（机器上没装 git）—— 与 M92 **不是同一支**，"
            "见上面那段说明：这两条必须分开打，否则其中一条打的是永不执行的行。"},
    {"id": "M94-l6-hooks-install-skips-isdir",
     "file": "agentguard/cli.py", "kind": "exact",
     "old": "        if not os.path.isdir(repo):\n            print(\"repo 不存在: %s\" % repo)\n            return 2\n        b = _bash()",
     "new": "        b = _bash()",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_hooks_install_refuses_a_missing_repo",
     "why": "L6：`hooks install` 原先没有 isdir 校验（uninstall 分支早就有）——"
            "`setup --repo <不存在的目录>` 一路走到 subprocess 的 cwd 才崩。注意判据钉的是"
            "**拒绝发生在起子进程之前**（`_record_op` 在校验之后 ⇒ 被拒的那次不落台账）。"},
    {"id": "M95-l8-mcp-actually-goes-concurrent",
     "file": "agentguard/mcp.py", "kind": "exact",
     "old": "def serve():", "new": "import threading\ndef serve():",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_the_claim_and_the_code_are_same_true_same_false",
     "why": "L8：给壳真上一个并发原语，而 `INSTRUCTIONS`/docstring 里还写着「单线程串行」"
            "—— 文档随即变成过期件，而它会让人**不去查**真因（宿主超时/重连）。"
            "锁做成「承诺与事实同真同假」：真并发化**且**把承诺改成并发描述 → 照样绿。"},
    {"id": "M96-l8-host-facing-promise-deleted",
     "file": "agentguard/mcp.py", "kind": "exact",
     "old": "SINGLE-THREADED and strictly serial",
     "new": "SINGLE-PROCESS and strictly serial",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_the_host_facing_copy_is_the_one_that_counts",
     "why": "L8：承诺只写在模块 docstring 里（维护者读的）而宿主读的 `INSTRUCTIONS` 没有"
            "—— 等于没告诉当事人。宿主只会把「排队」读成「服务死了」。"
            "★ 改这句话而不是删整段：删整段会连 `harness_doctor` 的举例一起没，"
            "那就分不清是「没说清串行」还是「没说清哪个工具长」了。"},
    {"id": "M97-l12-trae-readme-reverts-to-quoted-form",
     "file": "integrations/trae-cn/README.md", "kind": "exact",
     "old": "\"command\": \"<PYTHON> <HARNESS_SHIM> trae; exit $LASTEXITCODE\"",
     "new": "\"command\": \"\\\"<PYTHON>\\\" \\\"<HARNESS_SHIM>\\\" trae; exit $LASTEXITCODE\"",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_trae_readme_block_equals_the_shipped_example",
     "why": "L12/M8：README §5.3 那段「可直接抄」退回加引号旧形态 —— 而这段已是 M9 那条锁"
            "盯着的出货样例的**副本**。判据不 grep「作废」两个字（改措辞就断），"
            "而是把 README 的 JSON 块**逐字对拍**出货样例。"},
    {"id": "M98-l12-verify-integrations-header-drifts",
     "file": "sandbox/verify_integrations.sh", "kind": "exact",
     "old": "#           / codebuddy / zcode / qoder",
     "new": "#           / zcode / qoder",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_verify_integrations_header_agrees_with_its_own_loop",
     "why": "L12：头注释把 codebuddy 从「强制型」名单里抹掉，而下面 for 循环照样按 yes 真跑它"
            "—— 原缺陷的形态就是这个（注释说「未跑只标 ⚠」、代码早按强制型跑），**说反了两个月**。"
            "写在注释里的那句「两处一起改」拦不住任何人；这条锁让它们对拍。"},
    {"id": "M99-l12-qoder-example-command-malformed",
     "file": "integrations/qoder-cn/settings.example.json", "kind": "exact",
     "old": "\"command\": \"\\\"<PYTHON>\\\" \\\"<HARNESS_SHIM>\\\" qoder PostToolUse\"",
     "new": "\"command\": \"<PYTHON> <HARNESS_SHIM> qoder PostToolUse\"",
     "lock": "test_review_2026_09_23.py",
     "expect": "test_qoder_readme_block_equals_the_shipped_example",
     "why": "L12/M9：出货样例的命令退回与 README §5.3 不一致的形态 —— 照它抄会拼出"
            "指向不存在的钩子路径的命令。判据同样是对拍（README 块 vs 样例文件）。"},
    {"id": "M102-only-space-form-ignored-again", "file": "sandbox/mutation_smoke.py",
     "kind": "exact",
     "old": '        if _a == "--only":\n'
            '            # 末尾不带值 ⇒ 造一个空过滤交给下面的守卫报错；**不静默退回全量**（那正是本次要修的）\n'
            '            _rest = sys.argv[2 + _i:]\n'
            '            only = _rest[0].split(",") if _rest else [""]\n'
            '            break\n',
     "new": '        if _a == "--only":\n'
            '            break  # mutation-smoke: 退回「只认等号」，空格写法静默忽略\n',
     "lock": "test_lock_inventory.py",
     "expect": "test_only_filter_is_never_silently_ignored_or_widened",
     "why": "把 `--only` 的解析退回**只认等号**（2026-09-24 修复前的原状）：等号写法照旧，"
            "**空格写法静默忽略** —— 而模块头的用法行写的正是空格写法，照它敲就变成跑全量"
            "（当时 95 条，约四十分钟；条数会随名单增长，这里只记当时的值）。"
            "★ 与 M103 是同一件事故的**两半**，必须分开登记：这一半是**选择被放宽**（想跑 1 条、"
            "跑了 95 条，约四十分钟），M103 那一半是**选择被清空却报绿**。两者的观感完全不同、"
            "排查方向也相反（一个去查“为什么跑了这么久”，一个去查“为什么这么快就绿了”），"
            "合成一条变异就只剩一个方向能被验。"
            "★ 它咬的是**提示词之外**的东西：那次是照着本文件自己的用法行敲的，用法行是散文，"
            "没有任何测试看得住它 —— 所以新锁把用法行示范的写法也拉进判据（见该用例末段）。"},
    {"id": "M103-only-empty-filter-reports-green", "file": "sandbox/mutation_smoke.py",
     "kind": "exact",
     "old": "    if only and not muts:\n",
     "new": "    if False and only and not muts:  # mutation-smoke: 空过滤照样往下跑\n",
     "lock": "test_lock_inventory.py",
     "expect": "test_only_filter_is_never_silently_ignored_or_widened",
     "why": "撤掉那条空过滤守卫（把它变成死代码，保留原块以便逐字对照）：`--only` 选不中任何一条时"
            "不再报错，而是**照常往下跑**，最后打印「0 个变异：命中 0」并**返回 0** —— 与「跑过了、"
            "全绿」在观感与退出码上都没有区别。现场是我自己把完整 id 写成短 id `M92`。"
            "★ 它与本文件里已经记了三遍的那个病同源（`discover` 匹配不到模块 → `Ran 0 tests … OK` "
            "→ rc=0 被读成绿），只是入口换成了过滤器：**空结论不得报成绿**。前三处挡的是「没跑成」，"
            "这一处挡的是「没让你跑」—— 所以它不能被前三条`_ran_nothing`覆盖，得单独一条。"
            "★ 与 M102 成对：那半是“选择被放宽”，这半是“选择被清空却报绿”（见 M102 的 why）。"},
    {"id": "M104-cli-corrupt-anchor-read-as-absent", "file": "agentguard/cli.py",
     "kind": "exact",
     "old": '        return None, "corrupt"         # ★ 有内容但尾行不是 JSON —— 不许当成"没有锚点"',
     "new": '        return None, "absent"',
     "lock": "test_gate_log_anchor.py",
     "expect": "test_corrupt_anchor_tail_is_red_and_named",
     "why": "把读侧三态退回**两态**（2026-09-30 修复前的原状）：`corrupt` 并回 `absent`。"
            "后果不是「少一条提示」，而是**锚点这半边防线静默消失** —— 保留链合法的前缀、"
            "砍掉台账尾巴（前缀保留的截断，行内链自己看不出来：每一行都还是合法的），"
            "再往锚点尾行追加**一个字节**，`gate log --verify` 就退回「尚无锚点」并 **rc=0**。"
            "而抓截断/重写正是锚点唯一的存在理由 —— 报绿的恰好是它唯一的职责。"
            "★ 同一处缺陷在 `harness/coverage.py` 另有一个**副本**（M105）：两处是各自独立的读侧，"
            "只改一处时另一处照旧把「读不到」说成「还没锚」，所以必须分开登记、分开咬。"},
    {"id": "M105-coverage-corrupt-anchor-read-as-absent", "file": "harness/coverage.py",
     "kind": "exact",
     "old": '    except ValueError:\n        return None, "corrupt"\n'
            '    if not isinstance(rec, dict):\n        return None, "corrupt"',
     "new": '    except ValueError:\n        return None, "absent"\n'
            '    if not isinstance(rec, dict):\n        return None, "absent"',
     "lock": "test_gate_log_anchor.py",
     "expect": "test_coverage_quick_distinguishes_corrupt_from_absent",
     "why": "M104 那处缺陷在**引擎侧的副本**（`coverage --quick` 的读侧）：同样把 `corrupt` 并回 "
            "`absent`。它不拦人（rc 恒 0），但这份报告存在的全部价值就是**把「没响」与「没跑」"
            "分开** —— 把损坏显示成「尚无锚点」，等于让人以为只是还没锚定，而锚点其实已被动过。"
            "★ 两处是各自独立的复制实现（不走同一个函数，只靠 tests 对拍咬住），"
            "所以「改了一处」推不出「另一处也改了」—— 与 M104 必须分开登记。"},
    {"id": "M106-anchor-appends-on-corrupt-tail", "file": "agentguard/cli.py",
     "kind": "exact",
     "old": '    prev, pstate = _anchor_probe()\n    if pstate == "corrupt":',
     "new": '    prev, pstate = _anchor_probe()\n    if False:',
     "lock": "test_gate_log_anchor.py",
     "expect": "test_anchor_refuses_to_append_on_corrupt_tail",
     "why": "撤掉**写侧**的坏尾行守卫（读侧留着，所以 M104 的锁不会替它红）：尾行已经坏了还往上"
            "追加新锚点，新锚点的 `prev` 会被记成「无前锚」—— 锚点之间的接续断在这里且**不留痕**"
            "（此后 `--verify` 只看得到新锚点，断口正好落在它背后）。"
            "★ 这是「读侧挡不住」的一类：把写侧弄坏，读侧只会照着被弄坏的输入给出**看起来正确**的"
            "结论 —— 所以它必须自己一条（与 M104 同文件、方向相反）。"},
    {"id": "M107-redirect-targets-for-all-clients",
     "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '    pats = list(_CMD_PATH_PATTERNS)\n'
            '    if client == "codex":\n        pats.append(_CMD_REDIRECT_PATTERN)',
     "new": '    pats = list(_CMD_PATH_PATTERNS) + [_CMD_REDIRECT_PATTERN]',
     "lock": "test_entry_level.py",
     "expect": "test_redirect_targets_are_codex_only",
     "why": "把重定向目标**放开给所有客户端**（2026-09-30 修复前的原状）：`… 2>/dev/null`、"
            "`… > /tmp/t.log` 这类**只读**命令的重定向目标被当成「被编辑的文件」→ "
            "`--only /dev/null` → auto-gate 一个都匹配不上 → **rc=2 fail-closed** → "
            "每一条带重定向的命令都往模型回灌「harness 门禁调用出错 rc=2（多半是配置问题）」。"
            "模型于是被反复教「门禁坏了」，而真毛病在**提取**这一步、不在配置"
            "（现场：ZCode 会话里那行提示是常驻注入的）。"
            "★ 与 M109 是**两道独立防线**（客户端门 + 仓外守卫）：各自单独撤掉时端到端那条锁"
            "**都还是绿的**（另一道在兜）—— 所以「哪一处被改坏」只能靠各自这条单元锁分辨。"},
    {"id": "M108-redirect-pattern-swallows-operator",
     "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '_CMD_REDIRECT_PATTERN = r"""(?:>>|>)\\s*[\'"]?([^\\s\'";|&>]+)"""',
     "new": '_CMD_REDIRECT_PATTERN = r"""(?:>|>>)\\s*[\'"]?([^\\s\'";|&]+)"""',
     "lock": "test_entry_level.py",
     "expect": "test_redirect_pattern_does_not_swallow_the_operator",
     "why": "把重定向正则退回「`(?:>|>>)` + 字符类不排除 `>`」—— 2026-09-30 与 M107 同批发现的"
            "**另一个**缺陷：正则里 `>` 先于 `>>` 匹配，于是 `echo x >> src/a.vue` 里第一个 `>` "
            "被当成操作符、剩下的那个 `>` 被当成**文件名** → `target_file` 返回 `'>'` → "
            "`--only <仓>/>` → 又是同一套 rc=2 假警报。实测三例全中（`>>` / `Add-Content >>` / "
            "`2>>`）。★ 它与 M107 的差别在**量级**：M107 让每条只读命令都误报，这条只在 `>>` 上"
            "误报 —— 现场观感是「偶发」，反而更不容易被认出来，更需要一条锁明写。"},
    {"id": "M109-in-tree-guard-dropped",
     "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '            elif not in_tree(tf, cwd):\n',
     "new": '            elif False:\n',
     "lock": "test_entry_level.py",
     "expect": "test_out_of_tree_path_defers_to_stop",
     "why": "撤掉仓外目标的那道兜底：`--only` 的值指到仓外**必然**一个都匹配不上 → rc=2 → "
            "回灌「门禁调用出错（多半是配置问题）」。这一道不挑上游是哪种提取规则出的错"
            "（M107/M108 只是**已发现**的两例），所以它是最后一道 —— 撤掉它，此后任何新出现的"
            "提取误判都会重新变成假警报。"
            "★ 放行的语义是「按取不到文件处理」→ 交给回合末 Stop 全仓兜底：不是放松，是别报假警。"
            "`test_out_of_tree_path_defers_to_stop` 两半都验（编辑期静默 + Stop 仍全仓）。"},
    {"id": "M110-injection-teaches-the-waiver-again",
     "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '    msg = f"{head}\\n{out}\\n\\n按上面的 ✅ FIX 提示改到通过。"',
     "new": '    msg = f"{head}\\n{out}\\n\\n按上面的 ✅ FIX 提示改到通过。'
            '若确认是合规写法，用行内豁免 `harness-ok: <规则ID> <理由>`（写在命中行或紧邻上一行）。"',
     "lock": "test_entry_level.py",
     "expect": "test_injected_message_does_not_teach_the_waiver",
     "why": "把「行内豁免」的教学句**加回注入正文**（2026-09-30 用户安全关切之前的老写法）："
            "`msg` 是**每一次失败/出错都出现、7 家客户端通用**的那一句 —— 最像「门禁自己的口吻」。"
            "注入通道若可被伪造，攻击者借这句话就能诱导 agent 把**任意**命中行合法豁免"
            "（伪造者不必自己编话术，门禁的原文就是话术）。所以语法只住**可信文档** "
            "`AGENTS.md`（它本来就在 agent 上下文里，正常用法一点没少）。"
            "★ 判据是**「教」不是「提到」**：`waiver_summary()` 那行「本次用掉 N 条行内豁免"
            "（harness-ok）」是**故意**要可见的审计（`test_review_v13_batch2.py:370` 正面要求），"
            "所以本锁**不能**写成 `assertNotIn(\"harness-ok\")` —— 那会把「用掉了豁免、又另有真违规」"
            "的**正常**文件判红（2026-09-30 当场探针验过，一条假阳性机器）。它锁的是两个**只有教学"
            "形态才命中**的点：模板占位符 `<规则ID>` 与位置指令「紧邻上一行」。"},
    {"id": "M112-lock-run-whole-module-again",
     "file": "sandbox/mutation_smoke.py",
     "kind": "exact",
     "old": '    if expect and "--full-module-lock" not in sys.argv:\n        cmd += ["-k", expect]\n',
     "new": '',
     "lock": "test_lock_inventory.py",
     "expect": "test_lock_run_targets_only_the_named_case",
     "why": "把「只跑 `expect` 点名那条」退回**整跑锁模块**（2026-09-30 优化之前的原状）。"
            "表面上只是「慢」—— 但慢到四十分钟的看守**实际不会有人跑**，而那正是护盾名单烂掉的起点"
            "（51 条变异挤在 test_review_2026_09_23.py 上：整跑 23.5s vs 点名 0.445s）。"
            "★ 这条变异还顺带守住另一件事：`_lock_cmd` 与 `apply_mutation` 都住在本文件里，"
            "拿这个文件自己当变异目标，等于确认「冒烟能改自己」这条路是通的。"},
    {"id": "M113-full-module-lock-becomes-a-no-op",
     "file": "sandbox/mutation_smoke.py",
     "kind": "exact",
     "old": '    if expect and "--full-module-lock" not in sys.argv:\n',
     "new": '    if expect:\n',
     "lock": "test_lock_inventory.py",
     "expect": "test_lock_run_targets_only_the_named_case",
     "why": "让 `--full-module-lock` **变成摆设**：旗标照收、却不改变行为。它比 M112 更阴 —— "
            "M112 至少是「全程慢、判据一样」，这条是「人以为在做 A/B，其实两次跑的是同一件事」："
            "将来某条变异依赖同模块残留状态时（`--full-module-lock` 存在的**唯一理由**），"
            "排查会被它骗着得出结论。故两向各一条护盾，同 M102/M103 对同一条四向锁的做法。"},
    {"id": "M114-in-tree-drops-normcase",
     "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '        p = os.path.normcase(os.path.abspath(path))\n'
            '        r = os.path.normcase(os.path.abspath(root))\n',
     "new": '        p = os.path.abspath(path)\n        r = os.path.abspath(root)\n',
     "lock": "test_entry_level.py",
     "expect": "test_in_tree_is_path_aware_not_string_prefix",
     "why": "撤掉 `in_tree` 的 `os.path.normcase`（少一次规范化的「原状」）。"
            "它同时咬掉两件事：① 大小写折叠（`D:/Repo/…` 与 `d:/repo`）—— **两个平台都会红**，"
            "所以这条护盾在 Windows 上也能验证；② `/` 与 `\\` 的归一（Windows 上 abspath 已经"
            "把 `/` 换成 `\\`，所以这一半只由 ① 兜着）。"
            "★ 为什么值得单独一条：2026-09-30 的 CI 红（`4fa05de`，两个 Python 版本都红在"
            "同一条锁上）暴露了这条锁原先**无条件**断言「大小写不敏感」，而 `normcase` 在 POSIX "
            "上是恒等变换 —— 修法是把判据改成跟平台走。本条护盾是那次改动**在 Windows 侧**的"
            "证据：改完之后 `normcase` 仍然被真正需要，不是被顺手删掉的。"
            "⚠ 它是**只在 Windows 上可见**的一条：`normcase` 在 POSIX 上是恒等变换 ⇒ 撤掉它"
            "**等于什么都没改**，变异体与原体语义相同，在 Linux 上永远绿。"
            "现场：`90b4b58` 推上去后 CI 的 `变异冒烟`（跑在 Linux）把它报成「变异后仍然绿 "
            "→ 锁被弱化」—— 诊断方向指反了：锁没弱，是这条护盾在那个平台上不可见。"
            "互补的那条是 `M115`（在 POSIX 上可见），两条合起来覆盖两个平台。",
     "blind_on": ["posix"],
     "blind_why": "`os.path.normcase` 在 POSIX 上是恒等变换 ⇒ 撤掉它不改变任何行为，"
                  "变异体与原体在 Linux 上语义相同，锁**没有理由**红。",
     "covered_by": "M115-in-tree-hardcoded-lower"},

    {"id": "M115-in-tree-hardcoded-lower",
     "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '        p = os.path.normcase(os.path.abspath(path))\n'
            '        r = os.path.normcase(os.path.abspath(root))\n',
     "new": '        p = os.path.abspath(path).lower()\n'
            '        r = os.path.abspath(root).lower()\n',
     "lock": "test_entry_level.py",
     "expect": "test_in_tree_is_path_aware_not_string_prefix",
     "why": "把 `in_tree` 的 `os.path.normcase` 换成**写死的 `.lower()`** —— 看着等价，"
            "实则是把「平台自己说了算」换成「我替你假定大小写不敏感」。POSIX 上后果很实："
            "两个**确实不同**的目录（`/srv/Repo` 与 `/srv/repo`）被折叠成同一个 ⇒ 隔壁那个仓"
            "被判成「仓内」⇒ 门禁对着**仓外**文件跑起来。这条锁的 POSIX 半边断言的正是那个否定，"
            "所以这条**在 Linux 上红**。"
            "⚠ 互补关系：它在 **Windows 上不可见**（ntpath 的 abspath 已把 `/` 换成 `\\`，"
            "`.lower()` 与 `normcase` 结果相同 ⇒ 变异体与原体等价）。"
            "两条合起来覆盖两个平台：`M114` 守 Windows 半边，本条守 POSIX 半边。",
     "blind_on": ["nt"],
     "blind_why": "Windows 上 `os.path.abspath` 已把分隔符统一成 `\\`，`.lower()` 与 `normcase` "
                  "结果相同 ⇒ 变异体与原体等价，在 Windows 上变不出红来。",
     "covered_by": "M114-in-tree-drops-normcase"},

    {"id": "M116-waiver-summary-echoes-reason",
     "file": "checks/rule_base.py",
     "kind": "exact",
     "old": '        lines.append("    · %s L%d" % (rid, lno))\n',
     "new": '        lines.append("    · %s L%d — %s" % (rid, lno, _reason))\n',
     "lock": "test_entry_level.py",
     "expect": "test_injected_context_carries_no_waiver_reason_text",
     "why": "把 `waiver_summary()` 里被删掉的**理由回显**加回去 —— 那一行会经 gate stdout → "
            "hook 的 `out` → **进入模型的上下文**。回显的 `_reason` 是源文件注释里的自由文本："
            "**谁能让一行注释进仓，谁的文字就穿着门禁自己的审计口吻进模型上下文**"
            "（`Stop` 钩子还扫全仓，跨会话也成立）⇒ 注入通道可被第三方借用。"
            "与 ③ 是同一条通道的**第二个面**：③ 删的是「教语法」的两处，本条守的是「别搬运字」。"},
    {"id": "M117-probe-restore-truncates-then-writes",
     "file": "tests/test_hooks_chain.py",
     "kind": "exact",
     "old": ('                with open(self._probe, "r+b") as f:\n'
             '                    f.truncate(n)           # ← 只改长度，不填空文件；读者见不到 0 字节\n'),
     "new": ('                with open(self._probe, "wb") as f:\n'
             '                    f.write(self._orig)\n'),
     "lock": "test_hooks_chain.py",
     "expect": "test_restore_is_never_seen_as_a_truncated_file",
     "why": "把探针还原改回**先截断后写** —— 正是 2026-09-30 修掉的那个形态。"
            "它与本仓 2026-09-16 那例是**同族第二次**（那例见 `tests/test_source_hygiene.py` "
            "头注：`sandbox/acceptance_check.py` 在真树里建 `sandbox/_accept_probe_selfcheck.py`、"
            "约 1 秒窗口；坏法是**文件消失** → FileNotFoundError），"
            "本例换了个坏法：**文件一直在、内容读出来是空的**。"
            "被还原的是 `checks/pc_code_check.py`，而 `test_entry_level.py` 读的正是它"
            "（断言缺 node 告警的接线）⇒ 读者落在截断与写入之间就读到 **0 字节**，"
            "那条断言当场拿到空串（2026-09-30 原文：`'warn_node_missing_once' not found in ''`），"
            "套件红一条**与本次改动毫无关系**的用例。形状是**负载相关**：换负载就复现、"
            "再跑一次又绿，很容易被读成「抖动」而放过。"
            "本机实测该形态 169/470 ≈ 36% 读到 0 字节，修好后 0/1888。"},
    {"id": "M118-guard-call-site-ignores-the-root-it-is-given",
     "file": "checks/harness_self_check.py",
     "kind": "exact",
     "old": '    pc_path = os.path.join(ROOT, "harness", "hooks", "pre-commit.py")\n',
     "new": '    pc_path = os.path.join(os.path.dirname(HERE), "harness", "hooks", "pre-commit.py")\n',
     "lock": "test_hooks_chain.py",
     "expect": "test_case2_missing_guard_reference_is_reported",
     "why": "让 ⑥ 自检**不再看它被给的 ROOT**，改成从 `HERE` 现算真实工程根"
            "（`os.path.dirname(HERE)` 与模块级 ROOT 同值，但**不受调用方覆盖 ROOT 的影响**）。"
            "这条守的不是 ⑥ 本身 —— 它守的是「**反向锁可以不改写真实文件**」这个前提："
            "前提一旦没了，那条反向锁就只能回去改写 `harness/hooks/pre-commit.py` —— "
            "全部接入仓 pre-commit 的**实体**，且同样是先截断后写。"
            "同一批的另一半（`_restore_probe` 的截断窗口）由 M117 守。"},
    # ---- 2026-10-01 后端作用域对齐批次（R2/R5/R6 作用域、R3/R9 判据）----
    # 这一批的共同现场：四条规则的**作用域/判据偏窄**在内部开发记录里挂了两周，写的却是
    # 「只量不动」；实测后才发现其中 R9 那半句是**错的**（它的 dirs 是死代码，见 M123 的 why）。
    # ★ 五条各自只咬一条锁：作用域三条（M119/M121/M122）+ 判据两条（M120/M123）。
    #   这批之前**一把锁都没有** —— 把四条就地放宽后 27 条相关用例全绿，所以这些锁是新增的。

    # ── 产物出生证明（2026-10-04）────────────────────────────────────────────
    {"id": "M127-scaffold-fingerprint-is-a-constant", "file": "sandbox/agents_scaffold.py",
     "kind": "exact",
     "old": '    with open(path, "rb") as f:\n'
            '        return hashlib.sha256(f.read()).hexdigest()\n',
     "new": '    return "0" * 64  # mutation-smoke\n',
     "lock": "test_agents_scaffold.py",
     "expect": "test_verify_catches_a_source_edited_after_emit",
     "why": "把 `fingerprint` 掏成一个常量 —— 于是生成时记的哈希与复核时算的哈希**恒等**，"
            "「源已改」再也报不出来。这正是本功能要防的那件事（产物对着更早的源生成而无声）。"
            "★ 打的是**唯一那个哈希函数**：生成与复核共用它，所以这一刀两处同时失效，"
            "`lost_lines` 那套自检照旧放行 —— 与真实事故的形态一致，不是人为构造的畸形。"
            "★ 这一条**不是**「同一条判据的另一半」那种配对护盾（对照 M125/M126）："
            "它守的是「有没有在算」，另一半由 M128 守「算出来有没有拿来比对」，"
            "两条删任何一条都只剩半边。"},

    {"id": "M128-verify-ignores-engine-drift", "file": "sandbox/agents_scaffold.py",
     "kind": "exact",
     "old": '    for k in ("scaffold", "audit"):\n',
     "new": '    for k in ():  # mutation-smoke\n',
     "lock": "test_agents_scaffold.py",
     "expect": "test_verify_flags_engine_drift",
     "why": "让复核**不再比对引擎自指纹**（生成器 / 判据模块两处），只剩源与产物两项。"
            "产物也可能是对着更早的**引擎**生成的 —— 那种情况下源一个字没动、产物哈希也自洽，"
            "只有引擎这一层能报出来。"
            "★ 与 M127 分开是对的：M127 证明「哈希真的在算」，这一条证明「算出来真的拿去比了」；"
            "合成一条只能证明其中一半（同 M51/M52/M53 的口径）。"},

    {"id": "M129-manifest-is-overwritten-again", "file": "sandbox/agents_scaffold.py",
     "kind": "exact",
     "old": '        text = merge_manifest(old, sections, head)\n',
     "new": '        text = _render_manifest(head, list(sections.values()))  # mutation-smoke\n',
     "lock": "test_agents_scaffold.py",
     "expect": "test_a_second_run_of_one_repo_keeps_the_other_section",
     "why": "把逐行归宿清单换回**旧的整写**：`--only k1` 跑完，清单里只剩 k1，其余仓凭空消失 ——"
            "而产物还在、出生证明也还在，两份文件互相打脸。★ 这是**假警**那一类："
            "它不会让谁漏掉问题，它教人不再相信清单（比漏报更贵）。"},

    {"id": "M130-merge-drops-the-old-head", "file": "sandbox/agents_scaffold.py",
     "kind": "exact",
     "old": '    return _render_manifest(head if head else head_lines, out)\n',
     "new": '    return _render_manifest(head_lines, out)  # mutation-smoke\n',
     "lock": "test_agents_scaffold.py",
     "expect": "test_the_head_note_survives_a_merge",
     "why": "合并时**无条件用我们自己的表头**（丢掉旧表头）：人在清单顶上写的那句备注会在"
            "下一次 `--only` 之后无声消失。★ 与 M129 是合并的两半 ——"
            "M129 守「别人的段要留住」，这一条守「表头也不是我们的私产」。",
     },

    # ── 知识库根占位：留守行/标题/`## 项目`（2026-10-05）──────────────────────────
    {"id": "M131-portable-back-to-move-lines-only", "file": "sandbox/agents_audit.py",
     "kind": "exact",
     "old": '    return [product_line(l) for l in out]\n',
     "new": '    _mv = set(move_lines)  # mutation-smoke\n'
            '    return [product_line(l) if l in _mv else l.rstrip() for l in out]\n',
     "lock": "test_agents_scaffold.py",
     "expect": "test_a_vault_root_in_a_stay_line_ships_as_a_placeholder",
     "why": "把 `_target_lines` 末尾那次收口换回**只在搬迁行上生效** —— 正是 2026-10-05 之前"
            "出货的形态：首行标题 / `## 项目` 块 / 留守行三处只做 `rstrip()`，里面带知识库根"
            "就**逐字进产物**（同事 clone 后那行指向一个不存在的地方，屏幕上一切正常）。"
            "★ 这一条刻意保留**搬迁行仍然换占位**：那才是真实事故的形状 —— 当时那把锁"
            "（夹具是 `知识库：` 开头的搬迁行）**全绿**，缺陷照常出货。变异若把搬迁行也一起"
            "弄坏，红的会是旧锁，就证不出新锁补上了那一半。"},

    {"id": "M132-self-check-transforms-move-lines-only", "file": "sandbox/agents_scaffold.py",
     "kind": "exact",
     "old": '    for l in list(keep_lines) + list(move_lines):\n'
            '        s = agents_audit.product_line(l)\n',
     "new": '    # mutation-smoke\n'
            '    for l in list(keep_lines) + [agents_audit.product_line(x) for x in move_lines]:\n'
            '        s = l.rstrip()\n',
     "lock": "test_agents_scaffold.py",
     "expect": "test_the_generator_self_check_agrees_on_a_stay_line",
     "why": "让逐行自检**只换算搬迁行**（2026-10-05 之前 `_moved` 的样子）：产物那一侧已经"
            "把留守行的知识库根换成了 `<VAULT>`，自检却拿**源行原样**去比 ⇒ 判成「丢知识」"
            "⇒ 整仓不写。那是**假警**：产物本来是对的，工具却拒绝出货 ——"
            "假警比漏报更贵，它教人不再相信这个自检。"
            "★ 与 M131 **必须分开**：M131 打的是「产物换没换」，这一条打的是"
            "「自检那一侧的换算式子跟没跟上」；两者各自单独生效都能让这一组锁变红，"
            "合成一条就只能证明其中一半（同 M127/M128 的口径）。"
            "★ 两条合起来还挡住一个更坏的中间态：把 `target_form` 改回旧式**但**自检仍换算，"
            "于是「产物带病」被自检拦成「整仓不写」—— 看起来没人写坏东西，实际是工具静默"
            "停产，而 `lost_lines` 报出来的那句话会把排查引向「丢了哪一行」这个错误方向。"},

    # ── 范式指路（2026-10-15 批次 1：命中输出补 [规则ID] + 📖 See）─────────────
    {"id": "M133-see-stamping-dropped", "file": "checks/rule_base.py",
     "kind": "exact",
     "old": '            f.see = f.see or getattr(self, "see", "")\n',
     "new": '            pass  # mutation-smoke\n',
     "lock": "test_checks.py",
     "expect": "test_rule_run_stamps_see_onto_findings",
     "why": "把 Rule.run 的 see 盖章拆掉：规则类上声明的范式指针再也到不了 Finding ——"
            "命中文案退回「只说应走 project-form、不给路径」，走廊断在最后一步。"
            "★ 坏的形状是「看着完好」：各类的 see 声明与格式化函数都还在，唯独没接线，"
            "只有把 run() 到 Finding 这一环锁住才防得住。"},

    {"id": "M134-finding-format-drops-rule-id", "file": "checks/rule_base.py",
     "kind": "exact",
     "old": '    line = "[%s] %s" % (fnd.rule_id, fnd.message) if fnd.rule_id else fnd.message\n',
     "new": '    line = fnd.message  # mutation-smoke\n',
     "lock": "test_checks.py",
     "expect": "test_format_finding_embeds_rule_id_and_see",
     "why": "把 [规则ID] 前缀丢掉：行内豁免要求「规则 ID 必须写对，写错不生效」，"
            "端级模板承诺「被拦时门禁会把 ID 直接打给你」—— 没了 ID，agent 只能靠猜。"
            "这正是本批修的另一半：PC/H5 原先在转换点把 rule_id 就地丢弃，"
            "后端一直是 ❌ RULE 格式，三端就此对齐。"},


    # ── el-drawer 回禁止清单（2026-10-15c：用户当场抓到的陈旧清单）──────────────

    # ── 台账归因（2026-10-15 批次 3：直调兜底 + 钩子 via）──────────────────
    {"id": "M137-direct-call-entry-fallback-dropped", "file": "harness/run_gate.py",
     "kind": "exact",
     "old": '        entry = (os.environ.get("HARNESS_ENTRY") or "").strip() or "run_gate"\n',
     "new": '        entry = (os.environ.get("HARNESS_ENTRY") or "").strip()  # mutation-smoke\n',
     "lock": "test_entry_level.py",
     "expect": "test_direct_run_gate_records_fallback_entry",
     "why": "把直调兜底拆掉：无标直调又落回**空 entry**（10 月实测 79 条的那批）——"
            "「谁跑的」永久无法归因，CodeBuddy 漏跑排查现场就是被它挡住的。"},

    {"id": "M138-hook-entry-attribution-dropped", "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '    os.environ["HARNESS_ENTRY"] = "hook:%s:%s" % (client, event)\n',
     "new": '    pass  # mutation-smoke\n',
     "lock": "test_entry_level.py",
     "expect": "test_hook_source_hard_assigns_entry",
     "why": "把钩子归因拆掉：编辑器钩子又一律塌缩成 entry=auto-gate，"
            "「哪家客户端、哪个事件」在台账里分不出来。（行为面由"
            "test_hook_run_records_via_attribution 另一条锁盯着。）"},

    # ── 钩子可见性（2026-10-15 批次 4：心跳 / 跳过不静默）──────────────────
    {"id": "M139-skip-heartbeat-dropped", "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '            _activity(client, event, "skip-no-file", tool=_tool_name(payload))\n',
     "new": '            pass  # mutation-smoke\n',
     "lock": "test_entry_level.py",
     "expect": "test_write_without_file_leaves_heartbeat_and_skip",
     "why": "把「提取失败」的心跳拆掉：IDE 形态的静默放行又变成不可见 ——"
            "「钩子没被调」与「调了提取失败」重新无法区分（CodeBuddy 对照实验卡的就是这个）。"},

    {"id": "M140-skip-record-dropped", "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '                _skip_note(client, event, "no-file", tool=_tool_name(payload))\n',
     "new": '                pass  # mutation-smoke\n',
     "lock": "test_entry_level.py",
     "expect": "test_write_without_file_leaves_heartbeat_and_skip",
     "why": "把跳过记录拆掉只剩心跳：跳过频率/形态没了留痕通道，回看现场只剩一行覆盖式状态——"
            "而心跳只能回答「最后一次」，答不了「跳过过多少次」。"
            "（与 M139 是两半，各自单独生效即红。）"},

    # ── done 选错仓（2026-10-15g：CodeBuddy 实测抓到的假凭证）──────────────────
    {"id": "M141-done-ignores-caller-cwd", "file": "agentguard/cli.py",
     "kind": "exact",
     "old": '    rc, out, err = _run(cmd, cwd=caller_cwd)\n',
     "new": '    rc, out, err = _run(cmd)  # mutation-smoke\n',
     "lock": "test_agentguard.py",
     "expect": "test_done_uses_caller_cwd_when_no_repo_flag",
     "why": "把 done 的「用调用方 cwd」拆掉：子进程 cwd 回退成工程仓 —— 在端仓里跑会被识别成"
            "harness_self 并打出 rc=0 **假凭证**（挑错仓比不产凭证更危险：让“没查端仓”看着像"
            "“查过且通过”）。CodeBuddy 实测正是撞上这个。"},

    # ── 批次 6（2026-10-15）：编辑期仓根从文件反推（CodeBuddy IDE cwd=安装目录）────────
    {"id": "M142-repo-root-ignores-file", "file": "integrations/hooks/agentguard-gate.py",
     "kind": "exact",
     "old": '            if root:\n                repo_root = root\n',
     "new": '            if False:  # mutation-smoke\n                repo_root = root\n',
     "lock": "test_entry_level.py",
     "expect": "test_wrong_cwd_still_gates_the_edited_file",
     "why": "把「从文件反推仓根」拆掉（退回永远用 payload cwd）：CodeBuddy IDE 形态又回到"
            "in_tree 判不在树内 → skip-out-of-tree 的 100% 静默放行 —— 正是抓到它的那次"
            "抓包实测的形状。反锁 test_file_outside_any_repo_still_skips 刻意不受影响。"},

    # ── 批次 7（2026-10-15）：超时杀整树 + 全量超时分档 ─────────────────────────
    {"id": "M143-taskkill-loses-slash-T", "file": "agentguard/cli.py",
     "kind": "exact",
     "old": '            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],\n',
     "new": '            subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],  # mutation-smoke\n',
     "lock": "test_agentguard.py",
     "expect": "test_timeout_kills_grandchild_and_returns_fast",
     "why": "把 taskkill 的 /T（杀整棵）拆掉：回到「只杀直接子进程」的旧形状 —— 孙进程继续跑、"
            "父进程又阻塞在管道 EOF 上，正是 100 分钟卡死的那个病灶。"},
    {"id": "M144-run-all-timeout-back-to-300", "file": "agentguard/cli.py",
     "kind": "exact",
     "old": '        timeout = 3600\n',
     "new": '        timeout = 300  # mutation-smoke\n',
     "lock": "test_agentguard.py",
     "expect": "test_run_all_gets_the_longer_default_timeout",
     "why": "全量扫描默认超时退回 300s：大仓全扫必然超时（实测小时级 wall），用户的 --all 又"
            "变回一次「超时报错 + 输出全丢」。"},
]


def run(cmd, cwd, timeout=600):
    """跑一次被测命令 —— **钉死在全严格档**，不跟调用方的 HARNESS_FAST 走。

    2026-09-17：本函数原先不传 `env=`，子进程**继承**调用方环境。而 M1/M3/M5/M7 四条变异的
    lock 正是 `tests/test_review_v9_batch1.py` —— 那个文件接上快跑开关后，谁 shell 里带着
    `HARNESS_FAST=1` 跑冒烟（README 把 `--fast` 列为日常档，export 出去很自然），这四条会报
    「变异后仍然绿」：**锁明明还在，却被读成"锁被弱化"**。假红与假绿同害 —— 都会把这条机制
    训成噪音，而冒烟这份"牙齿名单"一旦被训成噪音，就再没人信它了。
    所以只剔 `HARNESS_FAST` 这一个变量（不清空环境：其余变量照常继承，
    见 tests/test_lock_inventory.py::test_smoke_run_is_pinned_to_full_mode 的两向断言）。
    """
    env = dict(os.environ)
    env.pop("HARNESS_FAST", None)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, env=env, timeout=timeout)


def _lock_cmd(lock, expect):
    """跑某条变异的锁的命令 —— **只跑 `expect` 点名的那条用例**（2026-09-30）。

    原先每条变异都 `-p <锁模块>` **整跑整个模块**。而判据本来就只认一条用例
    （见 `main()` 里 `failed_names(out)` 与 `expect` 的比对），于是 105 条变异里
    有 51 条挤在同一个 `test_review_2026_09_23.py` 上 —— 等于把那个模块整跑了 51 遍。
    实测该模块整跑 23.5s、`-k` 点名跑 **0.445s**（53×），整趟冒烟从 ~35 分钟压到几分钟。
    ★ 语义没有放宽：判据仍是「**期望的那条锁**出现在失败清单里」，只是不再顺手跑无关用例。

    ★ `expect` 缺失时**退回整跑模块**（不是 `-k ""`）：`-k ""` 会匹配到全部用例、
    看起来"也跑了"，实际把「这条变异没登记期望锁」这件事藏了起来；退回整跑至少
    行为与旧版逐字相同，`main()` 里 `exp and …` 那一支也会照旧给出判定。
    两向锁见 tests/test_lock_inventory.py::test_lock_run_targets_only_the_named_case。

    ★ `--full-module-lock` 是**排查用的退路**，不是日常档：万一某条变异的失败依赖同模块
    里别的用例留下的状态，"点名跑"会与"整跑"给出不同判定 —— 那时用它对 A/B，别拿它当默认。
    """
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", lock]
    if expect and "--full-module-lock" not in sys.argv:
        cmd += ["-k", expect]
    return cmd


def copy_tree(dest):
    r = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    rels = [x for x in r.stdout.decode("utf-8", "replace").split("\0") if x]
    if r.returncode != 0 or not rels:
        # 评审 v12 §V12-3：原先不校验 → 无 .git 的树上静默复制 0 文件，随后报"对照失败（副本跑不绿）"，
        # 把排查引向错误方向。现在当场说清"需要在 git 检出处运行"。
        raise RuntimeError("未能建立副本：需要在 **git 检出处**运行（快照/导出包不含 .git）"
                           "；git ls-files rc=%d，文件数=%d" % (r.returncode, len(rels)))
    for rel in rels:
        src, dst = os.path.join(ROOT, rel), os.path.join(dest, rel)
        if not os.path.isfile(src):
            # 索引里有、工作区已删（尚未提交的删除）：副本按工作区现状建基线，否则 copy2
            # 直接抛 FileNotFoundError，整趟冒烟倒在第一步。
            sys.stderr.write("⚠ 副本跳过工作区已不存在的跟踪文件：%s\n" % rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    _make_work_tree(dest)


def _make_work_tree(dest):
    """把副本补成一个**干净的 git 工作区**（2026-09-17 实测：缺这一步 → 冒烟恒失败）。

    为什么必须有：`checks/harness_guard.py` 按 fail-closed 设计 —— 读不出工程仓的未提交改动
    就直接 rc=2 阻断（"查不了 ≠ 没改动"，用户 2026-09-16 拍板的行为）。副本原先没有 .git，
    于是**任何走真实 pre-commit 入口的锁都拿到 rc=2 而不是预期的 1**：
    `test_review_v9_batch1.py::test_basis_is_actually_used_backend_enc` 正是这么红的，
    而冒烟把它读成"对照失败：副本本身跑不绿 → 结论不可信" —— 结果是**每一次运行都倒在第一步**，
    名单里 19 条变异一条也没被真正执行过。观感是"冒烟在跑"，实际是空转：
    这正是本仓反复栽的那个形状（机制在、判据不在），只不过这次栽在机制自己身上。

    做法：init + 全量 add + 一次提交，让 HEAD 与工作区逐字节一致（status 干净）。
    副本因此等价于**一份刚 clone 出来的干净检出** —— 正是 CI 的形态；变异是之后才施加的，
    `git diff` 在工作区如实反映它。实测代价 ≈1.2s（copy 0.4s 之外）。
    """
    base = ["git", "-C", dest,
            "-c", "user.email=mutation-smoke@local", "-c", "user.name=mutation-smoke",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath="]
    # core.hooksPath= 是**夹具搭建**：副本里不跑任何钩子（同事若有全局 hooksPath，
    # 否则会在别人的机器上触发别人的钩子，把"环境差异"混进变异结论）。
    for cmd in (["init", "-q"], ["add", "-A", "-f"], ["commit", "-qm", "mutation-smoke base"]):
        r = subprocess.run(base + cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError("未能把副本补成 git 工作区（git %s rc=%d）：%s"
                               % (cmd[0], r.returncode, (r.stderr or "").strip()[:300]))


def apply_mutation(path, mut):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        s = f.read()
    if mut["kind"] == "exact":
        if s.count(mut["old"]) != 1:
            return "锚点命中 %d 次（期望 1）" % s.count(mut["old"])
        s = s.replace(mut["old"], mut["new"], 1)
    elif mut["kind"] == "regex":
        s, n = re.subn(mut["pattern"], mut["repl"], s)
        if n == 0:
            return "正则 0 命中"
    elif mut["kind"] == "after_def":
        i = s.find(mut["anchor"])
        if i < 0:
            return "锚点未找到"
        j = s.index("\n", i) + 1
        s = s[:j] + mut["inject"] + "\n" + s[j:]
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write(s)
    _purge_pycache(path)
    return None


def _purge_pycache(path):
    """删掉被测文件旁边的 `__pycache__` —— **必须**，否则上一条变异的字节码可能被复用。

    2026-09-23 实测复现（这条不是推演，是当场证出来的）：CPython 的**时间戳式** pyc 失效判据
    是「(源码 mtime 的**整秒**, 源码字节数)」这一对。同一文件的连续两条变异只要
      · 改出来的字节数相同（本例 M49/M50/M54/M55 都恰好 +18 字节），且
      · 前一条那次 `unittest` 跑完还没跨过整秒边界（本模块 6 条用例 0.02s，一个来回常在同一秒内），
    第二条就会被判成「pyc 仍有效」→ **跑的是上一条变异的字节码**。

    危害方向不对称，而且最坏的那半是真会骗人的：连红的那半只会在失败信息里露出生面孔
    （实测：M50 那一轮报的失败清单是 `['test_r1_cases']`，即 M49 的代码在跑），
    但**同名期望**的连号变异会变成假绿 —— 名单里 M51/M52/M53 三条都期望 `test_r2_cases`、
    都改同一个文件，谁的字节码被复用都照样"红"，那条变异其实一次都没被测过。

    为什么删 `__pycache__` 而不是加 `PYTHONDONTWRITEBYTECODE`：那个变量只挡**写**、不挡**读**，
    而副本里的 pyc 是子进程写的（锁用例还会自己 spawn 孙进程，孙进程可能带着自造的环境）。
    清目录对「谁写的」完全免疫。代价：每个变异多一次目录删除（命中才删，通常是空操作）。
    ★ 为什么只删这一个目录就够：每轮之间**只有 `m["file"]` 这个文件的内容变了**，
    其余文件的 pyc 再怎么陈旧也与它自己的源码一致。
    """
    d = os.path.join(os.path.dirname(os.path.abspath(path)), "__pycache__")
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)


def _ran_nothing(out):
    """这次 unittest 运行是不是**一条用例都没跑**（= 判据不存在，结论一律不可信）。

    为什么单单把这一种 rc=0 拎出来（2026-09-23 实测）：`unittest discover` 在**匹配不到任何模块**
    时的输出是 `Ran 0 tests in 0.000s` + `OK`、退出码 **0** —— 与"全绿"逐字同形。
    而本冒烟的副本只含 `git ls-files` 列出的文件（见 `copy_tree` / `_make_work_tree`：副本必须是
    干净工作区，未跟踪文件不能进），所以**未提交的锁模块**在这里就是"匹配不到"。
    表现是"变异后仍然绿"，读起来像"锁被写弱了"，实际是"这条锁今天没被跑过"。
    ★ 与 `failed_names` 同一族：判据落在**事实上**（跑了没有），而不是落在"输出里有没有那串字"。
    """
    return "Ran 0 tests" in (out or "")


def failed_names(out):
    """从 unittest 输出里取出**真正失败/出错**的用例名（`FAIL:` / `ERROR:` 那两行）。

    为什么要单独解析（2026-09-17）：判据原先写成 `exp in out` —— 那是"**这串字在输出里出现过**"，
    而不是"**这条用例失败了**"。两者不等价：用例名还会出现在别处（别的用例的断言消息、
    回溯文本、verbose 下的 skip 行都带着名字）。改成从失败清单取，判据落在事实上，
    失败时的提示也能直接列出"实际失败的是哪几条"，不用再去翻日志。
      · 有意保留**子串**语义：既有名单里有用例名是简写（如 `test_table` 实为
        `test_..._table_...`），改成全等会一次性冤枉掉一批本来就有效的变异。

    ★ 一次**没被证实**的怀疑（留档，免得后人重复挖）：我原以为这是"skip 能把判据骗过去"的漏洞
    —— 副本里那条要 `dist/` 真产物的用例必然 skip（`dist/` 被 gitignore，副本里没有），
    而它正是最容易被顺手写成 expect 的一条。实测**不成立**：冒烟跑的是 `discover` 而**不带 `-v`**，
    非 verbose 输出里根本不打印 skip 的用例名，旧判据同样识破了（见
    `reports/_falsify_u1b_lock.txt` 的对照 J）。**这是判据收紧，不是"修掉了一个假绿"** ——
    别照后者引用。
    """
    names = set()
    for line in out.splitlines():
        m = re.match(r"^(?:FAIL|ERROR): (\S+)", line)
        if m:
            names.add(m.group(1))
    return names


def main():
    # `--only` 两种写法都收：`--only=A,B` 与 `--only A,B`。★ 2026-09-24：原先**只认等号**，
    # 而模块头的用法行写的正是**空格**写法 ⇒ 照用法行敲会被**静默忽略**（`only=None`），
    # 「只想跑一条」于是变成**跑全量 95 条**（约四十分钟）。与下面那条空过滤守卫是同一个
    # 「过滤器不可信」的病，方向相反：一个**假绿**，一个**跑过头**。
    # 写法取自第一个出现的 `--only`（与原先"取第一个"的语义一致）。
    only = None
    for _i, _a in enumerate(sys.argv[1:]):
        if _a.startswith("--only="):
            only = _a.split("=", 1)[1].split(",")
            break
        if _a == "--only":
            # 末尾不带值 ⇒ 造一个空过滤交给下面的守卫报错；**不静默退回全量**（那正是本次要修的）
            _rest = sys.argv[2 + _i:]
            only = _rest[0].split(",") if _rest else [""]
            break
    muts = [m for m in MUTATIONS if not only or m["id"] in only]
    if only and not muts:
        # 2026-09-24（现场：我自己把 `--only=` 写成短 id `M92`，一条都没匹配上，冒烟空转一圈
        # 打印「0 个变异：命中 0」并**返回 0** —— 和"跑过了、全绿"在观感与退出码上都没有区别）。
        # 过滤器是**全等**匹配，不是前缀/模糊。这与本文件里已经记了三遍的那个病同源
        # （`discover` 匹配不到模块 → `Ran 0 tests … OK` → rc=0 被读成绿），只是入口换成了过滤器：
        # **空结论不得报成绿**。同类守卫本文件已有多处（`_ran_nothing`），这是第四处 ——
        # 前三处挡的是"没跑成"，这处挡的是"没让你跑"。
        # 回显**值**而不是回显 `--only=<值>`：两种写法都收，回显带等号的形态会把空格写法的用户
        # 引到"我明明是空格写的"上去（本仓对"诊断指错方向"的一贯态度：宁可少说，别说反）。
        print("✗ --only 要的 %s 一条变异都没匹配上 → 什么都没测（过滤器是**全等**匹配，要写完整 id；"
              "现有 %d 条，例：%s）" % (",".join(only) or "（空值）", len(MUTATIONS), MUTATIONS[0]["id"]))
        return 1
    if "--dry-run" in sys.argv:
        # 2026-09-24（本次新增）：把「选中了哪几条」在**跑之前**打出来。
        # 起因是上面那两处修复的同一个现场：`--only=` 写成短 id → 空转一圈报绿；`--only ` 空格写法
        # 被静默忽略 → 本来只想跑一条、结果跑了全量（当时 95 条，约四十分钟）。两种错方向相反，根因是同一个：
        # **选择不可见** —— 敲下命令之后、出结论之前，没有任何一处告诉你"你选中的是这些"。
        # 它也是 `tests/test_lock_inventory.py::test_only_filter_is_never_silently_ignored_or_widened`
        # 的观察窗：那条锁要验的是**真入口怎么解析**，而真跑一条变异要 ~84s（对照段是大头），
        # 四向加起来小十分钟 —— 干跑把这件事变成毫秒级且不落盘。
        # ⚠ 位置要紧：它在守卫**之后**。摆在守卫前面的话，`--only <短 id>` 会打印「选中 0 个变异」
        #   并返回 0 —— 那正是上面那条守卫要杀的"空结论报绿"，等于给干跑开了个后门。
        print("[变异冒烟] --dry-run：选中 %d 个变异 %s"
              % (len(muts), ",".join(m["id"] for m in muts)))
        return 0
    tmp = tempfile.mkdtemp(prefix="mutsmoke_")
    t0 = time.time()
    try:
        copy_tree(tmp)
        # 对照：未变异必须绿（防止副本自身坏掉造成假红）。只跑相关锁模块——冒烟要快，
        # 全量套件由 CI 跑；这里回答的是"锁还咬不咬"，不是"工程整体绿不绿"。
        ctrl = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_review_v9_batch*.py"]
        base = run(ctrl, tmp)
        base_out = (base.stdout or "") + (base.stderr or "")
        # ⚠ 顺序要紧：**先判「一条都没跑」，再判退出码**（2026-09-23 夜，WSL/Linux 实测查出）。
        # 原先两条是反的，在 Python 3.11 上恰好不出问题 —— 匹配不到模块时 discover 是
        # `Ran 0 tests` + `OK` + **rc=0**，于是 rc 那条不成立、落到下面这条对的；而 **3.12 起
        # discover 一条都没跑时改判 rc=5**，于是先撞上 rc 分支，报成「✗ 对照失败：副本本身
        # 跑不绿 → 结论不可信」—— 同一件事被诊断成相反的方向（该去查名单/提交，却被指去查副本）。
        # 这与下面 `_ran_nothing` 那段标注的误诊是**同一个病**，只是换了个 Python 版本暴露。
        # 判据本身没变（`_ran_nothing` + 退出码），只是把更具体的那条放到前面 —— 与下面变异
        # 循环里的次序（`_ran_nothing` 先于 rc）也就一致了。
        if _ran_nothing(base_out):
            # 2026-09-23：与下面那条同一根因（副本只含 git 跟踪的文件）。对照若一条都没跑，
            # "✓ 绿"就是个空结论 —— 而它恰恰是"整个副本可信"的唯一凭据。
            print("✗ 对照（未变异副本）一条用例都没跑：对照锁模块 %s 不在副本里"
                  "（多半是未跟踪/未提交/已改名）→ 结论不可信" % "test_review_v9_batch*.py")
            return 1
        if base.returncode != 0:
            print("✗ 对照失败：副本本身跑不绿 → 结论不可信")
            print("\n".join([l for l in (base.stdout + base.stderr).splitlines() if l.strip()][-8:]))
            return 1
        print("✓ 对照（未变异副本）：绿")
        bad = []
        blind = []
        for m in muts:
            if PLATFORM in m.get("blind_on", []):
                # 本平台看不见这条（变异体与原体语义相同，见模块头「平台盲区」）。
                # **不跑**：跑了只会得到绿，而那个绿既不能说明锁有牙、也不能说明没牙 ——
                # 拿它当"命中"是自欺，拿它当"失败"是误诊（90b4b58 的 CI 就是这么红的）。
                # 但**必须打印且必须计数**：静默跳过等于把盲区藏起来，那就是假绿。
                print("BLIND %-16s 本平台(%s)看不见这条 → 未计入命中/失败；由 %s 在另一平台顶上"
                      "（%s）" % (m["id"], PLATFORM, m.get("covered_by", "?"), m.get("blind_why", "")))
                blind.append(m["id"])
                continue
            path = os.path.join(tmp, m["file"])
            with io.open(path, encoding="utf-8", errors="replace") as f:
                backup = f.read()
            err = apply_mutation(path, m)
            if err:
                print("SKIP  %-16s 变异没施加（%s）→ 等于没测，按失败计" % (m["id"], err))
                bad.append(m["id"])
                continue
            exp = m.get("expect")
            r = run(_lock_cmd(m["lock"], exp), tmp)                # 只跑期望的那条（见 _lock_cmd）
            with io.open(path, "w", encoding="utf-8", newline="") as f:
                f.write(backup)                                    # 立刻还原
            out = (r.stdout or "") + (r.stderr or "")
            if _ran_nothing(out):
                # 2026-09-23 实测查出的**假失败**：`copy_tree` 只复制 `git ls-files`（见 `_make_work_tree`，
                # 副本必须是干净工作区 ⇒ 未跟踪文件不能进），于是**未跟踪/未提交**的锁模块在副本里
                # 根本不存在：`unittest discover -p <不存在的模块>` 打印 `Ran 0 tests … OK` 且 **rc=0**，
                # 被下面那条读成「变异后仍然绿 → 锁被弱化」。两种情形的处置**完全相反**
                # （一个去改锁，一个去提交/改名单），而当时的提示把人往反方向引 ——
                # 实测现场：M49…M55 七条 + 本批新登记的 M57/M58/M59，八条全报"锁被弱化"，
                # 真相是那七条锁**一条都没在副本里跑过**。所以单独判、单独说。
                # 2026-09-30 起还有**第二种**成因：命令带 `-k <expect>`（见 `_lock_cmd`），
                # 若 `expect` 名字写错/改了名，`-k` 过滤到 0 条 → 同样 `Ran 0 tests … OK`。
                # 两种成因都**必须红**（宁可假失败也不能假绿），所以这里只把话说全，不放过。
                print("FAIL  %-16s 锁模块 %s 在副本里一条用例都没跑 —— 要么模块没进副本（未跟踪/未提交/"
                      "已改名），要么 `expect: %s` 这个名字在该模块里不存在（-k 过滤到 0 条）"
                      "→ 这条变异**没被验证**；别去改锁，先核对这条与副本" % (m["id"], m["lock"], exp or "<未登记>"))
                bad.append(m["id"])
            elif r.returncode == 0:
                print("FAIL  %-16s 变异后仍然绿 → 锁被弱化/没打在判据上（%s）" % (m["id"], m["why"]))
                bad.append(m["id"])
            elif exp and not any(exp in n for n in failed_names(out)):
                # 评审 v12 §V12-2：文件级判据可能被"蹭红"（同文件里别的红源）→ 断言**期望的锁**真的红了
                # 2026-09-17：判据从 `exp in out` 收紧到"出现在**失败清单**里"（见 failed_names）
                print("FAIL  %-16s 红了但不是期望的那条（期望 %s 出现在失败清单里；实际失败 %s）"
                      % (m["id"], exp, sorted(failed_names(out)) or "无"))
                bad.append(m["id"])
            else:
                tail = [l for l in (r.stdout + r.stderr).splitlines() if "FAILED" in l or "ERROR" in l]
                print("RED   %-16s ✓ %s" % (m["id"], (tail[-1].strip() if tail else "rc!=0")))
        # 盲区**单列**，不混进命中（混进去 = 把"没验"报成"验了"），也不混进失败
        # （混进去 = 把"本平台看不见"报成"锁坏了"，90b4b58 那次误诊）。
        print("\n[变异冒烟] %d 个变异：命中 %d，平台盲区 %d%s，失败/跳过 %d，用时 %.0fs"
              % (len(muts), len(muts) - len(bad) - len(blind), len(blind),
                 ("（%s）" % ",".join(blind)) if blind else "", len(bad), time.time() - t0))
        return 0 if not bad else 1
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
