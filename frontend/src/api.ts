export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, options)
  if (!response.ok) {
    if (!response.headers.get('content-type')?.includes('application/json')) {
      throw new Error(`API 请求失败（${response.status}），请检查后端日志`)
    }
    const body: { detail?: string | { msg: string }[] } = await response.json()
    const message = Array.isArray(body.detail)
      ? body.detail.map(item => item.msg).join('；')
      : body.detail
    throw new Error(message || `请求失败（${response.status}）`)
  }
  return response.json() as Promise<T>
}
