/** Compile against the pinned native host, keeping its singleton SDK/React. */
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { parseArgs } from 'node:util'

const { values } = parseArgs({ options: {
  upstream: { type: 'string' }, entry: { type: 'string' }, outfile: { type: 'string' }
}})
if (!values.upstream || !values.entry || !values.outfile) {
  throw new Error('--upstream, --entry and --outfile are required')
}
const requireUpstream = createRequire(resolve(values.upstream, 'package.json'))
const esbuild = requireUpstream('esbuild')
await esbuild.build({
  // esbuild emits source-path comments. Anchor them to the entry directory so
  // fresh staging directories produce the same independent installation ZIP.
  absWorkingDir: dirname(resolve(values.entry)),
  entryPoints: [resolve(values.entry)], outfile: resolve(values.outfile),
  bundle: true, format: 'esm', platform: 'browser', target: 'es2022',
  jsx: 'automatic', external: ['@hermes/plugin-sdk', 'react', 'react/jsx-runtime'],
  sourcemap: false, legalComments: 'eof', logLevel: 'warning'
})
