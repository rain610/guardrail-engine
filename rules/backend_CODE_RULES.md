# 后端实体规范（可执行版 = checks/backend_ruleset.py）

> 这里只写**人要看的标准**；机器执行以 `checks/backend_ruleset.py` 的规则为准（改规则必须同步本文件）。
> 两条都由「改动行」触发（`error_in_targeted` + 按行过滤）→ 存量历史文件不会被拦。

## R10 时间字段必须显式声明列类型

```csharp
// ✅ 标准写法（新增时间字段一律这样写）
[Column(TypeName = "datetime")]
public DateTime CreateTime { get; set; }

[Column(TypeName = "datetime")]
public DateTime? FinishTime { get; set; }   // 可空时间同样要声明
```

- 为什么：不声明时 EF 按 provider 默认映射（常见 datetime2），而库里列是 `datetime`
  → **查询条件一用到该字段就报类型不匹配**。
- 认的写法：特性写在属性上一行、或同一行内联；中间夹 `[Required]` 等其它特性也能找到。

## R11 实体里不得写 `[Length(n)]`

```csharp
// ❌ 不要（长度被固化在实体上）
[Length(50)]
public string Name { get; set; }

// ✅ 删掉特性，长度交给数据库与业务代码
public string Name { get; set; }
```

- 为什么：字段长度应由数据库/业务代码控制。写了 `[Length(n)]` 之后，**改长度必须改代码重新发版**，
  扩库字段也没用。
- 同族一并覆盖：`[StringLength(n)]`、`[MaxLength(n)]`。

## 作用域与触发

| 项 | 值 |
|---|---|
| 目录 | **任何 `*Entities` 目录**（实扫全客户仓：`Core/Eagle.Entities`、`Module/Eagle.Module.Entities`、`RMC/Eagle.RMC.Entities`、`RMCEx/Eagle.RMCEx.Entities`、`RMCHY/Eagle.RMCHY.Entities`、`Support/Eagle.Support.Entities`、`Audit/Eagle.Audit.Entities`、`DataReport/Eagle.DataReport.Entities`）—— 判定按目录名模式，新模块自动覆盖 |
| 触发 | 新增文件、或改动的行（`legacy_baseline` 仓同样按行判定，不动存量） |
| 覆盖度 | 命中计入 `reports/gate_log.jsonl` 的 rules 明细，可用 `run_gate --coverage` 看 |
