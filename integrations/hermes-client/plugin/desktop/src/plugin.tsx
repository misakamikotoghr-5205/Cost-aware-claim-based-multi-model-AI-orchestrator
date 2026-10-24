import { Button, ConfirmDialog, ErrorState, Input, Loader, Switch, Textarea, host, ROUTES_AREA, SIDEBAR_NAV_AREA,
  type HermesPlugin, type PluginContext } from '@hermes/plugin-sdk'
import { localApi } from './local-api'
import { useEffect, useRef, useState } from 'react'

interface NodeInfo { owner_id: string; node_id: string; label: string; online: boolean; last_seen_at: number;
  capabilities: { platform?: string; tools?: string[]; skills?: string[]; workspace_aliases?: string[]; max_actions?: number } }
interface ExtensionInfo { id: string; version?: string; installed?: boolean; enabled?: boolean }
interface Settings { enabled?: boolean; fleet_tools?: { enabled?: boolean; allowed_tools?: string[]; max_actions?: number }; server?: { url?: string; token_env?: string; allow_http?: boolean }; node?: {
  owner_id?: string; node_id?: string; workspaces?: Record<string, string>; runtime?: Record<string, unknown> }; [key: string]: unknown }
const CSS = `.orch-page{padding:24px;display:grid;align-content:start;gap:24px;color:var(--ui-text-primary);max-width:980px;margin:0 auto;overflow:auto;height:100%}.orch-page h1{font-size:20px;margin:0}.orch-page h2{font-size:15px;margin:0}.orch-page p{font-size:12px;color:var(--ui-text-secondary);margin:0}.orch-section{display:grid;gap:12px}.orch-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.orch-field{display:grid;gap:6px;font-size:12px;flex:1;min-width:200px}.orch-node{display:grid;gap:6px;border-bottom:1px solid var(--ui-stroke-tertiary);padding:10px 0}.orch-pre{white-space:pre-wrap;word-break:break-word;color:var(--ui-text-secondary);font-size:12px}.orch-page textarea{min-height:100px}.orch-node[aria-selected=true]{color:var(--ui-accent)}`

