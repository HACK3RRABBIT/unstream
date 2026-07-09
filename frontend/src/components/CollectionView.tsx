import { useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Archive, Download, LoaderCircle, Music2 } from 'lucide-react'
import clsx from 'clsx'
import {
  apiError,
  getJob,
  jobZipUrl,
  startDownload,
  type Collection,
  type JobTrack,
} from '../lib/api'
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
  const [jobId, setJobId] = useState<string | null>(null)

  const start = useMutation({
    mutationFn: () => startDownload(url),
    onSuccess: setJobId,
  })

  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => getJob(jobId!),
    enabled: jobId !== null,
    refetchInterval: (query) => (query.state.data?.finished ? false : 800),
  })

  const jobTracks = useMemo(() => {
    const map = new Map<string, JobTrack>()
    for (const t of job.data?.tracks ?? []) map.set(t.id, t)
    return map
  }, [job.data])

  const totalMs = collection.tracks.reduce((sum, t) => sum + t.duration_ms, 0)
  const running = jobId !== null && !job.data?.finished

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

        <div className="min-w-0 flex-1">
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
            <span className="mx-1.5 text-ink-600">·</span>
            {formatTotal(totalMs)}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {job.data && job.data.done > 0 && (
            <a
              href={jobZipUrl(jobId!)}
              className="flex items-center gap-1.5 rounded-xl border border-ink-600 px-4 py-2.5 text-sm font-medium text-ink-100 transition hover:border-ink-400"
            >
              <Archive className="size-4" />
              ZIP ({job.data.done})
            </a>
          )}
          {(!jobId || running) && (
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
                  {job.data ? `${job.data.done}/${job.data.total}` : 'Starting…'}
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

      {start.isError && (
        <p role="alert" className="border-b border-ink-800 px-5 py-3 text-sm text-danger">
          {apiError(start.error)}
        </p>
      )}

      <ol>
        {collection.tracks.map((track, index) => (
          <TrackRow
            key={`${track.id}-${index}`}
            index={index + 1}
            track={track}
            jobId={jobId}
            state={jobTracks.get(track.id)}
          />
        ))}
      </ol>

      {job.data?.finished && (
        <p className="border-t border-ink-800 px-5 py-3.5 text-sm text-ink-300">
          Finished — {job.data.done} of {job.data.total} downloaded
          {job.data.failed > 0 && (
            <span className="text-danger"> · {job.data.failed} failed</span>
          )}
        </p>
      )}
    </section>
  )
}
