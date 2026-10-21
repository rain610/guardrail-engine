# -*- coding: utf-8 -*-
"""接入验收门（评审 v8 / Codex P2）—— 一个客户 gate 算不算"上线"，看四项证据，不看配置文件。

背景：「全部 gate 都是 `enabled`」是**配置事实**；本机台账里只有少数几个真的跑过。
（这里原先硬写着「33 个 gate」：本仓 2026-09-14 注册 harness_self 自检 gate 后总数变成 34，
注释却留在 33 —— 硬写 gate 总数的注释都会这样烂掉，所以本文件不再写绝对数。）
"配好了" ≠ "有强制力"，所以把上线判据写成可执行的四项：

  ① hook 活性   ：端仓 core.hooksPath 指向共享分发器，且分发器/实现文件在位（G2）
  ② 违规探针    ：真造一个违规文件 → 门禁必须 rc=1（证明规则引擎真的在拦）
  ③ 合规反锁    ：造一个合规文件 → 门禁必须 rc=0（证明不是"一律拦"的假阳性机器）
  ④ 台账有记录  ：reports/gate_log.jsonl 里该 gate 至少有一条运行记录（证明链路真被触发过）
  ⑤ syntax 传感器：pc/h5 追加两向探针 —— 合规文件 + required=1 必须 rc=0（证明 node **可用**，
                    否则"未检查"会被 required 当失败拦下）、改成坏语法必须 rc=1（证明语法规则真在拦）；
                    backend / 仓库级 gate 不适用（记 None，不参与 pass）。

四项全 ✅ 才算"上线可交付"；②③ 一律走**密闭夹具**（临时仓 / 临时 gate 配置 + 临时覆盖层），
不碰端仓、**不向工程树写任何文件**。harness_self 是个特例但同样密闭：它查的是本工程仓的
仓库级事实，用不了临时仓，于是改用**临时配置覆盖层**造违规（见 check_gate 内的说明）。

用法：
    python sandbox/acceptance_check.py                 # 检查全部已配置 gate
    python sandbox/acceptance_check.py example         # 只检查一个
    python sandbox/acceptance_check.py --json          # 机器可读（CI/报告用）
退出码：0=全部通过；1=有 gate 未通过。
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
sys.path.insert(0, os.path.join(ROOT, "checks"))

# 坏语法探针（评审 v3 P2）：行尾按端约定（pc=CRLF / h5=LF），否则会先被行尾规则拦下、
# 变成"用别的规则证明语法规则还活着"的假证据。
SYNTAX_BAD = {
    "pc": "<template>\n  <div>h</div>\n</template>\n<script setup>\nconst = ;\n</script>\n",
    "h5": "<template>\n  <div>h</div>\n</template>\n<script setup>\nconst = ;\n</script>\n",
}

VIOLATION = {                      # 各端的"已知违规"探针内容（新文件 → 不走 legacy 豁免）
    "pc": ("a.vue", '<template>\n  <div>h</div>\n</template>\n<script setup>\nconsole.log(1);\n</script>\n'),
    "h5": ("a.vue", '<template>\n  <div>h</div>\n</template>\n<script setup>\nconsole.log(1);\n</script>\n'),
    "backend": ("a.cs", None),     # backend 用编码探针（见下）
}
CLEAN = {
    "pc": ("b.vue", '<template>\n  <div>h</div>\n</template>\n<script setup>\nconst a = 1;\n</script>\n'),
    "h5": ("b.vue", '<template>\n  <div>h</div>\n</template>\n<script setup>\nconst a = 1;\n</script>\n'),
    "backend": ("b.cs", None),
}


def _kind(gate_name):
    try:
        import file_types
        return file_types.kind_of(gate_name) or "pc"
    except Exception:
        return "pc"


def _fixture(gate_name, kind, violation):
    """密闭夹具：临时 git 仓 + 目标文件 + 指向临时 gate 的配置（不碰端仓）。"""
    d = tempfile.mkdtemp(prefix="acc-")
    repo = os.path.join(d, "repo")
    script = {"pc": "checks/pc_code_check.py", "h5": "checks/h5_code_check.py",
              "backend": "checks/backend_layer_check.py"}[kind]
    # 后端规则按目录判定，探针文件要落在规则作用域内
    sub = "RMC/Eagle.RMC.Repository" if kind == "backend" else "src"
    os.makedirs(os.path.join(repo, sub))
    subprocess.run(["git", "init", "-q", "."], cwd=repo, capture_output=True)
    cfg = os.path.join(d, "cfg.yaml")
    gate_block = ("repos:\n  probe: %s\ngates:\n  probe:\n    enabled: true\n    repo: probe\n"
                  "    endpoint: %s\n    script: %s\n    mode: strict\n    run_on: [pre_commit]\n"
                  % (repo.replace("\\", "/"), kind, script))
    if violation or kind == "backend":
        gate_block += "    encoding: utf-8-bom\n"
    with io.open(cfg, "w", encoding="utf-8", newline="") as f:
        f.write(gate_block)
    name, body = (VIOLATION if violation else CLEAN)[kind]
    p = os.path.join(repo, sub, name)
    if kind == "backend":
        # 编码探针：期望 utf-8-bom（带 BOM），这里写**不带 BOM** 的 UTF-8 → 违规
        raw = b"\xef\xbb\xbf" + "public class A { }\n".encode("utf-8") if not violation \
            else "public class A { }\n".encode("utf-8")
        with io.open(p, "wb") as f:
            f.write(raw)
    else:
        txt = body.replace("\n", "\r\n") if kind == "pc" else body
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write(txt)
    return d, cfg, p


def _run_gate(gate_name, cfg, target, node_required=False):
    env = dict(os.environ, HARNESS_CONFIG=cfg, PYTHONUTF8="1",
               HARNESS_LOG_FILE=os.path.join(os.path.dirname(cfg), "gate_log.jsonl"))
    if node_required:
        # 探针显式要求"语法未检查=失败"：这样才能用「合规文件必须通过」反推出 node 真的可用。
        env["HARNESS_NODE_REQUIRED"] = "1"
    # 批次 3（2026-10-15）：直调来源归因 —— 无标直调会落 run_gate 兜底，探针这里显式命名。
    env["HARNESS_ENTRY"] = "acceptance"
    r = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "run_gate.py"), "probe", target],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=ROOT, timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def check_gate(gate_name, cfg_all, log_path):
    """返回 {gate, hook, probe, clean, ledger, notes}。"""
    res = {"gate": gate_name, "hook": False, "probe": False, "clean": False, "ledger": False, "notes": []}
    kind = _kind(gate_name)
    g = (cfg_all.get("gates") or {}).get(gate_name) or {}
    repo = (cfg_all.get("repos") or {}).get(g.get("repo")) or ""
    # 2026-09-18（跨平台批次 2）：`harness_self` 的「仓」**不是** repos 段里的条目 —— 它的仓就是
    # 工程仓自身，由 `_config._sanity_check` 在 gate()/all_gates() 里无条件注入。本函数拿的是
    # `_config.load()` 的**原始合并结果**（没有那一步），所以这里要按同一条规则补上。
    # 漏了它的后果实测：共享 config 里那行死值一删，① 就从「真在接入机上」变成
    # 「端仓路径本机不存在 → 跳过记 ❌」—— 而工程仓自己的 hooksPath 明明是设好的。
    if not repo and g.get("repo") == "harness_self":
        repo = ROOT
    # ① hook 活性
    if repo and os.path.isdir(repo):
        hp = subprocess.run(["git", "-C", repo, "config", "--get", "core.hooksPath"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
        ok_disp = os.path.isfile(os.path.join(ROOT, "harness", "hooks", "pre-commit.py"))
        res["hook"] = bool(hp) and os.path.normcase(hp.replace("/", os.sep)).endswith(
            os.path.normcase(os.path.join("harness", "hooks-dir"))) and ok_disp
        if not res["hook"]:
            # ① 为假有**两条**成因，备注必须说清是哪一条（静默等于报平安）：
            #   · 本支（仓在、钩子没接）：干净 clone / CI 容器上属正常（环境不适用），
            #     接入机上则应已指向 —— 判定权交给跑验收的人，不由脚本替他判"是故障"。
            #   · 下面 else 支（端仓路径本机不存在）：同事机常态。
            # 2026-09-19 补：本行的措辞此前只有「应指向 harness/hooks-dir」，
            # 既没说这是环境差异还是故障，也让 `test_four_items_all_true_for_self_gate`
            # 那句「备注必须说明原因」无从判定 —— 该断言原先找的是**另一支**的措辞（'端仓'），
            # 而批次 2 给 `harness_self` 补上 repo 之后，本 gate 永远走不到那一支（CI 实红）。
            res["notes"].append("hooksPath=%r（本仓没接入钩子，应指向 harness/hooks-dir）—— "
                                "干净 clone / CI 容器上属正常（环境不适用，不是故障）；"
                                "接入机上应已指向。" % hp)
    else:
        res["notes"].append("端仓路径本机不存在 → hook 项跳过记 ❌（同事机器上属正常，验收请在接入机跑）")
    # ②③ 违规探针 / 合规反锁（密闭夹具）
    for key, viol in (("probe", True), ("clean", False)):
        if gate_name == "harness_self":
            # 自检 gate 查的是**仓库级**事实（零依赖 / UTF-8 兜底 / 配置结构…），所以用不了
            # _fixture() 那种临时仓 —— 临时仓里没有本工程的代码可查。
            #
            # 2026-09-16：原先靠「在工程树里建一个 `import requests` 的文件、跑完删掉」造违规。
            # 那与本文件开头承诺的「②③ 用密闭夹具、不写业务仓文件」直接相抵，而且留了个真实
            # 隐患：验收中途被 Ctrl-C / 超时打断，那个文件就留在树里，此后**每次提交都红**
            # （第三方依赖是阻断级），得人工去找它 —— 一次被打断的验收会把仓库弄瘸。
            #
            # 现在改为**密闭配置覆盖层**：临时目录里放一份 gates 覆盖，让自检的「⑤配置结构」
            # 命中一个真缺陷（gate 引用未定义的 repo）→ rc=1；换成一份合法覆盖 → rc=0。
            # 全程一个字节都不写进工程树，跑的仍是**本安装的**那份 checks/harness_self_check.py
            # （不是副本），所以证据强度不降反升：它证的是"你这套安装里的自检真的会拦"。
            #
            # 2026-09-18（跨平台批次 2）：覆盖层里**必须自带一份 repos 表**，不能再只写 gates。
            # 因为 `HARNESS_CONFIG` 是**替换**本机 local.yaml 而不是叠在它上面（_config.load:198），
            # 于是只写 gates 的覆盖层会让 repos 段整个为空 —— 而「repos 段为空」自本批起
            # **不再是缺陷**（新 clone / CI 干净容器就是这个形态，逐条报错会把人指去改错文件）。
            # 后果实测：探针造出来的"违规"在新语义下合法 → 自检 rc=0 → ②假红、连带 ③ 的密闭性
            # 断言也红。修法不是把判据改回去，而是让探针造出**真正**的缺陷：本机**有**路径表，
            # 而新加的这个 gate 引用了表里没有的键 —— 那才是「⑤ gate 引用未定义的 repo」。
            # 表里的键**从 gates 段反查**（不是从 cfg["repos"] 抄）：后者在有 local.yaml 的机器上
            # 是 33 条、在干净容器里是 0 条，抄它会让这条锁随环境漂移。路径一律用工程根
            # （本项只判 `os.path.isdir`，而工程根必然存在）。
            #
            # 2026-09-18（跨平台批次 7）：键名表**搬回共享配置**了（批次 2 把它一起删掉了，
            # 于是「键没声明」与「本机没配」塌成一件事 —— 只填了 1 条 local.yaml 的同事机上
            # 32 个 gate 判 ❌、提交链 fail-closed，详见 tests/test_repo_key_registry.py）。
            # 本探针**逐字未动**，但要记下它在新语义下的两支各靠什么红/绿：
            #   · 违规支：`zzz_accept_probe` 引用表里没有的键 → ⑤ ❌ → rc=1（与批次 2 同源）
            #   · 合规支：`harness_self` 这个键**不在** base 的 repos 段里（它是引擎无条件注入的，
            #     而自检读的是**原始** C.load()，拿不到那份注入）—— 所以 repos_block 里的
            #     `harness_self: <ROOT>` 现在是合规支**唯一**的来源，删掉它这里会假红。
            # 另：批次 2 那句「只写 gates 的覆盖层会让 repos 段整个为空」自本批起不再成立 ——
            # base 又有了键名表，覆盖层没写 repos 也照样合并出 33 个空值键。
            d = tempfile.mkdtemp(prefix="acc-self-")
            try:
                cfg = os.path.join(d, "overlay.yaml")
                # 合规那支的 repo 键**从配置里取**，不写死：写死某个客户的键，等那个键被改名
                # 或挪走，这里就会变成"合规也红"的假失败，而现象看起来像门禁坏了。
                _ok_repo = (((cfg_all.get("gates") or {}).get("harness_self") or {}).get("repo")
                            or "harness_self")
                _root = ROOT.replace("\\", "/")
                _keys = sorted({(g or {}).get("repo")
                                for g in (cfg_all.get("gates") or {}).values()
                                if isinstance(g, dict) and (g or {}).get("repo")})
                repos_block = "repos:\n" + "".join("  %s: %s\n" % (k, _root) for k in _keys)
                body = (repos_block +
                        "gates:\n  zzz_accept_probe:\n    enabled: true\n    repo: %s\n"
                        "    script: checks/example_code_check.py\n"
                        % ("zzz_no_such_repo_key" if viol else _ok_repo))
                with io.open(cfg, "w", encoding="utf-8", newline="") as f:
                    f.write(body)
                # 显式剔除放行开关：否则调用方环境里若开着它，探针的判定会随环境漂移。
                env = dict(os.environ, HARNESS_CONFIG=cfg, PYTHONUTF8="1")
                env.pop("HARNESS_ALLOW_POLICY_OVERRIDE", None)
                r = subprocess.run([sys.executable, os.path.join(ROOT, "checks", "harness_self_check.py")],
                                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", env=env, timeout=200)
                res[key] = (r.returncode == 1) if viol else (r.returncode == 0)
                if not res[key]:
                    res["notes"].append("%s 期望 rc=%d，实得 rc=%d：%s"
                                        % ("违规探针" if viol else "合规反锁", 1 if viol else 0,
                                           r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()[:120]))
            finally:
                shutil.rmtree(d, ignore_errors=True)
            continue
        d, cfg, target = _fixture(gate_name, kind, viol)
        try:
            rc, out = _run_gate(gate_name, cfg, target)
            res[key] = (rc == 1) if viol else (rc == 0)
            if not res[key]:
                res["notes"].append("%s 期望 rc=%d，实得 rc=%d：%s"
                                    % ("违规" if viol else "合规", 1 if viol else 0, rc, out.strip()[:120]))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    # ⑤ syntax 传感器活性（评审 v3 P2）：node 语法检查只有 pc/h5 有
    res["syntax"] = None
    if kind in ("pc", "h5") and gate_name != "harness_self":
        res["syntax"] = False
        d, cfg, target = _fixture(gate_name, kind, False)      # 合规内容起步
        try:
            rc_ok, out_ok = _run_gate(gate_name, cfg, target, node_required=True)
            txt = SYNTAX_BAD[kind]
            if kind == "pc":
                txt = txt.replace("\n", "\r\n")
            with io.open(target, "w", encoding="utf-8", newline="") as f:
                f.write(txt)
            rc_bad, out_bad = _run_gate(gate_name, cfg, target, node_required=True)
            res["syntax"] = (rc_ok == 0 and rc_bad == 1)
            if not res["syntax"]:
                res["notes"].append(
                    "syntax 传感器可疑：合规探针 rc=%d（应 0 → 证明 node 可用）/ 坏语法 rc=%d（应 1）：%s"
                    % (rc_ok, rc_bad, (out_bad or out_ok).strip()[:120]))
        finally:
            shutil.rmtree(d, ignore_errors=True)
    else:
        res["notes"].append("syntax 传感器不适用（本 gate 非 pc/h5）")

    # ④ 台账
    if os.path.isfile(log_path):
        try:
            with io.open(log_path, encoding="utf-8", errors="replace") as f:
                res["ledger"] = any('"%s"' % gate_name in line for line in f)
        except Exception as e:
            res["notes"].append("读台账失败：%r" % (e,))
    if not res["ledger"]:
        res["notes"].append("台账 %s 里没有该 gate 的记录（链路可能从未真正触发）"
                            % os.path.basename(log_path))
    _keys = ["hook", "probe", "clean", "ledger"]
    if res.get("syntax") is not None:
        _keys.append("syntax")          # 不适用（非 pc/h5）不参与判定；适用时**必检**
    res["pass"] = all(res[k] for k in _keys)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gate", nargs="?", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    import _config as C
    cfg = C.load()
    gates = sorted((cfg.get("gates") or {}).keys())
    if a.gate:
        gates = [g for g in gates if g == a.gate] or [a.gate]
    log_path = os.environ.get("HARNESS_LOG_FILE") or os.path.join(ROOT, "reports", "gate_log.jsonl")
    rows = [check_gate(g, cfg, log_path) for g in gates]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print("=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===")
        for r in rows:
            mark = lambda b: "✅" if b else "❌"
            print("  %s %-16s ①%s ②%s ③%s ④%s" % (mark(r["pass"]), r["gate"], mark(r["hook"]),
                                                    mark(r["probe"]), mark(r["clean"]), mark(r["ledger"])))
            syn = "－" if r.get("syntax") is None else mark(r["syntax"])
            print("      ⑤ syntax 传感器 %s" % syn)
            for n in r["notes"][:2]:
                print("      · %s" % n)
        bad = [r["gate"] for r in rows if not r["pass"]]
        print("\n通过 %d/%d；未通过：%s" % (len(rows) - len(bad), len(rows), ", ".join(bad) or "无"))
    return 1 if any(not r["pass"] for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
