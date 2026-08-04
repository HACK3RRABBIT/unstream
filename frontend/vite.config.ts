import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type PluginOption } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const SW_TEMPLATE = fileURLToPath(new URL('./sw.template.js', import.meta.url))

/** Emits sw.js with a version stamped from the built index.html.
 *
 *  Any app change alters index.html (hashed asset names), which alters the
 *  stamp, which makes sw.js byte-different — the browser then installs the
 *  new worker on its next update check and, thanks to skipWaiting +
 *  clients.claim in the worker, the new release takes over immediately. */
function pwaServiceWorker(): PluginOption {
  let outDir = ''
  return {
    name: 'unstream-pwa-sw',
    apply: 'build',
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir)
    },
    closeBundle() {
      const html = readFileSync(resolve(outDir, 'index.html'))
      const version = createHash('sha256').update(html).digest('hex').slice(0, 16)
      const sw = readFileSync(SW_TEMPLATE, 'utf8').replaceAll(
        '__UNSTREAM_BUILD__',
        version,
      )
      writeFileSync(resolve(outDir, 'sw.js'), sw)
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), pwaServiceWorker()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  // `npm run preview` serves the production build — the only way to test
  // the service worker locally — so it needs the same API proxy.
  preview: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
