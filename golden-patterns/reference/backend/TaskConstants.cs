namespace App.Tasks;

public static class TaskStatus
{
    public const string Draft = "Draft";
    public const string InProgress = "InProgress";
    public const string Done = "Done";

    public static readonly string[] All = { Draft, InProgress, Done };
}

public static class TaskPriority
{
    public const string Low = "Low";
    public const string Normal = "Normal";
    public const string High = "High";

    public static readonly string[] All = { Low, Normal, High };
}
