# 配置：地址、端口、密钥只从环境来

> 适用：接口地址、上传地址、开关、第三方密钥、数据库连接等所有「随环境变化」的值。
> 参考实现：`golden-patterns/reference/config/runtime-config.js`
> 对接规则：`NO-HARDIP` —— 禁止硬编码 IP 地址；命中时门禁输出会带
> `📖 See: golden-patterns/patterns/cross-cutting/config-and-env.md`。

## 适用

- 接口地址、上传地址、开关、第三方密钥、数据库连接等随环境变化的值。
- 新增环境（测试/预发/生产），或把本地地址误提交进仓库。
- 不适用：真正的常量（状态码、枚举、业务上限），它们应该进常量文件而不是配置。

## 会被拦的写法

```js
const baseURL = "http://10.20.30.40:8080/api" // ❌ NO-HARDIP
```

以及这类「看起来像默认值」的写法：

```js
const baseURL = import.meta.env.VITE_API_BASE_URL || "http://10.20.30.40/api"
```

默认值把生产地址写进了代码，换环境时一定会漏。

## 前端：一个配置入口 + 启动期校验

```js
// src/config/runtime-config.js
const required = (name) => {
  const value = import.meta.env[name]
  if (!value) {
    throw new Error(`缺少环境变量 ${name}，请参照 .env.example 补齐`)
  }
  return value
}

export const runtimeConfig = Object.freeze({
  apiBaseUrl: required("VITE_API_BASE_URL"),
  enableMock: import.meta.env.VITE_ENABLE_MOCK === "true",
})
```

`.env.example` 只写键名和示例，不写真实地址/密钥：

```dotenv
VITE_API_BASE_URL=https://api.example.test
VITE_ENABLE_MOCK=false
```

`.env.local`、`.env.*.local` 加进 `.gitignore`；真实值由部署环境注入。

## 后端：配置节 + 启动期 fail-fast

```csharp
public sealed class ApiOptions
{
    public string BaseUrl { get; set; } = "";
}

builder.Services
    .AddOptions<ApiOptions>()
    .Bind(builder.Configuration.GetSection("Api"))
    .Validate(o => Uri.TryCreate(o.BaseUrl, UriKind.Absolute, out _),
        "Api:BaseUrl 未配置或不是合法地址")
    .ValidateOnStart();
```

`appsettings.json` 只放非敏感默认值和键名；数据库密码、第三方密钥从环境变量或密钥管理服务读取，不提交到仓库。

## 照抄要点

1. **代码里没有环境地址**。IP、域名、端口、bucket 名、集群名，全部从配置来。
2. **配置只读一次**。前端集中在 `runtime-config`，后端集中在 Options；不要在几十个模块里各读一次环境变量。
3. **缺失就启动失败**。必填配置缺失时在启动期报错，不要等到用户点按钮才 `undefined`。
4. **不写生产默认值**。`|| "http://prod..."` 是最危险的写法：本地能跑，上线连错库。
5. **前端 env 不是秘密**。`VITE_*` 会被打进 bundle，任何人都能看到；密钥只能放后端。
6. **示例文件进仓库，真实文件不进**。`.env.example` 给同事看键名；`.env`、`.env.local` 进 `.gitignore`。
7. **环境差异只改配置**。测试/预发/生产用同一份代码，靠配置区分，不靠 `if (import.meta.env.MODE === ...)` 散落分支。

## 常见坑

- ❌ 把 IP 写在代码、注释、测试夹具里。门禁只能拦一部分，评审要看。
- ❌ 用 `|| "默认地址"` 兜底，缺配置时静默连到错误环境。
- ❌ `.env` 提交进仓库，密钥随 git 历史永久泄露。
- ❌ 前端 env 里放数据库密码/第三方 secret，打包后人人可见。
- ❌ 配置键名拼错，运行到某个功能才报 `undefined`。
- ❌ 每个模块自己 `import.meta.env.VITE_XXX`，同一个地址出现五种读法。

## 自检

- [ ] `grep -rn "http://\|https://" src` 没有业务地址（文档链接除外）。
- [ ] 必填配置缺失时启动即失败，不是运行到一半才报错。
- [ ] `.env*` 真实文件在 `.gitignore` 里，仓库只有 `.env.example`。
- [ ] `VITE_*` 里没有任何服务端密钥。
- [ ] 所有环境差异通过配置注入，没有散落的环境判断。
