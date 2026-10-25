import { createElement } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ navigate: vi.fn(), ensure: vi.fn(async () => undefined), open: vi.fn(async (_route: any) => undefined), pick: vi.fn(async () => '/snapshots/usage.json') }))
vi.mock('@hermes/plugin-sdk', async () => {
  const React = await import('react')
  return { Button: ({ variant, size, children, ...props }: any) => React.createElement('button', props, children),
    Input: (props: any) => React.createElement('input', props), Textarea: (props: any) => React.createElement('textarea', props),
    Loader: ({ label }: any) => React.createElement('div', { role: 'status' }, label),
    ErrorState: ({ title, description, children }: any) => React.createElement('div', { role: 'alert' }, title, description, children),
    ConfirmDialog: () => null, Switch: () => null, ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav',
    host: { navigate: mocks.navigate, ensureAgent: mocks.ensure, openLocalProviderSettings: mocks.open } }
})
import { host } from '@hermes/plugin-sdk'
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
beforeEach(() => { mocks.navigate.mockClear(); mocks.ensure.mockClear(); mocks.pick.mockClear(); mocks.open.mockReset(); mocks.open.mockResolvedValue(undefined); (host as any).openLocalProviderSettings = mocks.open })
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
  expect(within(known).getByText(/来源：Codex 官方用量快照/)).toBeTruthy()
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
  await waitFor(() => expect(mocks.open).toHaveBeenCalledWith({ connectionId: 'local', profile: 'work', signal: expect.any(AbortSignal) }))
  expect(mocks.navigate).not.toHaveBeenCalled()
  expect(mocks.ensure).not.toHaveBeenCalled()
  expect(api).toHaveBeenCalledTimes(1)
})

it('older clients without explicit native settings scope show an upgrade action without ambient navigation', async () => {
  delete (host as any).openLocalProviderSettings
  ;(window as any).hermesDesktop = { api: vi.fn(async () => snapshot([])) }
  render(createElement(DashboardPage, { ctx: context('work') }))
  fireEvent.click(await screen.findByRole('button', { name: '打开原生账号设置' }))
  expect((await screen.findByRole('alert')).textContent).toContain('请升级 MISAKA AGENT 客户端后重试')
  expect(mocks.navigate).not.toHaveBeenCalled(); expect(mocks.ensure).not.toHaveBeenCalled()
})

it('changing the local configuration cancels pending native account navigation', async () => {
  let finish: () => void = () => undefined
  mocks.open.mockImplementationOnce(async () => new Promise<void>(resolve => { finish = resolve }))
  ;(window as any).hermesDesktop = { api: vi.fn(async () => snapshot([])) }
  render(createElement(DashboardPage, { ctx: context() }))
  fireEvent.click(await screen.findByRole('button', { name: '打开原生账号设置' }))
  const signal = mocks.open.mock.calls[0][0].signal as AbortSignal
  expect(signal.aborted).toBe(false)
  fireEvent.change(screen.getByRole('textbox', { name: '额度本机配置' }), { target: { value: 'work' } })
  fireEvent.click(screen.getByRole('button', { name: '连接此本机配置' }))
  expect(signal.aborted).toBe(true)
  await act(async () => finish())
  expect(mocks.navigate).not.toHaveBeenCalled()
})

