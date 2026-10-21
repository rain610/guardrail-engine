# 端到端范本：任务管理（常量 → 接口 → 页面 → 状态机）

> 适用：新增一个「有状态、有列表、有新增/编辑」的完整业务模块。
> 参考实现：`golden-patterns/reference/` 下的后端与前端文件。
> 这篇不是规则清单，而是**照着串一遍**的施工图：每一层都能落到一个文件。

## 适用

- 从零新增一个业务模块，前后端都要动。
- 给现有模块补状态流转、批量操作、软删除。
- 只想抄其中一层时，回到对应的技术骨架范式，不必整篇照搬。

## 一、业务全景与文件地图

| 层 | 参考文件 | 职责 |
|---|---|---|
| 常量 | `reference/backend/TaskConstants.cs` | 状态/优先级枚举，前后端共享同一组值 |
| 实体 | `reference/backend/TaskEntity.cs` | 表模型 + 软删标记 + 时间戳 |
| 契约 | `reference/backend/TaskContracts.cs` | Query / Input / 分页结果 / 领域异常 |
| 数据访问 | `reference/backend/TaskRepository.cs` | 分页查询、软删、状态迁移、业务校验 |
| HTTP 面 | `reference/backend/TaskController.cs` | 路由、参数绑定、调仓储、包统一壳 |
| 统一壳 | `reference/backend/ApiResult.cs` | `{ success, message, data }` |
| 异常 | `reference/backend/GlobalExceptionMiddleware.cs` | 业务异常 → 4xx，未知异常 → 500 |
| 配置 | `reference/config/runtime-config.js` | 前端环境配置唯一入口 |
| 请求层 | `reference/web/request.js` | token、响应壳、401、统一错误提示 |
| 列表页 | `reference/web/task-list.vue` | 筛选、分页、批量、行操作、状态流转 |
| 表单弹窗 | `reference/web/task-form.vue` | 新增/编辑共用、校验、提交 |

## 二、状态机（先定死，再写代码）

```text
Draft ──开始──▶ InProgress ──完成──▶ Done
  ▲                  │
  └──────退回────────┘

任意状态 ──删除──▶ IsDeleted = true（软删，不再出现在列表）
Done 是终态：不能编辑、不能回退、不能再次开始。
```

后端是状态迁移的唯一权威：

```csharp
private static bool CanTransition(string current, string target) =>
    (current, target) switch
    {
        (TaskStatus.Draft, TaskStatus.InProgress) => true,
        (TaskStatus.InProgress, TaskStatus.Done) => true,
        (TaskStatus.InProgress, TaskStatus.Draft) => true,
        _ => false,
    };
```

前端只决定「显示哪个按钮」，不决定「这个迁移是否合法」：

```vue
<el-button v-if="row.status === 'Draft'" @click="changeStatus(row, 'InProgress')">开始</el-button>
<el-button v-else-if="row.status === 'InProgress'" @click="changeStatus(row, 'Done')">完成</el-button>
```

## 三、接口约定

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/api/tasks` | `TaskQuery`（keyword/status/page/pageSize） | `PagedResult<TaskEntity>` |
| GET | `/api/tasks/{id}` | — | `TaskEntity` |
| POST | `/api/tasks` | `TaskInput` | `TaskEntity` |
| PUT | `/api/tasks/{id}` | `TaskInput` | `{}` |
| PATCH | `/api/tasks/{id}/status` | `ChangeStatusInput` | `{}` |
| DELETE | `/api/tasks/{id}` | — | `{}` |
| POST | `/api/tasks/batch-delete` | `BatchDeleteInput` | `{}` |

统一响应壳：

```json
{ "success": true, "message": "", "data": { "items": [], "total": 0 } }
```

分页参数在进入查询前归一化，防止 `pageSize=100000` 拖垮数据库：

```csharp
public void Normalize()
{
    Page = Math.Max(Page, 1);
    PageSize = Math.Clamp(PageSize, 1, 200);
}
```

## 四、一条请求怎么走

以列表为例：

1. 页面 `request.get("/tasks", { params: query })`；
2. 请求拦截器加 token；
3. `TaskController.GetPage` 绑定 `TaskQuery`，调用 `TaskRepository.GetPageAsync`；
4. Repository 过滤软删 → 加查询条件 → `CountAsync` → `Skip/Take` → 返回 `PagedResult`；
5. Controller 包成 `ApiResult` 返回；
6. 响应拦截器拆壳，把 `data` 还给页面；
7. 页面写 `rows / total`，`finally` 关 loading。

出错时：

- `WarningException` → 400，`message` 是给用户看的中文；
- `NotFoundException` → 404；
- 未知异常 → 500 + traceId，堆栈只进日志；
- 前端拦截器统一弹一次错误，页面 `catch` 只恢复状态。

启动注册：

```csharp
builder.Services.AddDbContext<AppDbContext>(options =>
    options.UseSqlite(builder.Configuration.GetConnectionString("Default")));
builder.Services.AddScoped<TaskRepository>();
builder.Services.AddControllers();

var app = builder.Build();
app.UseMiddleware<GlobalExceptionMiddleware>(); // 放管道最外层，才能接住后面的异常
app.MapControllers();
app.Run();
```

## 五、照抄顺序（新增类似业务）

1. **定常量**：状态、优先级、类型值写进 `XxxConstants.cs`，前后端只认这一组字符串。
2. **定实体**：字段 + 软删标记 + 创建/更新时间；不要提前塞展示字段。
3. **定契约**：`XxxQuery / XxxInput / PagedResult`，入参不出 Entity。
4. **写 Repository**：分页、软删过滤、状态迁移、业务校验；多表写入加事务。
5. **写 Controller**：只做绑定 → 调仓储 → 包 `ApiResult`。
6. **接统一错误**：领域异常 + 异常中间件 + 启动注册。
7. **写前端**：`runtime-config` → `request` → 列表页 → 表单弹窗。
8. **验证**：跑门禁；再按下面的自检表逐条过。

## 六、常见坑

- ❌ 状态字符串前后端各写一套，改一个值要全仓搜。常量或字典必须单一事实源。
- ❌ 前端按状态显示按钮，就以为后端不用校验。直接调接口可以跳过页面。
- ❌ `Done` 还能编辑/回退。终态必须在 Repository 里强制。
- ❌ 删除用物理删除，审计和历史数据全丢。软删 + 查询过滤。
- ❌ 批量删除循环调用单个删除接口。N 次请求 + N 次 SQL，失败一半无法回滚。
- ❌ 列表 `total` 用当前页条数。分页器永远只有一页。
- ❌ 接口失败返回 200 + `success:false`，监控看不到错误率。
- ❌ 前端把整行对象塞进编辑表单，取消时列表被改脏。

## 七、自检

| 检查项 | 通过标准 |
|---|---|
| 状态值 | 前后端只有一组常量/字典，没有魔法字符串散落 |
| 状态迁移 | 非法迁移在服务端被拒绝，前端只负责显示按钮 |
| 分页 | 数据库分页，返回真实 `total` |
| 软删 | 所有查询过滤 `IsDeleted`，删除只置标记 |
| 错误 | 400/404/500 分类明确，500 不含内部细节 |
| 权限/归属 | 每个写接口都校验当前用户能操作该数据 |
| 门禁 | 参考实现整目录能通过示例 gate（测试会跑） |
