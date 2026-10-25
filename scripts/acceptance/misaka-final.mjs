/** Acceptance of the real installed MISAKA native surface; no provider replies. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const ownDirectory = dirname(fileURLToPath(import.meta.url))
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

export async function auditNativeCapture(application) {
  const records = []
  let partial = ''
  const prefix = '[MISAKA_NATIVE_ACCEPTANCE_AUDIT]'
  const listener = chunk => {
    partial = (partial + chunk.toString()).slice(-64 * 1024)
    const lines = partial.split('\n')
    partial = lines.pop()
    for (const line of lines) {
      const offset = line.indexOf(prefix)
      if (offset < 0) continue
      try {
        const value = JSON.parse(line.slice(offset + prefix.length))
        if (records.length < 200) records.push(value)
      } catch { /* Only this harness's complete bounded JSON records are retained. */ }
    }
  }
  application.process().stdout?.on('data', listener)
  await application.evaluate(({ desktopCapturer, dialog, utilityProcess }, marker) => {
    const restorations = []
    const record = (operation, stage, code) => console.log(marker + JSON.stringify({ operation, stage, timestamp_ms: Date.now(), ...(code === undefined ? {} : { code }) }))
    for (const [target, name, operation] of [[desktopCapturer, 'getSources', 'desktopCapturer.getSources'],
      [dialog, 'showMessageBox', 'dialog.showMessageBox'], [utilityProcess, 'fork', 'utilityProcess.fork']]) {
      const original = target[name]
      target[name] = function (...args) {
        record(operation, 'start')
        try {
          const result = Reflect.apply(original, this, args)
          if (name === 'fork') {
            result.once('spawn', () => record(operation, 'spawn'))
            result.once('exit', code => record(operation, 'exit', code))
            result.once('message', () => record(operation, 'message'))
          }
          if (result && typeof result.then === 'function') result.then(() => record(operation, 'end'), () => record(operation, 'rejected'))
          else record(operation, 'end')
          return result
        } catch (error) { record(operation, 'threw'); throw error }
      }
      restorations.push(() => { target[name] = original })
    }
    globalThis.__misakaAcceptanceRestoreAudit = () => restorations.forEach(restore => restore())
  }, prefix)
  return {
    records,
    async stop() {
      try {
        await deadline(application.evaluate(() => { globalThis.__misakaAcceptanceRestoreAudit?.() }), 1500, 'Main-process audit shutdown timed out')
        return { records, main_process_responsive: true, parameters_and_returns_unmodified: true }
      } catch {
        return { records, main_process_responsive: false, parameters_and_returns_unmodified: true }
      } finally { application.process().stdout?.removeListener('data', listener) }
    },
  }
}
async function until(read, accept, message, milliseconds = 15_000) {
  const end = Date.now() + milliseconds
  do {
    const value = await read()
    if (accept(value)) return value
    await sleep(100)
  } while (Date.now() < end)
  throw new Error(message)
}
async function deadline(promise, milliseconds, message) {
  let timer
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), milliseconds)
    })])
  } finally { clearTimeout(timer) }
}

export function provenance(upstream, repository) {
  const spec = JSON.parse(readFileSync(join(repository, 'integrations/hermes-client/upstream.json'), 'utf8'))
  const packageInfo = JSON.parse(readFileSync(join(upstream, 'apps/desktop/package.json'), 'utf8'))
  const patchRoot = join(repository, 'integrations/hermes-client/patches')
  const patches = readdirSync(patchRoot).filter(name => name.endsWith('.patch')).sort()
    .map(name => ({ file: name, sha256: sha(readFileSync(join(patchRoot, name))) }))
  const dist = join(upstream, 'apps/desktop/dist')
  const files = []
  function walk(folder) {
    for (const item of readdirSync(folder, { withFileTypes: true })) {
      assert(!item.isSymbolicLink(), 'Native dist must be self-contained')
      const path = join(folder, item.name)
      if (item.isDirectory()) walk(path)
      else if (item.isFile()) files.push(path)
    }
  }
  walk(dist)
  const digest = createHash('sha256')
  let bytes = 0
  for (const path of files.sort()) {
    const content = readFileSync(path)
    bytes += content.byteLength
    digest.update(relative(dist, path).replaceAll('\\', '/') + '\0' + sha(content) + '\0')
  }
  return { release_version: spec.core_version, application_package_version: packageInfo.version,
    expected_product_name: packageInfo.productName, patches,
    dist_sha256: digest.digest('hex'), dist_files: files.length, dist_bytes: bytes }
}

