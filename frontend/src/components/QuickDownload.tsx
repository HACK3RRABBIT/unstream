import { useState } from 'react'
import { ArrowDownToLine, Check, LoaderCircle, TriangleAlert } from 'lucide-react'
import { apiError, type SearchResult } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useToast } from '../lib/toast'

/** One-click "add to download list" button for a track row in any list. */
export function QuickDownload({ result }: { result: SearchResult }) {
  const { startFromResult, entryForUrl, quality } = useDownloads()
  const { push } = useToast()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const entry = entryForUrl(result.url)
  const queued = entry != null && !entry.job?.finished
  const done = entry?.job?.finished ?? false

  const handleClick = async () => {
    if (pending) return
    setError(null)
    setPending(true)
    try {
      await startFromResult(result)
      push(`«${result.name}» رفت تو صف`)
    } catch (err) {
      const message = apiError(err)
      setError(message)
      push(message, 'error')
    } finally {
      setPending(false)
    }
  }

  // Dimming until hover is a mouse idiom: it keeps a dense list quiet and lets
  // the row's action light up under the cursor. A phone has no hover, so the
  // button would sit dimmed forever — `pointer-fine` keeps the effect where it
  // works and leaves the button at full strength everywhere else.
  return (
    <button
      onClick={handleClick}
      title={
        error ??
        (done
          ? 'دانلود شده — تو لیستته'
          : queued
            ? 'در حال دانلود…'
            : quality === 'original'
              ? 'دانلود بدون انکود دوباره'
              : `دانلود با ${quality} kbps`)
      }
      aria-label={`دانلود ${result.name}`}
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
