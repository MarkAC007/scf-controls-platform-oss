/// <reference types="vite-plugin-pwa/client" />
import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './data/queryClient'
import App from './App'
import { checkFeatureFlagParity } from './data/featureFlags'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
)

// #787 ISC-80 — the webclient's compiled per-window-review flag and the
// backend's runtime one are set in different places and can disagree. Ask
// the backend what it believes and complain loudly if it differs. Fire and
// forget: this must never delay or block the first render.
void checkFeatureFlagParity()

if (import.meta.env.PROD) {
  import('virtual:pwa-register')
    .then(({ registerSW }) => {
      registerSW({ immediate: true })
    })
    .catch((error: unknown) => {
      console.error('Service worker registration failed', error)
    })
}
