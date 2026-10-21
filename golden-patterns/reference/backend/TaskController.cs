using Microsoft.AspNetCore.Mvc;

namespace App.Tasks;

[ApiController]
[Route("api/tasks")]
public sealed class TaskController : ControllerBase
{
    private readonly TaskRepository _repository;

    public TaskController(TaskRepository repository) => _repository = repository;

    [HttpGet]
    public async Task<ActionResult<ApiResult<PagedResult<TaskEntity>>>> GetPage(
        [FromQuery] TaskQuery query,
        CancellationToken ct)
    {
        var data = await _repository.GetPageAsync(query, ct);
        return Ok(ApiResult<PagedResult<TaskEntity>>.Ok(data));
    }

    [HttpGet("{id}")]
    public async Task<ActionResult<ApiResult<TaskEntity>>> Get(
        string id,
        CancellationToken ct)
    {
        var data = await _repository.FindAsync(id, ct)
            ?? throw new NotFoundException("任务不存在");
        return Ok(ApiResult<TaskEntity>.Ok(data));
    }

    [HttpPost]
    public async Task<ActionResult<ApiResult<TaskEntity>>> Create(
        [FromBody] TaskInput input,
        CancellationToken ct)
    {
        var data = await _repository.CreateAsync(input, ct);
        return Ok(ApiResult<TaskEntity>.Ok(data));
    }

    [HttpPut("{id}")]
    public async Task<ActionResult<ApiResult<object>>> Update(
        string id,
        [FromBody] TaskInput input,
        CancellationToken ct)
    {
        await _repository.UpdateAsync(id, input, ct);
        return Ok(ApiResult<object>.Ok(new { }));
    }

    [HttpPatch("{id}/status")]
    public async Task<ActionResult<ApiResult<object>>> ChangeStatus(
        string id,
        [FromBody] ChangeStatusInput input,
        CancellationToken ct)
    {
        await _repository.ChangeStatusAsync(id, input.Status, ct);
        return Ok(ApiResult<object>.Ok(new { }));
    }

    [HttpDelete("{id}")]
    public async Task<ActionResult<ApiResult<object>>> Delete(
        string id,
        CancellationToken ct)
    {
        await _repository.DeleteAsync(id, ct);
        return Ok(ApiResult<object>.Ok(new { }));
    }

    [HttpPost("batch-delete")]
    public async Task<ActionResult<ApiResult<object>>> DeleteMany(
        [FromBody] BatchDeleteInput input,
        CancellationToken ct)
    {
        await _repository.DeleteManyAsync(input, ct);
        return Ok(ApiResult<object>.Ok(new { }));
    }
}
