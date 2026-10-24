import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
// Test-only source checkout selector; app behavior never reads this variable.
const source = process.env.ORCHESTRATOR_HERMES_SOURCE
if (!source) throw new Error('Set ORCHESTRATOR_HERMES_SOURCE to the Hermes source checkout with installed desktop dependencies')
const resolve = createRequire(path.resolve(source, 'package.json')).resolve
const ownDirectory = path.dirname(fileURLToPath(import.meta.url))
export default {
  resolve: { alias: {
    '@hermes/plugin-sdk': path.resolve(source, 'apps/desktop/src/sdk/index.ts'),
    'react/jsx-runtime': resolve('react/jsx-runtime'),
    'react/jsx-dev-runtime': resolve('react/jsx-dev-runtime'),
    'react': resolve('react'),
    'vitest': path.join(path.dirname(resolve('vitest/package.json')), 'dist/index.js'),
    '@testing-library/react': resolve('@testing-library/react')
  } },
  test: { environment: 'jsdom', globals: true, env: { NODE_ENV: 'development' },
    include: [path.resolve(ownDirectory, '../../../extensions/**/desktop/src/*.test.tsx')] }
}