export async function verifyBrand(application, page) {
  const value = await application.evaluate(({ app, BrowserWindow }, url) => {
    const main = BrowserWindow.getAllWindows().find(window => window.webContents.getURL() === url)
    return { application_name: app.getName(), main_window_title: main?.getTitle(), owner_id: main?.id }
  }, page.url())
  assert.equal(value.application_name, 'MISAKA AGENT')
  assert.equal(value.main_window_title, 'MISAKA AGENT')
  const updates = await page.evaluate(async () => {
    const api = window.hermesDesktop.updates
    return { check: await api.check(), apply: await api.apply(), channel: await api.setChannel('stable') }
  })
  for (const result of [updates.check, updates.channel]) {
    assert.equal(result.supported, false)
    assert.equal(result.reason, 'misaka-managed-build')
    assert.equal(result.channelSelectable, false)
  }
  assert.equal(updates.apply.ok, false)
  assert.equal(updates.apply.manual, true)
  assert.equal(updates.apply.error, 'misaka-managed-build')
  return { ...value, official_update_guard: updates }
}

async function windowState(application, ownerId) {
  return application.evaluate(({ BrowserWindow, screen }, owner) => {
    const main = BrowserWindow.fromId(owner)
    const pointer = BrowserWindow.getAllWindows().find(window => window.webContents.getURL().includes('win=pointer'))
    const display = pointer ? screen.getDisplayMatching(pointer.getBounds()) : null
    return { main_visible: main?.isVisible(), pointer_visible: pointer?.isVisible(),
      pointer_topmost: pointer?.isAlwaysOnTop(), pointer_bounds: pointer?.getBounds(),
      work_area: display?.workArea }
  }, ownerId)
}

async function realCompactCrop(application, compact, output) {
  const pid = await application.evaluate(() => process.pid)
  const helper = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
    join(ownDirectory, 'native-dialog.ps1'), '-ApplicationPid', String(pid),
    '-DiagnosticsPath', join(output, 'compact-native-dialog-report.json')], { stdio: 'ignore' })
  const consent = new Promise((resolve, reject) => {
    helper.once('exit', code => code === 0 ? resolve() : reject(new Error('Actual compact screen consent failed')))
    helper.once('error', reject)
  })
  consent.catch(() => {})
  try {
    await compact.getByRole('button', { name: '开始圈选', exact: true }).click()
    await deadline(consent, 80_000, 'Actual compact screen chooser exceeded its budget')
    const viewport = await until(() => application.evaluate(async ({ BrowserWindow }) => {
      const overlay = BrowserWindow.getAllWindows().find(window => window.webContents.getURL().startsWith('data:text/html'))
      if (!overlay?.isVisible()) return null
      return overlay.webContents.executeJavaScript('document.querySelector("#screen")?.naturalWidth ? ({width:innerWidth,height:innerHeight,bitmapWidth:document.querySelector("#screen").naturalWidth,bitmapHeight:document.querySelector("#screen").naturalHeight}) : null')
    }), value => Boolean(value), 'Actual compact native overlay did not open')
    const point = { x: Math.min(560, viewport.width - 20), y: Math.min(460, viewport.height - 20) }
    const png = await application.evaluate(async ({ BrowserWindow }, end) => {
      const overlay = BrowserWindow.getAllWindows().find(window => window.webContents.getURL().startsWith('data:text/html'))
      overlay.webContents.sendInputEvent({ type: 'mouseDown', x: 60, y: 140, button: 'left', clickCount: 1 })
      overlay.webContents.sendInputEvent({ type: 'mouseMove', ...end, button: 'left' })
      await new Promise(resolve => setTimeout(resolve, 100))
      return (await overlay.webContents.capturePage()).toPNG().toString('base64')
    }, point)
    writeFileSync(join(output, '07-misaka-compact-native-drag.png'), Buffer.from(png, 'base64'))
    await application.evaluate(({ BrowserWindow }, end) => {
      const overlay = BrowserWindow.getAllWindows().find(window => window.webContents.getURL().startsWith('data:text/html'))
      overlay.webContents.sendInputEvent({ type: 'mouseUp', ...end, button: 'left', clickCount: 1 })
    }, point)
    await compact.locator('summary').filter({ hasText: '预览与环境' }).waitFor({ state: 'visible', timeout: 30_000 })
    await compact.locator('summary').filter({ hasText: '预览与环境' }).click()
    const image = compact.getByAltText('已圈选区域预览', { exact: true })
    await image.waitFor({ state: 'visible' })
    const size = await image.evaluate(element => ({ width: element.naturalWidth, height: element.naturalHeight }))
    assert.equal(size.width, Math.ceil(point.x * viewport.bitmapWidth / viewport.width) - Math.floor(60 * viewport.bitmapWidth / viewport.width))
    assert.equal(size.height, Math.ceil(point.y * viewport.bitmapHeight / viewport.height) - Math.floor(140 * viewport.bitmapHeight / viewport.height))
    const url = await image.getAttribute('src')
    assert(url.startsWith('data:image/png;base64,'))
    writeFileSync(join(output, '08-misaka-compact-cropped-region.png'), Buffer.from(url.split(',')[1], 'base64'))
    await compact.getByRole('button', { name: '文档', exact: true }).click()
    await compact.locator('summary').filter({ hasText: '预览与环境' }).click()
    return size
  } finally {
    if (helper.exitCode === null) helper.kill()
  }
}

