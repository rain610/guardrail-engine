# 后端分层：Controller 只做 HTTP 面，业务规则落在 Repository

> 适用：新增一个业务模块的接口层与数据层。
> 参考实现：`golden-patterns/reference/backend/TaskController.cs`、`TaskRepository.cs`、`TaskContracts.cs`
> 对接规则：无机械规则（分层是结构约定，靠评审与范式）。

## 适用

- 新增业务模块：Entity + Repository + Controller。
- 给现有模块加端点或业务方法。
- 不适用：纯查询报表、一次性运维脚本（仍建议把 SQL/业务从 Controller 里拿出去）。

## 文件放哪

| 层 | 文件 | 职责 |
|---|---|---|
| 实体 | `Tasks/TaskEntity.cs` | 数据表模型，只放字段 |
| 常量 | `Tasks/TaskConstants.cs` | 状态/优先级等枚举值，单一事实源 |
| 出入参 | `Tasks/TaskContracts.cs` | Query / Input / 分页结果，不暴露 Entity |
| 数据访问 | `Tasks/TaskRepository.cs` | 查询、分页、软删、状态迁移、业务校验 |
| HTTP 面 | `Tasks/TaskController.cs` | 路由、绑定参数、调 Repository、返回统一壳 |
| 异常 | `GlobalExceptionMiddleware.cs` | 业务异常 → 4xx，未知异常 → 500 |

## 骨架（可照抄）

Controller 只做三件事：接参数、调仓储、包返回。

```csharp
[ApiController]
[Route("api/tasks")]
public sealed class TaskController : ControllerBase
{
    private readonly TaskRepository _repository;

    public TaskController(TaskRepository repository) => _repository = repository;

    [HttpGet]
    public async Task<ActionResult<ApiResult<PagedResult<TaskEntity>>>> GetPage(
        [FromQuery] TaskQuery query, CancellationToken ct)
    {
        var data = await _repository.GetPageAsync(query, ct);
        return Ok(ApiResult<PagedResult<TaskEntity>>.Ok(data));
    }

    [HttpPost]
    public async Task<ActionResult<ApiResult<TaskEntity>>> Create(
        [FromBody] TaskInput input, CancellationToken ct)
    {
        var data = await _repository.CreateAsync(input, ct);
        return Ok(ApiResult<TaskEntity>.Ok(data));
    }

    [HttpPut("{id}")]
    public async Task<ActionResult<ApiResult<object>>> Update(
        string id, [FromBody] TaskInput input, CancellationToken ct)
    {
        await _repository.UpdateAsync(id, input, ct);
        return Ok(ApiResult<object>.Ok(new { }));
    }
}
```

Repository 承担业务规则和查询：

```csharp
public sealed class TaskRepository
{
    private readonly AppDbContext _db;

    public TaskRepository(AppDbContext db) => _db = db;

    public async Task<PagedResult<TaskEntity>> GetPageAsync(TaskQuery query, CancellationToken ct)
    {
        query.Normalize();
        var source = _db.Tasks.AsNoTracking().Where(x => !x.IsDeleted);

        if (!string.IsNullOrWhiteSpace(query.Keyword))
        {
            var keyword = query.Keyword.Trim();
            source = source.Where(x => x.Title.Contains(keyword) || x.Owner.Contains(keyword));
        }

        var total = await source.CountAsync(ct);
        var items = await source
            .OrderByDescending(x => x.UpdatedAt)
            .Skip((query.Page - 1) * query.PageSize)
            .Take(query.PageSize)
            .ToListAsync(ct);

        return new PagedResult<TaskEntity>(items, total);
    }

    public async Task UpdateAsync(string id, TaskInput input, CancellationToken ct)
    {
        Validate(input);
        var entity = await _db.Tasks.FirstOrDefaultAsync(x => x.Id == id && !x.IsDeleted, ct)
            ?? throw new NotFoundException("任务不存在");

        if (entity.Status == TaskStatus.Done)
            throw new WarningException("已完成的任务不能修改");

        entity.Title = input.Title.Trim();
        entity.Owner = input.Owner.Trim();
        entity.Priority = input.Priority;
        entity.UpdatedAt = DateTimeOffset.UtcNow;
        await _db.SaveChangesAsync(ct);
    }

    private static void Validate(TaskInput input)
    {
        if (string.IsNullOrWhiteSpace(input.Title))
            throw new WarningException("标题不能为空");
        if (!TaskPriority.All.Contains(input.Priority))
            throw new WarningException("优先级不合法");
    }
}
```

启动时注册依赖和异常中间件：

```csharp
builder.Services.AddDbContext<AppDbContext>(options =>
    options.UseSqlite(builder.Configuration.GetConnectionString("Default")));
builder.Services.AddScoped<TaskRepository>();

var app = builder.Build();
app.UseMiddleware<GlobalExceptionMiddleware>();
app.MapControllers();
app.Run();
```

> 参考实现按 .NET 8 + 默认 ImplicitUsings 编写；老项目没有隐式 using 时，补上
> `System` / `System.Linq` / `System.Collections.Generic`。

## 照抄要点

1. **Controller 薄**。方法体只允许：绑定参数 → 调 Repository → 包 `ApiResult`。出现 `if` 业务判断、循环查库、事务，就是放错层了。
2. **出入参用 DTO**。不要把 Entity 直接当 `[FromBody]` 入参；Entity 加字段会意外变成外部契约。
3. **分页在数据库**。`CountAsync + Skip/Take` 一次完成；禁止 `ToList()` 后在内存里分页。
4. **软删统一过滤**。查询入口先 `!x.IsDeleted`；删除只置标记，不物理 `Remove`。
5. **业务异常用 `WarningException`**。Repository 只管抛，Controller 不写 `try/catch`；统一由中间件翻译成 400/404。
6. **时间统一 UTC**。写入用 `DateTimeOffset.UtcNow`，展示时在前端按用户时区格式化。
7. **查询只读用 `AsNoTracking`**。列表/详情不需要变更跟踪，省内存也避免误更新。
8. **批量操作一条 SQL**。批量删用 `ExecuteUpdateAsync`，不要循环 `SaveChanges`。
9. **项目已有泛型基类就继承**。基类已提供 `GetPage / GetDetail / Add / Edit / Delete` 时，不要重复手写一套同义方法。

## 常见坑

- ❌ Controller 里直接注入 `DbContext` 写 LINQ。接口一多，同一个业务规则散落在多个 Action。
- ❌ `var list = db.Tasks.ToList(); return list.Skip(...).Take(...)`。数据量一大就全表加载。
- ❌ 列表里循环查关联表（N+1）。用 `Contains` 批量查或一次联查后在内存匹配。
- ❌ 删除用 `Remove` 物理删除，历史数据无法追溯。
- ❌ 业务校验只写在前端。绕过页面直接调接口就能写入非法数据。
- ❌ `catch (Exception) { return Ok(false); }`。HTTP 200 + `false`，前端和监控都看不出失败。
- ❌ 返回 EF 实体且带导航属性，序列化时循环引用或把内部字段泄露出去。

## 自检

- [ ] Controller 方法体没有业务分支、没有 `DbContext`、没有 `try/catch`。
- [ ] 列表接口是数据库分页，返回 `total`。
- [ ] 所有查询都带软删过滤。
- [ ] 业务校验在 Repository/领域层，前端校验只是体验优化。
- [ ] 对外返回 DTO 或明确的响应模型，不是 EF 实体图。
- [ ] 多表写入有事务边界。
