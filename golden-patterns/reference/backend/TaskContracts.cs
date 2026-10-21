namespace App.Tasks;

public sealed class TaskQuery
{
    public string Keyword { get; set; } = "";
    public string Status { get; set; } = "";
    public int Page { get; set; } = 1;
    public int PageSize { get; set; } = 20;

    public void Normalize()
    {
        Page = Math.Max(Page, 1);
        PageSize = Math.Clamp(PageSize, 1, 200);
    }
}

public sealed class TaskInput
{
    public string Title { get; set; } = "";
    public string Owner { get; set; } = "";
    public string Priority { get; set; } = TaskPriority.Normal;
    public DateOnly? DueDate { get; set; }
}

public sealed class BatchDeleteInput
{
    public List<string> Ids { get; set; } = new();
}

public sealed class ChangeStatusInput
{
    public string Status { get; set; } = "";
}

public sealed record PagedResult<T>(IReadOnlyList<T> Items, int Total);

public sealed class WarningException : Exception
{
    public WarningException(string message) : base(message) { }
}

public sealed class NotFoundException : Exception
{
    public NotFoundException(string message) : base(message) { }
}
