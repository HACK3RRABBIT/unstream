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
  getJobs,
  isQuality,
  resolveUrl,
  startDownload,
  type Collection,
  type Job,
  type Quality,
  type SearchResult,
  type Track,
} from './api'

/** All the dock needs of a track: the backend's job payload carries ids and
 *  status but no titles. Deliberately narrow — the whole entry is written to
 *  localStorage, and a 100-track playlist has no business storing cover URLs
 *  and release dates it never reads. */
export type DockTrack = Pick<Track, 'id' | 'title'>

/** One download job the user kicked off, tracked until they dismiss it.
 *  Jobs live here (not in a view) so navigating away never loses them, and
 *  they are mirrored to localStorage so a reload doesn't either. */
export interface DownloadEntry {
  jobId: string
  url: string
  name: string
  kind: Collection['kind']
  cover_url: string | null
  tracks: DockTrack[] // metadata snapshot for titles in the dock
  job: Job | null // latest polled backend state
  /** Seconds until the job finishes, estimated from recent poll deltas. */
  etaSeconds: number | null
  /** Quality this job was started at — changing the preference later must
   *  not relabel jobs that are already encoding at the old one. */
  quality: Quality
  /** When the job was queued, for expiring stored entries client-side. */
  startedAt: number
  /** Set when the backend no longer knows this job: its files were swept
   *  after the 24h TTL, or the process restarted. The entry keeps its last
   *  known state but its download links are dead. */
  expired?: boolean
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
/** Nothing more will ever arrive for this entry, so stop polling it. */
const isSettled = (e: DownloadEntry) => e.expired === true || isFinished(e)

const QUALITY_KEY = 'unstream:quality'
const JOBS_KEY = 'unstream:jobs'

/** Mirrors DOWNLOADS_TTL_HOURS in backend/app/jobs.py: past this the files
 *  are gone, so a stored entry is only ever going to resolve to "expired".
 *  Dropping it up front spares the user a dock full of dead rows. */
const JOBS_TTL_MS = 24 * 60 * 60 * 1000
/** Enough to cover a session's worth of jobs without filling the quota. */
const MAX_STORED_JOBS = 20

function storedQuality(): Quality {
  try {
    const saved = localStorage.getItem(QUALITY_KEY)
    return isQuality(saved) ? saved : DEFAULT_QUALITY
  } catch {
    return DEFAULT_QUALITY // private mode / storage disabled
  }
}

/** Jobs from a previous page load. Restored with their last polled state, so
 *  the dock renders fully before the first tick comes back — and so finished
 *  jobs aren't re-announced as if they had just completed. */
function storedEntries(): DownloadEntry[] {
  try {
    const raw = localStorage.getItem(JOBS_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const cutoff = Date.now() - JOBS_TTL_MS
    return (parsed as DownloadEntry[]).filter(
      (e) =>
        e && typeof e.jobId === 'string' && typeof e.startedAt === 'number' && e.startedAt > cutoff,
    )
  } catch {
    return [] // storage disabled, or a shape from an older release
  }
}

export function DownloadsProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<DownloadEntry[]>(storedEntries)
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
        tracks: (wanted
          ? collection.tracks.filter((t) => wanted.has(t.id))
          : collection.tracks
        ).map(({ id, title }) => ({ id, title })),
        job: null,
        etaSeconds: null,
        quality: chosen,
        startedAt: Date.now(),
      },
    ])
    setPanelOpen(true)
  }, [])

  const startFromResult = useCallback(
    async (result: SearchResult) => {
      const existing = entriesRef.current.filter((e) => e.url === result.url).at(-1)
      // Already running at the quality being asked for — just show it. At a
      // different quality it's a different file, so let it queue again.
      if (existing && !isSettled(existing) && existing.quality === qualityRef.current) {
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

  // One poller for every unsettled job, independent of what's on screen, and
  // one request per tick regardless of how many jobs are in flight.
  useEffect(() => {
    const tick = async () => {
      const pending = entriesRef.current.filter((e) => !isSettled(e))
      if (pending.length === 0) return

      let polled: Job[]
      try {
        polled = await getJobs(pending.map((e) => e.jobId))
      } catch {
        return // network blip or a 429 — the next tick tries again
      }
      const fresh = new Map(polled.map((job) => [job.id, job]))
      // Anything the server didn't answer for is gone: swept past its TTL, or
      // lost to a restart. Either way its files are unreachable, so the entry
      // is retired rather than polled forever.
      const missing = pending.filter((e) => !fresh.has(e.jobId)).map((e) => e.jobId)
      if (fresh.size === 0 && missing.length === 0) return

      const now = Date.now()
      setEntries((prev) =>
        prev.map((e) => {
          if (missing.includes(e.jobId)) {
            samplesRef.current.delete(e.jobId)
            return { ...e, expired: true, etaSeconds: null }
          }
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
    // Restored entries have a stale snapshot on screen — catch them up now
    // rather than after the first interval.
    void tick()
    const timer = setInterval(tick, 900)
    return () => clearInterval(timer)
  }, [])

  // Mirror the queue to localStorage so a reload — including the silent one
  // main.tsx performs on a hidden tab when a new release ships — doesn't
  // destroy tracking for jobs the backend is still working on.
  useEffect(() => {
    try {
      if (entries.length === 0) localStorage.removeItem(JOBS_KEY)
      else localStorage.setItem(JOBS_KEY, JSON.stringify(entries.slice(-MAX_STORED_JOBS)))
    } catch {
      // Quota or private mode. Losing persistence is survivable; the session
      // itself still tracks everything in memory.
    }
  }, [entries])

  const activeCount = useMemo(() => entries.filter((e) => !isSettled(e)).length, [entries])

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
