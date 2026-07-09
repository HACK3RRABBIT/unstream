import { Check, Download, TriangleAlert } from 'lucide-react'
import clsx from 'clsx'
import { trackFileUrl, type JobTrack, type Track } from '../lib/api'

interface Props {
  index: number
  track: Track
  jobId: string | null
  state?: JobTrack
  /** Queue just this track; button shown while the track has no job yet. */
  onDownload?: () => void
}

function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const STAGE_LABEL: Record<string, string> = {
  queued: 'Queued',
  searching: 'Searching…',
  downloading: 'Downloading',
  tagging: 'Tagging…',
  retrying: 'Retrying…',
}

export function TrackRow({ index, track, jobId, state, onDownload }: Props) {
  const status = state?.status
  const active =
    status === 'searching' ||
    status === 'downloading' ||
    status === 'tagging' ||
    status === 'retrying'

  return (
    <li className="group relative border-b border-ink-800 last:border-b-0">
      <div className="flex items-center gap-4 px-5 py-3 transition-colors group-hover:bg-ink-800/40">
        <span className="w-6 shrink-0 text-right font-display text-sm tabular-nums text-ink-600">
          {index}
        </span>

        {track.cover_url ? (
          <img
            src={track.cover_url}
            alt=""
            loading="lazy"
            className="size-10 shrink-0 rounded-md object-cover"
          />
        ) : (
          <div className="size-10 shrink-0 rounded-md bg-ink-800" />
        )}

        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-medium text-ink-100">
            {track.title}
          </p>
          <p className="truncate text-[13px] text-ink-400">
            {track.artists.join(', ')}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {status === 'error' ? (
            <span
              className="flex items-center gap-1.5 text-[13px] text-danger"
              title={state?.error ?? undefined}
            >
              <TriangleAlert className="size-3.5" />
              Failed
            </span>
          ) : status === 'done' && jobId ? (
            <a
              href={trackFileUrl(jobId, track.id)}
              className="flex items-center gap-1.5 rounded-lg border border-ink-600 px-2.5 py-1.5 text-[13px] font-medium text-lime-flash transition hover:border-lime-flash/50"
            >
              <Check className="size-3.5" />
              mp3
              <Download className="size-3.5" />
            </a>
          ) : active || status === 'queued' ? (
            <span className="text-[13px] text-ink-300 tabular-nums">
              {STAGE_LABEL[status!]}
              {status === 'downloading' &&
                ` ${Math.round((state?.progress ?? 0) * 100)}%`}
            </span>
          ) : (
            <>
              <span className="text-[13px] text-ink-400 tabular-nums">
                {formatDuration(track.duration_ms)}
              </span>
              {onDownload && (
                <button
                  onClick={onDownload}
                  title="Download this track"
                  aria-label={`Download ${track.title}`}
                  className="grid size-8 shrink-0 place-items-center rounded-lg border border-ink-700 text-ink-400 transition hover:border-lime-flash/50 hover:text-lime-flash focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lime-flash"
                >
                  <Download className="size-4" />
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {active && (
        <div className="absolute inset-x-0 bottom-0 h-px bg-ink-700">
          <div
            className={clsx(
              'h-full bg-lime-flash transition-[width] duration-300',
              status !== 'downloading' && 'animate-pulse',
            )}
            style={{
              width:
                status === 'downloading'
                  ? `${Math.max(3, (state?.progress ?? 0) * 100)}%`
                  : '100%',
            }}
          />
        </div>
      )}
    </li>
  )
}
