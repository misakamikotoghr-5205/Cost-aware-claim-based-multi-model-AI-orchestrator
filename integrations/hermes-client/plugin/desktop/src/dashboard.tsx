import { Button, ErrorState, Input, Loader, host, type PluginContext } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { localApi } from './local-api'

interface QuotaWindow {
  id: string; label: string; unit: string; capacity: number | null; used: number | null; remaining: number | null
  reset_at: number | null; scope: string; state: string; held?: number | null; available?: number | null
}
interface QuotaAccount {
  id: string; provider: string; label: string; state: string; source: string; observed_at: number | null
  expires_at: number | null; unknown_reason: string | null; windows: QuotaWindow[]
}
export interface DashboardSnapshot {
  accounts: QuotaAccount[]; refreshing: boolean; refreshed_at: number | null; notes: string[]
  cloud: { configured: boolean; state: string; source: string; accounts: QuotaAccount[]; unknown_reason: string | null }
  node: { execution_location: string; platform: string; running: boolean; status: string }
}
type ImportFormat = 'normalized-v1' | 'codex' | 'anthropic' | 'openrouter' | 'deepseek'

const CSS = `.misaka-dashboard{height:100%;overflow:auto;color:var(--ui-text-primary);padding:28px;container-type:inline-size}.misaka-dashboard-inner{display:grid;gap:24px;max-width:1180px;margin:auto}.misaka-dashboard h1,.misaka-dashboard h2,.misaka-dashboard h3,.misaka-dashboard p{margin:0}.misaka-dashboard h1{font-size:26px;font-weight:650;letter-spacing:-.5px}.misaka-dashboard h2{font-size:16px;font-weight:600}.misaka-dashboard h3{font-size:14px;font-weight:600}.misaka-dashboard p,.misaka-muted{font-size:12px;color:var(--ui-text-secondary);line-height:1.7}.misaka-brand{display:flex;gap:8px;align-items:center;color:#3b82f6;font-size:11px;letter-spacing:1.8px;font-weight:700}.misaka-brand-dot{width:7px;height:7px;border-radius:50%;background:#3b82f6}.misaka-head{display:flex;gap:16px;justify-content:space-between;align-items:center;flex-wrap:wrap}.misaka-heading{display:grid;gap:8px}.misaka-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.misaka-profile{display:flex;gap:8px;align-items:end;flex-wrap:wrap}.misaka-profile label{display:grid;gap:6px;font-size:12px;color:var(--ui-text-secondary)}.misaka-profile input{width:180px}.misaka-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.misaka-stat,.misaka-quota-card,.misaka-node{border:1px solid var(--ui-stroke-tertiary);border-radius:12px;background:var(--ui-bg-secondary)}.misaka-stat{padding:16px;display:grid;gap:8px;font-size:12px;color:var(--ui-text-secondary)}.misaka-stat strong{font-size:23px;font-weight:600;color:var(--ui-text-primary);font-variant-numeric:tabular-nums}.misaka-stat time{font-size:13px;color:var(--ui-text-primary)}.misaka-section{display:grid;gap:14px}.misaka-account-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(285px,1fr));gap:14px}.misaka-quota-card{padding:18px;display:flex;flex-direction:column;gap:16px;min-width:0}.misaka-account-head{display:flex;align-items:center;gap:10px}.misaka-provider-icon{display:grid;place-items:center;flex:none;width:34px;height:34px;border-radius:10px;background:color-mix(in srgb,#3b82f6 12%,transparent);color:#3b82f6;font-weight:650;font-size:12px}.misaka-account-name{flex:1;min-width:0;display:grid;gap:3px}.misaka-account-name h3{overflow-wrap:anywhere}.misaka-account-name p{font-size:11px}.misaka-badge{font-size:10px;padding:3px 7px;border-radius:6px;background:var(--ui-bg-tertiary);color:var(--ui-text-secondary);white-space:nowrap}.misaka-badge[data-state=ready]{color:#3b82f6;background:color-mix(in srgb,#3b82f6 12%,transparent)}.misaka-badge[data-state=stale],.misaka-badge[data-state=expired],.misaka-badge[data-state=partial]{color:var(--ui-warning,#b7832f)}.misaka-window{display:grid;gap:9px}.misaka-window-title{display:flex;justify-content:space-between;gap:10px;font-size:12px}.misaka-window-title strong{font-variant-numeric:tabular-nums;font-weight:600}.misaka-window small{color:var(--ui-text-secondary);font-size:11px;line-height:1.6}.misaka-window progress{appearance:none;width:100%;height:6px;border:0;border-radius:9px;overflow:hidden;background:var(--ui-bg-quaternary);color:#3b82f6}.misaka-window progress::-webkit-progress-bar{background:var(--ui-bg-quaternary);border-radius:9px}.misaka-window progress::-webkit-progress-value{background:#3b82f6;border-radius:9px}.misaka-window progress::-moz-progress-bar{background:#3b82f6;border-radius:9px}.misaka-window-meta{display:flex;gap:6px 14px;flex-wrap:wrap}.misaka-source{margin-top:auto;padding-top:12px;border-top:1px solid var(--ui-stroke-tertiary);font-size:10px;color:var(--ui-text-secondary);line-height:1.8;overflow-wrap:anywhere}.misaka-reason{font-size:12px;line-height:1.7;color:var(--ui-text-secondary)}.misaka-empty{border:1px dashed var(--ui-stroke-tertiary);border-radius:12px;padding:24px;display:grid;justify-items:start;gap:12px}.misaka-node{padding:18px;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.misaka-node div{display:grid;gap:6px;font-size:12px}.misaka-node span{color:var(--ui-text-secondary)}.misaka-import{padding:16px 0;border-top:1px solid var(--ui-stroke-tertiary)}.misaka-import summary{font-size:12px;cursor:pointer}.misaka-import-fields{display:flex;align-items:end;gap:12px;flex-wrap:wrap;margin-top:14px}.misaka-import label{display:grid;gap:6px;font-size:12px;color:var(--ui-text-secondary)}.misaka-import select{height:30px;padding:4px 8px;border:1px solid var(--ui-stroke-tertiary);background:var(--ui-bg-secondary);color:var(--ui-text-primary);border-radius:6px;font-size:12px}.misaka-import p{margin-top:12px}@container(max-width:650px){.misaka-overview{grid-template-columns:1fr}.misaka-node{grid-template-columns:1fr}.misaka-account-grid{grid-template-columns:1fr}}`