it('partial cloud accounts retain their valid windows while blocked and unknown windows stay unmetered', async () => {
  const data = snapshot([])
  data.cloud = { configured: true, state: 'partial', source: 'fleet-ledger', unknown_reason: '部分配额仍待核对。', accounts: [
    { ...account('部分可用账本'), state: 'partial', windows: [account().windows[0],
      { ...account().windows[0], id: 'unknown-window', label: '待核对窗口', state: 'unknown' }] },
    { ...account('冻结账本'), state: 'blocked', unknown_reason: '上次任务的用量尚未核对。' }
  ] }
  ;(window as any).hermesDesktop = { api: vi.fn(async () => data) }
  render(createElement(DashboardPage, { ctx: context() }))
  const partial = await screen.findByRole('article', { name: '部分可用账本 额度' })
  expect(within(partial).getAllByRole('progressbar')).toHaveLength(1)
  expect(within(partial).getByText('剩余 40%')).toBeTruthy()
  expect(within(within(partial).getByRole('region', { name: '待核对窗口' })).queryByRole('progressbar')).toBeNull()
  const blocked = screen.getByRole('article', { name: '冻结账本 额度' })
  expect(within(blocked).getByText('暂不可调度')).toBeTruthy()
  expect(within(blocked).queryByRole('progressbar')).toBeNull()
  expect(within(blocked).getByText('上次任务的用量尚未核对。')).toBeTruthy()
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
  expect((await screen.findByRole('alert')).textContent).toContain('在原生插件页启用智能体功能，重启对应本机代理，再重新连接')
  const register = vi.fn()
  plugin.register({ register, registerSettingsPage: vi.fn() } as any)
  expect(register.mock.calls.some(([entry]) => entry.area === 'nav' && entry.data.label === '额度总览' && entry.data.path === '/dashboard')).toBe(true)
  expect(register.mock.calls.some(([entry]) => entry.area === 'routes' && entry.data.path === '/dashboard' && typeof entry.render === 'function')).toBe(true)
})

it('explains unsupported Copilot usage and an unconfigured cloud in Chinese without inventing zero quota', async () => {
  const data = snapshot([{ ...account('copilot'), provider: 'copilot', state: 'unknown', windows: [],
    unknown_reason: '此服务商暂不支持自动读取额度，可导入已核实的数据。' }])
  data.cloud.unknown_reason = 'No cloud quota server is configured.'
  const original = JSON.stringify(data)
  ;(window as any).hermesDesktop = { api: vi.fn(async () => data) }
  render(createElement(DashboardPage, { ctx: context() }))
  const card = await screen.findByRole('article', { name: 'GitHub Copilot 额度' })
  expect(within(card).getByText(/当前尚不支持读取其用量接口，额度未获取/)).toBeTruthy()
  expect(within(card).queryByRole('progressbar')).toBeNull()
  expect(within(card).queryByText(/剩余 0/)).toBeNull()
  expect(screen.getByText(/尚未配置云端服务/)).toBeTruthy()
  expect(screen.queryByText('No cloud quota server is configured.')).toBeNull()
  expect(screen.queryByText(/来源：fleet-ledger/)).toBeNull()
  expect(JSON.stringify(data)).toBe(original)
})

it('keeps actual currency balances separate from subscription percentages and translates their metadata', async () => {
  const data = snapshot([{ ...account('订阅账号'), provider: 'anthropic', source: 'official-usage-api',
    windows: [{ ...account().windows[0], label: 'Session', scope: 'account' }] },
  { ...account('API 账号'), provider: 'openrouter', source: 'official-usage-api',
    windows: [{ ...account().windows[0], label: 'API 余额', unit: 'USD', capacity: 10, used: 10, remaining: 0, scope: 'api_key' }] }])
  const original = JSON.stringify(data)
  ;(window as any).hermesDesktop = { api: vi.fn(async () => data) }
  render(createElement(DashboardPage, { ctx: context() }))
  const subscription = await screen.findByRole('article', { name: '订阅账号 额度' })
  expect(within(subscription).getByText('剩余 40%')).toBeTruthy()
  expect(within(subscription).getByText(/订阅窗口百分比/)).toBeTruthy()
  expect(within(subscription).getByText('范围：账号内共享')).toBeTruthy()
  const currency = screen.getByRole('article', { name: 'API 账号 额度' })
  expect(within(currency).getByText('剩余 0 美元')).toBeTruthy()
  expect(within(currency).queryByText(/订阅窗口百分比/)).toBeNull()
  expect(within(currency).getByText('范围：API 密钥限额')).toBeTruthy()
  expect(within(currency).getByRole('progressbar').getAttribute('value')).toBe('100')
  expect(JSON.stringify(data)).toBe(original)
})

it('does not display arbitrary native transport error text or credential-shaped URLs', async () => {
  ;(window as any).hermesDesktop = { api: vi.fn(async () => { throw new Error('503: https://example.invalid/?api_key=synthetic-private-value') }) }
  render(createElement(DashboardPage, { ctx: context() }))
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('请检查本机连接、账号状态或导入文件后重试')
  expect(alert.textContent).not.toContain('example.invalid')
  expect(alert.textContent).not.toContain('synthetic-private-value')
})
