import clsx from 'clsx'
import { VIDEO_QUALITIES, videoQualityLabel } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useMessages } from '../lib/i18n'

/** Segmented picker for the resolution Unstream asks a video provider for.
 *
 *  A different axis from the audio QualityPicker in the header — 720p is not
 *  the same choice as 192 kbps — but it works the same way: one global
 *  preference, persisted, applied to every anime job started after it
 *  changes. Mirrors the audio picker's visual language exactly. */
export function VideoQualityPicker({ className }: { className?: string }) {
  const { videoQuality, setVideoQuality } = useDownloads()
  const m = useMessages()

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span className="text-micro font-semibold text-ink-400">{m.anime.quality.label}</span>
      <div
        role="radiogroup"
        aria-label={m.anime.quality.label}
        className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
      >
        {VIDEO_QUALITIES.map((option) => {
          const active = option === videoQuality
          return (
            <button
              key={option}
              role="radio"
              aria-checked={active}
              title={m.anime.quality.hint}
              onClick={() => setVideoQuality(option)}
              className={clsx(
                'rounded-ctl px-2 py-1 text-micro font-medium tabular-nums transition duration-200 active:scale-95 pointer-coarse:px-2.5 pointer-coarse:py-2',
                active ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100',
              )}
            >
              {videoQualityLabel(option, m)}
            </button>
          )
        })}
      </div>
    </div>
  )
}
