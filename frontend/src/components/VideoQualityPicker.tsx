import { useMemo } from 'react'
import clsx from 'clsx'
import { LoaderCircle } from 'lucide-react'
import { videoQualityLabel, type AnimeSource, type VideoQuality } from '../lib/api'
import type { DiscoveryState } from '../lib/animeDiscovery'
import { useDownloads } from '../lib/downloads'
import { useMessages } from '../lib/i18n'

/** Compute the qualities actually verified available for a set of sources.
 *
 *  The backend's probes report, per source, the resolutions it is *verified*
 *  to hold — or null when it wasn't probed (Nyaa/hianime). A null or unknown
 *  source is no proof a quality is absent, and also no proof it *exists* — it
 *  never widens the set. The union of the authoritative lists is exactly what
 *  this chain can serve, plus `original` (every source serves its own best
 *  stream). There is no hard-coded resolution list: a provider may report
 *  240/360/540/720/1080/1440/2160 or anything else, and it renders as
 *  discovered. */
export function availableQualities(sources: AnimeSource[] | undefined | null): VideoQuality[] {
  if (!sources || sources.length === 0) return ['original']
  const authoritative = sources.filter((s) => Array.isArray(s.qualities))
  if (authoritative.length === 0) return ['original']
  const verified = new Set(authoritative.flatMap((s) => s.qualities as string[]))
  const sorted = [...verified].sort(dimSortReversed)
  if (sorted.length === 0) return ['original']
  return [...sorted, 'original']
}

/** Did any source give an authoritative (non-null) qualities verdict? Null
 *  providers never count — one must exist before a quality can be hidden. */
export function hasAuthoritativeSources(sources: AnimeSource[] | null | undefined): boolean {
  return !!sources && sources.some((s) => Array.isArray(s.qualities))
}

/** Sort discovered resolutions high→low (1080, 720, 480…) then "original"
 *  last. Lexicographic sorting would misorder 1080 before 720. */
export function dimSortReversed(a: string, b: string): number {
  const na = Number(a)
  const nb = Number(b)
  if (Number.isNaN(na) || Number.isNaN(nb)) return String(a).localeCompare(String(b))
  return nb - na
}

/** Segmented picker for the resolution Unstream asks a video provider for.
 *
 *  A different axis from the audio QualityPicker — 720p is not the same choice
 *  as 192 kbps — but it works the same way: one global preference, persisted,
 *  applied to every anime job started after it changes.
 *
 *  Discovery is the source of truth. `discovery` is the state of an
 *  authoritative probe:
 *    - undefined (no season context — header/settings on the anime landing):
 *      the only honest choice is `original`; no hard-coded list is shown.
 *    - loading: shows "Checking…" and is disabled — the user cannot choose
 *      before discovery finishes.
 *    - unknown/failed: shows "couldn't determine" + retry rather than guessing.
 *    - ready: renders ONLY the verified available resolutions (plus original),
 *      never a hard-coded list.
 */
export function VideoQualityPicker({
  className,
  discovery,
  mUndetermined,
  mRetry,
  onRetry,
}: {
  className?: string
  discovery?: DiscoveryState
  mUndetermined?: string
  mRetry?: string
  onRetry?: () => void
}) {
  const { videoQuality, setVideoQuality } = useDownloads()
  const m = useMessages()
  const undetermined = mUndetermined ?? m.anime.quality.undetermined
  const retry = mRetry ?? m.anime.quality.retry
  const hasDiscovery = discovery != null

  const available = useMemo(() => {
    if (!hasDiscovery) return ['original']
    if (discovery!.kind !== 'ready') return []
    return availableQualities(discovery!.sources)
  }, [discovery, hasDiscovery])

  const loading = hasDiscovery && discovery!.kind === 'loading'
  const undeterminedState =
    hasDiscovery && (discovery!.kind === 'unknown' || discovery!.kind === 'error')
  const selectionUnavailable =
    hasDiscovery &&
    discovery!.kind === 'ready' &&
    discovery!.sources != null &&
    hasAuthoritativeSources(discovery!.sources) &&
    !available.includes(videoQuality)

  return (
    <div className={clsx('flex flex-col items-start gap-1', className)}>
      <div className="flex items-center gap-2">
        <span className="text-micro font-semibold text-ink-400">{m.anime.quality.label}</span>
        <div
          role="radiogroup"
          aria-label={m.anime.quality.label}
          className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
        >
          {loading ? (
            <span className="flex items-center gap-1.5 px-2 py-1 text-micro font-medium text-ink-400">
              <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
              {m.anime.quality.checking}
            </span>
          ) : undeterminedState ? (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-ctl px-2 py-1 text-micro font-medium text-ink-400 transition hover:text-lime-flash"
            >
              {undetermined}
              {onRetry && (
                <>
                  {' '}
                  <span className="font-medium underline underline-offset-2">{retry}</span>
                </>
              )}
            </button>
          ) : (
            available.map((option) => {
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
            })
          )}
        </div>
      </div>
      {!loading && selectionUnavailable && (
        <p role="alert" className="text-micro text-danger">
          {m.anime.quality.unavailable(videoQualityLabel(videoQuality, m))}
        </p>
      )}
    </div>
  )
}
