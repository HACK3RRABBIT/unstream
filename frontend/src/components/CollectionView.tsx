import { useMemo } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Archive, Download, LoaderCircle, Music2 } from 'lucide-react'
import clsx from 'clsx'
import { apiError, jobZipUrl, type Collection, type JobTrack } from '../lib/api'
import { useDownloads } from '../lib/downloads'
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
  const entries = downloads.entriesForUrl(url)

  const start = useMutation({
    mutationFn: () => downloads.start(url, collection),
  })

  const startTrack = useMutation({
    mutationFn: (track: Props['collection']['tracks'][number]) =>
      downloads.start(
        url,
        { ...collection, name: track.title, cover_url: track.cover_url ?? collection.cover_url },
        [track.id],
      ),
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
  const settled = entries.reduce(
    (n, e) => n + (e.job ? e.job.done + e.job.failed : 0),
    0,
  )
  const queuedTotal = entries.reduce(
    (n, e) => n + (e.job?.total ?? e.tracks.length),
    0,
  )
  const doneTotal = entries.reduce((n, e) => n + (e.job?.done ?? 0), 0)
  const failedTotal = entries.reduce((n, e) => n + (e.job?.failed ?? 0), 0)
  const allFinished = entries.length > 0 && !running
  const allTracksDone = collection.tracks.every(
    (t) => jobTracks.get(t.id)?.state.status === 'done',
  )
  // ZIP covers one job — offer it for the newest job that has files.
  const zipEntry = [...entries].reverse().find((e) => (e.job?.done ?? 0) > 0)

  return (
    <section className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-900">
      <div className="flex flex-wrap items-center gap-5 border-b border-ink-800 p-5 sm:p-6">
        {collection.cover_url ? (
          <img
            src={collection.cover_url}
            alt=""
            className="size-20 rounded-xl object-cover shadow-lg sm:size-24"
          />
        ) : (
          <div className="grid size-20 place-items-center rounded-xl bg-ink-800 sm:size-24">
            <Music2 className="size-8 text-ink-400" />
          </div>
        )}

        {/* basis keeps the title from being crushed on phones — the buttons
            wrap to their own row instead of truncating the name */}
        <div className="min-w-0 grow basis-40">
          <span className="text-[11px] font-semibold tracking-[0.14em] text-lime-flash uppercase">
            {KIND_LABEL[collection.kind]}
          </span>
          <h2 className="mt-1 truncate font-display text-2xl font-bold tracking-tight">
            {collection.name}
          </h2>
          <p className="mt-1 text-sm text-ink-300">
            {collection.owner}
            <span className="mx-1.5 text-ink-600">·</span>
            {collection.tracks.length}{' '}
            {collection.tracks.length === 1 ? 'track' : 'tracks'}
            {totalMs > 0 && (
              <>
                <span className="mx-1.5 text-ink-600">·</span>
                {formatTotal(totalMs)}
              </>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {zipEntry && (zipEntry.job?.total ?? zipEntry.tracks.length) > 1 && (
            <a
              href={jobZipUrl(zipEntry.jobId)}
              className="flex items-center gap-1.5 rounded-xl border border-ink-600 px-4 py-2.5 text-sm font-medium text-ink-100 transition hover:border-ink-400"
            >
              <Archive className="size-4" />
              ZIP ({zipEntry.job!.done})
            </a>
          )}
          {(running || !allTracksDone) && (
            <button
              onClick={() => start.mutate()}
              disabled={start.isPending || running}
              className={clsx(
                'flex items-center gap-1.5 rounded-xl bg-lime-flash px-4 py-2.5 text-sm font-medium text-lime-ink',
                'transition hover:bg-lime-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lime-flash',
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
          )}
        </div>
      </div>

      {(start.isError || startTrack.isError) && (
        <p role="alert" className="border-b border-ink-800 px-5 py-3 text-sm text-danger">
          {apiError(start.error ?? startTrack.error)}
        </p>
      )}

      <ol>
        {collection.tracks.map((track, index) => {
          const tj = jobTracks.get(track.id)
          const queuing =
            startTrack.isPending && startTrack.variables?.id === track.id
          return (
            <TrackRow
              key={`${track.id}-${index}`}
              index={index + 1}
              track={track}
              jobId={tj?.jobId ?? null}
              state={tj?.state}
              downloading={queuing}
              onDownload={tj ? undefined : () => startTrack.mutate(track)}
            />
          )
        })}
      </ol>

      {allFinished && (
        <p className="border-t border-ink-800 px-5 py-3.5 text-sm text-ink-300">
          Finished — {doneTotal} of {queuedTotal} downloaded
          {failedTotal > 0 && (
            <span className="text-danger"> · {failedTotal} failed</span>
          )}
        </p>
      )}
    </section>
  )
}
