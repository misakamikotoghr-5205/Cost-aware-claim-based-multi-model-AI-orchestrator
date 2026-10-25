import type { PluginRestOptions } from '@hermes/plugin-sdk'
interface LocalRequest extends PluginRestOptions { path: string; connectionId: 'local'; profile: string; priority: 'foreground' }
interface NativeApi { api<T>(request: LocalRequest): Promise<T> }
/** Never resolve machine-local capabilities through the active chat gateway. */
export function localApi(profile = 'default') {
  if (!profile.trim() || /[\\/\0]/.test(profile)) throw new Error('Invalid local profile')
  return async function request<T>(path: string, options: PluginRestOptions = {}): Promise<T> {
    if (!path.startsWith('/') || path.split(/[?#]/, 1)[0].split('/').includes('..')) throw new Error('Invalid plugin path')
    const bridge = (window as unknown as { hermesDesktop?: NativeApi }).hermesDesktop
    if (!bridge?.api) throw new Error('本机 Hermes 代理不可用，请更新桌面客户端；不会转发到远程聊天连接。')
    try {
      return await bridge.api<T>({ ...options, path: '/api/plugins/local-judge' + path,
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
      if (notFound && ['/settings', '/setup'].includes(endpoint)) {
        throw new Error('本机扩展接口尚未加载。请在原生插件页启用 Agent，重启对应本机代理，再重新连接。请等待当前任务结束后重启。', { cause: failure })
      }
      throw failure
    }
  }
}
