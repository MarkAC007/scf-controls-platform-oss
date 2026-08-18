import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import packageJson from './package.json'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      // Registration is explicit in src/main.tsx; letting the plugin inject too would double-register.
      injectRegister: null,
      // Stated explicitly so the dev servers on :5173 and :7794 provably never serve a service worker.
      devOptions: {
        enabled: false,
      },
      // No includeAssets: globPatterns below already matches every public/ asset once it is
      // copied into the build output, so listing them here only duplicates precache entries.
      manifest: {
        name: 'SCF Controls Platform',
        short_name: 'SCF',
        description: 'GRC control management for the Secure Controls Framework',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        background_color: '#1a202c',
        theme_color: '#1a202c',
        icons: [
          { src: '/pwa-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/pwa-512-maskable.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        // json is deliberately excluded because public/data/ holds 23 MB of SCF catalog JSON
        // (control_guidance.json alone is 12 MB), so precaching it would produce an unusable service worker.
        globPatterns: ['**/*.{js,css,html,ico,png,svg,webp,woff,woff2}'],
        navigateFallback: '/index.html',
        // The service worker must never intercept API requests.
        navigateFallbackDenylist: [/^\/api\//],
        cleanupOutdatedCaches: true,
        // Largest emitted asset is currently ~1.06 MiB; Workbox's 2 MiB default would silently
        // drop a chunk from the precache (build warning only) if the bundle grew past it.
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
      },
    }),
  ],
  define: {
    // Expose package.json version to the app
    __APP_VERSION__: JSON.stringify(packageJson.version),
  },
  root: '.',
  server: {
    port: 5173,
    host: '0.0.0.0',
    strictPort: true,
    allowedHosts: ['localhost', 'cg-scf-frontend', 'host.docker.internal'],
    proxy: {
      // Proxy API requests to backend during development
      '/api': {
        target: 'http://backend:8000',
        changeOrigin: true,
      }
    }
  },
  preview: {
    port: 5173,
    host: '0.0.0.0',
    strictPort: true,
    // Host checking configuration for production deployment
    // In production behind a load balancer, we disable host checking entirely
    // because:
    // 1. The service is not directly exposed (ingress: internal-and-cloud-load-balancing)
    // 2. The load balancer handles SSL termination and routing
    // 3. Cloud Run health checks may use different host headers
    //
    // Setting allowedHosts to true disables host checking (allows all hosts)
    // This is safe because the service is only accessible via the load balancer
    allowedHosts: process.env.VITE_ALLOWED_HOSTS
      ? process.env.VITE_ALLOWED_HOSTS.split(',').map(h => h.trim()).filter(h => h.length > 0)
      : true, // Allow all hosts in production (safe behind load balancer)
  }
})
