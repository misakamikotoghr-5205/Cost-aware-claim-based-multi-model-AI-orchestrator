/** Run the actual pinned Desktop source-backend resolver, not a Python stand-in. */
import { createRequire } from 'node:module'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { parseArgs } from 'node:util'

const { values } = parseArgs({ options: {
  upstream: { type: 'string' }, 'hermes-home': { type: 'string' }
}})
if (!values.upstream || !values['hermes-home']) throw new Error('--upstream and --hermes-home are required')
const upstream = resolve(values.upstream)
const home = resolve(values['hermes-home'])
const requireUpstream = createRequire(resolve(upstream, 'package.json'))
const esbuild = requireUpstream('esbuild')
const entry = [
  `export { resolveSourceInstallationBackend } from ${JSON.stringify(resolve(upstream, 'apps/desktop/electron/source-backend.ts'))};`,
  `export { resolveInstallationLauncher, launcherTargetsInstallation } from ${JSON.stringify(resolve(upstream, 'apps/desktop/electron/updater-process.ts'))};`
].join('\n')
const result = await esbuild.build({
  stdin: { contents: entry, loader: 'ts', resolveDir: upstream },
  bundle: true, write: false, platform: 'node', format: 'esm', target: 'node22',
  logLevel: 'warning'
})
const original = await import('data:text/javascript;base64,' + Buffer.from(result.outputFiles[0].text).toString('base64'))
const launcher = original.resolveInstallationLauncher(upstream, process.platform === 'win32', home)
if (!launcher || !original.launcherTargetsInstallation(launcher, upstream)) {
  // A failed real probe must remain a failure. Give source-install users a
  // bounded version diagnostic so platform launch failures are actionable.
  const names = process.platform === 'win32' ? ['hermes.exe', 'hermes.cmd'] : ['hermes']
  const candidates = names.map(name => resolve(upstream, '.hermes/bin', name))
  const candidate = launcher || candidates.find(path => existsSync(path))
  if (candidate) {
    const viaCmd = process.platform === 'win32' && /\.cmd$/i.test(candidate)
    if (!viaCmd || !/["%&|<>^\r\n]/.test(candidate)) {
      const started = Date.now()
      const probe = spawnSync(viaCmd ? (process.env.ComSpec || 'cmd.exe') : candidate,
        viaCmd ? ['/d', '/s', '/c', `""${candidate}" --version"`] : ['--version'], {
          cwd: upstream, env: { ...process.env, HERMES_INSTALL_ROOT: upstream },
          timeout: 60_000, windowsHide: true, windowsVerbatimArguments: viaCmd, encoding: 'utf8',
        })
      const redact = text => String(text || '')
        .replace(/([?&](?:token|ticket|key)=)[^&\s]+/gi, '$1<redacted>')
        .replace(/(Bearer\s+)[^\s]+/gi, '$1<redacted>').slice(0, 4000)
      console.error(JSON.stringify({ native_launcher_diagnostic: {
        candidate, expected_install_root: upstream, elapsed_ms: Date.now() - started,
        status: probe.status, signal: probe.signal, error_code: probe.error?.code,
        stdout: redact(probe.stdout), stderr: redact(probe.stderr),
      } }))
    }
  } else {
    console.error(JSON.stringify({ native_launcher_diagnostic: { candidate: null,
      expected_install_root: upstream, checked: candidates } }))
  }
  throw new Error('Pinned Hermes native launcher does not report this checkout')
}
const backend = await original.resolveSourceInstallationBackend(upstream, ['gateway', 'ensure', '--json'], {
  env: process.env, hermesHome: home
})
if (!backend) {
  throw new Error('Pinned Hermes native backend is not ready: its PM launcher/version probe did not resolve this checkout')
}
console.log(JSON.stringify({ ready: true, kind: backend.kind, root: backend.root, launcher }))
