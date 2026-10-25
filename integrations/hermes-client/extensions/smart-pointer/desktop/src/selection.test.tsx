import { createElement } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cropRegion, imagePoint, RevisionGate, sameScope } from './selection'
import { localApi } from '../../../../plugin/desktop/src/local-api'
import { localApi as judgeApi } from '../../../local-judge/desktop/src/local-api'
import { localApi as freeApi } from '../../../free-models/desktop/src/local-api'
import { localApi as pointerApi } from './local-api'
const mocks = vi.hoisted(() => ({ insert: vi.fn(async () => true), submit: vi.fn(), attach: vi.fn(async () => true), profile: 'default', connection: 'cloud', session: 'chat-one' }))
vi.mock('@hermes/plugin-sdk', async () => {
  const React = await import('react')
  return { Button: ({ variant, size, children, ...props }: any) => React.createElement('button', props, children),
    Input: (props: any) => React.createElement('input', props), Textarea: (props: any) => React.createElement('textarea', props),
    Loader: ({ label }: any) => React.createElement('div', { role: 'status' }, label),
    ErrorState: ({ title, description, children }: any) => React.createElement('div', { role: 'alert' }, title, description, children),
    Switch: ({ checked, onCheckedChange, ...props }: any) => React.createElement('input', { ...props, type: 'checkbox', checked, onChange: (event: any) => onCheckedChange(event.target.checked) }),
    SegmentedControl: ({ options, onChange }: any) => React.createElement('div', null, options.map((option: any) => React.createElement('button', { key: option.id, onClick: () => onChange(option.id) }, option.label))),
    ConfirmDialog: () => null, ROUTES_AREA: 'routes', SIDEBAR_NAV_AREA: 'nav', PANES_AREA: 'panes', PALETTE_AREA: 'palette', host: { state: {
      focusedStoredSessionId: { get: () => mocks.session }, focusedSessionId: { get: () => mocks.session },
      focusedSessionProfile: { get: () => mocks.profile }, connectionId: { get: () => mocks.connection },
      focusedSessionOwner: { get: () => ({ connectionId: mocks.connection, profile: mocks.profile }) }
    }, composer: { insertText: mocks.insert, submit: mocks.submit, attachImages: mocks.attach }, revealPane: vi.fn() } }
})
import { PointerPane } from './plugin'
import { ClientPage } from '../../../../plugin/desktop/src/plugin'
import { JudgeSettingsPage } from '../../../local-judge/desktop/src/plugin'
const ctx = { storage: { get: (_: string, fallback: unknown) => fallback }, rest: vi.fn() } as any
const captured = { status: 'ok', imageDataUrl: 'data:image/png;base64,aGVsbG8=', width: 1000, height: 500 }
beforeEach(() => {
  mocks.insert.mockClear(); mocks.submit.mockClear(); mocks.attach.mockClear(); mocks.connection = 'cloud'; mocks.profile = 'default'; mocks.session = 'chat-one'
  Object.defineProperty(window, 'PointerEvent', { value: MouseEvent, configurable: true }); Element.prototype.setPointerCapture = vi.fn()
  vi.spyOn(HTMLImageElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 200, height: 100, top: 0, left: 0, right: 200, bottom: 100, toJSON() {} })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() } as any)
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,Y3JvcA==')
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })
async function selectRegion() {
  fireEvent.click(screen.getByText('开始圈选'))
  const image = await screen.findByAltText('本机截图，拖动以圈选'); const target = image.parentElement!
  fireEvent.pointerDown(target, { button: 0, clientX: 10, clientY: 10, pointerId: 1 })
  fireEvent.pointerMove(target, { clientX: 80, clientY: 50, pointerId: 1 }); fireEvent.pointerUp(target, { clientX: 80, clientY: 50, pointerId: 1 })
  expect(await screen.findByAltText('已圈选区域预览')).toBeTruthy()
  fireEvent.change(screen.getByRole('textbox', { name: '选区原文 / 表格数据（可粘贴）' }), { target: { value: '原文示例' } })
}
it('machine operations stay local while active chat is cloud; unavailable bridge fails closed', async () => {
  const api = vi.fn(async () => ({ online: true })); (window as any).hermesDesktop = { api }
  await localApi('work')('/worker/start', { method: 'POST', body: {} })
  expect(api).toHaveBeenCalledWith({ path: '/api/plugins/orchestrator-core/worker/start', method: 'POST', body: {}, connectionId: 'local', profile: 'work', priority: 'foreground' })
  expect(ctx.rest).not.toHaveBeenCalled(); delete (window as any).hermesDesktop
  await expect(localApi('work')('/worker/start')).rejects.toThrow('本机 Hermes 代理不可用')
})
it.each([
  { name: 'core', makeApi: localApi, entry: '/extensions' },
  { name: 'judge', makeApi: judgeApi, entry: '/setup' },
  { name: 'free models', makeApi: freeApi, entry: '/models' },
  { name: 'pointer', makeApi: pointerApi, entry: '/sessions' }
])('$name explains unloaded static entries without misdiagnosing missing resources or restarting', async ({ makeApi, entry }) => {
  const api = vi.fn(); (window as any).hermesDesktop = { api }
  const request = makeApi('work')
  api.mockRejectedValueOnce(Object.assign(new Error('Not Found'), { statusCode: 404 }))
  await expect(request('/settings?reload=1')).rejects.toThrow('在原生插件页启用 Agent，重启对应本机代理，再重新连接')
  api.mockRejectedValueOnce(new Error("Error invoking remote method 'api': Error: 404: Not Found"))
  await expect(request(entry)).rejects.toThrow('在原生插件页启用 Agent，重启对应本机代理，再重新连接')
  for (const path of ['/tasks/missing', '/sessions/missing/commit', '/models/pull/missing', '/nodes/missing/revoke']) {
    const failure = new Error('404: Resource no longer exists'); api.mockRejectedValueOnce(failure)
    await expect(request(path)).rejects.toBe(failure)
  }
  for (const failure of [Object.assign(new Error('401: Unauthorized'), { statusCode: 401 }),
    new Error('500: A remote diagnostic mentioned 404: missing')]) {
    api.mockRejectedValueOnce(failure)
    await expect(request('/settings')).rejects.toBe(failure)
  }
  expect(api).toHaveBeenCalledTimes(8)
  expect(api.mock.calls.every(([value]) => value.connectionId === 'local' && value.profile === 'work')).toBe(true)
})
it('crop scales reverse drag to exact pixels and keeps newer revisions/source ownership', () => {
  const point = imagePoint({ x: 110, y: 60 }, { x: 10, y: 10, width: 200, height: 100 }, { width: 1000, height: 500 })
  expect(cropRegion(point, { x: 100, y: 20 })).toEqual({ x: 100, y: 20, width: 400, height: 230 })
  expect(cropRegion({ x: 0, y: 0 }, { x: 3, y: 100 })).toBeNull()
  const gate = new RevisionGate(), previous = gate.next(); gate.next(); expect(gate.accepts(previous)).toBe(false)
  expect(sameScope({ sessionId: 'x', profile: 'p', connectionId: 'a' }, { sessionId: 'x', profile: 'p', connectionId: 'b' })).toBe(false)
})
it('capture -> crop -> multiple choices -> preview -> exact composer draft, without image upload or send', async () => {
  const api = vi.fn(async (request: any) => request.path.endsWith('/sessions')
    ? { session_id: 'session', revision: 1, candidates: [{ action_id: 'format', label: '整理段落', description: '整理文档', preview_prompt: '请整理选中的段落，先给出修改建议。' }, { action_id: 'translate', label: '翻译', description: '翻译文字', preview_prompt: '翻译内容' }] }
    : { executed: false, draft_prompt: '请整理选中的段落，先给出修改建议。' })
  ;(window as any).hermesDesktop = { api, regionCapture: { capture: async () => captured } }
  render(createElement(PointerPane, { ctx })); await selectRegion(); fireEvent.click(screen.getByText('生成多个候选'))
  fireEvent.click(await screen.findByText('整理段落 — 整理文档'))
  const preview = await screen.findByRole('textbox', { name: '聊天草稿预览' })
  fireEvent.change(preview, { target: { value: '请按正式文体整理原文示例。' } })
  expect(mocks.insert).not.toHaveBeenCalled(); fireEvent.click(screen.getByText('确认加入聊天草稿'))
  await waitFor(() => expect(mocks.insert).toHaveBeenCalledWith('chat-one', '请按正式文体整理原文示例。', { mode: 'block' }))
  expect(mocks.submit).not.toHaveBeenCalled(); expect(mocks.attach).not.toHaveBeenCalled(); expect(JSON.stringify(api.mock.calls)).not.toContain('base64')
  expect(api.mock.calls[0][0].body.context.region).toEqual({ x: 50, y: 50, width: 350, height: 200 })
})
it('late candidates do not replace a new crop; switching connection blocks insertion', async () => {
  let resolveCandidates: (value: unknown) => void = () => undefined
  const api = vi.fn((request: any) => request.path.endsWith('/sessions') ? new Promise(resolve => { resolveCandidates = resolve }) : Promise.resolve({ executed: false, draft_prompt: 'new draft' }))
  ;(window as any).hermesDesktop = { api, regionCapture: { capture: async () => captured } }
  render(createElement(PointerPane, { ctx })); await selectRegion(); fireEvent.click(screen.getByText('生成多个候选'))
  const image = screen.getByAltText('本机截图，拖动以圈选').parentElement!
  fireEvent.pointerDown(image, { button: 0, clientX: 40, clientY: 20 }); fireEvent.pointerUp(image, { clientX: 100, clientY: 90 })
  await act(async () => resolveCandidates({ session_id: 'old', revision: 1, candidates: [{ action_id: 'old', label: '旧区域候选', description: '不应出现' }] }))
  expect(screen.queryByText('旧区域候选 — 不应出现')).toBeNull()
  fireEvent.change(screen.getByLabelText('选区原文 / 表格数据（可粘贴）'), { target: { value: '新选区原文' } })
  api.mockImplementation(async (request: any) => request.path.endsWith('/sessions') ? { session_id: 'new', revision: 2, candidates: [{ action_id: 'new', label: '新候选', description: '有效', preview_prompt: 'new draft' }] } : { executed: false, draft_prompt: 'new draft' })
  fireEvent.click(screen.getByText('生成多个候选')); fireEvent.click(await screen.findByText('新候选 — 有效')); await screen.findByText('确认加入聊天草稿')
  mocks.connection = 'another-cloud'; fireEvent.click(screen.getByText('确认加入聊天草稿'))
  expect(mocks.insert).not.toHaveBeenCalled(); expect(await screen.findByRole('alert')).toBeTruthy()
})

