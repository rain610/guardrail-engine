# 校验与错误：在边界拦，按类型翻译，不吞异常

> 适用：接口参数校验、业务规则校验、统一错误返回。
> 参考实现：`golden-patterns/reference/backend/GlobalExceptionMiddleware.cs`、`TaskRepository.cs`、`reference/web/task-form.vue`
> 对接规则：无机械规则（语义判断，门禁管不了）。

## 适用

- 新增接口，需要定义「什么算非法输入、非法时返回什么」。
- 现有接口把异常吞掉、返回 200 + false、或把堆栈直接吐给前端。
- 不适用：纯静态配置校验（用启动期 fail-fast，见 `patterns/cross-cutting/config-and-env.md`）。

## 四类错误，四种返回

| 类型 | 例子 | HTTP | 用户看到 |
|---|---|---|---|
| 参数/业务校验失败 | 标题为空、状态不允许迁移 | 400 | `WarningException` 的中文消息 |
| 资源不存在 | 任务 ID 找不到 | 404 | 「任务不存在」 |
| 冲突/并发 | 版本号已变、重复提交 | 409 | 明确可操作的提示 |
| 未知异常 | 空引用、数据库连接失败 | 500 | 通用文案 + traceId |

前端只负责「体验层校验」：必填、长度、格式。**服务端才是权威**，因为接口可以被直接调用。

## 骨架（可照抄）

业务校验放在 Repository，抛领域异常：

```csharp
private static void Validate(TaskInput input)
{
    if (string.IsNullOrWhiteSpace(input.Title))
        throw new WarningException("标题不能为空");

    if (input.Title.Trim().Length > 80)
        throw new WarningException("标题不能超过 80 个字符");

    if (!TaskPriority.All.Contains(input.Priority))
        throw new WarningException("优先级不合法");
}
```

统一异常中间件把异常翻译成响应，Controller 不写 `try/catch`：

```csharp
public async Task InvokeAsync(HttpContext context)
{
    try
    {
        await _next(context);
    }
    catch (WarningException ex)
    {
        await WriteAsync(context, StatusCodes.Status400BadRequest, ex.Message);
    }
    catch (NotFoundException ex)
    {
        await WriteAsync(context, StatusCodes.Status404NotFound, ex.Message);
    }
    catch (Exception ex)
    {
        var traceId = context.TraceIdentifier;
        _logger.LogError(ex, "未处理异常 traceId={TraceId}", traceId);
        await WriteAsync(context, StatusCodes.Status500InternalServerError,
            $"服务器内部错误（traceId: {traceId}）");
    }
}
```

前端提交前先过表单校验，服务端错误由请求层统一提示：

```js
const valid = await formRef.value.validate().catch(() => false)
if (!valid) return

submitting.value = true
try {
  await request.post("/tasks", form)
  ElMessage.success("保存成功")
  visible.value = false
  emit("saved")
} catch {
  // 服务端消息已由 request 拦截器弹出，这里只恢复提交状态
} finally {
  submitting.value = false
}
```

## 照抄要点

1. **校验分层**。前端校验提升体验，服务端校验保证正确性；两边都要有，但不能只靠前端。
2. **业务错误用领域异常**。`WarningException` 表示「用户能看懂、能改」；`NotFoundException` 表示资源不存在；不要用 `Exception` 一把梭。
3. **异常翻译集中**。一个中间件/过滤器负责映射 HTTP 状态码；Controller 里出现 `try/catch` 就是重复劳动。
4. **未知异常不泄露细节**。返回通用文案 + traceId；堆栈、SQL、连接串只进服务端日志。
5. **错误消息可操作**。「保存失败」没有信息；「标题不能超过 80 个字符」用户知道怎么改。
6. **前端不重复弹错**。请求层已经弹出服务端消息，调用方只恢复 loading/按钮状态。
7. **批量接口也要校验**。「什么都没选」应返回 400，而不是静默成功。

## 常见坑

- ❌ `catch (Exception) { }` 空吞。线上只剩「没反应」，日志里什么都没有。
- ❌ `catch (Exception ex) { return Ok(ApiResult.Fail(ex.Message)); }`。HTTP 200 + 失败，监控和重试机制全部失效。
- ❌ 把 `ex.ToString()` 返回给前端。泄露文件路径、SQL、内部类名。
- ❌ 只在 Controller 校验，Repository 被后台任务/其他入口复用时绕过规则。
- ❌ 前端校验通过就认为服务端一定成功；接口并发/权限变化仍会失败。
- ❌ 每条错误都加一个自定义异常类，最后没人记得哪个映射 400 哪个映射 500。

## 自检

- [ ] 每个写接口都有服务端校验，不只依赖前端。
- [ ] 业务异常能稳定映射到 400/404/409，未知异常是 500。
- [ ] 500 响应不含堆栈、SQL、连接串。
- [ ] 前端 `catch` 不重复弹错误。
- [ ] 日志里能用 traceId 找到对应异常。
