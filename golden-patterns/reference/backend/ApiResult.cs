namespace App.Tasks;

public sealed class ApiResult<T>
{
    public bool Success { get; init; }
    public string Message { get; init; } = "";
    public T Data { get; init; } = default!;

    public static ApiResult<T> Ok(T data) => new() { Success = true, Data = data };

    public static ApiResult<T> Fail(string message) =>
        new() { Success = false, Message = message };
}
