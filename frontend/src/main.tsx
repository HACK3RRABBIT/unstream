import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)

// PWA: register the service worker (production only — in dev it would get
// in the way of HMR) and listen for release updates.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  // True when this page loaded under an existing worker — i.e. a worker
  // activating mid-session is a genuine new release, not the first install.
  const hadController = !!navigator.serviceWorker.controller

  navigator.serviceWorker.addEventListener('message', (event) => {
    if (event.data?.type !== 'unstream:update' || !hadController) return
    // Hidden tabs can reload invisibly; visible ones ask the user first
    // (a reload would clear in-memory download tracking).
    if (document.visibilityState === 'hidden') {
      window.location.reload()
    } else {
      window.dispatchEvent(new CustomEvent('unstream:update'))
    }
  })

  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
