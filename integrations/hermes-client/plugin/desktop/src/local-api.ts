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
    return bridge.api<T>({ ...options, path: '/api/plugins/orchestrator-core' + path,
      connectionId: 'local', profile, priority: 'foreground' })
  }
}
