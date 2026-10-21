namespace App.Tasks;

public sealed class TaskEntity
{
    public string Id { get; set; } = "";
    public string Title { get; set; } = "";
    public string Owner { get; set; } = "";
    public string Priority { get; set; } = TaskPriority.Normal;
    public string Status { get; set; } = TaskStatus.Draft;
    public DateOnly? DueDate { get; set; }
    public bool IsDeleted { get; set; }
    public DateTimeOffset CreatedAt { get; set; }
    public DateTimeOffset UpdatedAt { get; set; }
}
