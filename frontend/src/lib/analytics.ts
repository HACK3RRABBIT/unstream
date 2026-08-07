/** The handful of things the server can't see for itself.
 *
 *  Almost everything the dashboard shows is recorded on the backend inside
 *  the endpoints that already run — searches, resolves, downloads, per-track
 *  outcomes. That leaves page views and a couple of browser-only moments,
 *  which is all this file sends. There is no third-party script, no cookie
 *  and no id that survives the day; the server derives a daily-rotating
 *  pseudonym from the request itself.
 *
 *  Every call is fire-and-forget: `sendBeacon` hands the request to the
 *  browser, which delivers it even if the page is closing, and nothing here
 *  ever waits on or reacts to the response. */

const ENDPOINT = '/api/collect'

/** Installed PWA vs browser tab — worth knowing separately once the app is
 *  installable, since installs are the users who come back. */
function isStandalone(): boolean {
  return (
    window.matchMedia?.('(display-mode: standalone)').matches ||
    // iOS Safari predates display-mode and still only has this.
    (navigator as { standalone?: boolean }).standalone === true
  )
}

function device(): 'mobile' | 'tablet' | 'desktop' {
  const width = Math.min(window.screen.width, window.screen.height)
  if (width < 600) return 'mobile'
  return width < 900 ? 'tablet' : 'desktop'
}

function send(name: string, extra: Record<string, unknown> = {}): void {
  const body = JSON.stringify({ name, standalone: isStandalone(), device: device(), ...extra })
  try {
    const blob = new Blob([body], { type: 'application/json' })
    if (navigator.sendBeacon?.(ENDPOINT, blob)) return
    // sendBeacon refuses when its queue is full, and isn't everywhere.
    void fetch(ENDPOINT, {
      method: 'POST',
      body,
      headers: { 'content-type': 'application/json' },
      keepalive: true,
    }).catch(() => {})
  } catch {
    // Analytics is never a reason for anything on the page to break.
  }
}

export function trackPageView(): void {
  send('page_view', { path: window.location.pathname, referrer: document.referrer })
}

/** The OS share sheet, and whether it was gone through with. Saving via the
 *  download link needs nothing here — the server sees that file leave. */
export function trackShare(outcome: 'shared' | 'cancelled' | 'unsupported'): void {
  send('share', { detail: outcome })
}

/** Listens for the PWA being installed, and for the browser offering to.
 *  The gap between the two is the interesting number. */
export function watchInstall(): void {
  window.addEventListener('appinstalled', () => send('pwa_install'), { once: true })
  window.addEventListener('beforeinstallprompt', () => send('install_prompt'), { once: true })
}
