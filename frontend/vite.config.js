import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Dev server proxies /api -> backend at :8000, so the frontend can call
// relative URLs (e.g. fetch('/api/documents')) without CORS config and without
// hardcoding the backend origin. VITE_API_BASE_URL still exists for the rare
// case of pointing at a different backend (e.g. a deployed one).
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      // SSE (POST /api/chat) must NOT be buffered by the proxy, so we set
      // ws:false (not a websocket) and rely on Vite's streaming passthrough.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
