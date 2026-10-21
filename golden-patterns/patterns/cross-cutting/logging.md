# 日志：走统一封装，带上下文，不带秘密

> 适用：需要保留调试信息、错误现场、关键状态迁移的任何代码。
> 参考实现：`golden-patterns/reference/web/request.js`（集中错误出口）
> 对接规则：`NO-CONSOLE` —— 禁止 `console.log` 残留；命中时门禁输出会带
> `📖 See: golden-patterns/patterns/cross-cutting/logging.md`。

## 适用

- 需要保留调试信息、错误现场、关键状态迁移的代码。
- 排查线上问题时，需要临时增加可开关的输出。
- 不适用：一次性临时脚本（用完即删）；用户可见的错误提示（走统一错误响应，不是日志）。

## 会被拦的写法

```js
function saveOrder(order) {
  console.log("saveOrder", order) // ❌ NO-CONSOLE
  return api.post("/orders", order)
}
```

问题不在「打印」本身，而在它**无条件、不可关闭、会跟着提交进生产**，而且经常把整个对象（含 token、手机号）打进控制台。

## 已验收的写法

项目里已有日志封装时，直接走封装：

```js
import { logger } from "@/utils/logger"

function saveOrder(order) {
  logger.debug("saveOrder", { orderId: order.id })
  return api.post("/orders", order)
}
```

没有封装时，先建一个最小的（等级可配、生产默认只留 warn 以上）：

```js
// utils/logger.js
const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 }
const current = import.meta.env.DEV ? LEVELS.debug : LEVELS.warn

export const logger = {
  debug: (...args) => current <= LEVELS.debug && console.debug(...args),
  info: (...args) => current <= LEVELS.info && console.info(...args),
  warn: (...args) => current <= LEVELS.warn && console.warn(...args),
  error: (...args) => current <= LEVELS.error && console.error(...args),
}
```

> 封装内部使用 `console.debug/info/warn/error`；业务代码一律走 `logger`。
> 如果你们的 `NO-CONSOLE` 口径覆盖全部 `console.*` 调用，把这个封装文件加入排除目录
> （例如 `utils/logger.js`），而不是在每个业务文件里加豁免。

后端对应写法（结构化日志，不要拼字符串）：

```csharp
_logger.LogWarning("任务状态迁移失败 taskId={TaskId} from={From} to={To}",
    taskId, current, target);
```

## 照抄要点

1. **一个出口**。前端 `logger`、后端 `ILogger<T>`，业务代码不直接调 `console.*` / `Console.WriteLine`。
2. **按级别用**。`debug` 只进开发环境；`info` 记录关键业务动作；`warn` 记录可恢复异常；`error` 记录需要人介入的失败。
3. **带可检索的上下文**。记录 id、状态、耗时、traceId，而不是整页 HTML 或整个请求体。
4. **绝不记录秘密**。密码、token、Cookie、身份证号、银行卡号、完整手机号，一律脱敏或不记。
5. **错误日志带上异常对象**。后端用 `_logger.LogError(ex, "...")`，保留堆栈；只写 `ex.Message` 会丢掉现场。
6. **日志不是业务返回**。不要靠日志给前端传错误消息；用户提示走统一错误响应。
7. **循环里不刷日志**。批量处理记录总数和失败样本，不要每条一行把磁盘打满。

## 常见坑

- ❌ `console.log` 调试完忘记删，随提交进生产。
- ❌ 日志里打印完整 `request.body`，把登录密码、token 一起落盘。
- ❌ `catch (e) { }` 什么都不记，线上只能看到「操作失败」。
- ❌ 同一个异常先 `log.error` 再 `throw`，上层又 `log.error`，一条错误重复三遍。
- ❌ 用字符串拼接日志：`"user " + id + " failed"`，日志系统无法按字段检索。
- ❌ 把日志级别写死成 `debug`，生产环境每秒钟刷几万行。

## 自检

- [ ] 业务代码没有 `console.log` / `Console.WriteLine`。
- [ ] 日志封装是唯一的控制台出口，并且已在门禁排除目录里。
- [ ] 日志字段里没有密码、token、完整证件号。
- [ ] 错误日志保留了异常对象或堆栈。
- [ ] 批量/循环日志记录了汇总，而不是逐条刷屏。
