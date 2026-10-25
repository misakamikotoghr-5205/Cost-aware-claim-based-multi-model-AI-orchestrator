import { Button, ErrorState, Input, Loader, type HermesPlugin, type PluginContext } from '@hermes/plugin-sdk'
import { localApi } from './local-api'
import { useEffect, useRef, useState } from 'react'
interface ModelJob { job_id: string; status: string; model: string; progress?: unknown; error?: string }
interface JudgeSettings { base_url: string; model: string; timeout: number; max_task_chars: number; max_candidates: number }
const CSS = `.judge-settings{display:grid;align-content:start;gap:16px;padding:20px;color:var(--ui-text-primary)}.judge-settings label{display:grid;gap:6px;font-size:12px}.judge-settings p{font-size:12px;color:var(--ui-text-secondary);margin:0}.judge-actions{display:flex;gap:8px;flex-wrap:wrap}`
export function JudgeSettingsPage({ ctx }: { ctx: PluginContext }) {
  const [localProfile, setLocalProfile] = useState(() => ctx.storage.get('local-profile', 'default'))
  const [profileInput, setProfileInput] = useState(localProfile)
  const api = localApi(localProfile)
  const [settings, setSettings] = useState<JudgeSettings>({ base_url: 'http://127.0.0.1:11434', model: 'qwen3:4b', timeout: 5, max_task_chars: 512, max_candidates: 3 })
  const [job, setJob] = useState<ModelJob | null>(null)
  const [setup, setSetup] = useState<{ runtime_reachable: boolean; model_installed: boolean; model: string } | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [result, setResult] = useState('')
  const generation = useRef(0)
  useEffect(() => { const current = ++generation.current; setBusy(true)
    api<JudgeSettings>('/settings').then(value => { if (current === generation.current) setSettings(value) })
      .catch(failure => { if (current === generation.current) setError(String(failure)) })
      .finally(() => { if (current === generation.current) setBusy(false) })
    return () => { generation.current++ }
  }, [ctx, localProfile])
  useEffect(() => {
    if (!job || ['complete', 'completed', 'succeeded', 'success', 'failed', 'cancelled', 'unknown'].includes(job.status)) return
    return ctx.setTimeout(() => {
      api<ModelJob>(`/models/pull/${encodeURIComponent(job.job_id)}`).then(setJob).catch(failure => setError(String(failure)))
    }, 1000)
  }, [job, ctx])
  async function inspectSetup() {
    setError('')
    try { setSetup(await api('/setup')) } catch (failure) { setError(String(failure)) }
  }
  async function pullModel() {
    setBusy(true); setError('')
    try {
      await api('/settings', { method: 'PUT', body: settings })
      setJob(await api<ModelJob>('/models/pull', { method: 'POST', body: { model: settings.model } }))
    } catch (failure) { setError(String(failure)) }
    finally { setBusy(false) }
  }
  async function action(test: boolean) {
    const current = ++generation.current; setBusy(true); setError(''); setResult('')
    try {
      if (test) {
        const value = await api<{ ok: boolean; models: string[] }>('/test', { method: 'POST', body: settings })
        if (current === generation.current) setResult(value.ok ? `本机连接成功，可用模型：${value.models.join('、') || '无，请先在 Ollama 安装模型'}` : '连接未通过，请检查本机 Ollama。')
      } else {
        const value = await api<JudgeSettings>('/settings', { method: 'PUT', body: settings })
        if (current === generation.current) { setSettings(value); setResult('本地判断模型设置已保存。') }
      }
    } catch (failure) { if (current === generation.current) setError(String(failure)) }
    finally { if (current === generation.current) setBusy(false) }
  }
  return <section className="judge-settings"><style>{CSS}</style><h2>本地判断模型</h2>
    <label>本机代理 profile<Input value={profileInput} onChange={event => setProfileInput(event.target.value)} /></label>
    <Button size="sm" variant="secondary" disabled={busy || !profileInput.trim()} onClick={() => { try { localApi(profileInput.trim()); generation.current++; ctx.storage.set('local-profile', profileInput.trim()); setLocalProfile(profileInput.trim()); setJob(null); setSetup(null); setResult(''); setError('') } catch (failure) { setError(String(failure)) } }}>连接此本机代理</Button>
    <p>此独立扩展使用本机 Ollama。未安装扩展时不注册判断入口，客户端仍可使用确定性评分。</p>
    <label>本机 Ollama 地址<Input value={settings.base_url} disabled={busy} onChange={event => setSettings(value => ({ ...value, base_url: event.target.value }))} /></label>
    <label>判断模型<Input value={settings.model} disabled={busy} onChange={event => setSettings(value => ({ ...value, model: event.target.value }))} /></label>
    <label>超时（秒）<Input type="number" min={1} max={60} value={settings.timeout} disabled={busy} onChange={event => setSettings(value => ({ ...value, timeout: Number(event.target.value) }))} /></label>
    <label>任务摘要最多字符数<Input type="number" min={128} max={2048} value={settings.max_task_chars} disabled={busy} onChange={event => setSettings(value => ({ ...value, max_task_chars: Number(event.target.value) }))} /></label>
    <label>参与判断的候选数<Input type="number" min={1} max={8} value={settings.max_candidates} disabled={busy} onChange={event => setSettings(value => ({ ...value, max_candidates: Number(event.target.value) }))} /></label>
    <p>仅有多个合格候选时才调用判断模型，默认查看最多 512 个字符的当前任务摘要和 3 个候选。长任务保留开头与末尾，并标明省略；仅附带相关能力与确定性评分，不读取对话历史、附件或截图。</p>
    <div className="judge-actions"><Button disabled={busy} onClick={() => action(false)}>保存设置</Button><Button variant="secondary" disabled={busy} onClick={() => action(true)}>测试本机连接</Button></div>
    <div className="judge-actions"><Button variant="secondary" disabled={busy} onClick={inspectSetup}>检测安装状态</Button>
      <Button variant="secondary" disabled={busy || (!!job && ['queued', 'running', 'cancelling'].includes(job.status))} onClick={pullModel}>安装所选本地模型</Button>
      <Button variant="ghost" onClick={() => ctx.os.openExternal('https://ollama.com/download')}>安装 Ollama 本机运行时</Button></div>
    <p>扩展包包含完整功能代码。模型权重由你点击后下载到本机 Ollama；此操作会使用网络和本机磁盘。</p>
    {setup && <p role="status">{setup.runtime_reachable ? (setup.model_installed ? `本机模型已安装：${setup.model}` : `运行时可用，尚未安装 ${setup.model}`) : '未连接到本机 Ollama，请先通过官方安装页安装并启动运行时。'}</p>}
    {job && <><pre role="status">{JSON.stringify(job, null, 2)}</pre>
      {['queued', 'running'].includes(job.status) && <Button variant="secondary" onClick={async () => { try { setJob(await api<ModelJob>(`/models/pull/${encodeURIComponent(job.job_id)}/cancel`, { method: 'POST', body: {} })) } catch (failure) { setError(String(failure)) } }}>停止跟踪此下载</Button>}
      <Button size="sm" variant="ghost" onClick={inspectSetup}>重新确认本机模型状态</Button></>}
    {busy && <Loader label="正在连接本地判断模型" />}
    {error && <ErrorState title="本地判断模型暂不可用" description={error} />}
    {result && <p role="status">{result}</p>}
    <p>判断结果只能从已通过额度、费用、工具与时效检查的候选中选择。无效输出或超时返回本机确定性评分。</p>
  </section>
}
export default { id: 'local-judge', name: '本地判断模型', description: '可独立安装的本机 Ollama 判断扩展。', defaultEnabled: true,
  register(ctx) { ctx.registerSettingsPage({ id: 'settings', title: '本地判断模型', icon: 'circuit-board', render: () => <JudgeSettingsPage ctx={ctx} /> }) }
} satisfies HermesPlugin
