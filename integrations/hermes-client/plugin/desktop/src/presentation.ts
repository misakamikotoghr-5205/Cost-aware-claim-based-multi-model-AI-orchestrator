/** Presentation only. Original provider observations and machine reasons stay in the API snapshot. */
const reasons: Record<string, string> = {
  'No cloud quota server is configured.': '尚未配置云端服务。本机聊天和账号额度查看可独立使用。',
  'No valid cloud account/model pool identities are configured.': '尚未指定云端配额对应的账号和模型，请先完成云端配额配置。',
  "Cloud quota configuration changed or has not been refreshed; the previous connection's observations are not displayed.": '云端连接尚未刷新或已改变。请读取最新额度，旧连接的数据不会混入当前页面。',
  'Cloud quota configuration changed during refresh; refresh the current connection again.': '刷新期间云端连接已改变，请重新读取当前连接的额度。',
  'Cloud quota could not be read; check the configured connection and account grant.': '未能读取云端工作配额，请检查连接和账号授权。',
  'The cloud quota refresh deadline was reached.': '本次读取云端工作配额已超时，请稍后刷新。',
  'Uncertain consumption blocks this cloud pool until reconciliation.': '有任务用量尚未核对，此云端配额暂不可分配。',
  'The cloud observation has reached its reset time.': '云端配额已到重置时间，需要重新读取后才能使用。',
  'The cloud observation is stale; it is not spendable quota.': '云端配额记录已过期，不能按此记录分配任务，请刷新。',
  'The cached cloud observation has no valid freshness deadline.': '云端记录缺少有效的采集时间或有效期，请重新读取。',
  'The cached cloud observation is stale; refresh to obtain a new observation.': '云端配额记录已过期，请读取最新数据。',
  'The cached cloud observation state is invalid.': '云端配额记录状态无法确认，请重新读取。',
  'One or more cached cloud quota windows are unavailable, stale, expired or blocked.': '部分云端用量窗口未获取、已过期或暂不可分配，请查看各窗口状态。',
  'One or more cached cloud quota pools do not have a fresh usable observation.': '部分云端配额尚无有效记录，请刷新后核对。',
  'One or more cloud quota windows are unavailable, stale, expired or blocked.': '部分云端用量窗口未获取、已过期或暂不可分配，请查看各窗口状态。',
  'One or more cloud quota pools do not have a fresh usable observation.': '部分云端配额尚无有效记录，请刷新后核对。',
  'Some configured pool identities were invalid or exceeded the refresh limit.': '部分账号或模型的配额配置无效，或超过本次读取数量上限。',
  'Invalid quota import request': '用量快照导入参数无效，请重新选择格式和文件。',
  'Choose a sanitized quota JSON response, not a credential file': '请选择已移除凭证的用量快照，不能导入账号凭证文件。',
  'Import must contain quota observations, not credentials': '该文件包含凭证字段，请移除凭证后再导入用量快照。',
  'Quota import requires a regular JSON file': '请选择本机普通 JSON 文件，不能导入目录或链接。',
  'Quota import exceeds 1 MiB': '用量快照超过 1 MiB，请选择较小的文件。',
  'quota JSON exceeds 1 MiB': '用量快照超过 1 MiB，请选择较小的文件。',
  'invalid quota JSON': '用量快照格式无效，请检查文件是否为支持的 JSON。',
  'expected schema_version, source, observations only': '标准快照需包含版本、来源和用量记录，请按标准格式整理后导入。',
  'unsupported quota schema version': '此用量快照版本暂不支持。',
  'observations must contain 1..1000 items': '用量快照需要包含 1 至 1000 条记录。',
  'quota observation has missing or unknown fields': '用量记录存在缺少或不支持的字段，请核对快照格式。',
  'duplicate account/model observation': '同一账号和模型存在重复用量记录，请核对后导入。',
  'remaining exceeds capacity': '剩余额度超过上限，请核对原始用量记录。',
  'max_age_seconds must be positive': '用量记录的有效期必须大于零。',
  'Saved provider responses require their actual observation time': '请填写服务商快照的实际采集时间，不能用导入时间代替。',
  'Unsupported provider quota response': '服务商快照格式暂不支持，请选择匹配的格式。',
  'Codex quota requires rate_limit': 'Codex 快照缺少用量窗口，请导入完整的官方用量响应。',
  'Invalid Codex additional allowances': 'Codex 附加用量窗口格式无效，请核对官方快照。',
  'Anthropic quota requires an allowance window': 'Claude 快照缺少订阅用量窗口。',
  'OpenRouter quota requires credits or API-key usage data': 'OpenRouter 快照缺少余额或 API 密钥用量数据。',
  'DeepSeek balance is unavailable': 'DeepSeek 快照未提供可读取的货币余额。',
  'Stop the node worker and wait for completion before pairing': '请先停止任务接收，并等待已开始的操作结束后再配对。',
  'Configure the fleet server origin before pairing': '请先配置有效的云端服务地址，再进行配对。',
}
const chineseReasons = new Set([
  '此服务商暂不支持自动读取额度，可导入已核实的数据。', '未找到本机凭证，请在原生账号设置中登录。',
  '原生账号当前不可用，请检查账号设置。', 'Claude 订阅额度需原生 OAuth 账号；API 密钥不能代表订阅额度。',
  '无法读取官方用量，请检查本机登录状态和访问权限。', '官方响应未提供可读取的额度窗口。',
  '官方额度请求失败，或地址、响应暂不支持；剩余额度未知。', '服务商响应中没有可读取的额度数值。',
  '尚未刷新。可读取官方用量；如需登录，请前往原生账号设置。', '无法读取本机账号列表。', '尚未读取云端台账。',
  '观测已过期，请刷新或导入更新的数据。', '重置时间已到，需重新读取额度；当前数值仅供核对。',
  '本次刷新已达到账号数量或时间上限；该账号额度未知。', '同时刷新的配置过多，请稍后重试。',
  '额度刷新失败；旧数据保留原始采集时间。', '服务商额度与云端台账分别显示；查看不会预留额度。',
  '公开模型标价为零不代表账号有可用额度；缺失数据保持未知。',
  '未登录此服务，无法读取真实额度。', '部分配额仍待核对。', '上次任务的用量尚未核对。',
  '尚未配置私网调度服务。', '当前客户端无法绑定原生账号设置的本机配置，请升级 MISAKA AGENT 客户端后重试。',
  '本机扩展接口尚未加载。请在原生插件页启用智能体功能，重启对应本机代理，再重新连接。请等待当前任务结束后重启。',
  '本机代理不可用，请更新桌面客户端；请求不会转发到远程聊天连接。',
  '本机配置名称无效，请使用不含路径分隔符的配置名称。', '扩展请求路径无效，请重新打开页面后重试。',
])

