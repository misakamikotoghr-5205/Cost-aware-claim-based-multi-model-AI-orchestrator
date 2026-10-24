import { Button, ErrorState, Input, Loader, Switch, type HermesPlugin, type PluginContext } from '@hermes/plugin-sdk'
import { localApi } from './local-api'
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
interface DiscoverySettings { ollama_url: string; openrouter: boolean; ollama: boolean; ttl_seconds: number }
interface ModelInfo { model_id: string; provider: string; billing_mode: string; available: boolean; expires_at?: number; metadata?: { capability_evidence?: string } }
const CSS = `.free-settings{display:grid;align-content:start;gap:16px;padding:20px;color:var(--ui-text-primary)}.free-settings label{display:grid;gap:6px;font-size:12px}.free-settings p{font-size:12px;color:var(--ui-text-secondary);margin:0}.free-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.free-model{display:grid;gap:4px;border-bottom:1px solid var(--ui-stroke-tertiary);padding:8px 0}.free-output{white-space:pre-wrap;font-size:12px;color:var(--ui-text-secondary)}.free-settings input[type=file]{max-width:100%}`
export function DiscoveryPage({ ctx }: { ctx: PluginContext }) {
  const [localProfile, setLocalProfile] = useState(() => ctx.storage.get('local-profile', 'default'))
  const [profileInput, setProfileInput] = useState(localProfile)
  const api = localApi(localProfile)
  const [settings, setSettings] = useState<DiscoverySettings>({ ollama_url: 'http://127.0.0.1:11434', openrouter: true, ollama: true, ttl_seconds: 900 })
  const [models, setModels] = useState<ModelInfo[]>([])
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [result, setResult] = useState('')
  const [importSource, setImportSource] = useState('token-scout')
  const [payload, setPayload] = useState<unknown>(null), [report, setReport] = useState<unknown>(null)
  const [observedAt, setObservedAt] = useState<number>(0)
  const generation = useRef(0)
  useEffect(() => { const current = ++generation.current; setBusy(true)
    Promise.all([api<DiscoverySettings>('/settings'), api<{ models: ModelInfo[] }>('/models')]).then(([config, cards]) => {
      if (current === generation.current) { setSettings(config); setModels(cards.models) }
    }).catch(failure => { if (current === generation.current) setError(String(failure)) })
      .finally(() => { if (current === generation.current) setBusy(false) })
    return () => { generation.current++ }
  }, [ctx, localProfile])
  async function file(event: ChangeEvent<HTMLInputElement>, isReport = false) {
    const selected = event.target.files?.[0]; if (!selected) return
    try {
      if (selected.size > 4 * 1024 * 1024) throw new Error('发现文件超过 4 MiB。')
      const data = JSON.parse(await selected.text())
      if (isReport) { setReport(data); setObservedAt(value => Math.min(value || selected.lastModified / 1000, selected.lastModified / 1000)) }
      else { setPayload(data); setObservedAt(selected.lastModified / 1000) }
    } catch (failure) { setError(String(failure)) }
  }
  async function action(kind: 'save' | 'refresh' | 'import') {
    const current = ++generation.current; setBusy(true); setError(''); setResult('')
    try {
      if (kind === 'save') {
        const saved = await api<DiscoverySettings>('/settings', { method: 'PUT', body: settings })
        if (current === generation.current) { setSettings(saved); setResult('发现源设置已保存。') }
      } else {
        const response = kind === 'refresh'
          ? await api<{ models: ModelInfo[]; events: unknown[]; errors?: unknown[] }>('/refresh', { method: 'POST', body: {} })
          : await api<{ models: ModelInfo[]; events: unknown[]; errors?: unknown[] }>('/import', { method: 'POST', body: {
            source: importSource, payload, ...(report ? { report } : {}), observed_at: observedAt } })
        if (current === generation.current) { setModels(response.models); setResult(JSON.stringify({ events: response.events, errors: response.errors || [] }, null, 2)) }
      }
    } catch (failure) { if (current === generation.current) setError(String(failure)) }
    finally { if (current === generation.current) setBusy(false) }
  }
  return <section className="free-settings"><style>{CSS}</style><h2>免费模型发现</h2>
    <label>本机代理 profile<Input value={profileInput} onChange={event => setProfileInput(event.target.value)} /></label>
    <Button size="sm" variant="secondary" disabled={busy || !profileInput.trim()} onClick={() => { try { localApi(profileInput.trim()); generation.current++; ctx.storage.set('local-profile', profileInput.trim()); setLocalProfile(profileInput.trim()); setModels([]); setPayload(null); setReport(null); setResult(''); setError('') } catch (failure) { setError(String(failure)) } }}>连接此本机代理</Button>
    <p>只有安装此扩展并点击刷新才查询发现源。发现免费价格不等于账户有额度；静态、过期或没有能力证据的卡片不会自动获得执行资格。</p>
    <div className="free-row"><span>OpenRouter 免费模型</span><Switch aria-label="查询 OpenRouter 免费模型" checked={settings.openrouter} disabled={busy} onCheckedChange={value => setSettings(config => ({ ...config, openrouter: value }))} /></div>
    <div className="free-row"><span>本机 Ollama 模型</span><Switch aria-label="查询本机 Ollama" checked={settings.ollama} disabled={busy} onCheckedChange={value => setSettings(config => ({ ...config, ollama: value }))} /></div>
    <label>本机 Ollama 地址<Input value={settings.ollama_url} disabled={busy} onChange={event => setSettings(value => ({ ...value, ollama_url: event.target.value }))} /></label>
    <label>发现缓存有效期（秒）<Input type="number" min={1} value={settings.ttl_seconds} disabled={busy} onChange={event => setSettings(value => ({ ...value, ttl_seconds: Number(event.target.value) }))} /></label>
    <div className="free-row"><Button disabled={busy} onClick={() => action('save')}>保存设置</Button><Button disabled={busy} variant="secondary" onClick={() => action('refresh')}>现在联网刷新</Button></div>
    <details><summary>导入发现工具结果</summary><label>发现源<Input value={importSource} onChange={event => setImportSource(event.target.value)} placeholder="token-scout / free-models-monitor / ollama" /></label>
      <label>JSON 文件<input type="file" accept="application/json,.json" onChange={event => file(event)} /></label>
      <label>Free Models Monitor 报告（该来源必需）<input type="file" accept="application/json,.json" onChange={event => file(event, true)} /></label>
      <Button disabled={busy || !payload || (importSource === 'free-models-monitor' && !report)} onClick={() => action('import')}>确认导入</Button></details>
    {busy && <Loader label="正在查询模型发现源" />}{error && <ErrorState title="模型发现未完成" description={error} />}
    {!busy && !models.length && <p>尚无发现结果。手工配置的模型仍可在原生聊天使用。</p>}
    {models.map(model => <div className="free-model" key={model.model_id}><strong>{model.model_id}</strong><p>{model.provider} · {model.billing_mode} · {model.available ? '发现源可用' : '未验证或不可用'}</p>
      {model.expires_at && <p>活动截止：{new Date(model.expires_at * 1000).toLocaleString()}</p>}</div>)}
    {result && <pre className="free-output" role="status">{result}</pre>}
  </section>
}
export default { id: 'free-models', name: '免费模型发现', description: '独立安装的免费与本机模型发现扩展。', defaultEnabled: true,
  register(ctx) { ctx.registerSettingsPage({ id: 'settings', title: '免费模型发现', icon: 'search', render: () => <DiscoveryPage ctx={ctx} /> }) }
} satisfies HermesPlugin
