# 请求层：页面只调业务方法，不直接碰 axios / fetch

> 适用：任何需要发 HTTP 请求的页面、组件或状态层。
> 参考实现：`golden-patterns/reference/web/request.js`、`golden-patterns/reference/config/runtime-config.js`
> 对接规则：无机械规则（结构约定，靠评审与范式）；写死地址会被示例 gate 的 `NO-HARDIP` 拦住。

## 适用

- 新增页面、组件、store 里要调后端接口。
- 项目里已经有一层 `src/utils/request.js`；没有就先建一层，再写业务。
- 不适用：一次性脚本、单元测试里的 stub、静态资源请求。

## 骨架（可照抄）

`src/utils/request.js` 是**唯一**出现 axios 的地方：

```js
import axios from "axios"
import { ElMessage } from "element-plus"
import { runtimeConfig } from "@/config/runtime-config"

const request = axios.create({
  baseURL: runtimeConfig.apiBaseUrl,
  timeout: 15000,
})

request.interceptors.request.use((config) => {
  const token = localStorage.getItem("token")
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

const redirectToLogin = () => {
  localStorage.removeItem("token")
  if (window.location.pathname !== "/login") window.location.assign("/login")
}

request.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && body.success === false) {
      const message = body.message || "请求失败"
      ElMessage.error(message)
      return Promise.reject(new Error(message))
    }
    return body?.data
  },
  (error) => {
    if (axios.isCancel(error)) return Promise.reject(error)
    if (error.response?.status === 401) {
      redirectToLogin()
      return Promise.reject(error)
    }
    const message = error.response?.data?.message || error.message || "网络异常，请稍后重试"
    ElMessage.error(message)
    return Promise.reject(error)
  },
)

export default request
```

页面里只写业务语义：

```js
import request from "@/utils/request"

const data = await request.get("/tasks", { params })
await request.post("/tasks", form)
await request.patch(`/tasks/${row.id}/status`, { status })
```

## 照抄要点

1. **axios / fetch 只在请求层出现**。页面、组件、store 一律 `import request`，不要再建第二个实例。
2. **地址从配置来**。`baseURL` 走 `runtime-config`，不在代码里写 `http://10.x.x.x`；配置读取方式见 `patterns/cross-cutting/config-and-env.md`。
3. **统一拆响应壳**。后端返回 `{ success, message, data }`，拦截器负责判断 `success` 并把 `data` 还给调用方；页面不再写 `res.data.data`。
4. **401 只处理一次**。清 token、跳登录，放在响应拦截器里；每个页面各写一遍必然漏。
5. **错误提示只弹一次**。请求层已经把后端 `message` 弹给用户，调用方 `catch` 里只负责恢复状态，不要再弹一遍。
6. **接口多了抽模块**。同一业务超过 3 个接口时，建 `src/api/task.js` 收口 `fetchTaskPage / createTask / changeTaskStatus`，页面不直接拼 URL。
7. **取消请求要区分**。`axios.isCancel(error)` 的分支直接返回，不弹「网络异常」——用户主动取消不是错误。

## 常见坑

- ❌ 页面里 `import axios from "axios"`，自己配 baseURL 和 token。换环境时改不完。
- ❌ 拦截器 `return response`，页面到处 `res.data.data.items`。响应壳一改，全仓跟着改。
- ❌ 请求层 `catch` 后 `return null`。调用方分不清「接口返回空」和「请求失败」，空状态和错误状态会混在一起。
- ❌ 401 只弹提示不跳登录；token 过期后用户会连续点十次，收到十次「登录已失效」。
- ❌ 把后端 `error.response.data` 原样 `throw` 到页面，页面再把整段 JSON 弹给用户。
- ❌ 在拦截器里对每个请求 `ElMessage.success("操作成功")`。查询也会弹，用户会被提示淹没。

## 自检

- [ ] `grep -r "from \"axios\"" src` 只命中 `src/utils/request.js`。
- [ ] `grep -r "http://\|https://" src` 没有写死的业务地址（第三方文档链接除外）。
- [ ] 页面 `catch` 块里没有第二次错误弹窗。
- [ ] 401 分支会清 token 并跳登录。
- [ ] 新增接口没有绕开请求层直接 `fetch()`。
