import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API/WebSocket origin is configurable so the dev server works both locally and
// behind a preview/tunnel host, where the browser only sees this Vite origin.
const API_TARGET = process.env.VITE_API_PROXY || 'http://127.0.0.1:8000'
const WS_TARGET = process.env.VITE_WS_PROXY || API_TARGET.replace(/^http/, 'ws')

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Preview/tunnel hosts are arbitrary subdomains; don't let the host allowlist block them.
    allowedHosts: true,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
      '/ws': { target: WS_TARGET, ws: true, timeout: 3600000, proxyTimeout: 3600000 }
    }
  },
  preview: { host: '0.0.0.0', port: 4173, allowedHosts: true },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1600 }
})
