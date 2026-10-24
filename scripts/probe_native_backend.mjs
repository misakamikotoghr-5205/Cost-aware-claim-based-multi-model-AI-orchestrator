/** Run the actual pinned Desktop source-backend resolver, not a Python stand-in. */
import { createRequire } from 'node:module'
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
  throw new Error('Pinned Hermes native launcher does not report this checkout')
}
const backend = await original.resolveSourceInstallationBackend(upstream, ['gateway', 'ensure', '--json'], {
  env: process.env, hermesHome: home
})
if (!backend) {
  throw new Error('Pinned Hermes native backend is not ready: its PM launcher/version probe did not resolve this checkout')
}
console.log(JSON.stringify({ ready: true, kind: backend.kind, root: backend.root, launcher }))
