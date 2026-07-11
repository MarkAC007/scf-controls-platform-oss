// Vite config for the live dev demo (scripts/dev-demo.sh).
// Port 7794 sits inside the host's allowed firewall range (7778-7799);
// /api proxies to the demo backend on 7795.
import { defineConfig, mergeConfig } from 'vite'
import baseConfig from './vite.config'

export default mergeConfig(
  baseConfig,
  defineConfig({
    server: {
      port: 7794,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:7795',
          changeOrigin: true,
        },
      },
    },
  })
)
