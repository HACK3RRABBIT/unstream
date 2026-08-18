import { useMemo } from 'react'
import clsx from 'clsx'
import { VIDEO_QUALITIES, videoQualityLabel, type AnimeSource, type VideoQuality } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useMessages } from '../lib/i18n'

/** Compute the qualities actually verified available for a season.
 *
 *  The backend's /sources probe reports, per source, the resolutions it is
 *  *verified* to hold — or null when it wasn't probed (Nyaa/hianime). A null
 *  or unknown source is no proof a quality is absent, but it is also no proof
 *  it *exists* — the chain can still deliver whatever the verified sources
 *  carry, so a quality is hidden only when every source that reported an
 *  authoritative list lacks it and at least one such source was reported.
 *
 *  Crucially: a per-episode (null/unknown) source must NOT widen the set back
 *  to "all qualities". Each authoritative list is exact; the union of the
 *  authoritative lists is the set of qualities any provider in this chain can
 *  actually serve. Nyaa marking a season 720p-only while anivexa carries
 *  [360,720,1080] still means 480p is a doomed request, and hiding it is
 *  exactly the gate's point. "original" is always shown: every source serves
 *  its own best stream. */
export function availableQualities(sources: AnimeSource[] | undefined | null): VideoQuality[] {
  if (!sources || sources.length === 0) return [...VIDEO_QUALITIES]
  const authoritative = sources.filter((s) => Array.isArray(s.qualities))
  if (authoritative.length === 0) return [...VIDEO_QUALITIES]
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
