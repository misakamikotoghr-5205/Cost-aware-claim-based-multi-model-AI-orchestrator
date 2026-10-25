import type { PluginRestOptions } from '@hermes/plugin-sdk'
interface LocalRequest extends PluginRestOptions { path: string; connectionId: 'local'; profile: string; priority: 'foreground' }
interface NativeApi { api<T>(request: LocalRequest): Promise<T> }
/** Never resolve machine-local capabilities through the active chat gateway. */
export function localApi(profile = 'default') {
  if (!profile.trim() || /[\\/\0]/.test(profile)) throw new Error('本机配置名称无效，请使用不含路径分隔符的配置名称。')
  return async function request<T>(path: string, options: PluginRestOptions = {}): Promise<T> {
    if (!path.startsWith('/') || path.split(/[?#]/, 1)[0].split('/').includes('..')) throw new Error('扩展请求路径无效，请重新打开页面后重试。')
    const bridge = (window as unknown as { hermesDesktop?: NativeApi }).hermesDesktop
    if (!bridge?.api) throw new Error('本机代理不可用，请更新桌面客户端；请求不会转发到远程聊天连接。')
    try {
      return await bridge.api<T>({ ...options, path: '/api/plugins/orchestrator-core' + path,
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
      if (notFound && ['/settings', '/extensions', '/quota/dashboard'].includes(endpoint)) {
        throw new Error('本机扩展接口尚未加载。请在原生插件页启用智能体功能，重启对应本机代理，再重新连接。请等待当前任务结束后重启。', { cause: failure })
      }
      throw failure
    }
  }
}
