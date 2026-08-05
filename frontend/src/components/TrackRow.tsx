import type { CSSProperties } from 'react'
import { Check, Download, LoaderCircle, Pause, Play, TriangleAlert } from 'lucide-react'
import clsx from 'clsx'
import { trackFileUrl, type JobTrack, type Track } from '../lib/api'
import { togglePreview, usePlayingPreviewId, usePreviewLoading } from '../lib/preview'

interface Props {
  index: number
  track: Track
  jobId: string | null
  state?: JobTrack
  /** Queue just this track; button shown while the track has no job yet. */
  onDownload?: () => void
  /** True while the queue request for this row is in flight. */
  downloading?: boolean
  /** Multi-select: render a checkbox when a toggle handler is provided. */
  selected?: boolean
  onToggleSelect?: () => void
  /** Carries the `--i` stagger index from the parent list. */
  style?: CSSProperties
}

function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const STAGE_LABEL: Record<string, string> = {
  queued: 'تو صف',
  searching: 'در حال جستجو…',
  downloading: 'در حال دانلود',
  tagging: 'در حال تگ زدن…',
  retrying: 'تلاش دوباره…',
}

/** Three bouncing bars shown in place of the row number while previewing. */
function Equalizer() {
  return (
    <span className="flex h-4 items-end justify-end gap-[2.5px]" aria-hidden>
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="w-[3px] origin-bottom animate-eq rounded-sm bg-lime-flash"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  )
}

export function TrackRow({
  index,
  track,
  jobId,
  state,
  onDownload,
  downloading = false,
  selected = false,
  onToggleSelect,
  style,
}: Props) {
  const status = state?.status
  const active =
    status === 'searching' ||
    status === 'downloading' ||
    status === 'tagging' ||
    status === 'retrying'

  const playingId = usePlayingPreviewId()
  const previewLoading = usePreviewLoading()
  const isPlaying = playingId === track.id && !previewLoading
  const isCurrentPreview = playingId === track.id

  return (
    <li style={style} className="group relative border-b border-ink-800 last:border-b-0">
      <div className="flex items-center gap-4 px-5 py-3 transition-colors group-hover:bg-ink-800/40">
        {onToggleSelect && (
          <button
            onClick={onToggleSelect}
            role="checkbox"
            aria-checked={selected}
            aria-label={`انتخاب ${track.title}`}
            className={clsx(
              'grid size-5 shrink-0 place-items-center rounded-[6px] border transition-all duration-150 active:scale-90',
              selected
                ? 'border-lime-flash bg-lime-flash text-lime-ink'
                : 'border-ink-600 text-transparent opacity-40 group-hover:opacity-100 hover:border-ink-400',
            )}
          >
            {selected && <Check className="size-3 animate-pop" strokeWidth={3.5} />}
          </button>
        )}

        <span className="w-6 shrink-0 text-end font-display text-mini tabular-nums text-ink-600">
          {isCurrentPreview ? <Equalizer /> : index}
        </span>

        {track.cover_url ? (
          <img
            src={track.cover_url}
            alt=""
            loading="lazy"
            className={clsx(
              'size-10 shrink-0 rounded-ctl object-cover transition duration-300',
              isCurrentPreview && 'ring-2 ring-lime-flash/70',
            )}
          />
        ) : (
          <div className="size-10 shrink-0 rounded-ctl bg-ink-800" />
        )}

        <div className="min-w-0 flex-1">
          <p
            className={clsx(
              'truncate text-body font-medium transition-colors',
              isCurrentPreview ? 'text-lime-flash' : 'text-ink-100',
            )}
            dir="auto"
          >
            {track.title}
          </p>
          <p className="truncate text-mini text-ink-400" dir="auto">
            {track.artists.join(', ')}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {track.preview_url && (
            <button
              onClick={() => togglePreview(track.id, track.preview_url!)}
              title={isCurrentPreview ? 'توقف پیش‌نمایش' : 'پخش ۳۰ ثانیه پیش‌نمایش'}
              aria-label={
                isCurrentPreview
                  ? `توقف پیش‌نمایش ${track.title}`
                  : `پخش ۳۰ ثانیه از ${track.title}`
              }
              className={clsx(
                'grid size-8 shrink-0 place-items-center rounded-ctl border transition duration-200 active:scale-90',
                isCurrentPreview
                  ? 'border-lime-flash/50 bg-lime-flash/10 text-lime-flash'
                  : 'border-ink-700 text-ink-400 hover:border-lime-flash/50 hover:text-lime-flash',
              )}
            >
              {isCurrentPreview && previewLoading ? (
                <LoaderCircle className="size-3.5 animate-spin" />
              ) : isPlaying ? (
                <Pause className="size-3.5" />
              ) : (
                <Play className="size-3.5 translate-x-px" />
              )}
            </button>
          )}

          {status === 'error' ? (
            <span
              className="flex animate-pop items-center gap-1.5 text-mini text-danger"
              title={state?.error ?? undefined}
            >
              <TriangleAlert className="size-3.5" />
              ناموفق
            </span>
          ) : status === 'done' && jobId ? (
            <a
              href={trackFileUrl(jobId, track.id)}
              download
              className="flex animate-pop items-center gap-1.5 rounded-ctl border border-ink-600 px-2.5 py-1.5 text-mini font-medium text-lime-flash transition duration-200 hover:border-lime-flash/50 hover:bg-lime-flash/10 active:scale-95"
            >
              <Check className="size-3.5" />
              {state?.ext ?? 'mp3'}
              <Download className="size-3.5" />
            </a>
          ) : active || status === 'queued' ? (
            <span
              className={clsx(
                'text-mini text-ink-300 tabular-nums',
                status !== 'downloading' && 'animate-breathe',
              )}
            >
              {STAGE_LABEL[status!]}
              {status === 'downloading' && ` ${Math.round((state?.progress ?? 0) * 100)}%`}
            </span>
          ) : (
            <>
              <span className="text-mini text-ink-400 tabular-nums">
                {track.duration_ms > 0 ? formatDuration(track.duration_ms) : '—'}
              </span>
              {onDownload && (
                <button
                  onClick={onDownload}
                  disabled={downloading}
                  title={downloading ? 'در حال شروع دانلود…' : 'دانلود این آهنگ'}
                  aria-label={`دانلود ${track.title}`}
                  aria-busy={downloading}
                  className={clsx(
                    'grid size-8 shrink-0 place-items-center rounded-ctl border transition duration-200 active:scale-90',
                    downloading
                      ? 'cursor-not-allowed border-lime-flash/40 text-lime-flash opacity-70'
                      : 'border-ink-700 text-ink-400 opacity-60 group-hover:opacity-100 hover:border-lime-flash/50 hover:text-lime-flash',
                  )}
                >
                  {downloading ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <Download className="size-4" />
                  )}
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {active && (
        <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-ink-800">
          {status === 'downloading' ? (
            <div
              className="h-full bg-lime-flash transition-[width] duration-500 ease-out"
              style={{ width: `${Math.max(3, (state?.progress ?? 0) * 100)}%` }}
            />
          ) : (
            // Searching / tagging / retrying report no percentage — a
            // travelling band says "working" without faking a number.
            <div className="h-full w-1/4 animate-sweep bg-lime-flash/70" />
          )}
        </div>
      )}
    </li>
  )
}
