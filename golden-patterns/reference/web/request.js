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
