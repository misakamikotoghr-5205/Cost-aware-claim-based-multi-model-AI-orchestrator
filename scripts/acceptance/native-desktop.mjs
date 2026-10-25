/** Actual pinned Electron application; no mock backend, UI, inference or auth. */
import assert from 'node:assert/strict'
import { spawn, spawnSync, execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { auditNativeCapture, provenance, verifyBrand, verifyCompact, verifyDashboard } from './misaka-final.mjs'

const { values } = parseArgs({ options: {
  upstream: { type: 'string' }, 'hermes-home': { type: 'string' }, output: { type: 'string' },
  'native-launcher': { type: 'string' },
} })
for (const key of ['upstream', 'hermes-home', 'output']) assert(values[key], `--${key} is required`)
const upstream = resolve(values.upstream)
const profile = resolve(values['hermes-home'])
const output = resolve(values.output)
mkdirSync(output, { recursive: true })
const requireUpstream = createRequire(join(upstream, 'package.json'))
const { _electron } = requireUpstream('@playwright/test')
const executablePath = requireUpstream('electron')
const desktopRoot = join(upstream, 'apps/desktop')
const nativeLauncher = values['native-launcher'] ? resolve(values['native-launcher'])
  : (process.platform === 'win32' ? ['hermes.exe', 'hermes.cmd'] : ['hermes'])
    .map(name => join(upstream, '.hermes/bin', name)).find(candidate => existsSync(candidate))
assert(nativeLauncher && existsSync(nativeLauncher), 'The actual prepared installation launcher is missing')
const ownDirectory = dirname(fileURLToPath(import.meta.url))
const userData = join(profile, 'acceptance-electron-user-data')
mkdirSync(userData, { recursive: true })
// This throwaway profile deliberately has no account or inference provider.
// Disable the optional guest tier and tutorial, exactly as native config allows.
if (!existsSync(join(profile, 'config.yaml'))) {
  writeFileSync(join(profile, 'config.yaml'), JSON.stringify({ nous: { guest: false }, onboarding: { run: false }, plugins: { enabled: [] } }))
}
writeFileSync(join(userData, 'window-state.json'), JSON.stringify({ x: 0, y: 0, width: 1280, height: 900, isMaximized: false }))
writeFileSync(join(userData, 'zoom-state.json'), JSON.stringify({ zoomLevel: 0 }))
const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
  !/(?:_API_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS|_PRIVATE_KEY)$/.test(name) &&
  !name.startsWith('HERMES_') && !['PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'ELECTRON_RUN_AS_NODE'].includes(name)))
Object.assign(env, {
  HERMES_HOME: profile, HERMES_RUNTIME_DIR: join(profile, 'tools'),
  HERMES_DESKTOP_USER_DATA_DIR: userData,
  HERMES_DESKTOP_HERMES_ROOT: upstream,
  HERMES_DESKTOP_HERMES: nativeLauncher,
  HERMES_DESKTOP_ISOLATED_BACKEND: '1',
  HERMES_DESKTOP_SKIP_QUIT_CONFIRM: '1', HERMES_DESKTOP_SKIP_PROTOCOL_REGISTRATION: '1',
})
const report = {
  platform: process.platform, architecture: process.arch,
  upstream_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: upstream, encoding: 'utf8' }).trim(),
  acceptance_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: resolve(ownDirectory, '../..'), encoding: 'utf8' }).trim(),
  started_at: new Date().toISOString(),
  model_inference: 'not_tested_no_provider_credentials',
  screenshots: [], checks: [],
}
let application
let nativeDialogProcess
let captureAudit
function checkpoint(phase) {
  report.phase = phase
  writeFileSync(join(output, 'native-desktop-report.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ phase, checks: report.checks.length, screenshots: report.screenshots }))
}
function bounded(promise, timeoutMs, label) {
  let timer
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(label + ' exceeded ' + timeoutMs + 'ms')), timeoutMs)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}
const redact = value => String(value).replace(/([?&](?:token|ticket|key)=)[^&\s]+/gi, '$1<redacted>').slice(0, 2000)
try {
  checkpoint('fingerprinting_actual_native_build')
  report.provenance = provenance(upstream, resolve(ownDirectory, '../..'))
  if (process.platform === 'win32') {
    checkpoint('probing_real_windows_capture_environment')
    report.screen_api_diagnostics = []
    for (const variant of [{ thumbnail: 0, disabled: true }, { thumbnail: 0, disabled: false }, { thumbnail: 1, disabled: false }, { thumbnail: 0, disabled: false, metadata: true }]) {
      const file = variant.metadata ? 'get-windows-environment.json' : 'capture-environment-' + variant.thumbnail + (variant.disabled ? '-gpu-disabled' : '-gpu-default') + '.json'
      const diagnostic = spawnSync(executablePath, [join(ownDirectory, 'capture-environment.mjs'), '--no-sandbox',
        ...(variant.disabled ? ['--disable-gpu'] : []), '--capture-thumbnail=' + variant.thumbnail,
        ...(variant.metadata ? ['--capture-mode=metadata'] : []),
        '--capture-report=' + join(output, file)], { env, cwd: desktopRoot, stdio: ['ignore', 'pipe', 'pipe'], timeout: 25_000, windowsHide: true, maxBuffer: 1024 * 1024 })
      let result = existsSync(join(output, file)) ? JSON.parse(readFileSync(join(output, file), 'utf8'))
        : { result: 'process_failed', status: diagnostic.status, code: diagnostic.error?.code, error: redact(diagnostic.stderr?.toString() || '') }
      if (diagnostic.error || diagnostic.status !== 0) {
        result = { ...result, result: 'process_failed', status: diagnostic.status,
          code: diagnostic.error?.code, error: redact(diagnostic.stderr?.toString() || '') }
        writeFileSync(join(output, file), JSON.stringify(result, null, 2))
      }
      report.screen_api_diagnostics.push({ file, ...result })
      console.log(JSON.stringify({ screen_api_diagnostic: file, result: result.result, elapsed_ms: result.elapsed_ms, error: result.error }))
    }
    report.native_dialog_diagnostics = []
    for (const hidden of [false, true]) for (const withSignal of [false, true]) {
      const file = 'dialog-environment-hide-' + hidden + '-signal-' + withSignal + '.json'
      const diagnostic = spawnSync(executablePath, [join(ownDirectory, 'dialog-environment.mjs'), '--no-sandbox',
        '--dialog-report=' + join(output, file), '--dialog-windows-hide=' + hidden,
        ...(withSignal ? ['--dialog-with-signal'] : [])], {
        env, cwd: desktopRoot, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: hidden, timeout: 20_000, maxBuffer: 1024 * 1024,
      })
      const value = existsSync(join(output, file)) ? JSON.parse(readFileSync(join(output, file), 'utf8')) : { result: 'process_failed' }
      const result = diagnostic.error || diagnostic.status !== 0 ? { ...value, result: 'process_failed', status: diagnostic.status, code: diagnostic.error?.code } : value
      writeFileSync(join(output, file), JSON.stringify(result, null, 2))
      report.native_dialog_diagnostics.push({ file, ...result })
      console.log(JSON.stringify({ native_dialog_diagnostic: file, result: result.result, elapsed_ms: result.elapsed_ms }))
    }
  }
  checkpoint('launching_real_electron')
  application = await _electron.launch({ executablePath, args: [desktopRoot, ...(process.platform === 'win32' ? [] : ['--disable-gpu']), '--no-sandbox'], env, cwd: desktopRoot, timeout: 120_000 })
  const page = await application.firstWindow({ timeout: 90_000 })
  checkpoint('waiting_for_real_renderer')
  await page.waitForFunction(() => document.getElementById('root')?.textContent?.length > 100, null, { timeout: 120_000 })
  checkpoint('waiting_for_real_backend_ui')
  await page.screenshot({ path: join(output, '00-windows-native-boot.png'), timeout: 5000 })
  report.screenshots.push('00-windows-native-boot.png')
  // Wait for the actual backend boot to settle; no fake boot or fixture server.
  await page.waitForFunction(() => {
    const text = document.body.textContent || ''
    return /provider|API key|Sign in|ready|Ready|提供方|API 密钥|登录|就绪/.test(text) && !/Starting Hermes|正在启动 MISAKA/.test(text)
  }, null, { timeout: 180_000 })
  checkpoint('checking_authenticated_native_config')
  await bounded(page.evaluate(async () => {
    const status = await window.hermesDesktop.api({ path: '/api/config', method: 'GET',
      connectionId: 'local', profile: 'default', priority: 'foreground' })
    if (!status || typeof status !== 'object') throw new Error('Real authenticated native config API did not respond')
  }), 90_000, 'Real authenticated native config API')
  report.checks.push('Real authenticated native config API responded through the actual preload transport')
  await page.screenshot({ path: join(output, '01-windows-native-first-run.png') })
  report.screenshots.push('01-windows-native-first-run.png')
  report.checks.push('Real native Electron renderer booted with isolated no-account profile')
  assert(await page.evaluate(() => typeof window.hermesDesktop?.regionCapture?.capture === 'function'))
  report.checks.push('Patched native region-capture capability exposed by actual preload')
  report.identity = await verifyBrand(application, page)
  report.checks.push('Actual application name and native owner title equal MISAKA AGENT; upstream update gates protect this build')

  const chooseLater = page.getByRole('button', { name: /^(I'll choose a provider later|稍后再选择提供方)$/ })
  await chooseLater.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {})
  if (await chooseLater.isVisible().catch(() => false)) {
    await chooseLater.click()
    await chooseLater.waitFor({ state: 'hidden', timeout: 15_000 })
    report.checks.push('Native choose-provider-later control exposes the real chat without an account')
  }
  await page.getByRole('button', { name: '额度总览', exact: true }).waitFor({ state: 'visible', timeout: 30_000 })
  report.checks.push('Fresh native preparation exposes the Chinese quota entry without manually enabling the core Desktop half')
  await page.screenshot({ path: join(output, '02-windows-native-chat.png') })
  report.screenshots.push('02-windows-native-chat.png')

  const openSettings = page.getByRole('button', { name: /^(Open settings|打开设置)$/ })
  await openSettings.waitFor({ state: 'visible', timeout: 15_000 })
  await openSettings.click({ timeout: 5000 })
  await page.getByRole('button', { name: /^(Close settings|关闭设置)$/ }).waitFor({ state: 'visible', timeout: 15000 })
  await page.getByRole('button', { name: /^(Appearance|外观)$/ }).click()
  await page.getByRole('button', { name: /^(Theme|主题)$/ }).click()
  await page.getByPlaceholder(/^(Search your themes or the VS Code Marketplace…|搜索本地主题或 VS Code 市场…)$/).waitFor({ state: 'visible', timeout: 15_000 })
  await page.getByText(/^(Light|明亮)$/).first().waitFor({ state: 'visible', timeout: 15_000 })
  await page.screenshot({ path: join(output, '03-windows-native-settings.png') })
  report.screenshots.push('03-windows-native-settings.png')
  report.checks.push('Native settings opened through its actual control')
  await page.getByRole('button', { name: /^(Close settings|关闭设置)$/ }).click()
  checkpoint('verifying_real_chinese_quota_dashboard')
  await page.getByRole('button', { name: /^新建会话/ }).first().waitFor({ state: 'visible', timeout: 15_000 })
  report.renderer_language = await page.evaluate(() => document.documentElement.lang)
  report.dashboard = await verifyDashboard(page, output)
  report.screenshots.push('03a-windows-native-quota-dashboard.png')
  report.checks.push('Chinese native shell and installed core Desktop quota overview show actual account records; real refresh keeps unavailable offline-provider balances unknown')
  captureAudit = await auditNativeCapture(application)

  if (process.platform === 'win32') {
    checkpoint('opening_actual_native_screen_dialog')
    report.capture_owner = await application.evaluate(({ BrowserWindow }, id) => {
      const owner = BrowserWindow.fromId(id)
      if (!owner || owner.isDestroyed()) throw new Error('The actual capture owner is unavailable')
      const state = () => ({ visible: owner.isVisible(), focused: owner.isFocused(), minimized: owner.isMinimized(), bounds: owner.getBounds() })
      const before = state()
      if (owner.isMinimized()) owner.restore()
      owner.hide()
      owner.show()
      owner.focus()
      return { before, after: state() }
    }, report.identity.owner_id)
    assert.equal(report.capture_owner.after.visible, true, 'Actual capture requires its user-visible owner')
    assert.equal(report.capture_owner.after.minimized, false, 'Actual capture owner must be restored')
    const electronPid = await application.evaluate(() => process.pid)
    report.native_displays = await application.evaluate(({ screen }) => screen.getAllDisplays().map(display => ({
      id: display.id, bounds: display.bounds, scaleFactor: display.scaleFactor,
    })))
    nativeDialogProcess = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', join(ownDirectory, 'native-dialog.ps1'), '-ApplicationPid', String(electronPid), '-DiagnosticsPath', join(output, 'native-dialog-report.json')], { stdio: ['ignore', 'pipe', 'pipe'] })
    let dialogMessages = ''
    const rememberDialog = data => { dialogMessages = (dialogMessages + data.toString()).slice(-5000) }
    nativeDialogProcess.stdout.on('data', rememberDialog)
    nativeDialogProcess.stderr.on('data', rememberDialog)
    const dialogDone = new Promise((resolveDialog, rejectDialog) => {
      nativeDialogProcess.once('exit', code => code === 0 ? resolveDialog() : rejectDialog(new Error('Actual native source dialog selection failed: ' + redact(dialogMessages))))
      nativeDialogProcess.once('error', rejectDialog)
    })
    dialogDone.catch(() => {})
    // Handle native consent as a real UI Automation click, not a stubbed API.
    const captureDone = page.evaluate(() => window.hermesDesktop.regionCapture.capture())
    // Mark the promise handled while the native dialog is still open.
    captureDone.catch(() => {})
    await bounded(Promise.race([
      dialogDone,
      captureDone.then(result => { throw new Error('Native screen acquisition ended before source consent: ' + result.status + ': ' + (result.message || '')) }),
    ]), 80_000, 'Actual native screen consent')
    checkpoint('dragging_actual_native_region_overlay')
    let viewport
    for (let tries = 0; tries < 100; tries++) {
      viewport = await application.evaluate(async ({ BrowserWindow }) => {
        const overlay = BrowserWindow.getAllWindows().find(candidate => candidate.webContents.getURL().startsWith('data:text/html'))
        if (!overlay?.isVisible()) return null
        return await overlay.webContents.executeJavaScript('document.querySelector("#screen")?.naturalWidth ? ({width:innerWidth,height:innerHeight,bitmapWidth:document.querySelector("#screen").naturalWidth,bitmapHeight:document.querySelector("#screen").naturalHeight}) : null')
      })
      if (viewport) break
      await new Promise(resolveWait => setTimeout(resolveWait, 100))
    }
    assert(viewport, 'Actual native region overlay did not open')
    const { width, height } = viewport
    const end = { x: Math.min(520, width - 20), y: Math.min(440, height - 20) }
    const overlayPng = await application.evaluate(async ({ BrowserWindow }, point) => {
      const overlay = BrowserWindow.getAllWindows().find(candidate => candidate.webContents.getURL().startsWith('data:text/html'))
      overlay.webContents.sendInputEvent({ type: 'mouseDown', x: 40, y: 120, button: 'left', clickCount: 1 })
      overlay.webContents.sendInputEvent({ type: 'mouseMove', ...point, button: 'left' })
      await new Promise(resolveWait => setTimeout(resolveWait, 100))
      return (await overlay.webContents.capturePage()).toPNG().toString('base64')
    }, end)
    writeFileSync(join(output, '04-windows-native-region-selection.png'), Buffer.from(overlayPng, 'base64'))
    report.screenshots.push('04-windows-native-region-selection.png')
    await application.evaluate(({ BrowserWindow }, point) => {
      const overlay = BrowserWindow.getAllWindows().find(candidate => candidate.webContents.getURL().startsWith('data:text/html'))
      overlay.webContents.sendInputEvent({ type: 'mouseUp', ...point, button: 'left', clickCount: 1 })
    }, end)
    const result = await bounded(captureDone, 100_000, 'Actual native region capture')
    assert.equal(result.status, 'ok')
    assert.equal(result.region_already_cropped, true)
    assert.equal(result.width, Math.ceil(end.x * viewport.bitmapWidth / width) - Math.floor(40 * viewport.bitmapWidth / width))
    assert.equal(result.height, Math.ceil(end.y * viewport.bitmapHeight / height) - Math.floor(120 * viewport.bitmapHeight / height))
    assert(result.width < viewport.bitmapWidth && result.height < viewport.bitmapHeight)
    assert(result.imageDataUrl.startsWith('data:image/png;base64,'))
    writeFileSync(join(output, '05-windows-native-cropped-region.png'), Buffer.from(result.imageDataUrl.split(',')[1], 'base64'))
    report.screenshots.push('05-windows-native-cropped-region.png')
    report.capture = { status: result.status, selectionMode: result.selectionMode, width: result.width, height: result.height, nativeDialogInvoked: true, wholeScreenReturned: false }
    report.checks.push('Actual Windows screen consent, native overlay drag and strict PNG crop passed')
  }
  checkpoint('verifying_actual_installed_compact_pointer')
  report.compact = await verifyCompact(application, page, output, report.identity.owner_id)
  report.screenshots.push('06-misaka-empty-compact.png', '07-misaka-compact-native-drag.png',
    '08-misaka-compact-cropped-region.png', '09-misaka-document-candidates.png',
    '10-misaka-review-and-image-consent.png', '11-misaka-restored-chat-draft.png')
  report.checks.push('Installed native compact pointer opens at the top right, hides its owner, captures a real region, refines candidates and hands off an unsent draft with one image; close and Escape restore the owner')
  report.result = 'passed'
} catch (error) {
  report.result = 'failed'
  report.error = redact(error.message)
  console.error(JSON.stringify({ phase: report.phase, error: report.error }))
  process.exitCode = 1
} finally {
  checkpoint('closing_actual_native_application')
  if (nativeDialogProcess && nativeDialogProcess.exitCode === null) nativeDialogProcess.kill()
  if (captureAudit) {
    report.native_capture_audit = await captureAudit.stop()
    if (!report.native_capture_audit.main_process_responsive && report.result === 'passed') {
      report.result = 'failed'
      report.error = 'The actual native main process did not respond after the acceptance flow'
      process.exitCode = 1
    }
  }
  if (application) {
    try {
      const page = application.windows()[0]
      if (report.result === 'failed' && page && !page.isClosed()) {
        await page.screenshot({ path: join(output, 'failure.png'), timeout: 5000 })
        report.screenshots.push('failure.png')
      }
    } catch {}
    try {
      await bounded(application.close(), 15_000, 'Actual Electron close')
    } catch (error) {
      application.process().kill('SIGKILL')
      report.teardown = 'forced_after_close_timeout'
      if (report.result === 'passed') {
        report.result = 'failed'
        report.error = redact(error.message)
        process.exitCode = 1
      }
    }
  }
  report.finished_at = new Date().toISOString()
  checkpoint('completed')
  console.log(JSON.stringify({ result: report.result, checks: report.checks, screenshots: report.screenshots }))
}
