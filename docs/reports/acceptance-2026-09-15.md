# 逐客户 acceptance 留档（2026-09-15 · 评审 v9 P2-2 下半段）

> 对象：覆盖率报告里**从未运行**的 20 个 gate
> 方式：`sandbox/acceptance_check.py <gate>`（cwd=客户仓），捕获五项证据
> 说明：本文件由脚本生成；「仓不在本机」= 该客户仓未 checkout 到本机，需在有仓的机器上补跑

## c712_h5

- 仓库：`D:/MyProject/aiauto/agentguard_mobile_net-712`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ c712_h5          ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：c712_h5
```

## c712_pc

- 仓库：`D:/MyProject/aiauto/agentguard_pc_net-712`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ c712_pc          ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：c712_pc
```

## c712v3_backend

- 仓库：`D:/MyProject/aiauto/agentguard_back_net-712_v3`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ c712v3_backend   ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 －
      · syntax 传感器不适用（本 gate 非 pc/h5）
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：c712v3_backend
```

## c712v3_h5

- 仓库：`D:/MyProject/aiauto/agentguard_mobile_net-712_v3`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ c712v3_h5        ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：c712v3_h5
```

## c712v3_pc

- 仓库：`D:/MyProject/aiauto/agentguard_pc_net-712_v3`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ c712v3_pc        ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：c712v3_pc
```

## hanbogaoxin_backend

- 仓库：`D:/MyProject/aiauto/agentguard_back_net-HanBoGaoXin`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ hanbogaoxin_backend ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 －
      · syntax 传感器不适用（本 gate 非 pc/h5）
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：hanbogaoxin_backend
```

## hanbogaoxin_h5

- 仓库：`D:/MyProject/aiauto/agentguard_mobile_net-HanBoGaoXin`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ hanbogaoxin_h5   ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：hanbogaoxin_h5
```

## hanbogaoxin_pc

- 仓库：`D:/MyProject/aiauto/agentguard_pc_net-HanBoGaoXin`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ hanbogaoxin_pc   ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：hanbogaoxin_pc
```

## hy_base_backend

- 仓库：`D:/MyProject/aiauto/eagle-rmc-back-net-hy-hy_base`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ hy_base_backend  ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 －
      · syntax 传感器不适用（本 gate 非 pc/h5）
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：hy_base_backend
```

## hy_base_h5

- 仓库：`D:/MyProject/aiauto/eagle-rmc-mobile-net-hy-hy_base`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ hy_base_h5       ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：hy_base_h5
```

## hy_base_pc

- 仓库：`D:/MyProject/aiauto/eagle-rmc-pc-net-hy-hy_base`
- 结果：rc=0

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ✅ hy_base_pc       ①✅ ②✅ ③✅ ④✅
      ⑤ syntax 传感器 ✅
通过 1/1；未通过：无
```

## keji_backend

- 仓库：`D:/MyProject/aiauto/agentguard_back_net-keji`
- 结果：rc=0

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ✅ keji_backend     ①✅ ②✅ ③✅ ④✅
      ⑤ syntax 传感器 －
      · syntax 传感器不适用（本 gate 非 pc/h5）
通过 1/1；未通过：无
```

## keji_h5

- 仓库：`D:/MyProject/aiauto/agentguard_mobile_net-keji`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ keji_h5          ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：keji_h5
```

## keji_pc

- 仓库：`D:/MyProject/aiauto/agentguard_pc_net-keji`
- 结果：rc=0

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ✅ keji_pc          ①✅ ②✅ ③✅ ④✅
      ⑤ syntax 传感器 ✅
通过 1/1；未通过：无
```

## ntrx_backend

- 仓库：`D:/MyProject/aiauto/agentguard_back_net-ntrx`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ ntrx_backend     ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 －
      · syntax 传感器不适用（本 gate 非 pc/h5）
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：ntrx_backend
```

## ntrx_h5

- 仓库：`D:/MyProject/aiauto/agentguard_mobile_net-ntrx`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ ntrx_h5          ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：ntrx_h5
```

## ntrx_pc

- 仓库：`D:/MyProject/aiauto/agentguard_pc_net-ntrx`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ ntrx_pc          ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：ntrx_pc
```

## zhongdj_backend

- 仓库：`D:/MyProject/aiauto/agentguard_back_net-zhongdj`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ zhongdj_backend  ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 －
      · syntax 传感器不适用（本 gate 非 pc/h5）
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：zhongdj_backend
```

## zhongdj_h5

- 仓库：`D:/MyProject/aiauto/agentguard_mobile_net-zhongdj`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ zhongdj_h5       ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：zhongdj_h5
```

## zhongdj_pc

- 仓库：`D:/MyProject/aiauto/agentguard_pc_net-zhongdj`
- 结果：rc=1

```text
=== 接入验收门（hook 活性 / 违规探针 / 合规反锁 / 台账记录 / syntax 传感器）===
  ❌ zhongdj_pc       ①✅ ②✅ ③✅ ④❌
      ⑤ syntax 传感器 ✅
      · 台账 gate_log.jsonl 里没有该 gate 的记录（链路可能从未真正触发）
通过 0/1；未通过：zhongdj_pc
```

## 汇总

| gate | 结果 | 仓库 |
|---|---|---|
| c712_h5 | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_mobile_net-712` |
| c712_pc | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_pc_net-712` |
| c712v3_backend | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_back_net-712_v3` |
| c712v3_h5 | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_mobile_net-712_v3` |
| c712v3_pc | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_pc_net-712_v3` |
| hanbogaoxin_backend | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_back_net-HanBoGaoXin` |
| hanbogaoxin_h5 | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_mobile_net-HanBoGaoXin` |
| hanbogaoxin_pc | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_pc_net-HanBoGaoXin` |
| hy_base_backend | rc=1 ⚠未过 | `D:/MyProject/aiauto/eagle-rmc-back-net-hy-hy_base` |
| hy_base_h5 | rc=1 ⚠未过 | `D:/MyProject/aiauto/eagle-rmc-mobile-net-hy-hy_base` |
| hy_base_pc | rc=0 ✅通过 | `D:/MyProject/aiauto/eagle-rmc-pc-net-hy-hy_base` |
| keji_backend | rc=0 ✅通过 | `D:/MyProject/aiauto/agentguard_back_net-keji` |
| keji_h5 | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_mobile_net-keji` |
| keji_pc | rc=0 ✅通过 | `D:/MyProject/aiauto/agentguard_pc_net-keji` |
| ntrx_backend | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_back_net-ntrx` |
| ntrx_h5 | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_mobile_net-ntrx` |
| ntrx_pc | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_pc_net-ntrx` |
| zhongdj_backend | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_back_net-zhongdj` |
| zhongdj_h5 | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_mobile_net-zhongdj` |
| zhongdj_pc | rc=1 ⚠未过 | `D:/MyProject/aiauto/agentguard_pc_net-zhongdj` |
