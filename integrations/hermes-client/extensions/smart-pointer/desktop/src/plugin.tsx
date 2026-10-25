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
interface CompactBridge {
  context: { generation: string; scope: ComposerScope } | null
  open(scope: ComposerScope): Promise<{ ok: boolean; message?: string }>
  close(): Promise<boolean>
  handoff(payload: { generation: string; scope: ComposerScope; text: string; imageDataUrl?: string }): Promise<{ ok: boolean; message?: string }>
}
const compactBridge = () => (window as unknown as { hermesDesktop?: { pointerCompact?: CompactBridge } }).hermesDesktop?.pointerCompact
const scope = (): ComposerScope => compactBridge()?.context?.scope ?? ({ sessionId: host.state.focusedStoredSessionId.get() ?? host.state.focusedSessionId.get() ?? 'new',
  profile: host.state.focusedSessionOwner.get()?.profile ?? host.state.focusedSessionProfile.get(), connectionId: host.state.focusedSessionOwner.get()?.connectionId ?? host.state.connectionId.get() })
const CSS = `.pointer-pane{padding:16px;display:flex;flex-direction:column;gap:16px;color:var(--ui-text-primary);height:100%;overflow:auto}.pointer-pane p{color:var(--ui-text-secondary);font-size:12px;margin:0}.pointer-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.pointer-image{position:relative;line-height:0;touch-action:none;user-select:none;cursor:crosshair;width:100%}.pointer-image img{display:block;width:100%;height:auto;pointer-events:none}.pointer-region{position:absolute;border:2px solid var(--ui-accent);background:var(--ui-bg-quaternary);opacity:.45;pointer-events:none}.pointer-intents{display:grid;gap:8px}.pointer-intents button{white-space:normal;text-align:left;justify-content:flex-start;height:auto}.pointer-chips{display:flex;gap:6px;flex-wrap:wrap}.pointer-preview{max-height:180px;object-fit:contain}.pointer-label{font-size:12px;font-weight:600}.pointer-pane textarea{min-height:110px}`

