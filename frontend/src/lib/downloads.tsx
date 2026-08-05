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
  DEFAULT_QUALITY,
  getJob,
  isQuality,
  resolveUrl,
  startDownload,
  type Collection,
  type Job,
  type Quality,
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
  /** Seconds until the job finishes, estimated from recent poll deltas. */
  etaSeconds: number | null
  /** Quality this job was started at — changing the preference later must
   *  not relabel jobs that are already encoding at the old one. */
  quality: Quality
}

interface DownloadsContextValue {
  entries: DownloadEntry[]
  activeCount: number
  panelOpen: boolean
  setPanelOpen: (open: boolean) => void
  /** Audio quality every new job is started at; persisted across sessions. */
  quality: Quality
  setQuality: (quality: Quality) => void
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

const QUALITY_KEY = 'unstream:quality'

function storedQuality(): Quality {
  try {
    const saved = localStorage.getItem(QUALITY_KEY)
    return isQuality(saved) ? saved : DEFAULT_QUALITY
  } catch {
    return DEFAULT_QUALITY // private mode / storage disabled
  }
}

export function DownloadsProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<DownloadEntry[]>([])
  const [panelOpen, setPanelOpen] = useState(false)
  const [quality, setQualityState] = useState<Quality>(storedQuality)
  const entriesRef = useRef(entries)
  entriesRef.current = entries
  // Read through a ref so `start` stays referentially stable — every
  // download button downstream depends on it.
  const qualityRef = useRef(quality)
  qualityRef.current = quality
  // (time, settled-count) samples per job, for ETA estimation.
  const samplesRef = useRef<Map<string, { t: number; settled: number }[]>>(new Map())

  const setQuality = useCallback((next: Quality) => {
    setQualityState(next)
    try {
      localStorage.setItem(QUALITY_KEY, next)
    } catch {
      // Not persisting is survivable; the session still honours the choice.
    }
  }, [])

  const start = useCallback(async (url: string, collection: Collection, trackIds?: string[]) => {
    const chosen = qualityRef.current
    const jobId = await startDownload(url, trackIds, chosen)
    const wanted = trackIds ? new Set(trackIds) : null
    setEntries((prev) => [
      ...prev,
      {
        jobId,
        url,
        name: collection.name,
        kind: collection.kind,
        cover_url: collection.cover_url,
        tracks: wanted ? collection.tracks.filter((t) => wanted.has(t.id)) : collection.tracks,
        job: null,
        etaSeconds: null,
        quality: chosen,
      },
    ])
    setPanelOpen(true)
  }, [])

  const startFromResult = useCallback(
    async (result: SearchResult) => {
      const existing = entriesRef.current.filter((e) => e.url === result.url).at(-1)
      // Already running at the quality being asked for — just show it. At a
      // different quality it's a different file, so let it queue again.
      if (existing && !isFinished(existing) && existing.quality === qualityRef.current) {
        setPanelOpen(true)
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
        const now = Date.now()
        setEntries((prev) =>
          prev.map((e) => {
            const job = fresh.get(e.jobId)
            if (!job) return e
            // ETA: settle-rate over a ~45s sliding window of polls.
            const settled = job.done + job.failed
            const samples = samplesRef.current.get(e.jobId) ?? []
            samples.push({ t: now, settled })
            while (samples.length > 2 && now - samples[0].t > 45000) samples.shift()
            let etaSeconds: number | null = null
            if (job.finished) {
              samplesRef.current.delete(e.jobId)
            } else {
              samplesRef.current.set(e.jobId, samples)
              const first = samples[0]
              const dt = (now - first.t) / 1000
              const dSettled = settled - first.settled
              if (dt > 3 && dSettled > 0) {
                etaSeconds = Math.max(1, Math.round((job.total - settled) / (dSettled / dt)))
              }
            }
            return { ...e, job, etaSeconds }
          }),
        )
      }
    }
    const timer = setInterval(tick, 900)
    return () => clearInterval(timer)
  }, [])

  const activeCount = useMemo(() => entries.filter((e) => !isFinished(e)).length, [entries])

  const entriesForUrl = useCallback(
    (url: string) => entriesRef.current.filter((e) => e.url === url),
    // entries in deps so consumers re-render as polling updates arrive
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [entries],
  )

  const entryForUrl = useCallback((url: string) => entriesForUrl(url).at(-1), [entriesForUrl])

  const value = useMemo(
    () => ({
      entries,
      activeCount,
      panelOpen,
      setPanelOpen,
      quality,
      setQuality,
      start,
      startFromResult,
      dismiss,
      entryForUrl,
      entriesForUrl,
    }),
    [
      entries,
      activeCount,
      panelOpen,
      quality,
      setQuality,
      start,
      startFromResult,
      dismiss,
      entryForUrl,
      entriesForUrl,
    ],
  )

  return <DownloadsContext.Provider value={value}>{children}</DownloadsContext.Provider>
}

export function useDownloads(): DownloadsContextValue {
  const ctx = useContext(DownloadsContext)
  if (!ctx) throw new Error('useDownloads must be used inside <DownloadsProvider>')
  return ctx
}
