import { useState } from 'react'
import { ArrowDownToLine, Check, LoaderCircle, TriangleAlert } from 'lucide-react'
import { apiError, type SearchResult } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useToast } from '../lib/toast'

/** One-click "add to download list" button for a track row in any list. */
export function QuickDownload({ result }: { result: SearchResult }) {
  const { startFromResult, entryForUrl } = useDownloads()
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
      push(`Queued “${result.name}”`)
    } catch (err) {
      const message = apiError(err)
      setError(message)
      push(message, 'error')
    } finally {
      setPending(false)
    }
  }

  return (
    <button
      onClick={handleClick}
      title={
        error ??
        (done ? 'Downloaded — in your list' : queued ? 'Downloading…' : 'Download mp3')
      }
      aria-label={`Download ${result.name}`}
      className="grid size-8 shrink-0 place-items-center rounded-ctl border border-ink-700 text-ink-400 opacity-60 transition duration-200 group-hover:opacity-100 hover:border-lime-flash/50 hover:text-lime-flash active:scale-90"
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
