import { createElement } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ navigate: vi.fn(), ensure: vi.fn(async () => undefined), pick: vi.fn(async () => '/snapshots/usage.json') }))
vi.mock('@hermes/plugin-sdk', async () => {
  const React = await import('react')
  return { Button: ({ variant, size, children, ...props }: any) => React.createElement('button', props, children),
    Input: (props: any) => React.createElement('input', props), Textarea: (props: any) => React.createElement('textarea', props),
    Loader: ({ label }: any) => React.createElement('div', { role: 'status' }, label),
    ErrorState: ({ title, description, children }: any) => React.createElement('div', { role: 'alert' }, title, description, children),
    ConfirmDialog: () => null, Switch: () => null, ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav',
    host: { navigate: mocks.navigate, ensureAgent: mocks.ensure } }
})
import { DashboardPage, type DashboardSnapshot } from './dashboard'
import plugin from './plugin'

function context(profile = 'default') {
  return { storage: { get: () => profile, set: vi.fn() }, os: { pickOpenPath: mocks.pick },
    setTimeout: (callback: () => void) => { let active = true; Promise.resolve().then(() => { if (active) callback() }); return () => { active = false } }
  } as any
}
function account(label = '工作账号') {
  return { id: label, provider: 'Codex', label, state: 'ready', source: 'codex-rate-limit-snapshot', observed_at: 1792886400,
    expires_at: 1792890000, unknown_reason: null, windows: [{ id: '5h', label: '5 小时窗口', unit: 'percent', capacity: 100,
      used: 60, remaining: 40, reset_at: 1792897200, scope: '账号内共享', state: 'ready' }] }
}
function snapshot(accounts = [account()]): DashboardSnapshot {
  return { accounts, refreshing: false, refreshed_at: 1792886400, notes: [],
    cloud: { configured: false, state: 'unknown', source: 'fleet-ledger', accounts: [], unknown_reason: '尚未配置私网调度服务。' },
    node: { execution_location: 'this_backend_host', platform: 'linux', running: false, status: 'stopped' } }
}
beforeEach(() => { mocks.navigate.mockClear(); mocks.ensure.mockClear(); mocks.pick.mockClear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('reads cached records locally and keeps unknown quota distinct from a real zero or provider balance', async () => {
  const unknown = { ...account('未登录账号'), state: 'unknown', unknown_reason: '未登录此服务，无法读取真实额度。',
    windows: [{ ...account().windows[0], state: 'unknown', used: 0, remaining: 100 }] }
  const data = snapshot([account(), unknown])
  data.cloud = { configured: true, state: 'ready', source: 'fleet-ledger', unknown_reason: null,
    accounts: [{ ...account('共享工作配额'), provider: 'private-fleet', source: 'fleet-ledger', windows: [{
      ...account().windows[0], unit: 'requests', used: 20, remaining: 80, held: 5, available: 75 }] }] }
  const api = vi.fn(async () => data); (window as any).hermesDesktop = { api }
  render(createElement(DashboardPage, { ctx: context('work') }))
  const known = await screen.findByRole('article', { name: '工作账号 额度' })
  expect(within(known).getByRole('progressbar', { name: '5 小时窗口已用比例' }).getAttribute('value')).toBe('60')
  expect(within(known).getByText('剩余 40%')).toBeTruthy()
  expect(within(known).getByText(/来源：codex-rate-limit-snapshot/)).toBeTruthy()
  expect(within(known).getByText(/下次重置：/).textContent).not.toContain('未提供')
  const missing = screen.getByRole('article', { name: '未登录账号 额度' })
  expect(within(missing).getByText('未登录此服务，无法读取真实额度。')).toBeTruthy()
  expect(within(missing).queryByRole('progressbar')).toBeNull()
  expect(within(missing).queryByText(/0%/)).toBeNull()
  const cloud = screen.getByRole('article', { name: '共享工作配额 额度' })
  expect(within(cloud).getByText('已占用 5 次')).toBeTruthy()
  expect(within(cloud).getByText('可调度 75 次')).toBeTruthy()
  expect(api).toHaveBeenCalledTimes(1)
  expect(api).toHaveBeenCalledWith({ path: '/api/plugins/orchestrator-core/quota/dashboard', connectionId: 'local', profile: 'work', priority: 'foreground' })
  expect(mocks.ensure).not.toHaveBeenCalled()
})

it('refreshes only after the explicit button, then polls the same local profile for the real result', async () => {
  let refreshing = false
  const initial = snapshot()
  const updated = snapshot([{ ...account(), windows: [{ ...account().windows[0], used: 80, remaining: 20 }] }])
  const api = vi.fn(async (request: any) => {
    if (request.path.endsWith('/quota/refresh')) { refreshing = true; return { ...initial, refreshing: true } }
    return refreshing ? updated : initial
  }); (window as any).hermesDesktop = { api }
  render(createElement(DashboardPage, { ctx: context('work') }))
  await screen.findByText('剩余 40%')
  expect(api.mock.calls.some(([request]) => request.method === 'POST')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '读取最新额度' }))
  await screen.findByText('剩余 20%')
  expect(api.mock.calls.filter(([request]) => request.method === 'POST')).toHaveLength(1)
  expect(api.mock.calls.find(([request]) => request.method === 'POST')![0]).toEqual({ path: '/api/plugins/orchestrator-core/quota/refresh', method: 'POST', body: {}, connectionId: 'local', profile: 'work', priority: 'foreground' })
  expect(api.mock.calls.every(([request]) => request.connectionId === 'local' && request.profile === 'work')).toBe(true)
})