export function PointerPane({ ctx }: { ctx: PluginContext }) {
  const compact = compactBridge()?.context
  const [localProfile, setLocalProfile] = useState(() => ctx.storage.get('local-profile', 'default'))
  const [profileInput, setProfileInput] = useState(localProfile)
  const api = localApi(localProfile)
  const [localSettings, setLocalSettings] = useState({ enabled: false, model: 'qwen3:4b', base_url: 'http://127.0.0.1:11434', timeout: 2 })
  const [capture, setCapture] = useState<Capture | null>(null)
  const [region, setRegion] = useState<Region | null>(null)
  const [crop, setCrop] = useState('')
  const [kind, setKind] = useState<Kind>('auto')
  const [keywords, setKeywords] = useState('')
  const [selectedText, setSelectedText] = useState('')
  const [attachImage, setAttachImage] = useState(false)
  const [session, setSession] = useState<IntentSession | null>(null)
  const [draft, setDraft] = useState('')
  const [selectedAction, setSelectedAction] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [delivering, setDelivering] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const image = useRef<HTMLImageElement>(null)
  const start = useRef<Point | null>(null)
  const dragging = useRef(false)
  const gate = useRef(new RevisionGate())
  const address = useRef<ComposerScope | null>(null)
  const attached = useRef<{ image: string; scope: ComposerScope } | null>(null)
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
    invalidate(); setCapture(null); setCrop(''); setRegion(null); setAttachImage(false); setSelectedText(''); attached.current = null
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
    invalidate(); setRegion(null); setCrop(''); setAttachImage(false); setSelectedText(''); attached.current = null; start.current = point(event); dragging.current = true
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
    setSelectedAction(candidate.action_id); setDraft(candidate.preview_prompt + (selectedText.trim() ? '\n\n选区原文 / 表格数据：\n' + selectedText.trim() : '')); setError(''); setNotice('')
  }

  async function insertDraft() {
    if (!draft || !address.current || !session || !selectedAction || dirty || (!attachImage && !selectedText.trim())) return
    if (!sameScope(address.current, scope())) { setError('聊天或连接已切换，请返回开始圈选时的聊天后再添加草稿。'); return }
    const generation = gate.current.next(); setBusy(true); setError('')
    try {
      const committed = await api<{ draft_prompt: string; executed: false }>(`/sessions/${encodeURIComponent(session.session_id)}/commit`, {
        method: 'POST', body: { revision: session.revision, action_id: selectedAction } })
      if (!gate.current.accepts(generation)) return
      if (committed.executed !== false || typeof committed.draft_prompt !== 'string') throw new Error('候选没有返回可确认的草稿。')
      if (!sameScope(address.current, scope())) throw new Error('聊天或连接已切换，请返回原聊天后再添加草稿。')
      if (compact) {
        const bridge = compactBridge()
        if (!bridge) throw new Error('原生浮框连接已关闭。')
        setDelivering(true)
        const result = await bridge.handoff({ generation: compact.generation, scope: address.current,
          text: draft, ...(attachImage && crop ? { imageDataUrl: crop } : {}) })
        if (!gate.current.accepts(generation)) return
        if (!result.ok) throw new Error(result.message || '原聊天没有确认接收，草稿仍保留在浮框。')
        setNotice(attachImage ? '草稿和选区图片已就绪。返回聊天检查后发送。' : '草稿已就绪。返回聊天检查后发送。')
        setSession(null); setDraft(''); setSelectedAction(''); setAttachImage(false)
        return
      }
      if (attachImage && crop && (!attached.current || attached.current.image !== crop || !sameScope(attached.current.scope, address.current))) {
        const attach = host.composer.attachImages
        if (typeof attach !== 'function') throw new Error('当前 Hermes 版本不支持向草稿添加图片。请更新，或粘贴选区原文。')
        const bytes = Uint8Array.from(atob(crop.slice(crop.indexOf(',') + 1)), char => char.charCodeAt(0))
        if (bytes.byteLength > 8 * 1024 * 1024) throw new Error('选区图片超过 8 MiB，请缩小圈选区域。')
        const accepted = await attach(address.current.sessionId, [new Blob([bytes], { type: 'image/png' })])
        if (!accepted) throw new Error('图片未加入原生草稿，未报告附图成功。请重试或改为粘贴原文。')
        if (!gate.current.accepts(generation) || !sameScope(address.current, scope())) return
        attached.current = { image: crop, scope: { ...address.current } }
      }
      if (!gate.current.accepts(generation) || !sameScope(address.current, scope())) return
      const inserted = await host.composer.insertText(address.current.sessionId, draft, { mode: 'block' })
      if (!inserted) setError('当前聊天输入框未就绪，草稿仍保留在此处。')
      else { setNotice(attachImage ? '草稿和所选区域图片已加入原生聊天。请检查后在聊天中发送。' : '含选区原文的草稿已加入原生聊天。请检查后发送。'); setSession(null); setDraft(''); setSelectedAction(''); setAttachImage(false); attached.current = null }
    } catch (failure) { if (gate.current.accepts(generation)) setError(String(failure)) }
    finally { setDelivering(false); if (gate.current.accepts(generation)) setBusy(false) }
  }

  if (compact) return <section className="pointer-pane pointer-compact" aria-label="智能指针浮框"><style>{CSS + `.pointer-compact{padding:10px;gap:9px}.pointer-compact p{font-size:11px}.pointer-compact .pointer-preview{max-height:64px;width:100%;object-fit:contain}.pointer-compact textarea{min-height:60px;font-size:12px}.pointer-compact .pointer-label{font-size:11px}.pointer-compact .pointer-image{max-height:140px;overflow:auto}.pointer-compact .pointer-row{gap:6px}.pointer-compact details{font-size:11px}.pointer-compact details[open]{display:flex;flex-direction:column;gap:7px}.pointer-compact .pointer-intents button{padding:7px;font-size:12px;line-height:1.4}`}</style>
    <div className="pointer-row"><Button size="sm" disabled={busy} onClick={beginCapture}>{capture ? '重新圈选' : '开始圈选'}</Button>
      <span style={{ fontSize: 11 }}>{region ? `${region.width} × ${region.height} · 仅选区` : '先圈选，再补充关键词'}</span></div>
    {busy && <Loader label={delivering ? '正在加入原聊天草稿' : '正在准备候选'} />}
    {error && <ErrorState title="暂时无法继续" description={error}><Button size="sm" onClick={() => setError('')}>返回操作</Button></ErrorState>}
    {capture && !capture.region_already_cropped && <div className="pointer-image" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}>
      <img ref={image} src={capture.imageDataUrl} alt="本机截图，拖动以圈选" draggable={false} />
      {region && <div className="pointer-region" style={{ left: `${region.x / capture.width * 100}%`, top: `${region.y / capture.height * 100}%`, width: `${region.width / capture.width * 100}%`, height: `${region.height / capture.height * 100}%` }} />}</div>}
    {crop && <img className="pointer-preview" src={crop} alt="已圈选区域预览" />}
    {capture && <SegmentedControl<Kind> value={kind} options={[{ id: 'auto', label: '自动' }, { id: 'document', label: '文档' }, { id: 'spreadsheet', label: '表格' }, { id: 'unknown', label: '其他' }]} onChange={value => { invalidate(); setKind(value) }} />}
    <Input aria-label="关键词" value={keywords} onChange={event => editKeywords(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); void propose() } }} placeholder={session ? '都不合适？补充关键词，再换一批' : '例如：润色、统计报表、整理格式'} />
    <Button size="sm" disabled={!region || busy} onClick={() => propose()}>{session ? '追加关键词 / 换一批' : '生成多个候选'}</Button>
    {session?.keywords && <p>累计关键词：{session.keywords}</p>}
    {session?.question && <p role="status">{session.question}</p>}
    {dirty && session && <p>关键词已修改，请先换一批。</p>}
    {session && !draft && <div className="pointer-intents">{session.candidates.map(candidate => <Button key={candidate.action_id} variant="secondary" disabled={busy || dirty} onClick={() => choose(candidate)}>{candidate.label} — {candidate.description}</Button>)}</div>}
    {capture && <><div className="pointer-row"><Switch aria-label="附上所选区域图片" disabled={delivering} checked={attachImage} onCheckedChange={enabled => { gate.current.next(); setAttachImage(enabled); if (busy) { setBusy(false); setSession(null); setDraft(''); setSelectedAction('') } }} /><span style={{ fontSize: 11 }}>确认后附上这张选区图片</span></div>
      <details><summary>粘贴选区原文 / 表格数据</summary><Textarea aria-label="选区原文 / 表格数据（可粘贴）" value={selectedText} disabled={busy} onChange={event => { gate.current.next(); setSelectedText(event.target.value); setDraft(''); setSelectedAction('') }} placeholder="不附图时，请粘贴要处理的内容。" /></details></>}
    {draft && <><Textarea aria-label="聊天草稿预览" value={draft} disabled={busy} onChange={event => setDraft(event.target.value)} />
      <div className="pointer-row"><Button size="sm" disabled={busy || dirty || (!attachImage && !selectedText.trim())} onClick={insertDraft}>确认加入聊天草稿</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => { setDraft(''); setSelectedAction('') }}>重选</Button></div>
      {!attachImage && !selectedText.trim() && <p>请粘贴原文，或明确选择附上选区图片。</p>}</>}
    {notice && <p role="status">{notice}</p>}
    <p>截图留在本机，候选不上传图片。确认仅加入草稿。</p>
    <details><summary>本机代理与本地建议设置</summary>
      <Input aria-label="本机代理 profile" value={profileInput} onChange={event => setProfileInput(event.target.value)} />
      <Button size="sm" disabled={busy || !profileInput.trim()} onClick={() => { try { localApi(profileInput.trim()); ctx.storage.set('local-profile', profileInput.trim()); setLocalProfile(profileInput.trim()); invalidate(); setNotice('本机代理已切换，请重新生成候选。') } catch (failure) { setError(String(failure)) } }}>连接此本机代理</Button>
      <div className="pointer-row"><span>本机模型建议</span><Switch aria-label="启用本机意图建议" checked={localSettings.enabled} onCheckedChange={enabled => setLocalSettings(value => ({ ...value, enabled }))} /></div>
      <Input aria-label="本机 Ollama 地址" value={localSettings.base_url} onChange={event => setLocalSettings(value => ({ ...value, base_url: event.target.value }))} />
      <Input aria-label="本地模型" value={localSettings.model} onChange={event => setLocalSettings(value => ({ ...value, model: event.target.value }))} />
      <Input aria-label="超时（秒）" type="number" min={0.05} max={5} step={0.05} value={localSettings.timeout} onChange={event => setLocalSettings(value => ({ ...value, timeout: Number(event.target.value) }))} />
      <div className="pointer-row"><Button size="sm" onClick={loadLocalSettings}>读取设置</Button><Button size="sm" onClick={saveLocalSettings}>保存建议设置</Button></div>
    </details>
  </section>

  return <section className="pointer-pane" aria-label="智能指针"><style>{CSS}</style>
    <div className="pointer-row"><strong>智能指针</strong><Button size="sm" onClick={beginCapture} disabled={busy}>开始圈选</Button>
      <Button size="sm" variant="ghost" onClick={() => { invalidate(); setCapture(null); setRegion(null); setCrop(''); setSelectedText(''); setAttachImage(false); attached.current = null; address.current = null }}>清除</Button></div>
    <label className="pointer-label">本机代理 profile<Input value={profileInput} onChange={event => setProfileInput(event.target.value)} /></label>
    <Button size="sm" variant="secondary" disabled={busy || !profileInput.trim()} onClick={() => { try { localApi(profileInput.trim()); ctx.storage.set('local-profile', profileInput.trim()); setLocalProfile(profileInput.trim()); invalidate(); setCapture(null); setCrop(''); setRegion(null); setSelectedText(''); setAttachImage(false); attached.current = null; address.current = null } catch (failure) { setError(String(failure)) } }}>连接此本机代理</Button>
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
    <label className="pointer-label">关键词<Input aria-label="关键词" value={keywords} onChange={event => editKeywords(event.target.value)} placeholder="例如：整理、公式、翻译、检查" /></label>
    <div className="pointer-chips">{['整理', '检查', '翻译', '解释', '公式', '格式'].map(keyword => <Button key={keyword} variant="outline" size="xs"
      onClick={() => editKeywords([keywords, keyword].filter(Boolean).join(' '))}>{keyword}</Button>)}</div>
    <div className="pointer-row"><Button disabled={!region || busy} onClick={() => propose()}> {session ? '追加关键词 / 换一批' : '生成多个候选'} </Button></div>
    <label className="pointer-label">选区原文 / 表格数据（可粘贴）<Textarea aria-label="选区原文 / 表格数据（可粘贴）" value={selectedText} disabled={busy} onChange={event => { gate.current.next(); setSelectedText(event.target.value); setDraft(''); setSelectedAction(''); if (busy) setSession(null); setBusy(false) }} placeholder="不发送图片时，请在这里粘贴要处理的原文或表格内容。" /></label>
    <div className="pointer-row"><Switch aria-label="附上所选区域图片" checked={attachImage} onCheckedChange={enabled => {
      gate.current.next(); setAttachImage(enabled)
      if (busy) { setBusy(false); setSession(null); setDraft(''); setSelectedAction('') }
    }} /><span>附上所选区域图片（发送时使用当前会话模型）</span></div>
    <p>图片默认不加入草稿。只有勾选并最终确认后才加入严格裁剪的选区；点击聊天发送后才由原生附件流程交给会话模型。</p>
    {session?.keywords && <p>累计关键词：{session.keywords}</p>}
    {dirty && session && <p>关键词已修改，请先追加生成新候选。</p>}
    {session?.question && <p role="status">{session.question}</p>}
    {session && <div className="pointer-intents">{session.candidates.map(candidate => <Button key={candidate.action_id} variant={selectedAction === candidate.action_id ? 'default' : 'secondary'} aria-pressed={selectedAction === candidate.action_id} disabled={busy || dirty}
      onClick={() => choose(candidate)}>{candidate.label} — {candidate.description}</Button>)}</div>}
    {draft && <><label className="pointer-label">聊天草稿预览<Textarea aria-label="聊天草稿预览" value={draft} disabled={busy} onChange={event => setDraft(event.target.value)} /></label>
      <Button onClick={insertDraft} disabled={busy || dirty || (!attachImage && !selectedText.trim())}>确认加入聊天草稿</Button><p>请提供选区原文或明确勾选附图，确保会话模型能看到要处理的内容。此扩展不会自动发送聊天。</p></>}
    {notice && <p role="status">{notice}</p>}
    </>}
  </section>
}

export default { id: 'smart-pointer', name: '智能指针', description: '圈选、关键词与多候选意图，确认后加入聊天草稿。', defaultEnabled: true,
  register(ctx) {
    ctx.register({ id: 'pane', area: PANES_AREA, title: '智能指针', data: { placement: 'right', width: '390px' }, render: () => <PointerPane ctx={ctx} /> })
    ctx.register({ id: 'open', area: PALETTE_AREA, data: { id: 'smart-pointer.open', label: '打开智能指针',
      run: async () => {
        const bridge = compactBridge()
        if (!bridge) { host.notifyError('请更新客户端以使用智能指针浮框。'); return }
        const result = await bridge.open(scope())
        if (!result.ok) host.notifyError(result.message || '无法打开智能指针浮框。')
      } } })
  }
} satisfies HermesPlugin
