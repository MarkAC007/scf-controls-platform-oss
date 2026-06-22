import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import packageJson from './package.json'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
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
