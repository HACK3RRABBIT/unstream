import { useMemo } from 'react'
import clsx from 'clsx'
import { VIDEO_QUALITIES, videoQualityLabel, type AnimeSource, type VideoQuality } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useMessages } from '../lib/i18n'

/** Compute the qualities actually verified available for a season.
 *
 *  The backend's /sources probe reports, per source, the resolutions it is
 *  *verified* to hold — or null when it wasn't probed (Nyaa/hianime). A null
 *  or unknown source can never rule a quality out, so a quality is hidden only
 *  when every source with an authoritative list lacks it and at least one
 *  authoritative list was reported. "original" is always shown: every source
 *  serves its own best stream. */
export function availableQualities(sources: AnimeSource[] | undefined | null): VideoQuality[] {
  if (!sources || sources.length === 0) return [...VIDEO_QUALITIES]
  const authoritative = sources.flatMap((s) => (Array.isArray(s.qualities) ? [s] : []))
  const unknown = sources.some((s) => !Array.isArray(s.qualities) || s.status === 'unknown')
  if (unknown || authoritative.length === 0) return [...VIDEO_QUALITIES]
  const verified = new Set(authoritative.flatMap((s) => s.qualities as string[]))
  return VIDEO_QUALITIES.filter((q) => q === 'original' || verified.has(q))
}

/** Segmented picker for the resolution Unstream asks a video provider for.
 *
 *  A different axis from the audio QualityPicker in the header — 720p is not
 *  the same choice as 192 kbps — but it works the same way: one global
 *  preference, persisted, applied to every anime job started after it
 *  changes. Mirrors the audio picker's visual language exactly.
 *
 *  With `sources` (the season's /sources capability) it renders only verified
 *  available qualities and, if the current global selection isn't among them,
 *  says so instead of silently falling back. Without `sources` (the header /
 *  settings pickers, which have no season context) it renders every quality
 *  unchanged.
 */
export function VideoQualityPicker({
  className,
  sources,
}: {
  className?: string
  sources?: AnimeSource[] | null
}) {
  const { videoQuality, setVideoQuality } = useDownloads()
  const m = useMessages()

  const available = useMemo(() => availableQualities(sources), [sources])
  const selectionUnavailable = sources != null && !available.includes(videoQuality)

  return (
    <div className={clsx('flex flex-col items-start gap-1', className)}>
      <div className="flex items-center gap-2">
        <span className="text-micro font-semibold text-ink-400">{m.anime.quality.label}</span>
        <div
          role="radiogroup"
          aria-label={m.anime.quality.label}
          className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
        >
          {available.map((option) => {
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
      {selectionUnavailable && (
        <p role="alert" className="text-micro text-danger">
          {m.anime.quality.unavailable(videoQualityLabel(videoQuality, m))}
        </p>
      )}
    </div>
  )
}
