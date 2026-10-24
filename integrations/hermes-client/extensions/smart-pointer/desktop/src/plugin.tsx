import { Button, ErrorState, Input, Loader, SegmentedControl, Switch, Textarea, host,
  PANES_AREA, PALETTE_AREA, type HermesPlugin, type PluginContext } from '@hermes/plugin-sdk'
import { localApi } from './local-api'
import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { cropRegion, imagePoint, RevisionGate, sameScope, type ComposerScope, type Point, type Region } from './selection'

interface Capture { status: 'ok'; imageDataUrl: string; width: number; height: number; selectionMode?: 'image-crop' | 'screen-region'; region_already_cropped?: boolean;
  appContext?: { sourceName?: string; sourceId?: string; appName?: string; windowTitle?: string } }
interface NativeCapture { capture(): Promise<Capture | { status: 'cancelled' | 'unsupported' | 'denied'; message?: string }> }
interface Candidate { action_id: string; label: string; description: string; preview_prompt: string }
interface IntentSession { session_id: string; revision: number; candidates: Candidate[]; question?: string | null; keywords?: string }
type Kind = 'auto' | 'document' | 'spreadsheet' | 'unknown'
const scope = (): ComposerScope => ({ sessionId: host.state.focusedStoredSessionId.get() ?? host.state.focusedSessionId.get() ?? 'new',
  profile: host.state.focusedSessionOwner.get()?.profile ?? host.state.focusedSessionProfile.get(), connectionId: host.state.focusedSessionOwner.get()?.connectionId ?? host.state.connectionId.get() })
const CSS = `.pointer-pane{padding:16px;display:flex;flex-direction:column;gap:16px;color:var(--ui-text-primary);height:100%;overflow:auto}.pointer-pane p{color:var(--ui-text-secondary);font-size:12px;margin:0}.pointer-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.pointer-image{position:relative;line-height:0;touch-action:none;user-select:none;cursor:crosshair;width:100%}.pointer-image img{display:block;width:100%;height:auto;pointer-events:none}.pointer-region{position:absolute;border:2px solid var(--ui-accent);background:var(--ui-bg-quaternary);opacity:.45;pointer-events:none}.pointer-intents{display:grid;gap:8px}.pointer-intents button{white-space:normal;text-align:left;justify-content:flex-start;height:auto}.pointer-chips{display:flex;gap:6px;flex-wrap:wrap}.pointer-preview{max-height:180px;object-fit:contain}.pointer-label{font-size:12px;font-weight:600}.pointer-pane textarea{min-height:110px}`