it('core node start invokes local agent and absent optional extensions have no controls', async () => {
  const api = vi.fn(async (request: any) => {
    if (request.path.endsWith('/settings')) return { server: { url: 'https://cloud.example' }, node: { owner_id: 'owner' }, extensions: {} }
    if (request.path.endsWith('/extensions')) return { extensions: [{ id: 'local-judge', installed: false }, { id: 'free-models', installed: false }] }
    if (request.path.endsWith('/nodes')) return { nodes: [] }
    if (request.path.endsWith('/worker/start')) return { running: true }
    throw new Error('Unexpected request ' + request.path)
  })
  ;(window as any).hermesDesktop = { api }
  render(createElement(ClientPage, { ctx }))
  await waitFor(() => expect((screen.getByText('启动本机任务接收') as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByText('启动本机任务接收'))
  await waitFor(() => expect(api.mock.calls.some(([request]) => request.path.endsWith('/worker/start'))).toBe(true))
  const start = api.mock.calls.find(([request]) => request.path.endsWith('/worker/start'))![0]
  expect(start.connectionId).toBe('local'); expect(start.profile).toBe('default')
  expect(screen.queryByText('local-judge')).toBeNull(); expect(screen.queryByText('free-models')).toBeNull()
  expect(api.mock.calls.every(([request]) => request.path.startsWith('/api/plugins/orchestrator-core/'))).toBe(true)
})
it('model weights are requested only after install click and progress reaches backend-confirmed complete', async () => {
  const api = vi.fn(async (request: any) => {
    if (request.path.endsWith('/settings')) return { base_url: 'http://127.0.0.1:11434', model: 'qwen3:4b', timeout: 5 }
    if (request.path.endsWith('/models/pull')) return { job_id: 'download', status: 'queued', model: 'qwen3:4b', progress: 0 }
    if (request.path.endsWith('/models/pull/download')) return { job_id: 'download', status: 'complete', model: 'qwen3:4b', progress: 1 }
    throw new Error('Unexpected request')
  })
  ;(window as any).hermesDesktop = { api }
  const scoped = { ...ctx, setTimeout: (callback: () => void) => { let active = true; Promise.resolve().then(() => { if (active) callback() }); return () => { active = false } } }
  render(createElement(JudgeSettingsPage, { ctx: scoped }))
  await waitFor(() => expect((screen.getByText('安装所选本地模型') as HTMLButtonElement).disabled).toBe(false))
  expect(api.mock.calls.some(([request]) => request.path.includes('/models/pull'))).toBe(false)
  fireEvent.click(screen.getByText('安装所选本地模型'))
  await waitFor(() => expect(screen.getByText(/"status": "complete"/)).toBeTruthy())
  expect(api.mock.calls.find(([request]) => request.path.endsWith('/models/pull'))![0].body).toEqual({ model: 'qwen3:4b' })
})
it('native screen crop is ready for intent generation without forcing a second drag', async () => {
  ;(window as any).hermesDesktop = { api: vi.fn(), regionCapture: { capture: async () => ({ ...captured, selectionMode: 'screen-region', region_already_cropped: true }) } }
  render(createElement(PointerPane, { ctx })); fireEvent.click(screen.getByText('开始圈选'))
  await screen.findByAltText('已圈选区域预览')
  expect((screen.getByText('生成多个候选') as HTMLButtonElement).disabled).toBe(false)
})

it('keyword edits discard preview, disable old actions and send only the new delta', async () => {
  const api = vi.fn(async (request: any) => ({ session_id: 'keywords', revision: request.path.endsWith('/refine') ? 2 : 1,
    keywords: request.path.endsWith('/refine') ? '整理\n摘要' : '整理',
    candidates: [{ action_id: 'format', label: '整理', description: '按关键词整理', preview_prompt: '请整理' }] }))
  ;(window as any).hermesDesktop = { api, regionCapture: { capture: async () => captured } }
  render(createElement(PointerPane, { ctx })); await selectRegion()
  fireEvent.change(screen.getByRole('textbox', { name: '关键词' }), { target: { value: '整理' } }); fireEvent.click(screen.getByText('生成多个候选'))
  fireEvent.click(await screen.findByText('整理 — 按关键词整理'))
  expect(screen.getByText('确认加入聊天草稿')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('关键词'), { target: { value: '摘要' } })
  expect(screen.queryByText('确认加入聊天草稿')).toBeNull()
  expect((screen.getByText('整理 — 按关键词整理') as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByText('追加关键词 / 换一批'))
  await waitFor(() => expect(api.mock.calls.some(([request]) => request.path.endsWith('/refine'))).toBe(true))
  expect(api.mock.calls.find(([request]) => request.path.endsWith('/refine'))![0].body.keywords).toBe('摘要')
  await waitFor(() => expect((screen.getByLabelText('关键词') as HTMLInputElement).value).toBe(''))
  expect(mocks.insert).not.toHaveBeenCalled(); expect(mocks.submit).not.toHaveBeenCalled()
})
it('cropped image attachment requires an explicit checkbox and final confirmation; failed ACK never claims success', async () => {
  const api = vi.fn(async (request: any) => request.path.endsWith('/sessions')
    ? { session_id: 'image', revision: 1, candidates: [{ action_id: 'inspect', label: '解释', description: '解释图片', preview_prompt: '请解释图片' }] }
    : { executed: false, draft_prompt: '请解释图片' })
  ;(window as any).hermesDesktop = { api, regionCapture: { capture: async () => ({ ...captured, region_already_cropped: true, selectionMode: 'screen-region' }) } }
  render(createElement(PointerPane, { ctx })); fireEvent.click(screen.getByText('开始圈选')); await screen.findByAltText('已圈选区域预览')
  fireEvent.click(screen.getByText('生成多个候选')); fireEvent.click(await screen.findByText('解释 — 解释图片'))
  expect((screen.getByText('确认加入聊天草稿') as HTMLButtonElement).disabled).toBe(true)
  expect(mocks.attach).not.toHaveBeenCalled()
  fireEvent.click(screen.getByLabelText('附上所选区域图片'))
  mocks.attach.mockResolvedValueOnce(false)
  fireEvent.click(screen.getByText('确认加入聊天草稿'))
  await waitFor(() => expect(mocks.attach).toHaveBeenCalledTimes(1)); expect(mocks.insert).not.toHaveBeenCalled()
  expect(await screen.findByRole('alert')).toBeTruthy()
  expect(screen.queryByText('草稿和所选区域图片已加入原生聊天。请检查后在聊天中发送。')).toBeNull()
  fireEvent.click(screen.getByText('确认加入聊天草稿'))
  await waitFor(() => expect(mocks.insert).toHaveBeenCalledWith('chat-one', '请解释图片', { mode: 'block' }))
  const [target, images] = mocks.attach.mock.calls[1] as unknown as [string, Blob[]]
  expect(target).toBe('chat-one'); expect(images).toHaveLength(1); expect(images[0].type).toBe('image/png')
  expect(mocks.submit).not.toHaveBeenCalled()
})

it('revoking image consent while final commit waits invalidates delivery before attachment', async () => {
  let finishCommit: (value: unknown) => void = () => undefined
  const api = vi.fn((request: any) => request.path.endsWith('/sessions')
    ? Promise.resolve({ session_id: 'consent', revision: 1, candidates: [{ action_id: 'inspect', label: '分析选区', description: '检查内容', preview_prompt: '请分析选区' }] })
    : new Promise(resolve => { finishCommit = resolve }))
  ;(window as any).hermesDesktop = { api, regionCapture: { capture: async () => ({ ...captured, region_already_cropped: true, selectionMode: 'screen-region' }) } }
  render(createElement(PointerPane, { ctx }))
  fireEvent.click(screen.getByText('开始圈选')); await screen.findByAltText('已圈选区域预览')
  fireEvent.click(screen.getByText('生成多个候选')); fireEvent.click(await screen.findByText('分析选区 — 检查内容'))
  fireEvent.click(screen.getByLabelText('附上所选区域图片'))
  fireEvent.click(screen.getByText('确认加入聊天草稿'))
  expect((screen.getByLabelText('聊天草稿预览') as HTMLTextAreaElement).disabled).toBe(true)
  expect((screen.getByLabelText('选区原文 / 表格数据（可粘贴）') as HTMLTextAreaElement).disabled).toBe(true)
  expect(mocks.attach).not.toHaveBeenCalled()
  fireEvent.click(screen.getByLabelText('附上所选区域图片'))
  expect((screen.getByLabelText('附上所选区域图片') as HTMLInputElement).checked).toBe(false)
  await act(async () => finishCommit({ draft_prompt: '请分析选区', executed: false }))
  expect(mocks.attach).not.toHaveBeenCalled(); expect(mocks.insert).not.toHaveBeenCalled()
  expect(screen.queryByText('确认加入聊天草稿')).toBeNull()
})
