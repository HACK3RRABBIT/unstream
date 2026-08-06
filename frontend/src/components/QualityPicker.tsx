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
      <span className="hidden text-micro font-semibold text-ink-400 sm:inline">کیفیت</span>
      <div
        role="radiogroup"
        aria-label="کیفیت صدا"
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
                // Segments sit shoulder to shoulder, so a taller strip — not a
                // hit area per segment — is what makes this thumb-usable.
                'rounded-ctl px-2 py-1 text-micro font-medium tabular-nums transition duration-200 active:scale-95 pointer-coarse:px-2.5 pointer-coarse:py-2',
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
