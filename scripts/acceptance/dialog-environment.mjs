/** Real Windows native-dialog diagnostic fixture, never product acceptance. */
import { app, BrowserWindow, dialog } from 'electron'
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
const option = prefix => process.argv.find(value => value.startsWith(prefix))?.slice(prefix.length)
const destination = option('--dialog-report=')
if (!destination) throw new Error('Explicit dialog diagnostic destination required')
const withSignal = process.argv.includes('--dialog-with-signal')
const ownDirectory = dirname(fileURLToPath(import.meta.url))
mkdirSync(dirname(destination), { recursive: true })
const report = { diagnostic_fixture: true, product_ui: false, platform: process.platform,
  electron: process.versions.electron, with_parent: true, with_signal: withSignal,
  spawn_windows_hide: option('--dialog-windows-hide=') === 'true', started_at: new Date().toISOString() }
async function main() {
  let helper, timer
  try {
    await app.whenReady()
    const fixture = new BrowserWindow({ width: 640, height: 400, title: 'Native dialog diagnostic fixture',
      webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } })
    await fixture.loadURL('data:text/html,<h1>Windows native dialog diagnostic</h1><p>TEST FIXTURE, not MISAKA or Office.</p>')
    await new Promise(resolve => setTimeout(resolve, 500))
    report.owner = { visible: fixture.isVisible(), focused: fixture.isFocused(), minimized: fixture.isMinimized(), bounds: fixture.getBounds() }
    report.phase = 'calling_real_native_dialog'
    writeFileSync(destination, JSON.stringify(report, null, 2))
    helper = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
      join(ownDirectory, 'native-dialog.ps1'), '-ApplicationPid', String(process.pid),
      '-DiagnosticsPath', destination.replace(/\.json$/, '-controls.json'), '-DeadlineSeconds', '8'], { stdio: 'ignore', windowsHide: true })
    const controls = new Promise((resolve, reject) => {
      helper.once('exit', code => code === 0 ? resolve() : reject(new Error('Native diagnostic controls were not found')))
      helper.once('error', reject)
    })
    controls.catch(() => {})
    const controller = new AbortController()
    const started = Date.now()
    const choice = dialog.showMessageBox(fixture, { type: 'question', title: 'Select a screen',
      message: 'Choose a screen to capture for local region selection.',
      detail: 'The image stays in this client. Selecting a screen does not send a prompt or run an action.',
      buttons: ['Cancel', 'Entire screen'], defaultId: 0, cancelId: 0, noLink: true,
      ...(withSignal ? { signal: controller.signal } : {}) })
    choice.catch(() => {})
    report.dialog = await Promise.race([choice, controls.then(() => choice),
      new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('Real native diagnostic dialog exceeded 12000ms')) }, 12_000) })])
    report.elapsed_ms = Date.now() - started
    await controls
    report.result = report.dialog.response === 1 ? 'actual_source_button_invoked' : 'dialog_cancelled'
  } catch (error) {
    report.result = 'unavailable'
    report.error = error.message
  } finally {
    clearTimeout(timer)
    if (helper && helper.exitCode === null) helper.kill()
    report.finished_at = new Date().toISOString()
    writeFileSync(destination, JSON.stringify(report, null, 2))
    app.exit(0)
  }
}
void main()
