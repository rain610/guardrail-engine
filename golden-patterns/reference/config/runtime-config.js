// 环境相关配置的唯一读取点：业务代码不直接碰 import.meta.env，也不写死地址。
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
