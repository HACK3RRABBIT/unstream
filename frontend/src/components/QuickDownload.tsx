import { useState } from 'react'
import { ArrowDownToLine, Check, LoaderCircle, TriangleAlert } from 'lucide-react'
import { apiError, type SearchResult } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useMessages } from '../lib/i18n'
import { useToast } from '../lib/toast'

/** One-click "add to download list" button for a track row in any list. */
export function QuickDownload({ result }: { result: SearchResult }) {
  const { startFromResult, entryForUrl, quality } = useDownloads()
  const { push } = useToast()
  const m = useMessages()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const entry = entryForUrl(result.url)
  // Expired is neither running nor available — offer the download again.
  const live = entry && !entry.expired ? entry : null
  const queued = live != null && !live.job?.finished
  const done = live?.job?.finished ?? false

  const handleClick = async () => {
    if (pending) return
    setError(null)
    setPending(true)
    try {
      await startFromResult(result)
      push(m.quick.queued(result.name))
    } catch (err) {
      const message = apiError(err, m)
      setError(message)
      push(message, 'error')
    } finally {
      setPending(false)
    }
  }

  // `pointer-fine` on the dimming: a phone has no hover, so without it the
  // button would sit at 60% forever.
  return (
    <button
      onClick={handleClick}
      title={
        error ??
        (done
          ? m.quick.done
          : queued
            ? m.quick.running
            : quality === 'original'
              ? m.quick.original
              : m.quick.kbps(quality))
      }
      aria-label={m.quick.download(result.name)}
      className="tap-target grid size-8 shrink-0 place-items-center rounded-ctl border border-ink-700 text-ink-400 transition duration-200 pointer-fine:opacity-60 pointer-fine:group-hover:opacity-100 hover:border-lime-flash/50 hover:text-lime-flash active:scale-90"
    >
      {error ? (
        <TriangleAlert className="size-4 text-danger" />
      ) : pending || queued ? (
        <LoaderCircle className="size-4 animate-spin text-lime-flash" />
      ) : done ? (
        <Check className="size-4 text-lime-flash" />
      ) : (
        <ArrowDownToLine className="size-4" />
      )}
    </button>
  )
}
