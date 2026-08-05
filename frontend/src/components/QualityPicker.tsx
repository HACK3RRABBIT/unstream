import clsx from 'clsx'
import { QUALITIES, QUALITY_HINT, QUALITY_LABEL } from '../lib/api'
import { useDownloads } from '../lib/downloads'

/** Segmented picker for the audio Unstream asks ffmpeg for: an mp3 bitrate,
 *  or the upload's own stream untouched.
 *
 *  One global preference rather than a control on every download button —
 *  it applies to every job started after it changes, and is remembered
 *  across sessions. Jobs already running keep the quality they started at.
 *
 *  Deliberately neutral: lime marks the primary action on a surface, and
 *  in the header that is never this.
 */
export function QualityPicker({ className }: { className?: string }) {
  const { quality, setQuality } = useDownloads()

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span className="hidden text-micro font-semibold tracking-[0.14em] text-ink-400 uppercase sm:inline">
        Quality
      </span>
      <div
        role="radiogroup"
        aria-label="Audio quality"
        className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
      >
        {QUALITIES.map((option) => {
          const active = option === quality
          return (
            <button
              key={option}
              role="radio"
              aria-checked={active}
              title={QUALITY_HINT[option]}
              onClick={() => setQuality(option)}
              className={clsx(
                'rounded-ctl px-2 py-1 text-micro font-medium tabular-nums transition duration-200 active:scale-95',
                active ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100',
              )}
            >
              {QUALITY_LABEL[option]}
            </button>
          )
        })}
      </div>
    </div>
  )
}