function finite(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value) && value >= 0 }
function timestamp(value: number | null | undefined) {
  if (!finite(value) || value <= 0) return '未提供'
  const date = new Date(value * 1000)
  return Number.isNaN(date.valueOf()) ? '未提供' : date.toLocaleString('zh-CN', { hour12: false })
}
function quantity(value: number, unit: string) {
  const number = value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
  if (['usd', 'USD', 'dollars'].includes(unit)) return `$${number} USD`
  if (['cny', 'CNY'].includes(unit)) return `¥${number} CNY`
  if (['percent', '%', 'percentage'].includes(unit)) return `${number}%`
  const units: Record<string, string> = { requests: '次', tokens: 'Token', credits: '积分' }
  return `${number} ${units[unit] || unit}`.trim()
}
const states: Record<string, string> = { ready: '已读取', stale: '记录已过期', expired: '窗口已结束', partial: '部分已读取' }
const stateLabel = (state: string) => states[state] || '未获取'

function AccountCard({ account }: { account: QuotaAccount }) {
  return <article className="misaka-quota-card" aria-label={`${account.label || account.provider} 额度`}>
    <div className="misaka-account-head"><span className="misaka-provider-icon" aria-hidden="true">{account.provider.slice(0, 2).toUpperCase()}</span>
      <div className="misaka-account-name"><h3>{account.label || account.provider}</h3><p>{account.provider}</p></div>
      <span className="misaka-badge" data-state={account.state}>{stateLabel(account.state)}</span></div>
    {account.unknown_reason && <p className="misaka-reason">{account.unknown_reason}</p>}
    {!account.windows.length && <p>尚无可读取的额度窗口。</p>}
    {account.windows.map(window => {
      const ready = account.state === 'ready' && window.state === 'ready'
      const hasRatio = ready && finite(window.used) && finite(window.capacity) && window.capacity > 0
      return <section className="misaka-window" key={window.id} aria-label={window.label}>
        <div className="misaka-window-title"><span>{window.label}</span><strong>{ready && finite(window.remaining) ? `剩余 ${quantity(window.remaining, window.unit)}` : '—'}</strong></div>
        {hasRatio && <progress max={100} value={Math.min(100, window.used! / window.capacity! * 100)} aria-label={`${window.label}已用比例`} />}
        {ready && finite(window.used) && finite(window.capacity)
          ? <small>已用 {quantity(window.used, window.unit)} / 上限 {quantity(window.capacity, window.unit)}</small>
          : <small>{ready ? '服务未提供完整用量或上限，无法计算使用比例。' : '当前额度未确认，未计算使用比例。'}</small>}
        <div className="misaka-window-meta">
          {ready && finite(window.held) && <small>已占用 {quantity(window.held, window.unit)}</small>}
          {ready && finite(window.available) && <small>可调度 {quantity(window.available, window.unit)}</small>}
          <small>下次重置：{timestamp(window.reset_at)}</small>
        </div>
        {window.scope && <small>范围：{window.scope}</small>}
      </section>
    })}
    <footer className="misaka-source">来源：{account.source || '未提供'}<br />采集时间：{timestamp(account.observed_at)}
      {finite(account.expires_at) && <><br />记录有效至：{timestamp(account.expires_at)}</>}</footer>
  </article>
}