export function quotaReason(reason: string | null | undefined, provider = '') {
  if (!reason) return ''
  if (provider.toLowerCase() === 'copilot' && reason === '此服务商暂不支持自动读取额度，可导入已核实的数据。') {
    return 'GitHub Copilot 的登录可用状态与额度分开显示。当前尚不支持读取其用量接口，额度未获取，可导入已核实的快照。'
  }
  return reasons[reason] || (chineseReasons.has(reason) ? reason : '暂未获取可确认的数据，请检查账号、连接或快照后重试。')
}

export function operationError(failure: unknown) {
  const message = failure instanceof Error ? failure.message : typeof failure === 'string' ? failure : ''
  const candidates = [message, message.replace(/^(?:Error:\s*)?(?:\d{3}:\s*)?/, '')]
  for (const candidate of [...candidates]) {
    try { const value = JSON.parse(candidate); if (typeof value.detail === 'string') candidates.push(value.detail) } catch { /* Not a structured API error. */ }
  }
  // Electron may add its own wrapper. Match only our fixed messages, never echo response text or URLs.
  for (const [key, value] of Object.entries(reasons)) if (candidates.includes(key) || message.endsWith(': ' + key)) return value
  for (const key of chineseReasons) if (candidates.includes(key) || message.endsWith(': ' + key)) return key
  if (/\b(?:401|403)\b/.test(message)) return '当前请求未获授权，请检查本机登录状态和扩展权限。'
  return '操作未完成，请检查本机连接、账号状态或导入文件后重试。'
}

export function sourceLabel(source: string) {
  if (!source) return '未提供'
  if (source.startsWith('file:')) return '手动导入的用量快照'
  return ({ 'native-hermes': '本机账号配置', 'native-official-usage': '本机账号读取的官方用量',
    'official-usage-api': '服务商官方用量接口', 'provider-response': '服务商用量响应',
    'codex-rate-limit-snapshot': 'Codex 官方用量快照', 'fleet-ledger': '云端调度台账' } as Record<string, string>)[source] || '其他用量记录（来源待核实）'
}
export function providerLabel(provider: string) {
  return ({ 'openai-codex': 'Codex', codex: 'Codex', anthropic: 'Anthropic', openrouter: 'OpenRouter', deepseek: 'DeepSeek',
    copilot: 'GitHub Copilot', imported: '导入的账号', 'fleet-ledger': '云端工作配额', 'private-fleet': '云端工作配额', custom: '自定义服务' } as Record<string, string>)[provider.toLowerCase()] || provider
}
export function windowLabel(label: string) {
  return ({ Account: '账号额度', Session: '会话窗口（5小时）', Week: '每周窗口（7天）', 'Opus week': 'Opus 每周窗口',
    'Sonnet week': 'Sonnet 每周窗口', 'Shared account pool': '共享账号配额', '*': '共享账号配额' } as Record<string, string>)[label] || label.replace(/^Account · /, '账号 · ')
}
export function scopeLabel(scope: string) {
  return ({ account: '账号内共享', model: '指定模型', api_key: 'API 密钥限额' } as Record<string, string>)[scope] || (scope === '账号内共享' ? scope : '服务商未说明')
}
export function platformLabel(platform: string) {
  return ({ linux: 'Linux', win32: 'Windows', darwin: 'macOS' } as Record<string, string>)[platform] || '未确认'
}
