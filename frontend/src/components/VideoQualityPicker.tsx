import { useMemo, useState } from 'react'
import clsx from 'clsx'
import { Check, Plus } from 'lucide-react'
import type { AnimeSource, VideoQuality } from '../lib/api'
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

/** The resolutions offered by default, everywhere a quality is picked — not
 *  because the backend is limited to them (it accepts any 3-4 digit height:
 *  `is_video_resolution` in anime/downloader.py), but because a season's own
 *  discovery is unreliable as the *only* source of options: Nyaa and hianime
 *  are deliberately per-episode-only (no season-wide probe exists for them),
 *  and even the sidecar that does probe up front answers "unknown" whenever
 *  its own upstream sites are slow or unreachable. A picker built solely from
 *  that probe used to show nothing but "Original" most of the time — this
 *  ladder is what makes every request offer real choices from the first
 *  render, with per-episode verification layered on top as it arrives. */
export const COMMON_VIDEO_QUALITIES: VideoQuality[] = [
  '2160',
  '1440',
  '1080',
  '720',
  '480',
  '360',
  '240',
]

const _CUSTOM_RES_RE = /^\d{3,4}$/

/** A row of resolution pills plus "Original" and a "+" for any height outside
 *  the common ladder — the backend honors any 3-4 digit height a provider can
 *  actually serve, so the picker never forces a choice into a closed set.
 *
 *  `verified`, when given, is the set of resolutions a real per-context probe
 *  (a specific episode, once its quality check has answered) has confirmed —
 *  each renders with a check mark, and any that fall outside the common
 *  ladder (a source genuinely serving 900p) are folded into the option list
 *  rather than hidden. Omitting `verified` (a season-wide default with no
 *  single episode in view) still renders the full common ladder — this is
 *  the difference between "shown because it usually exists" and "confirmed
 *  right now", and both are legitimate reasons to offer a pill. */
export function QualityChips({
  value,
  onChange,
  verified,
  size = 'md',
  className,
  label,
}: {
  value: VideoQuality
  onChange: (quality: VideoQuality) => void
  verified?: VideoQuality[] | null
  size?: 'sm' | 'md'
  className?: string
  label?: string
}) {
  const m = useMessages()
  const [customOpen, setCustomOpen] = useState(false)
  const [customValue, setCustomValue] = useState('')

  const verifiedSet = useMemo(() => new Set(verified ?? []), [verified])
  const options = useMemo(() => {
    const merged = new Set<string>([...COMMON_VIDEO_QUALITIES, ...verifiedSet])
    // A value picked in a previous session (or via the custom input) must
    // stay visible even if it's neither common nor (yet) verified here —
    // an active pill can never go missing from its own row.
    if (value !== 'original') merged.add(value)
    return [...merged].sort(dimSortReversed)
  }, [verifiedSet, value])

  const commitCustom = () => {
    if (_CUSTOM_RES_RE.test(customValue)) onChange(customValue)
    setCustomOpen(false)
    setCustomValue('')
  }

  const pillClass = (active: boolean) =>
    clsx(
      'flex items-center gap-1 rounded-ctl font-medium tabular-nums transition duration-200 active:scale-95',
      size === 'sm'
        ? 'px-1.5 py-0.5 text-micro'
        : 'px-2 py-1 text-micro pointer-coarse:px-2.5 pointer-coarse:py-2',
      active ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100 hover:bg-ink-800',
    )

  return (
    <div
      role="radiogroup"
      aria-label={label ?? m.anime.quality.label}
      className={clsx(
        'flex flex-wrap items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5',
        className,
      )}
    >
      {options.map((option) => {
        const active = option === value
        const isVerified = verifiedSet.has(option)
        return (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={active}
            title={isVerified ? m.anime.quality.verified : undefined}
            onClick={() => onChange(option)}
            className={pillClass(active)}
          >
            {m.app.num(option)}
            {isVerified && <Check className="size-2.5 text-lime-flash" strokeWidth={3} />}
          </button>
        )
      })}
      <button
        type="button"
        role="radio"
        aria-checked={value === 'original'}
        onClick={() => onChange('original')}
        className={pillClass(value === 'original')}
      >
        {m.anime.quality.original}
      </button>
      {customOpen ? (
        <input
          type="number"
          inputMode="numeric"
          min={100}
          max={9999}
          autoFocus
          aria-label={m.anime.quality.customAria}
          value={customValue}
          onChange={(e) => setCustomValue(e.target.value)}
          onBlur={commitCustom}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitCustom()
            if (e.key === 'Escape') {
              setCustomOpen(false)
              setCustomValue('')
            }
          }}
          className="w-12 rounded-ctl border border-ink-700 bg-ink-950 px-1 py-0.5 text-center text-micro tabular-nums text-ink-100 outline-none focus:border-lime-flash"
        />
      ) : (
        <button
          type="button"
          title={m.anime.quality.custom}
          aria-label={m.anime.quality.custom}
          onClick={() => setCustomOpen(true)}
          className={clsx(pillClass(false), 'px-1')}
        >
          <Plus className="size-3" />
        </button>
      )}
    </div>
  )
}
