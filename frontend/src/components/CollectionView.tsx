import { useMemo, useState, type CSSProperties } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Archive, Check, Download, Link2, LoaderCircle, Music2, X } from 'lucide-react'
import clsx from 'clsx'
import { apiError, jobZipUrl, type Collection, type JobTrack } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useToast } from '../lib/toast'
import { TrackRow } from './TrackRow'

interface Props {
  url: string
  collection: Collection
}

const KIND_LABEL = { track: 'Track', album: 'Album', playlist: 'Playlist' }

function formatTotal(ms: number): string {
  const minutes = Math.round(ms / 60000)
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} hr ${minutes % 60} min`
}

export function CollectionView({ url, collection }: Props) {
  // Downloads live in the global store, so they keep running (and stay
  // visible in the dock) when the user navigates to another search.
  // A collection can spawn several jobs for the same URL — one "Download
  // all" plus any number of single-track ones — so merge them all here.
  const downloads = useDownloads()
  const { push } = useToast()
  const entries = downloads.entriesForUrl(url)

  // Tracks the user ticked to download as one batch.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [copied, setCopied] = useState(false)

  const copyLink = async () => {
    const share = `${window.location.origin}/?url=${encodeURIComponent(url)}`
    try {
      await navigator.clipboard.writeText(share)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
      push('Shareable link copied', 'success')
    } catch {
      push('Could not copy the link', 'error')
    }
  }

  const toggleSelect = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const selectAll = () => setSelected(new Set(collection.tracks.map((t) => t.id)))
  const clearSelection = () => setSelected(new Set())

  const trackWord = (n: number) => `${n} ${n === 1 ? 'track' : 'tracks'}`

  const start = useMutation({
    mutationFn: () => downloads.start(url, collection),
    onSuccess: () => push(`Queued ${trackWord(collection.tracks.length)} from ${collection.name}`),
    onError: (err) => push(apiError(err), 'error'),
  })

  const startSelected = useMutation({
    mutationFn: (ids: string[]) => downloads.start(url, collection, ids),
    onSuccess: (_data, ids) => {
      clearSelection()
      push(`Queued ${trackWord(ids.length)}`)
    },
    onError: (err) => push(apiError(err), 'error'),
  })

  const startTrack = useMutation({
    mutationFn: (track: Props['collection']['tracks'][number]) =>
      downloads.start(
        url,
        { ...collection, name: track.title, cover_url: track.cover_url ?? collection.cover_url },
        [track.id],
      ),
    onSuccess: (_data, track) => push(`Queued “${track.title}”`),
    onError: (err) => push(apiError(err), 'error'),
  })

  // Latest job state per track id, plus which job it belongs to (for the
  // per-track mp3 link). Later entries win.
  const jobTracks = useMemo(() => {
    const map = new Map<string, { jobId: string; state: JobTrack }>()
    for (const entry of entries) {
      for (const t of entry.job?.tracks ?? []) {
        map.set(t.id, { jobId: entry.jobId, state: t })
      }
    }
    return map
  }, [entries])

  const totalMs = collection.tracks.reduce((sum, t) => sum + t.duration_ms, 0)
  const running = entries.some((e) => !e.job?.finished)
  const settled = entries.reduce((n, e) => n + (e.job ? e.job.done + e.job.failed : 0), 0)
  const queuedTotal = entries.reduce((n, e) => n + (e.job?.total ?? e.tracks.length), 0)
  const doneTotal = entries.reduce((n, e) => n + (e.job?.done ?? 0), 0)
  const failedTotal = entries.reduce((n, e) => n + (e.job?.failed ?? 0), 0)
  const allFinished = entries.length > 0 && !running
  const allTracksDone = collection.tracks.every((t) => jobTracks.get(t.id)?.state.status === 'done')
  // ZIP covers one job — offer it for the newest job that has files.
  const zipEntry = [...entries].reverse().find((e) => (e.job?.done ?? 0) > 0)

  return (
    <section className="overflow-hidden rounded-panel border border-ink-700 bg-ink-900">
      <div className="flex flex-wrap items-center gap-5 border-b border-ink-800 p-5 sm:p-6">
        {collection.cover_url ? (
          <img
            src={collection.cover_url}
            alt=""
            className="size-20 rounded-btn object-cover ring-1 ring-ink-700 sm:size-24"
          />
        ) : (
          <div className="grid size-20 place-items-center rounded-btn bg-ink-800 ring-1 ring-ink-700 sm:size-24">
            <Music2 className="size-8 text-ink-400" />
          </div>
        )}

        {/* basis keeps the title from being crushed on phones — the buttons
            wrap to their own row instead of truncating the name */}
        <div className="min-w-0 grow basis-40">
          <span className="text-micro font-semibold tracking-[0.14em] text-lime-flash uppercase">
            {KIND_LABEL[collection.kind]}
          </span>
          <h2 className="mt-1 truncate font-display text-2xl font-bold tracking-[-0.02em]">
            {collection.name}
          </h2>
          <p className="mt-1 text-mini text-ink-300">
            {collection.owner}
            <span className="mx-1.5 text-ink-600">·</span>
            {collection.tracks.length} {collection.tracks.length === 1 ? 'track' : 'tracks'}
            {totalMs > 0 && (
              <>
                <span className="mx-1.5 text-ink-600">·</span>
                {formatTotal(totalMs)}
              </>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={copyLink}
            title="Copy a shareable link to this page"
            aria-label="Copy shareable link"
            className="grid size-10 place-items-center rounded-btn border border-ink-600 text-ink-300 transition duration-200 hover:border-ink-400 hover:text-ink-100 active:scale-95"
          >
            {copied ? (
              <Check className="size-4 animate-pop text-lime-flash" />
            ) : (
              <Link2 className="size-4" />
            )}
          </button>
          {zipEntry && (zipEntry.job?.total ?? zipEntry.tracks.length) > 1 && (
            <a
              href={jobZipUrl(zipEntry.jobId)}
              download
              className="flex animate-pop items-center gap-1.5 rounded-btn border border-ink-600 px-4 py-2.5 text-mini font-medium text-ink-100 transition duration-200 hover:border-ink-400 active:scale-[0.98]"
            >
              <Archive className="size-4" />
              ZIP ({zipEntry.job!.done})
            </a>
          )}
          {selected.size > 0 ? (
            <>
              <button
                onClick={clearSelection}
                title="Clear selection"
                aria-label="Clear selection"
                className="grid size-10 place-items-center rounded-btn border border-ink-600 text-ink-300 transition duration-200 hover:border-ink-400 hover:text-ink-100 active:scale-95"
              >
                <X className="size-4" />
              </button>
              <button
                onClick={() => startSelected.mutate([...selected])}
                disabled={startSelected.isPending}
                className={clsx(
                  'flex animate-pop items-center gap-1.5 rounded-btn bg-lime-flash px-4 py-2.5 text-mini font-medium text-lime-ink',
                  'transition duration-200 hover:bg-lime-soft active:scale-[0.98]',
                  'disabled:cursor-not-allowed disabled:opacity-50',
                )}
              >
                {startSelected.isPending ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
                Download {selected.size} selected
              </button>
            </>
          ) : (
            (running || !allTracksDone) && (
              <button
                onClick={() => start.mutate()}
                disabled={start.isPending || running}
                className={clsx(
                  'flex items-center gap-1.5 rounded-btn bg-lime-flash px-4 py-2.5 text-mini font-medium text-lime-ink',
                  'transition duration-200 hover:bg-lime-soft active:scale-[0.98]',
                  'disabled:cursor-not-allowed disabled:opacity-50',
                )}
              >
                {running || start.isPending ? (
                  <>
                    <LoaderCircle className="size-4 animate-spin" />
                    {entries.length > 0 ? `${settled}/${queuedTotal}` : 'Starting…'}
                  </>
                ) : (
                  <>
                    <Download className="size-4" />
                    Download all
                  </>
                )}
              </button>
            )
          )}
        </div>
      </div>

      {(start.isError || startTrack.isError || startSelected.isError) && (
        <p
          role="alert"
          className="animate-fade-up border-b border-ink-800 bg-danger/10 px-5 py-3 text-mini text-danger"
        >
          {apiError(start.error ?? startTrack.error ?? startSelected.error)}
        </p>
      )}

      {collection.tracks.length > 1 && (
        <div className="flex items-center justify-between border-b border-ink-800 bg-ink-950/50 px-5 py-2">
          <span className="text-xs text-ink-400 tabular-nums">
            {selected.size > 0
              ? `${selected.size} of ${collection.tracks.length} selected`
              : `${collection.tracks.length} tracks — tick any to download just those`}
          </span>
          <button
            onClick={selected.size === collection.tracks.length ? clearSelection : selectAll}
            className="text-xs font-medium text-lime-flash transition hover:text-lime-soft"
          >
            {selected.size === collection.tracks.length ? 'Clear all' : 'Select all'}
          </button>
        </div>
      )}

      <ol className="stagger">
        {collection.tracks.map((track, index) => {
          const tj = jobTracks.get(track.id)
          const queuing = startTrack.isPending && startTrack.variables?.id === track.id
          return (
            <TrackRow
              key={`${track.id}-${index}`}
              style={{ '--i': index } as CSSProperties}
              index={index + 1}
              track={track}
              jobId={tj?.jobId ?? null}
              state={tj?.state}
              downloading={queuing}
              onDownload={tj ? undefined : () => startTrack.mutate(track)}
              selected={selected.has(track.id)}
              onToggleSelect={
                collection.tracks.length > 1 ? () => toggleSelect(track.id) : undefined
              }
            />
          )
        })}
      </ol>

      {allFinished && (
        <p className="flex animate-fade-up items-center gap-2 border-t border-ink-800 px-5 py-3.5 text-mini text-ink-300">
          <Check className="size-4 shrink-0 text-lime-flash" />
          Finished — {doneTotal} of {queuedTotal} downloaded
          {failedTotal > 0 && <span className="text-danger">· {failedTotal} failed</span>}
        </p>
      )}
    </section>
  )
}
