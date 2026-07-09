import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import {
  getJob,
  resolveUrl,
  startDownload,
  type Collection,
  type Job,
  type SearchResult,
  type Track,
} from './api'

/** One download job the user kicked off, tracked for the whole session.
 *  Jobs live here (not in a view) so navigating away never loses them. */
export interface DownloadEntry {
  jobId: string
  url: string
  name: string
  kind: Collection['kind']
  cover_url: string | null
  tracks: Track[] // metadata snapshot for titles/covers in the dock
  job: Job | null // latest polled backend state
}

interface DownloadsContextValue {
  entries: DownloadEntry[]
  activeCount: number
  panelOpen: boolean
  setPanelOpen: (open: boolean) => void
  start: (url: string, collection: Collection, trackIds?: string[]) => Promise<void>
  /** One-click download of a single search result: resolve, then queue. */
  startFromResult: (result: SearchResult) => Promise<void>
  dismiss: (jobId: string) => void
  /** Latest entry started from this URL — lets CollectionView show inline progress. */
  entryForUrl: (url: string) => DownloadEntry | undefined
  /** Every entry started from this URL (per-track jobs share the collection URL). */
  entriesForUrl: (url: string) => DownloadEntry[]
}

const DownloadsContext = createContext<DownloadsContextValue | null>(null)

const isFinished = (e: DownloadEntry) => e.job?.finished ?? false

export function DownloadsProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<DownloadEntry[]>([])
  const [panelOpen, setPanelOpen] = useState(false)
  const entriesRef = useRef(entries)
  entriesRef.current = entries

  const start = useCallback(
    async (url: string, collection: Collection, trackIds?: string[]) => {
      const jobId = await startDownload(url, trackIds)
      const wanted = trackIds ? new Set(trackIds) : null
      setEntries((prev) => [
        ...prev,
        {
          jobId,
          url,
          name: collection.name,
          kind: collection.kind,
          cover_url: collection.cover_url,
          tracks: wanted
            ? collection.tracks.filter((t) => wanted.has(t.id))
            : collection.tracks,
          job: null,
        },
      ])
      setPanelOpen(true)
    },
    [],
  )

  const startFromResult = useCallback(
    async (result: SearchResult) => {
      const existing = entriesRef.current.filter((e) => e.url === result.url).at(-1)
      if (existing && !isFinished(existing)) {
        setPanelOpen(true) // already queued — just show it
        return
      }
      const collection = await resolveUrl(result.url)
      await start(result.url, collection)
    },
    [start],
  )

  const dismiss = useCallback((jobId: string) => {
    setEntries((prev) => prev.filter((e) => e.jobId !== jobId))
  }, [])

  // One poller for every unfinished job, independent of what's on screen.
  useEffect(() => {
    const tick = async () => {
      const pending = entriesRef.current.filter((e) => !isFinished(e))
      if (pending.length === 0) return
      const results = await Promise.allSettled(pending.map((e) => getJob(e.jobId)))
      const fresh = new Map<string, Job>()
      pending.forEach((e, i) => {
        const r = results[i]
        if (r.status === 'fulfilled') fresh.set(e.jobId, r.value)
      })
      if (fresh.size > 0) {
        setEntries((prev) =>
          prev.map((e) => (fresh.has(e.jobId) ? { ...e, job: fresh.get(e.jobId)! } : e)),
        )
      }
    }
    const timer = setInterval(tick, 900)
    return () => clearInterval(timer)
  }, [])

  const activeCount = useMemo(
    () => entries.filter((e) => !isFinished(e)).length,
    [entries],
  )

  const entriesForUrl = useCallback(
    (url: string) => entriesRef.current.filter((e) => e.url === url),
    // entries in deps so consumers re-render as polling updates arrive
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [entries],
  )

  const entryForUrl = useCallback(
    (url: string) => entriesForUrl(url).at(-1),
    [entriesForUrl],
  )

  const value = useMemo(
    () => ({
      entries,
      activeCount,
      panelOpen,
      setPanelOpen,
      start,
      startFromResult,
      dismiss,
      entryForUrl,
      entriesForUrl,
    }),
    [entries, activeCount, panelOpen, start, startFromResult, dismiss, entryForUrl, entriesForUrl],
  )

  return <DownloadsContext.Provider value={value}>{children}</DownloadsContext.Provider>
}

export function useDownloads(): DownloadsContextValue {
  const ctx = useContext(DownloadsContext)
  if (!ctx) throw new Error('useDownloads must be used inside <DownloadsProvider>')
  return ctx
}
