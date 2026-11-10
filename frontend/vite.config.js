import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true, timeout: 3600000, proxyTimeout: 3600000 }
    }
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1600 }
})