export async function verifyDashboard(main, output) {
  await main.getByRole('button', { name: /^(Capabilities|技能与工具)$/ }).click()
  await main.getByRole('button', { name: /^(Plugins|插件)$/ }).first().click()
  await main.getByPlaceholder(/^(Search plugins|搜索插件)$/).fill('云端本地协同')
  await main.locator('[data-catalog-cards="plugins"], [data-catalog-list="plugins"]')
    .getByRole('button', { name: '云端本地协同', exact: true }).click()
  const desktop = main.getByRole('switch', { name: /^(Desktop|桌面): 云端本地协同$/ })
  await desktop.check()
  assert.equal(await desktop.getAttribute('aria-checked'), 'true')
  await main.getByRole('button', { name: /^(Close|关闭)$/ }).last().click()
  await main.getByRole('button', { name: '额度总览', exact: true }).click()
  const dashboard = main.getByRole('main', { name: 'MISAKA AGENT 额度总览', exact: true })
  await dashboard.getByRole('heading', { name: '账号用量', exact: true }).waitFor({ state: 'visible' })
  const cards = dashboard.locator('.misaka-account-grid .misaka-quota-card')
  await cards.first().waitFor({ state: 'visible', timeout: 30_000 })
  const before = await dashboard.locator('.misaka-overview time').textContent()
  await dashboard.getByRole('button', { name: '读取最新额度', exact: true }).click()
  const refreshed = await until(() => dashboard.locator('.misaka-overview time').textContent(),
    value => Boolean(value) && value !== '未提供' && value !== before, 'Actual native quota refresh did not update its observation time', 30_000)
  await dashboard.getByRole('button', { name: '读取最新额度', exact: true }).waitFor({ state: 'visible' })
  const states = await cards.locator('.misaka-badge').evaluateAll(elements => elements.map(element => element.getAttribute('data-state')))
  assert(states.length >= 1)
  assert(states.every(state => state === 'unknown'), 'The offline test endpoint must not fabricate a provider balance')
  assert.equal(await cards.locator('progress').count(), 0)
  const reasons = await cards.locator('.misaka-reason').allTextContents()
  assert(reasons.length >= 1 && reasons.every(reason => reason.trim()))
  await main.screenshot({ path: join(output, '03a-windows-native-quota-dashboard.png') })
  await main.getByRole('button', { name: /^新建会话/ }).first().click()
  return { actual_core_desktop_enabled: true, real_refresh_completed: true, refreshed_time: refreshed,
    account_states: states, unknown_reasons: reasons, fabricated_progress_bars: 0,
    real_provider_balance: 'not_tested_no_account_credentials', offline_acceptance_profile: true }
}

