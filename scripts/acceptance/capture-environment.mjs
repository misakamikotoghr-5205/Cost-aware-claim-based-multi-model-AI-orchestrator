/** Diagnostic fixture for the real Electron/Windows screen API; not product UI. */
import { app, BrowserWindow, desktopCapturer, screen } from 'electron'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
const option = prefix => process.argv.find(value => value.startsWith(prefix))?.slice(prefix.length)
const destination = option('--capture-report=')
const thumbnail = Number(option('--capture-thumbnail='))
if (!destination || ![0, 1].includes(thumbnail)) throw new Error('Explicit report and thumbnail size are required')
mkdirSync(dirname(destination), { recursive: true })
const report = { diagnostic_fixture: true, product_ui: false, platform: process.platform,
  electron: process.versions.electron, thumbnail,
  gpu_disabled_flag: process.argv.includes('--disable-gpu'), started_at: new Date().toISOString() }
let timer
try {
  await app.whenReady()
  report.gpu_features = app.getGPUFeatureStatus()
  report.displays = screen.getAllDisplays().map(display => ({ bounds: display.bounds, scaleFactor: display.scaleFactor }))
  const fixture = new BrowserWindow({ width: 640, height: 400, title: 'Native capture diagnostic fixture',
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true } })
  await fixture.loadURL('data:text/html,<h1>Windows screen API diagnostic</h1><p>This is a test fixture, not the MISAKA client or Office.</p>')
  const started = Date.now()
  const sources = await Promise.race([
    desktopCapturer.getSources({ types: ['screen'], thumbnailSize: { width: thumbnail, height: thumbnail }, fetchWindowIcons: false }),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Real screen enumeration exceeded 15000ms')), 15000) }),
  ])
  report.elapsed_ms = Date.now() - started
  report.sources = sources.map(source => ({ id: source.id, name: source.name, display_id: source.display_id, thumbnail: source.thumbnail.getSize() }))
  report.result = sources.length ? 'available' : 'no_sources'
} catch (error) {
  report.result = 'unavailable'
  report.error = error.message
} finally {
  clearTimeout(timer)
  report.finished_at = new Date().toISOString()
  writeFileSync(destination, JSON.stringify(report, null, 2))
  app.exit(0)
}
