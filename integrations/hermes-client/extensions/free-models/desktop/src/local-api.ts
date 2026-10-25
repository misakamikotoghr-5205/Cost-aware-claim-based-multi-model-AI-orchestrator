import type { PluginRestOptions } from '@hermes/plugin-sdk'
interface LocalRequest extends PluginRestOptions { path: string; connectionId: 'local'; profile: string; priority: 'foreground' }
interface NativeApi { api<T>(request: LocalRequest): Promise<T> }
/** Never resolve machine-local capabilities through the active chat gateway. */
export function localApi(profile = 'default') {
  if (!profile.trim() || /[\\/\0]/.test(profile)) throw new Error('本机配置名称无效，请检查名称。')
  return async function request<T>(path: string, options: PluginRestOptions = {}): Promise<T> {
    if (!path.startsWith('/') || path.split(/[?#]/, 1)[0].split('/').includes('..')) throw new Error('扩展请求地址无效，请重新打开扩展页面。')
    const bridge = (window as unknown as { hermesDesktop?: NativeApi }).hermesDesktop
    if (!bridge?.api) throw new Error('本机代理不可用，请更新桌面客户端；请求不会转发到远程聊天连接。')
    try {
      return await bridge.api<T>({ ...options, path: '/api/plugins/free-models' + path,
        connectionId: 'local', profile, priority: 'foreground' })
    } catch (failure) {
      // Electron can preserve statusCode or wrap only the canonical error message.
      const status = failure && typeof failure === 'object'
        ? (failure as { statusCode?: unknown; status?: unknown }).statusCode ?? (failure as { status?: unknown }).status : undefined
      const message = failure instanceof Error ? failure.message : String(failure)
      const notFound = status === 404 || (status === undefined && /(?:^|Error:\s*)404(?:\s*:|\s*$)/.test(message))
      const endpoint = path.split(/[?#]/, 1)[0]
      // Only static entry routes diagnose an unloaded plugin. Missing task/session
      // IDs and download jobs must keep their original resource-specific errors.
      if (notFound && ['/settings', '/models'].includes(endpoint)) {
        throw new Error('本机扩展接口尚未加载。请在原生插件页启用智能体功能，重启对应本机代理，再重新连接。请等待当前任务结束后重启。', { cause: failure })
      }
      throw failure
    }
  }
}

/** Keep transport and machine error fields out of the ordinary Chinese UI. */
export function localError(failure: unknown): string {
  let message = failure instanceof Error ? failure.message : String(failure)
  message = message.replace(/^Error invoking remote method '[^']+':\s*/, '').replace(/^Error:\s*/, '').trim()
  const status = Number(message.match(/^(\d{3}):/)?.[1])
  const body = message.indexOf('{')
  if (body >= 0) {
    try { const detail = JSON.parse(message.slice(body)).detail; if (typeof detail === 'string') message = detail } catch { /* Not a structured backend error. */ }
  }
  if (/^[\u3400-\u9fff]/u.test(message)) return message.slice(0, 500)
  if (/loopback origin/i.test(message)) return 'Ollama 地址必须使用本机回环地址，请检查设置。'
  if (/judge model must name a local|no verified local weights/i.test(message)) return '请选择已安装的本机 Ollama 模型，不能使用云端模型。'
  if (/No download job|session.*(?:unknown|expired)|(?:unknown|expired).*session|stale revision|not found|no longer exists/i.test(message) || status === 404) return '当前记录不存在或已过期，请重新检测模型状态或重新生成候选。'
  if (status === 401 || status === 403) return '本机代理拒绝访问，请检查当前本机配置的登录状态与权限。'
  if (/JSON|Unexpected token|Unexpected end/i.test(message)) return '数据格式无效，请检查文件内容或服务响应。'
  if (status === 400 || status === 422) return '设置或请求内容无效，请检查输入项后重试。'
  if (/Operation unavailable/i.test(message)) return '本机扩展暂不可用，请检查本机配置后重试。'
  if (/ECONN|fetch|timed? out|timeout|network|unavailable/i.test(message)) return '暂时无法连接服务，请检查本机代理、模型运行时或网络后重试。'
  return '操作未完成，请检查本机配置与输入内容后重试。'
}