export function ClientPage({ ctx }: { ctx: PluginContext }) {
  const [localProfile, setLocalProfile] = useState(() => ctx.storage.get('local-profile', 'default'))
  const [profileInput, setProfileInput] = useState(localProfile)
  const api = localApi(localProfile)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [nodes, setNodes] = useState<NodeInfo[]>([])
  const [extensions, setExtensions] = useState<ExtensionInfo[]>([])
  const [selected, setSelected] = useState('')
  const [text, setText] = useState('')
  const [workspace, setWorkspace] = useState('default')
  const [relative, setRelative] = useState('.')
  const [tools, setTools] = useState('')
  const [revoke, setRevoke] = useState<NodeInfo | null>(null)
  const [lastTaskId, setLastTaskId] = useState('')
  const [worker, setWorker] = useState('')
  const [pairing, setPairing] = useState('')
  const [pairCode, setPairCode] = useState('')
  const [label, setLabel] = useState('')
  const [output, setOutput] = useState('')
  const [configText, setConfigText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  const pendingTask = useRef<string | null>(null)
  useEffect(() => { refresh(); return () => { generation.current++ } }, [localProfile])

  async function perform(action: () => Promise<void>) {
    const current = ++generation.current; setBusy(true); setError('')
    try { await action() } catch (failure) { if (current === generation.current) setError(String(failure)) }
    finally { if (current === generation.current) setBusy(false) }
  }
  async function refresh() {
    await perform(async () => {
      const current = generation.current
      const config = await api<Settings>('/settings')
      if (current !== generation.current) return
      setSettings(config); setConfigText(JSON.stringify(config, null, 2))
      const installed = await api<{ extensions: ExtensionInfo[] }>('/extensions')
      if (current !== generation.current) return
      setExtensions(installed.extensions.filter(extension => extension.installed === true))
      if (config.server?.url) {
        const fleet = await api<{ nodes: NodeInfo[] }>('/nodes')
        if (current === generation.current) setNodes(fleet.nodes)
      }
    })
  }
  async function sendTask() {
    if (!selected || !text.trim() || !settings) return
    await perform(async () => {
      const taskId = pendingTask.current ?? crypto.randomUUID(); pendingTask.current = taskId
      const result = await api('/tasks', { method: 'POST', body: { task_id: taskId, idempotency_key: taskId, payload: {
        client_id: settings.node?.owner_id, target: selected, task: text, workspace_alias: workspace, relative_dir: relative,
        tools: tools.split(',').map(item => item.trim()).filter(Boolean), max_actions: 20, metadata: {} } } })
      setOutput(JSON.stringify(result, null, 2)); setLastTaskId(taskId); pendingTask.current = null
    })
  }
  async function install() {
    const archive = await ctx.os.pickOpenPath({ title: '安装扩展包', filters: [{ name: '扩展包', extensions: ['zip'] }] })
    if (!archive) return
    await perform(async () => { const result = await api('/extensions/install', { method: 'POST', body: { archive_path: archive } }); setOutput(JSON.stringify(result, null, 2)) })
    await refresh()
  }
  return <main className="orch-page"><style>{CSS}</style>
    <ConfirmDialog open={!!revoke} onClose={() => setRevoke(null)} title="撤销节点连接" description={`停止节点 ${revoke?.label || revoke?.node_id || ''} 后续领取任务的权限。已在运行的操作可能无法撤回。`} destructive
      onConfirm={async () => { if (!revoke) return; await api(`/nodes/${encodeURIComponent(revoke.node_id)}/revoke`, { method: 'POST', body: {} }); setNodes(value => value.filter(item => item.node_id !== revoke.node_id)) }} />
    <div className="orch-row"><h1>云端与本地客户端</h1><Button size="sm" variant="secondary" onClick={refresh} disabled={busy}>刷新状态</Button></div>
    <div className="orch-row"><label className="orch-field">本机代理 profile<Input value={profileInput} onChange={event => setProfileInput(event.target.value)} /></label>
      <Button variant="secondary" size="sm" disabled={busy || !profileInput.trim()} onClick={() => { generation.current++; ctx.storage.set('local-profile', profileInput.trim()); setLocalProfile(profileInput.trim()); setNodes([]); setExtensions([]); setSelected(''); setOutput(''); pendingTask.current = null }}>连接此本机代理</Button></div>
    <p>节点、配对与扩展请求固定到本机代理，聊天当前连接不会改变执行位置。</p>
    <p>原生聊天、模型登录、Tools、Skills 和 MCP 保持在 Hermes 原有位置。此页管理节点、任务下发与独立扩展包。</p>
    {busy && <Loader label="正在同步客户端" />}
    {error && <ErrorState title="操作未完成" description={error}><Button variant="secondary" onClick={refresh}>重试读取</Button></ErrorState>}
    {settings && <section className="orch-section"><h2>本机代理连接</h2>
      <div className="orch-row"><label className="orch-field">私网调度服务地址<Input value={settings.server?.url || ''} onChange={event => setSettings(value => ({ ...value, server: { ...value?.server, url: event.target.value } }))} /></label>
      <label className="orch-field">令牌环境变量名<Input value={settings.server?.token_env || 'ORCHESTRATOR_TOKEN'} onChange={event => setSettings(value => ({ ...value, server: { ...value?.server, token_env: event.target.value } }))} /></label></div>
      <div className="orch-row"><label className="orch-field">所有者 ID<Input value={settings.node?.owner_id || ''} onChange={event => setSettings(value => ({ ...value, node: { ...value?.node, owner_id: event.target.value } }))} /></label>
      <label className="orch-field">本机节点 ID<Input value={settings.node?.node_id || ''} onChange={event => setSettings(value => ({ ...value, node: { ...value?.node, node_id: event.target.value } }))} /></label>
      <label className="orch-field">本机 default 工作区绝对路径<Input value={settings.node?.workspaces?.default || ''} onChange={event => setSettings(value => ({ ...value, node: { ...value?.node, workspaces: { ...value?.node?.workspaces, default: event.target.value } } }))} /></label></div>
      <div className="orch-row"><span>启用本机评分路由</span><Switch aria-label="启用本机评分路由" checked={settings.enabled === true} onCheckedChange={enabled => setSettings(value => ({ ...value, enabled }))} />
      <Button size="sm" disabled={busy} onClick={() => perform(async () => { const saved = await api<Settings>('/settings', { method: 'PUT', body: settings }); setSettings(saved); setConfigText(JSON.stringify(saved, null, 2)); setOutput('本机代理连接设置已保存') })}>保存连接设置</Button></div>
      <div className="orch-row"><span>允许云端 Agent 调度已配对节点</span><Switch aria-label="允许 Agent 调度节点" checked={settings.fleet_tools?.enabled === true} onCheckedChange={enabled => setSettings(value => ({ ...value, fleet_tools: { ...value?.fleet_tools, enabled } }))} /></div>
      <div className="orch-row"><label className="orch-field">Agent 可申请的工具（逗号分隔）<Input value={settings.fleet_tools?.allowed_tools?.join(', ') || ''} onChange={event => setSettings(value => ({ ...value, fleet_tools: { ...value?.fleet_tools, allowed_tools: event.target.value.split(',').map(item => item.trim()).filter(Boolean) } }))} /></label>
      <label className="orch-field">Agent 最大动作数<Input type="number" min={1} max={1000} value={settings.fleet_tools?.max_actions ?? 20} onChange={event => setSettings(value => ({ ...value, fleet_tools: { ...value?.fleet_tools, max_actions: Number(event.target.value) } }))} /></label></div>
      <p>修改上方调度授权后请保存连接设置。节点工具与工作区限制继续生效；提交和取消仍经过原生授权。默认不向 Agent 开放节点调度。</p>
      <p>本地路径只发送到上方指定的本机 profile。远程节点的真实路径由各节点自己配置，云端任务只携带工作区别名。</p>
    </section>}
    <section className="orch-section"><h2>本地节点</h2>
      {!nodes.length && <p>尚无可用节点。先保存私网服务地址与所有者凭据环境变量，再创建配对码。</p>}
      {nodes.map(node => <div key={node.node_id} className="orch-node" aria-selected={selected === node.node_id}>
        <div className="orch-row"><strong>{node.label || node.node_id}</strong><span>{node.online ? '在线' : '离线'}</span>
          <Button size="xs" variant={selected === node.node_id ? 'default' : 'secondary'} onClick={() => { pendingTask.current = null; setSelected(node.node_id) }}>选择此节点</Button>
          <Button size="xs" variant="ghost" disabled={busy} onClick={() => setRevoke(node)}>撤销节点</Button></div>
        <p>{node.capabilities.platform || '系统未知'} · 最近心跳 {node.last_seen_at ? new Date(node.last_seen_at * 1000).toLocaleString() : '未收到'}</p>
        <p>工作区：{node.capabilities.workspace_aliases?.join(', ') || '未配置'} · 工具：{node.capabilities.tools?.join(', ') || '未授权'}</p>
      </div>)}
      <div className="orch-row"><Input value={label} onChange={event => setLabel(event.target.value)} placeholder="节点名称" aria-label="节点名称" />
        <Button disabled={busy} onClick={() => perform(async () => { const result = await api('/pairing', { method: 'POST', body: { label } }); setPairing(JSON.stringify(result, null, 2)) })}>创建一次性配对码</Button></div>
      {pairing && <pre className="orch-pre">{pairing}</pre>}
      <div className="orch-row"><Input value={pairCode} onChange={event => setPairCode(event.target.value)} placeholder="在此电脑输入配对码" aria-label="配对码" />
        <Button disabled={busy || !pairCode.trim()} onClick={() => perform(async () => { const result = await api('/pairing/redeem', { method: 'POST', body: { code: pairCode, label } }); setPairCode(''); setOutput(JSON.stringify(result, null, 2)) })}>配对此电脑</Button></div>
      <div className="orch-row"><Button size="sm" variant="secondary" disabled={busy} onClick={() => perform(async () => { setWorker(JSON.stringify(await api('/node/status'), null, 2)) })}>读取本机节点状态</Button>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => perform(async () => { setWorker(JSON.stringify(await api('/worker/start', { method: 'POST', body: {} }), null, 2)) })}>启动本机任务接收</Button>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => perform(async () => { setWorker(JSON.stringify(await api('/worker/stop', { method: 'POST', body: {} }), null, 2)) })}>停止接收新任务</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => perform(async () => { setWorker(JSON.stringify(await api('/node/run_once', { method: 'POST', body: {} }), null, 2)) })}>领取执行一次</Button></div>
      <p>停止接收不会撤销已经开始的操作。修改节点绑定或工作区前，请读取状态并等待 running=false。</p>
      {worker && <pre className="orch-pre">{worker}</pre>}
    </section>
    <section className="orch-section"><h2>向指定节点下发任务</h2>
      <p>{selected ? `当前目标：${selected}` : '请先选择一个节点。工作目录只使用节点公开的别名。'}</p>
      <Textarea value={text} onChange={event => { pendingTask.current = null; setText(event.target.value) }} placeholder="描述任务" aria-label="任务内容" />
      <div className="orch-row"><label className="orch-field">工作区别名<Input value={workspace} onChange={event => { pendingTask.current = null; setWorkspace(event.target.value) }} /></label>
        <label className="orch-field">相对目录<Input value={relative} onChange={event => { pendingTask.current = null; setRelative(event.target.value) }} /></label>
        <label className="orch-field">工具授权（逗号分隔）<Input value={tools} onChange={event => { pendingTask.current = null; setTools(event.target.value) }} /></label></div>
      <Button disabled={busy || !selected || !text.trim()} onClick={sendTask}>确认下发任务</Button>
      {lastTaskId && <Button variant="secondary" disabled={busy} onClick={() => perform(async () => { setOutput(JSON.stringify(await api(`/tasks/${encodeURIComponent(lastTaskId)}`), null, 2)) })}>读取任务回执</Button>}
    </section>
    <section className="orch-section"><div className="orch-row"><h2>已安装扩展</h2><Button size="sm" variant="secondary" onClick={install} disabled={busy}>从扩展包安装</Button>
      <Button size="sm" variant="ghost" onClick={() => host.navigate('/capabilities?tab=plugins')}>管理原生插件</Button></div>
      {!extensions.length && <p>没有安装可选扩展。客户端的原生聊天与节点功能可独立使用。</p>}
      {extensions.map(extension => <div className="orch-row" key={extension.id}><strong>{extension.id}</strong><span>{extension.version || ''}</span><span>{extension.enabled === false ? '已安装，未启用' : '已安装'}</span>
        {extension.enabled && ['local-judge', 'free-models'].includes(extension.id) && <><span>用于任务路由</span><Switch aria-label={`在路由中使用 ${extension.id}`} checked={Boolean((settings?.extensions as Record<string, boolean> | undefined)?.[extension.id])} disabled={busy}
          onCheckedChange={enabled => perform(async () => { const updated = { ...settings, extensions: { ...(settings?.extensions as Record<string, boolean> || {}), [extension.id]: enabled } }; const saved = await api<Settings>('/settings', { method: 'PUT', body: updated }); setSettings(saved); setConfigText(JSON.stringify(saved, null, 2)) })} /></>}
      </div>)}
    </section>
    <details className="orch-section"><summary>客户端高级设置</summary><p>保存令牌环境变量名称，不在设置中填写密码或访问令牌。修改后端模型仍通过原生设置完成。</p>
      <Textarea aria-label="客户端设置 JSON" value={configText} onChange={event => setConfigText(event.target.value)} />
      <Button disabled={busy} onClick={() => perform(async () => { const saved = await api<Settings>('/settings', { method: 'PUT', body: JSON.parse(configText) }); setSettings(saved); setConfigText(JSON.stringify(saved, null, 2)); setOutput('设置已保存') })}>保存客户端设置</Button></details>
    {output && <section className="orch-section"><h2>操作回执</h2><pre className="orch-pre">{output}</pre></section>}
  </main>
}

export default { id: 'orchestrator-core', name: '云端本地协同', description: '多节点配对、定向任务和独立扩展安装。', defaultEnabled: true,
  register(ctx) {
    ctx.register({ id: 'page', area: ROUTES_AREA, data: { path: '/orchestrator' }, render: () => <ClientPage ctx={ctx} /> })
    ctx.register({ id: 'nav', area: SIDEBAR_NAV_AREA, order: 65, data: { codicon: 'remote', label: '云端本地协同', path: '/orchestrator' } })
    ctx.registerSettingsPage({ id: 'client', title: '云端本地协同', icon: 'remote', render: () => <ClientPage ctx={ctx} /> })
  }
} satisfies HermesPlugin