it('discards a late response from the previous local profile', async () => {
  let finishOld: (value: DashboardSnapshot) => void = () => undefined
  const api = vi.fn((request: any) => request.profile === 'default'
    ? new Promise(resolve => { finishOld = resolve }) : Promise.resolve(snapshot([account('新配置账号')])))
  ;(window as any).hermesDesktop = { api }
  render(createElement(DashboardPage, { ctx: context() }))
  fireEvent.change(screen.getByRole('textbox', { name: '额度本机配置' }), { target: { value: 'work' } })
  fireEvent.click(screen.getByRole('button', { name: '连接此本机配置' }))
  await screen.findByText('新配置账号')
  await act(async () => finishOld(snapshot([account('旧配置账号')])))
  expect(screen.queryByText('旧配置账号')).toBeNull()
  expect(screen.getByText('新配置账号')).toBeTruthy()
})

it('opens native account settings on the selected local source without manufacturing quota', async () => {
  const api = vi.fn(async () => snapshot([])); (window as any).hermesDesktop = { api }
  render(createElement(DashboardPage, { ctx: context('work') }))
  await screen.findByText('尚无可显示的账号用量')
  expect(screen.queryByRole('progressbar')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '打开原生账号设置' }))
  await waitFor(() => expect(mocks.navigate).toHaveBeenCalledWith('/settings?tab=providers&pview=accounts'))
  expect(mocks.ensure).toHaveBeenCalledWith('local', 'work')
  expect(mocks.ensure.mock.invocationCallOrder[0]).toBeLessThan(mocks.navigate.mock.invocationCallOrder[0])
  expect(api).toHaveBeenCalledTimes(1)
})

it('imports only an explicitly selected JSON with the user supplied collection time', async () => {
  const api = vi.fn(async () => snapshot([])); (window as any).hermesDesktop = { api }
  render(createElement(DashboardPage, { ctx: context('work') }))
  await waitFor(() => expect((screen.getByRole('button', { name: '读取最新额度' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByText('导入已有用量快照'))
  fireEvent.change(screen.getByLabelText('快照格式'), { target: { value: 'codex' } })
  expect((screen.getByRole('button', { name: '选择 JSON 并导入' }) as HTMLButtonElement).disabled).toBe(true)
  expect(mocks.pick).not.toHaveBeenCalled()
  const collected = '2026-10-25T00:00'
  fireEvent.change(screen.getByLabelText('快照实际采集时间'), { target: { value: collected } })
  fireEvent.change(screen.getByLabelText('快照账号名称'), { target: { value: '工作订阅' } })
  fireEvent.click(screen.getByRole('button', { name: '选择 JSON 并导入' }))
  await waitFor(() => expect(api.mock.calls.some(([request]) => request.path.endsWith('/quota/import'))).toBe(true))
  expect(api.mock.calls.find(([request]) => request.path.endsWith('/quota/import'))![0].body).toEqual({
    format: 'codex', path: '/snapshots/usage.json', observed_at: new Date(collected).valueOf() / 1000, account_label: '工作订阅' })
  expect(api.mock.calls.some(([request]) => request.path.endsWith('/quota/refresh'))).toBe(false)
})

it('gives a missing Dashboard entry the restart guidance and registers a real clickable native route', async () => {
  const api = vi.fn(async () => { throw new Error('404: Not Found') }); (window as any).hermesDesktop = { api }
  render(createElement(DashboardPage, { ctx: context() }))
  expect((await screen.findByRole('alert')).textContent).toContain('在原生插件页启用 Agent，重启对应本机代理，再重新连接')
  const register = vi.fn()
  plugin.register({ register, registerSettingsPage: vi.fn() } as any)
  expect(register.mock.calls.some(([entry]) => entry.area === 'nav' && entry.data.label === 'Dashboard' && entry.data.path === '/dashboard')).toBe(true)
  expect(register.mock.calls.some(([entry]) => entry.area === 'routes' && entry.data.path === '/dashboard' && typeof entry.render === 'function')).toBe(true)
})
