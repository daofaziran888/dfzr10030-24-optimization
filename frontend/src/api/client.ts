/** 统一请求封装：拼后端地址、抛网络错误、给页脚留一句可读的说明。 */
import { apiContext } from '@/api/context'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  return fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      'X-Team-Code': apiContext.teamCode,
      'X-Operator-Role': apiContext.role,
      // 请求头只能是 latin-1，中文操作人名先编码，后端再解码
      'X-Operator': encodeURIComponent(apiContext.operator),
    },
    ...init,
  }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}

/** 读取失败响应里的可读说明：FastAPI 用 detail，业务结果用 message。 */
export async function readError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = (await response.json()) as { detail?: string; message?: string }
    return new Error(payload.detail || payload.message || fallback)
  } catch {
    return new Error(fallback)
  }
}
