/** Actual pinned Electron application; no mock backend, UI, inference or auth. */
import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'

const { values } = parseArgs({ options: {
  upstream: { type: 'string' }, 'hermes-home': { type: 'string' }, output: { type: 'string' },
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
const ownDirectory = dirname(fileURLToPath(import.meta.url))
const userData = join(profile, 'acceptance-electron-user-data')
mkdirSync(userData, { recursive: true })
writeFileSync(join(userData, 'window-state.json'), JSON.stringify({ x: 0, y: 0, width: 1280, height: 900, isMaximized: false }))
writeFileSync(join(userData, 'zoom-state.json'), JSON.stringify({ zoomLevel: 0 }))
const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
  !/(?:_API_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS|_PRIVATE_KEY)$/.test(name) &&
  !name.startsWith('HERMES_') && !['PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'ELECTRON_RUN_AS_NODE'].includes(name)))
Object.assign(env, {
  HERMES_HOME: profile, HERMES_RUNTIME_DIR: join(profile, 'tools'),
  HERMES_DESKTOP_USER_DATA_DIR: userData,
  HERMES_DESKTOP_HERMES_ROOT: upstream,
  HERMES_DESKTOP_APP_NAME: `HermesAcceptance-${Date.now()}`,
  HERMES_DESKTOP_IGNORE_EXISTING: '1', HERMES_DESKTOP_ISOLATED_BACKEND: '1',
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
const redact = value => String(value).replace(/([?&](?:token|ticket|key)=)[^&\s]+/gi, '$1<redacted>').slice(0, 2000)
try {
  application = await _electron.launch({ executablePath, args: [desktopRoot, '--disable-gpu', '--no-sandbox'], env, cwd: desktopRoot, timeout: 120_000 })
  const page = await application.firstWindow({ timeout: 90_000 })
  await page.waitForFunction(() => document.getElementById('root')?.textContent?.length > 100, null, { timeout: 120_000 })
  // Wait for the actual backend boot to settle; no fake boot or fixture server.
  await page.waitForFunction(() => {
    const text = document.body.textContent || ''
    return /provider|API key|Sign in|ready|Ready/.test(text) && !/Starting Hermes/.test(text)
  }, null, { timeout: 180_000 })
  await page.screenshot({ path: join(output, '01-windows-native-first-run.png') })
  report.screenshots.push('01-windows-native-first-run.png')
  report.checks.push('Real native Electron renderer booted with isolated no-account profile')
  assert(await page.evaluate(() => typeof window.hermesDesktop?.regionCapture?.capture === 'function'))
  report.checks.push('Patched native region-capture capability exposed by actual preload')

  const openSettings = page.getByRole('button', { name: 'Open settings', exact: true })
  if (await openSettings.isVisible().catch(() => false)) {
    try {
      await openSettings.click({ timeout: 5000 })
      await page.getByRole('button', { name: 'Close settings', exact: true }).waitFor({ state: 'visible', timeout: 15000 })
      await page.screenshot({ path: join(output, '02-windows-native-settings.png') })
      report.screenshots.push('02-windows-native-settings.png')
      report.checks.push('Native settings opened through its actual control')
      await page.getByRole('button', { name: 'Close settings', exact: true }).click()
    } catch {
      report.settings = 'blocked_by_first_run_onboarding'
    }
  }

  if (process.platform === 'win32') {
    const electronPid = await application.evaluate(() => process.pid)
    const dialogProcess = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', join(ownDirectory, 'native-dialog.ps1'), '-ApplicationPid', String(electronPid)], { stdio: 'ignore' })
    const dialogDone = new Promise((resolveDialog, rejectDialog) => {
      dialogProcess.once('exit', code => code === 0 ? resolveDialog() : rejectDialog(new Error('Actual native source dialog selection failed')))
      dialogProcess.once('error', rejectDialog)
    })
    // Handle native consent as a real UI Automation click, not a stubbed API.
    const captureDone = page.evaluate(() => window.hermesDesktop.regionCapture.capture())
    // Mark the promise handled while the native dialog is still open.
    captureDone.catch(() => {})
    await dialogDone
    let viewport
    for (let tries = 0; tries < 100; tries++) {
      viewport = await application.evaluate(async ({ BrowserWindow }) => {
        const overlay = BrowserWindow.getAllWindows().find(candidate => candidate.webContents.getURL().startsWith('data:text/html'))
        if (!overlay?.isVisible()) return null
        return await overlay.webContents.executeJavaScript('document.querySelector("#screen")?.naturalWidth ? ({width:innerWidth,height:innerHeight}) : null')
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
    writeFileSync(join(output, '03-windows-native-region-selection.png'), Buffer.from(overlayPng, 'base64'))
    report.screenshots.push('03-windows-native-region-selection.png')
    await application.evaluate(({ BrowserWindow }, point) => {
      const overlay = BrowserWindow.getAllWindows().find(candidate => candidate.webContents.getURL().startsWith('data:text/html'))
      overlay.webContents.sendInputEvent({ type: 'mouseUp', ...point, button: 'left', clickCount: 1 })
    }, end)
    const result = await captureDone
    assert.equal(result.status, 'ok')
    assert.equal(result.region_already_cropped, true)
    assert(result.width < width && result.height < height)
    assert(result.imageDataUrl.startsWith('data:image/png;base64,'))
    writeFileSync(join(output, '04-windows-native-cropped-region.png'), Buffer.from(result.imageDataUrl.split(',')[1], 'base64'))
    report.screenshots.push('04-windows-native-cropped-region.png')
    report.capture = { status: result.status, selectionMode: result.selectionMode, width: result.width, height: result.height, nativeDialogInvoked: true, wholeScreenReturned: false }
    report.checks.push('Actual Windows screen consent, native overlay drag and strict PNG crop passed')
  }
  report.result = 'passed'
} catch (error) {
  report.result = 'failed'
  report.error = redact(error.message)
  process.exitCode = 1
} finally {
  if (application) {
    try {
      const page = application.windows()[0]
      if (report.result === 'failed' && page && !page.isClosed()) {
        await page.screenshot({ path: join(output, 'failure.png'), timeout: 5000 })
        report.screenshots.push('failure.png')
      }
    } catch {}
    await application.close().catch(() => {})
  }
  report.finished_at = new Date().toISOString()
  writeFileSync(join(output, 'native-desktop-report.json'), JSON.stringify(report, null, 2))
  console.log(JSON.stringify({ result: report.result, checks: report.checks, screenshots: report.screenshots }))
}