export function PointerPane({ ctx }: { ctx: PluginContext }) {
  const [localProfile, setLocalProfile] = useState(() => ctx.storage.get('local-profile', 'default'))
  const [profileInput, setProfileInput] = useState(localProfile)
  const api = localApi(localProfile)
  const [localSettings, setLocalSettings] = useState({ enabled: false, model: 'qwen3:4b', base_url: 'http://127.0.0.1:11434', timeout: 2 })
  const [capture, setCapture] = useState<Capture | null>(null)
  const [region, setRegion] = useState<Region | null>(null)
  const [crop, setCrop] = useState('')
  const [kind, setKind] = useState<Kind>('auto')
  const [keywords, setKeywords] = useState('')
  const [session, setSession] = useState<IntentSession | null>(null)
  const [draft, setDraft] = useState('')
  const [selectedAction, setSelectedAction] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const image = useRef<HTMLImageElement>(null)
  const start = useRef<Point | null>(null)
  const dragging = useRef(false)
  const gate = useRef(new RevisionGate())
  const address = useRef<ComposerScope | null>(null)
  useEffect(() => () => { gate.current.next() }, [])

  function invalidate() { gate.current.next(); setSession(null); setDraft(''); setSelectedAction(''); setDirty(false); setBusy(false); setError(''); setNotice('') }

  function editKeywords(value: string) {
    gate.current.next(); setDraft(''); setSelectedAction(''); setDirty(true); setError(''); setNotice('')
    // If a refinement is in flight, its server revision may advance after we
    // discard it. Start a new session from the known context instead of using
    // the stale revision on the next request.
    if (busy && session) { setKeywords([session.keywords, value].filter(Boolean).join(' ')); setSession(null) }
    else setKeywords(value)
    setBusy(false)
  }

  async function loadLocalSettings() {
    try { const config = await api<{ local_suggestions: typeof localSettings }>('/settings'); setLocalSettings(config.local_suggestions) }
    catch (failure) { setError(String(failure)) }
  }
  async function saveLocalSettings() {
    try { const config = await api<{ local_suggestions: typeof localSettings }>('/settings', { method: 'POST', body: { local_suggestions: localSettings } }); setLocalSettings(config.local_suggestions); invalidate(); setNotice('本地意图建议设置已保存，旧候选已清除。') }
    catch (failure) { setError(String(failure)) }
  }

  async function beginCapture() {
    invalidate(); setCapture(null); setCrop(''); setRegion(null)
    const generation = gate.current.next()
    address.current = scope()
    const bridge = (window as unknown as { hermesDesktop?: { regionCapture?: NativeCapture } }).hermesDesktop?.regionCapture
    if (!bridge) { setError('当前桌面版本不提供圈选能力，请安装匹配版本。'); return }
    setBusy(true)
    try {
      const result = await bridge.capture()
      if (!gate.current.accepts(generation)) return
      if (result.status === 'ok') {
        if (!result.imageDataUrl.startsWith('data:image/png;base64,') || result.width <= 0 || result.height <= 0) throw new Error('截图格式无效')
        setCapture(result); setKind('auto')
        if (result.region_already_cropped === true) { setRegion({ x: 0, y: 0, width: result.width, height: result.height }); setCrop(result.imageDataUrl) }
      } else if (result.status !== 'cancelled') setError(result.message || '系统暂不允许截图，请检查屏幕录制权限。')
    } catch (failure) { if (gate.current.accepts(generation)) setError(String(failure)) }
    finally { if (gate.current.accepts(generation)) setBusy(false) }
  }

  function point(event: PointerEvent<HTMLDivElement>): Point | null {
    if (!capture || !image.current) return null
    const bounds = image.current.getBoundingClientRect()
    return imagePoint({ x: event.clientX, y: event.clientY }, { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }, capture)
  }
  function pointerDown(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !capture) return
    invalidate(); setRegion(null); setCrop(''); start.current = point(event); dragging.current = true
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    const end = point(event)
    if (dragging.current && start.current && end) setRegion(cropRegion(start.current, end))
  }
  function pointerUp(event: PointerEvent<HTMLDivElement>) {
    const end = point(event)
    dragging.current = false
    if (!capture || !start.current || !end || !image.current) return
    const selected = cropRegion(start.current, end); start.current = null; setRegion(selected)
    if (!selected) { setError('请拖出至少 4 × 4 像素的区域。'); return }
    const canvas = document.createElement('canvas'); canvas.width = selected.width; canvas.height = selected.height
    const context = canvas.getContext('2d')
    if (!context) { setError('无法预览圈选区域。'); return }
    context.drawImage(image.current, selected.x, selected.y, selected.width, selected.height, 0, 0, selected.width, selected.height)
    setCrop(canvas.toDataURL('image/png'))
  }

  async function propose(replace = false) {
    if (!capture || !region || busy) return
    const generation = gate.current.next(); setBusy(true); setError(''); setDraft(''); setNotice('')
    try {
      const result = session && !replace
        ? await api<IntentSession>(`/sessions/${encodeURIComponent(session.session_id)}/refine`, { method: 'POST', body: {
          revision: session.revision, keywords, reject_action_ids: session.candidates.map(item => item.action_id) } })
        : await api<IntentSession>('/sessions', { method: 'POST', body: { keywords, context: { kind,
          app: capture.appContext?.appName || '', window_title: capture.appContext?.windowTitle || '',
          sourceName: capture.appContext?.sourceName || '', region, region_id: crypto.randomUUID() } } })
      if (gate.current.accepts(generation)) { setSession(result); setKeywords(''); setDirty(false) }
    } catch (failure) { if (gate.current.accepts(generation)) setError(String(failure)) }
    finally { if (gate.current.accepts(generation)) setBusy(false) }
  }

  function choose(candidate: Candidate) {
    if (busy || dirty) return
    setSelectedAction(candidate.action_id); setDraft(candidate.preview_prompt); setError(''); setNotice('')
  }

  async function insertDraft() {
    if (!draft || !address.current || !session || !selectedAction || dirty) return
    if (!sameScope(address.current, scope())) { setError('聊天或连接已切换，请返回开始圈选时的聊天后再添加草稿。'); return }
    const generation = gate.current.next(); setBusy(true); setError('')
    try {
      const committed = await api<{ draft_prompt: string; executed: false }>(`/sessions/${encodeURIComponent(session.session_id)}/commit`, {
        method: 'POST', body: { revision: session.revision, action_id: selectedAction } })
      if (!gate.current.accepts(generation)) return
      if (committed.executed !== false || typeof committed.draft_prompt !== 'string') throw new Error('候选没有返回可确认的草稿。')
      if (!sameScope(address.current, scope())) throw new Error('聊天或连接已切换，请返回原聊天后再添加草稿。')
      const inserted = await host.composer.insertText(address.current.sessionId, draft, { mode: 'block' })
      if (!inserted) setError('当前聊天输入框未就绪，草稿仍保留在此处。')
      else setNotice('草稿已加入原生聊天输入框。请检查后在聊天中发送。')
    } catch (failure) { if (gate.current.accepts(generation)) setError(String(failure)) }
    finally { if (gate.current.accepts(generation)) setBusy(false) }
  }

  return <section className="pointer-pane" aria-label="智能指针"><style>{CSS}</style>
    <div className="pointer-row"><strong>智能指针</strong><Button size="sm" onClick={beginCapture} disabled={busy}>开始圈选</Button>
      <Button size="sm" variant="ghost" onClick={() => { invalidate(); setCapture(null); setRegion(null); setCrop('') }}>清除</Button></div>
    <label className="pointer-label">本机代理 profile<Input value={profileInput} onChange={event => setProfileInput(event.target.value)} /></label>
    <Button size="sm" variant="secondary" disabled={busy || !profileInput.trim()} onClick={() => { try { localApi(profileInput.trim()); ctx.storage.set('local-profile', profileInput.trim()); setLocalProfile(profileInput.trim()); invalidate(); setCapture(null); setCrop(''); setRegion(null) } catch (failure) { setError(String(failure)) } }}>连接此本机代理</Button>
    <p>截图只保留在内存。请在图片内拖动圈选；候选只生成草稿，发送由你确认。</p>
    <details onToggle={event => { if (event.currentTarget.open) loadLocalSettings() }}><summary>本地意图建议（可选）</summary>
      <div className="pointer-row"><span>本机模型建议</span><Switch aria-label="启用本机意图建议" checked={localSettings.enabled} onCheckedChange={enabled => setLocalSettings(value => ({ ...value, enabled }))} /></div>
      <label className="pointer-label">本机 Ollama 地址<Input value={localSettings.base_url} onChange={event => setLocalSettings(value => ({ ...value, base_url: event.target.value }))} /></label>
      <label className="pointer-label">本地模型<Input value={localSettings.model} onChange={event => setLocalSettings(value => ({ ...value, model: event.target.value }))} /></label>
      <label className="pointer-label">超时（秒）<Input type="number" min={0.05} max={5} step={0.05} value={localSettings.timeout} onChange={event => setLocalSettings(value => ({ ...value, timeout: Number(event.target.value) }))} /></label>
      <Button size="sm" variant="secondary" onClick={saveLocalSettings}>保存建议设置</Button><p>默认使用确定性候选；开启后仅请求已安装的本机模型，截图仍不上传。</p>
    </details>
    {busy && <Loader label="正在准备候选" />}
    {error && <ErrorState title="暂时无法继续" description={error}><Button variant="secondary" onClick={() => setError('')}>返回操作</Button></ErrorState>}
    {capture && <><div className="pointer-image" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
      onPointerCancel={() => { dragging.current = false; start.current = null; invalidate(); setRegion(null); setCrop('') }}>
      <img ref={image} src={capture.imageDataUrl} alt="本机截图，拖动以圈选" draggable={false} />
      {region && <div className="pointer-region" style={{ left: `${region.x / capture.width * 100}%`, top: `${region.y / capture.height * 100}%`,
        width: `${region.width / capture.width * 100}%`, height: `${region.height / capture.height * 100}%` }} />}
    </div>
    {crop && <img className="pointer-preview" src={crop} alt="已圈选区域预览" />}
    <div className="pointer-label">操作环境</div>
    <SegmentedControl<Kind> value={kind} options={[{ id: 'auto', label: '自动识别' }, { id: 'document', label: 'Word / 文档' },
      { id: 'spreadsheet', label: 'Excel / 表格' }, { id: 'unknown', label: '其他' }]} onChange={value => { invalidate(); setKind(value) }} />
    <p>{capture.appContext?.windowTitle || capture.appContext?.sourceName || '系统未提供应用标题，可手动选择环境。'}</p>
    <label className="pointer-label">关键词<Input value={keywords} onChange={event => editKeywords(event.target.value)} placeholder="例如：整理、公式、翻译、检查" /></label>
    <div className="pointer-chips">{['整理', '检查', '翻译', '解释', '公式', '格式'].map(keyword => <Button key={keyword} variant="outline" size="xs"
      onClick={() => editKeywords([keywords, keyword].filter(Boolean).join(' '))}>{keyword}</Button>)}</div>
    <div className="pointer-row"><Button disabled={!region || busy} onClick={() => propose()}> {session ? '追加关键词 / 换一批' : '生成多个候选'} </Button></div>
    {session?.keywords && <p>累计关键词：{session.keywords}</p>}
    {dirty && session && <p>关键词已修改，请先追加生成新候选。</p>}
    {session?.question && <p role="status">{session.question}</p>}
    {session && <div className="pointer-intents">{session.candidates.map(candidate => <Button key={candidate.action_id} variant={selectedAction === candidate.action_id ? 'default' : 'secondary'} aria-pressed={selectedAction === candidate.action_id} disabled={busy || dirty}
      onClick={() => choose(candidate)}>{candidate.label} — {candidate.description}</Button>)}</div>}
    {draft && <><label className="pointer-label">聊天草稿预览<Textarea value={draft} onChange={event => setDraft(event.target.value)} /></label>
      <Button onClick={insertDraft} disabled={busy || dirty}>确认加入聊天草稿</Button><p>图片不会随草稿上传。需要视觉分析时，请在原生聊天中另行确认添加图片。</p></>}
    {notice && <p role="status">{notice}</p>}
    </>}
  </section>
}

export default { id: 'smart-pointer', name: '智能指针', description: '圈选、关键词与多候选意图，确认后加入聊天草稿。', defaultEnabled: true,
  register(ctx) {
    ctx.register({ id: 'pane', area: PANES_AREA, title: '智能指针', data: { placement: 'right', width: '390px' }, render: () => <PointerPane ctx={ctx} /> })
    ctx.register({ id: 'open', area: PALETTE_AREA, data: { id: 'smart-pointer.open', label: '打开智能指针',
      run: () => host.revealPane('smart-pointer:pane') } })
  }
} satisfies HermesPlugin
