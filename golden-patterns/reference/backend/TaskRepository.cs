using Microsoft.EntityFrameworkCore;

namespace App.Tasks;

public sealed class TaskRepository
{
    private readonly AppDbContext _db;

    public TaskRepository(AppDbContext db) => _db = db;

    public async Task<PagedResult<TaskEntity>> GetPageAsync(
        TaskQuery query,
        CancellationToken ct)
    {
        query.Normalize();
        var source = _db.Tasks.AsNoTracking().Where(x => !x.IsDeleted);

        if (!string.IsNullOrWhiteSpace(query.Keyword))
        {
            var keyword = query.Keyword.Trim();
            source = source.Where(x =>
                x.Title.Contains(keyword) || x.Owner.Contains(keyword));
        }

        if (!string.IsNullOrWhiteSpace(query.Status))
        {
            source = source.Where(x => x.Status == query.Status);
        }

        var total = await source.CountAsync(ct);
        var items = await source
            .OrderByDescending(x => x.UpdatedAt)
            .Skip((query.Page - 1) * query.PageSize)
            .Take(query.PageSize)
            .ToListAsync(ct);

        return new PagedResult<TaskEntity>(items, total);
    }

    public Task<TaskEntity?> FindAsync(string id, CancellationToken ct) =>
        _db.Tasks.AsNoTracking()
            .FirstOrDefaultAsync(x => x.Id == id && !x.IsDeleted, ct);

    public async Task<TaskEntity> CreateAsync(TaskInput input, CancellationToken ct)
    {
        Validate(input);
        var now = DateTimeOffset.UtcNow;
        var entity = new TaskEntity
        {
            Id = Guid.NewGuid().ToString("N"),
            Title = input.Title.Trim(),
            Owner = input.Owner.Trim(),
            Priority = input.Priority,
            Status = TaskStatus.Draft,
            DueDate = input.DueDate,
            CreatedAt = now,
            UpdatedAt = now,
        };

        _db.Tasks.Add(entity);
        await _db.SaveChangesAsync(ct);
        return entity;
    }

    public async Task UpdateAsync(string id, TaskInput input, CancellationToken ct)
    {
        Validate(input);
        var entity = await _db.Tasks
            .FirstOrDefaultAsync(x => x.Id == id && !x.IsDeleted, ct)
            ?? throw new NotFoundException("任务不存在");

        if (entity.Status == TaskStatus.Done)
        {
            throw new WarningException("已完成的任务不能修改");
        }

        entity.Title = input.Title.Trim();
        entity.Owner = input.Owner.Trim();
        entity.Priority = input.Priority;
        entity.DueDate = input.DueDate;
        entity.UpdatedAt = DateTimeOffset.UtcNow;
        await _db.SaveChangesAsync(ct);
    }

    public async Task ChangeStatusAsync(
        string id,
        string target,
        CancellationToken ct)
    {
        var entity = await _db.Tasks
            .FirstOrDefaultAsync(x => x.Id == id && !x.IsDeleted, ct)
            ?? throw new NotFoundException("任务不存在");

        if (!CanTransition(entity.Status, target))
        {
            throw new WarningException($"不能从 {entity.Status} 变更为 {target}");
        }

        entity.Status = target;
        entity.UpdatedAt = DateTimeOffset.UtcNow;
        await _db.SaveChangesAsync(ct);
    }

    public async Task DeleteAsync(string id, CancellationToken ct)
    {
        var entity = await _db.Tasks
            .FirstOrDefaultAsync(x => x.Id == id && !x.IsDeleted, ct)
            ?? throw new NotFoundException("任务不存在");

        entity.IsDeleted = true;
        entity.UpdatedAt = DateTimeOffset.UtcNow;
        await _db.SaveChangesAsync(ct);
    }

    public async Task DeleteManyAsync(BatchDeleteInput input, CancellationToken ct)
    {
        var ids = input.Ids
            .Where(x => !string.IsNullOrWhiteSpace(x))
            .Distinct()
            .ToList();
        if (ids.Count == 0)
        {
            throw new WarningException("请选择要删除的任务");
        }

        var now = DateTimeOffset.UtcNow;
        await _db.Tasks
            .Where(x => ids.Contains(x.Id) && !x.IsDeleted)
            .ExecuteUpdateAsync(
                setters => setters
                    .SetProperty(x => x.IsDeleted, true)
                    .SetProperty(x => x.UpdatedAt, now),
                ct);
    }

    private static bool CanTransition(string current, string target) =>
        (current, target) switch
        {
            (TaskStatus.Draft, TaskStatus.InProgress) => true,
            (TaskStatus.InProgress, TaskStatus.Done) => true,
            (TaskStatus.InProgress, TaskStatus.Draft) => true,
            _ => false,
        };

    private static void Validate(TaskInput input)
    {
        if (string.IsNullOrWhiteSpace(input.Title))
        {
            throw new WarningException("标题不能为空");
        }

        if (input.Title.Trim().Length > 80)
        {
            throw new WarningException("标题不能超过 80 个字符");
        }

        if (string.IsNullOrWhiteSpace(input.Owner))
        {
            throw new WarningException("请填写负责人");
        }

        if (!TaskPriority.All.Contains(input.Priority))
        {
            throw new WarningException("优先级不合法");
        }
    }
}