export function DashboardPage({ ctx }: { ctx: PluginContext }) {
  const [localProfile, setLocalProfile] = useState(() => ctx.storage.get('local-profile', 'default'))
  const [profileInput, setProfileInput] = useState(localProfile)
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [format, setFormat] = useState<ImportFormat>('normalized-v1')
  const [observedAt, setObservedAt] = useState(''), [accountLabel, setAccountLabel] = useState('')
  const generation = useRef(0)
  const mounted = useRef(true)
  const api = localApi(localProfile)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++ } }, [])
  useEffect(() => { setSnapshot(null); readCache(); return () => { generation.current++ } }, [localProfile])
  useEffect(() => {
    if (!snapshot?.refreshing) return
    const current = generation.current
    return ctx.setTimeout(() => {
      api<DashboardSnapshot>('/quota/dashboard').then(value => {
        if (mounted.current && current === generation.current) setSnapshot(value)
      }).catch(failure => { if (mounted.current && current === generation.current) setError(String(failure)) })
    }, 1000)
  }, [snapshot, localProfile, ctx])

  async function perform(operation: () => Promise<DashboardSnapshot>) {
    const current = ++generation.current; setBusy(true); setError('')
    try { const value = await operation(); if (mounted.current && current === generation.current) setSnapshot(value) }
    catch (failure) { if (mounted.current && current === generation.current) setError(String(failure)) }
    finally { if (mounted.current && current === generation.current) setBusy(false) }
  }
  async function readCache() { await perform(() => api<DashboardSnapshot>('/quota/dashboard')) }
  async function refresh() { await perform(() => api<DashboardSnapshot>('/quota/refresh', { method: 'POST', body: {} })) }
  async function openAccounts() {
    const current = generation.current; setError('')
    try {
      // Explicit user navigation activates the selected local source before
      // opening native provider settings, never the ambient remote chat source.
      await host.ensureAgent('local', localProfile)
      if (mounted.current && current === generation.current) host.navigate('/settings?tab=providers&pview=accounts')
    } catch (failure) { if (mounted.current && current === generation.current) setError(String(failure)) }
  }
  async function importSnapshot() {
    const current = generation.current
    const date = observedAt ? new Date(observedAt).valueOf() / 1000 : undefined
    if ((format !== 'normalized-v1' || date !== undefined) && (!finite(date) || date <= 0)) { setError('请填写快照实际采集时间，导入时间不能代替采集时间。'); return }
    let path: string | null | undefined
    try { path = await ctx.os.pickOpenPath({ title: '导入用量快照', filters: [{ name: 'JSON 快照', extensions: ['json'] }] }) }
    catch (failure) { if (mounted.current && current === generation.current) setError(String(failure)); return }
    if (!path || !mounted.current || current !== generation.current) return
    await perform(() => api<DashboardSnapshot>('/quota/import', { method: 'POST', body: {
      format, path, ...(date === undefined ? {} : { observed_at: date }), ...(accountLabel.trim() ? { account_label: accountLabel.trim() } : {})
    } }))
  }
  const accounts = snapshot?.accounts || []
  const ready = accounts.filter(account => account.state === 'ready').length
  const waiting = busy || snapshot?.refreshing === true
  return <main className="misaka-dashboard" aria-label="MISAKA AGENT 额度总览"><style>{CSS}</style><div className="misaka-dashboard-inner">
    <header className="misaka-head"><div className="misaka-heading"><div className="misaka-brand"><span className="misaka-brand-dot" />MISAKA AGENT</div>
      <h1>额度总览</h1><p>查看账号用量、共享配额与本机节点状态。</p></div>
      <div className="misaka-actions"><Button variant="secondary" onClick={openAccounts}>账号与模型设置</Button>
        <Button disabled={waiting} onClick={refresh}>{snapshot?.refreshing ? '正在刷新用量' : '读取最新额度'}</Button></div></header>
    <div className="misaka-profile"><label>本机配置（profile）<Input aria-label="额度本机配置" value={profileInput} onChange={event => setProfileInput(event.target.value)} /></label>
      <Button size="sm" variant="secondary" disabled={!profileInput.trim()} onClick={() => {
        try { const target = profileInput.trim(); localApi(target); ctx.storage.set('local-profile', target)
          if (target === localProfile) { readCache(); return }
          generation.current++; setLocalProfile(target); setSnapshot(null); setError('') }
        catch (failure) { setError(String(failure)) }
      }}>连接此本机配置</Button><p>当前数据来自本机 {localProfile}。聊天连接切换不会改变这里的账号范围。</p></div>
    {waiting && <Loader label={snapshot?.refreshing ? '正在读取实际用量，已缓存数据仍可查看' : '正在读取本机额度记录'} />}
    {error && <ErrorState title="额度读取未完成" description={error}><Button size="sm" variant="secondary" onClick={readCache}>重新读取记录</Button></ErrorState>}
    <section className="misaka-overview" aria-label="账号用量概况">
      <div className="misaka-stat"><span>已读取的账号</span><strong>{snapshot ? ready : '—'}</strong></div>
      <div className="misaka-stat"><span>待确认的账号</span><strong>{snapshot ? accounts.length - ready : '—'}</strong></div>
      <div className="misaka-stat"><span>最近一次刷新</span><time>{timestamp(snapshot?.refreshed_at)}</time></div>
    </section>
    <section className="misaka-section" aria-label="本机账号额度"><div className="misaka-head"><h2>账号用量</h2><p>不同计量单位分别展示，不合并为虚构余额。</p></div>
      {accounts.length ? <div className="misaka-account-grid">{accounts.map(account => <AccountCard key={account.id} account={account} />)}</div>
        : <div className="misaka-empty"><h3>尚无可显示的账号用量</h3><p>在此本机配置的原生账号设置中登录，然后点击“读取最新额度”。服务未公开用量时会显示原因。</p><Button size="sm" variant="secondary" onClick={openAccounts}>打开原生账号设置</Button></div>}
    </section>
    {snapshot && <><section className="misaka-section" aria-label="共享云端额度"><div className="misaka-head"><h2>共享云端额度</h2>
      <span className="misaka-badge" data-state={snapshot.cloud.state}>{stateLabel(snapshot.cloud.state)}</span></div>
      <p>私网调度账本的工作配额，独立于服务商账号余额。来源：{snapshot.cloud.source || '未提供'}</p>
      {snapshot.cloud.unknown_reason && <p>{snapshot.cloud.unknown_reason}</p>}
      {!!snapshot.cloud.accounts.length && <div className="misaka-account-grid">{snapshot.cloud.accounts.map(account => <AccountCard key={account.id} account={account} />)}</div>}
      {!snapshot.cloud.configured && <div><Button size="sm" variant="secondary" onClick={() => host.navigate('/orchestrator')}>配置云端与本地协同</Button></div>}
    </section><section className="misaka-section" aria-label="本机节点状态"><h2>本机节点</h2><div className="misaka-node">
      <div><span>执行位置</span><strong>{snapshot.node.execution_location === 'this_backend_host' ? '当前本机代理' : snapshot.node.execution_location || '未提供'}</strong></div>
      <div><span>操作系统</span><strong>{snapshot.node.platform || '未提供'}</strong></div>
      <div><span>任务接收</span><strong>{snapshot.node.running ? '运行中' : snapshot.node.status === 'unknown' ? '未确认' : '已停止'}</strong></div>
    </div></section></>}
    <details className="misaka-import"><summary>导入已有用量快照</summary><div className="misaka-import-fields">
      <label>快照格式<select aria-label="快照格式" value={format} onChange={event => setFormat(event.target.value as ImportFormat)}>
        <option value="normalized-v1">标准用量快照</option><option value="codex">Codex</option><option value="anthropic">Anthropic</option>
        <option value="openrouter">OpenRouter</option><option value="deepseek">DeepSeek</option></select></label>
      <label>快照实际采集时间<Input type="datetime-local" aria-label="快照实际采集时间" value={observedAt} onChange={event => setObservedAt(event.target.value)} /></label>
      <label>账号显示名称（可选）<Input aria-label="快照账号名称" value={accountLabel} onChange={event => setAccountLabel(event.target.value)} /></label>
      <Button size="sm" variant="secondary" disabled={waiting || (format !== 'normalized-v1' && !observedAt)} onClick={importSnapshot}>选择 JSON 并导入</Button></div>
      <p>仅导入你选择的本机文件。原始服务商快照必须填写实际采集时间，导入不会延长记录有效期。</p></details>
    {snapshot?.notes.map((note, index) => <p key={index}>{note}</p>)}
  </div></main>
}
