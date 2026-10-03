import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'Fuel Tracker',
        short_name: 'Fuel Tracker',
        description: 'Log fuel-ups into LubeLogger',
        theme_color: '#1f6feb',
        background_color: '#ffffff',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: 'icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
      workbox: {
        // Only the app shell is cached; API data is always loaded live
        navigateFallbackDenylist: [/^\/api\//],
      },
    }),
  ],
  build: {
    // The backend serves the built UI from backend/static
    outDir: '../backend/static',
    emptyOutDir: true,
  },
  server: {
    // During development, forward API calls to the backend (uvicorn on port 8000)
    proxy: { '/api': 'http://localhost:8000' },
  },
})