export async function verifyCompact(application, main, output, ownerId) {
  await main.getByRole('button', { name: /^(Capabilities|技能与工具)$/ }).click()
  await main.getByRole('button', { name: /^(Plugins|插件)$/ }).first().click()
  await main.getByPlaceholder(/^(Search plugins|搜索插件)$/).fill('智能指针')
  await main.locator('[data-catalog-cards="plugins"], [data-catalog-list="plugins"]')
    .getByRole('button', { name: '智能指针', exact: true }).click()
  const agent = main.getByRole('switch', { name: /^(Agent|智能体): 智能指针$/ })
  assert.equal(await agent.getAttribute('aria-checked'), 'true', 'Native Python half must already be admitted')
  const desktop = main.getByRole('switch', { name: /^(Desktop|桌面): 智能指针$/ })
  await desktop.check()
  assert.equal(await desktop.getAttribute('aria-checked'), 'true')
  await main.getByRole('button', { name: /^(Close|关闭)$/ }).last().click()
  await main.getByRole('button', { name: /^(New session|新建会话)/ }).first().click()
  const scope = { sessionId: 'new', profile: 'default', connectionId: 'local' }
  const opened = await main.evaluate(value => window.hermesDesktop.pointerCompact.open(value), scope)
  assert.equal(opened.ok, true, opened.message)
  const compact = await until(async () => application.windows().find(page => page.url().includes('win=pointer')),
    value => Boolean(value), 'Actual installed compact pointer window did not open')
  await compact.getByRole('region', { name: '智能指针浮框', exact: true }).waitFor({ state: 'visible', timeout: 30_000 })
  const empty = await until(() => windowState(application, ownerId), state => state.pointer_bounds?.height >= 180 && state.pointer_bounds.height <= 210,
    'Empty pointer did not settle into its compact height')
  assert.equal(empty.main_visible, false)
  assert.equal(empty.pointer_topmost, true)
  assert.equal(empty.pointer_bounds.width, 360)
  assert(Math.abs(empty.work_area.x + empty.work_area.width - empty.pointer_bounds.x - empty.pointer_bounds.width - 16) <= 2)
  assert(Math.abs(empty.pointer_bounds.y - empty.work_area.y - 16) <= 2)
  await compact.screenshot({ path: join(output, '06-misaka-empty-compact.png') })
  const crop = await realCompactCrop(application, compact, output)
  await compact.getByRole('textbox', { name: '关键词', exact: true }).fill('润色')
  await compact.getByRole('button', { name: '生成多个候选', exact: true }).click()
  const candidates = compact.locator('.pointer-intents button')
  await candidates.first().waitFor({ state: 'visible', timeout: 30_000 })
  const initial = await candidates.allTextContents()
  assert(initial.length >= 2 && initial.length <= 5)
  await compact.screenshot({ path: join(output, '09-misaka-document-candidates.png') })
  await compact.getByRole('textbox', { name: '关键词', exact: true }).fill('简洁')
  await compact.getByRole('button', { name: '追加关键词 / 换一批', exact: true }).click()
  await until(() => candidates.allTextContents(), value => value.length >= 2 && JSON.stringify(value) !== JSON.stringify(initial), 'Refinement did not replace the real candidates')
  const refined = await candidates.allTextContents()
  await candidates.first().click()
  await compact.getByText('预览操作草稿', { exact: true }).click()
  const draft = await compact.getByRole('textbox', { name: '聊天草稿预览', exact: true }).inputValue()
  assert(draft.trim())
  const consent = compact.getByRole('switch', { name: '附上所选区域图片', exact: true })
  assert.equal(await consent.getAttribute('aria-checked'), 'false')
  await consent.check()
  await compact.screenshot({ path: join(output, '10-misaka-review-and-image-consent.png') })
  await compact.getByRole('button', { name: '确认加入聊天草稿', exact: true }).click()
  await compact.getByText('草稿和选区图片已就绪。返回聊天检查后发送。', { exact: true }).waitFor({ state: 'visible', timeout: 30_000 })
  assert.equal((await windowState(application, ownerId)).main_visible, false)
  await compact.getByRole('button', { name: '返回聊天并关闭智能指针', exact: true }).click()
  await until(() => windowState(application, ownerId), value => value.main_visible && !value.pointer_visible, 'Compact close did not restore the original owner')
  const editor = main.getByRole('textbox', { name: /^(Message|消息)$/ })
  assert.equal((await editor.innerText()).trim(), draft.trim())
  assert.equal(await main.locator('[data-slot="composer-attachments"] img').count(), 1)
  await main.screenshot({ path: join(output, '11-misaka-restored-chat-draft.png') })
  const reopened = await main.evaluate(value => window.hermesDesktop.pointerCompact.open(value), scope)
  assert.equal(reopened.ok, true, reopened.message)
  const escapeWindow = await until(async () => application.windows().find(page => page.url().includes('win=pointer')),
    value => Boolean(value), 'Compact did not reopen for actual Escape')
  await escapeWindow.getByRole('region', { name: '智能指针浮框', exact: true }).waitFor({ state: 'visible' })
  await escapeWindow.keyboard.press('Escape').catch(error => {
    // Native keyDown may close its target before Playwright can deliver keyUp.
    if (!escapeWindow.isClosed()) throw error
  })
  await until(() => windowState(application, ownerId), value => value.main_visible && !value.pointer_visible, 'Actual Escape did not restore original chat')
  assert.equal((await editor.innerText()).trim(), draft.trim())
  assert.equal(await main.locator('[data-slot="composer-attachments"] img').count(), 1)
  assert.equal(await main.locator('[data-role="user"]').count(), 0)
  return { empty_window: empty, crop, initial_candidates: initial, refined_candidates: refined,
    exact_draft_received: true, native_images: 1, escape_restored_owner: true, no_sent_user_messages_in_renderer: true }
}
