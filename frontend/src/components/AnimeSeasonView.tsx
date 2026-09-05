import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Archive, Check, Clapperboard, Download, LoaderCircle, X } from 'lucide-react'
import clsx from 'clsx'
import {
  apiError,
  getAnimeSeasonEpisodeQualities,
  MAX_QUALITY_EPISODES,
  jobZipUrl,
  trackFileUrl,
  videoQualityLabel,
  type AnimeDetail,
  type AnimeSeason,
  type AnimeSource,
  type JobTrack,
  type VideoQuality,
} from '../lib/api'
import { faNumerals, useMessages, useStartAlign } from '../lib/i18n'
import { useDownloads } from '../lib/downloads'
import { useToast } from '../lib/toast'
import { SubtitlePicker } from './SubtitlePicker'
import { availableQualities, hasAuthoritativeSources, QualityChips } from './VideoQualityPicker'

interface Props {
  anime: AnimeDetail
  season: AnimeSeason
}

/** The season's episodes with individual or selected-subset downloads.
 *
 *  Mirrors the music CollectionView's structure — the same selection bar,
 *  mutation + toast download actions, per-row live progress (stage label,
 *  percentage while downloading, error / done-with-save-link), a ZIP button
 *  once a job has files, and a finished banner.
 *
 *  Quality is NOT one global choice any more. A season-wide default (the
 *  persisted `downloads.videoQuality`, same preference as before) applies to
 *  every episode that hasn't been told otherwise, but each episode can
 *  override it — a batch fansub released in 1080p and a fill-in episode that
 *  only ever shipped at 720p are both real, and forcing one resolution onto
 *  a whole season used to mean the request quietly failed for whichever
 *  episode didn't have it. There is deliberately no season-level `/sources`
 *  gate here any more either: Nyaa and hianime never probe ahead of time (by
 *  design — their availability is per-episode), so that endpoint answered
 *  "unknown" for nearly every real anime and the old picker rendered nothing
 *  but "Original" as a result. Per-episode discovery (below) is the only
 *  signal reliable enough to gate on. */
export function AnimeSeasonView({ anime, season }: Props) {
  const m = useMessages()
  const startAlign = useStartAlign()
  const downloads = useDownloads()
  const { push } = useToast()
  const entries = downloads
    .entriesForUrl(`anime://${anime.id}/${season.season}`)
    .filter((e) => !e.expired)

  // The aired count, not the planned total — an airing season lists only what
  // exists (12 planned, 6 aired → six rows, not twelve).
  const episodeCount = season.available_episodes > 0 ? season.available_episodes : season.episodes
  // The episode list is derived before the queries; a season arranges episodes
  // by number (SxxE01, SxxE02, ...).
  const episodeIds = useMemo(
    () =>
      Array.from({ length: episodeCount }, (_, i) => ({
        id: `${anime.id}:s${season.season}e${i + 1}`,
        number: i + 1,
      })),
    [anime.id, season.season, episodeCount],
  )

  const [selected, setSelected] = useState<Set<string>>(new Set())

  const toggleSelect = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const selectAll = () => setSelected(new Set(episodeIds.map((e) => e.id)))
  const clearSelection = () => setSelected(new Set())

  const start = useMutation({
    mutationFn: () => {
      const ids = episodeIds.map((e) => e.id)
      guardQualityFor(ids)
      return downloads.startAnime(
        { id: anime.id, title: anime.title, coverUrl: anime.cover_url },
        season,
        undefined,
        overridesFor(ids),
      )
    },
    onSuccess: () => push(m.anime.queuedSeason(season.title)),
    onError: (err) => push(apiError(err, m), 'error'),
  })

  const startSelected = useMutation({
    mutationFn: (ids: string[]) => {
      guardQualityFor(ids)
      return downloads.startAnime(
        { id: anime.id, title: anime.title, coverUrl: anime.cover_url },
        season,
        ids,
        overridesFor(ids),
      )
    },
    onSuccess: (_data, ids) => {
      clearSelection()
      push(m.anime.queuedSelected(ids.length))
    },
    onError: (err) => push(apiError(err, m), 'error'),
  })

  const startEpisode = useMutation({
    mutationFn: (id: string) => {
      guardQualityFor([id])
      return downloads.startAnime(
        { id: anime.id, title: anime.title, coverUrl: anime.cover_url },
        season,
        [id],
        overridesFor([id]),
      )
    },
    onSuccess: () => push(m.anime.queuedOne()),
    onError: (err) => push(apiError(err, m), 'error'),
  })

  // The episode currently queuing a solo download — its row also probes quality
  // so the guard can block an impossible solo request using that episode's data.
  const activeSolo = startEpisode.isPending ? startEpisode.variables : null

  // Per-episode verified qualities, fetched lazily — ONLY for what the user
  // focuses: everything selected (the multi-select gate) plus the episode
  // currently queuing a solo download. Never a whole-season fan-out. Each
  // episode's providers reuse the exact /sources shape, so the existing
  // `availableQualities` union (null never widens, all when no authority)
  // computes its real options.
  //
  // They travel in ONE request. A query per episode meant the browser ran six
  // at a time and the backend charged each against the resolve rate limit, so
  // selecting a long season left its tail stuck on "checking" and then failed.
  // Answers are kept per episode as they arrive, so a later selection only
  // asks about the episodes not already determined.
  const seasonKey = `${anime.id}:${season.season}`
  const [determined, setDetermined] = useState<{
    key: string
    byEpisode: Record<number, AnimeSource[]>
  }>({ key: seasonKey, byEpisode: {} })
  // Answers only count for the season they were fetched for — switching
  // seasons inside a mounted view starts from nothing rather than showing the
  // previous season's verdicts.
  const known = useMemo(
    () => (determined.key === seasonKey ? determined.byEpisode : {}),
    [determined, seasonKey],
  )

  // Every resolution any probed episode has verified so far this session —
  // grows as rows get checked. Purely a hint for the season-default control
  // (which pills get a checkmark); it never gates what's selectable there.
  const knownUnion = useMemo(
    () =>
      Array.from(
        new Set(
          Object.values(known).flatMap((providers) =>
            availableQualities(providers).filter((q) => q !== 'original'),
          ),
        ),
      ),
    [known],
  )

  // One request's worth of the still-undetermined episodes. A selection longer
  // than the backend's cap is answered a window at a time: each batch that
  // lands shrinks this list, which starts the next one.
  const missing = useMemo(() => {
    const focused = episodeIds.filter((ep) => selected.has(ep.id) || activeSolo === ep.id)
    return focused
      .map((ep) => ep.number)
      .filter((n) => !(n in known))
      .slice(0, MAX_QUALITY_EPISODES)
  }, [episodeIds, selected, activeSolo, known])

  const qualitiesQuery = useQuery({
    queryKey: ['anime-episode-qualities', seasonKey, missing],
    queryFn: () => getAnimeSeasonEpisodeQualities(anime.id, season.season, missing),
    staleTime: 2 * 60 * 1000,
    enabled: missing.length > 0,
  })

  useEffect(() => {
    const answered = qualitiesQuery.data
    if (!answered) return
    setDetermined((prev) => {
      const base = prev.key === seasonKey ? prev.byEpisode : {}
      const next = { ...base }
      for (const [episode, entry] of Object.entries(answered.episodes)) {
        next[Number(episode)] = entry.providers
      }
      return { key: seasonKey, byEpisode: next }
    })
  }, [qualitiesQuery.data, seasonKey])

  // Per-episode quality overrides. An episode with no entry here downloads at
  // the season-wide default (`downloads.videoQuality`); one with an entry
  // downloads at that resolution instead. Keyed by episode NUMBER (stable
  // within a season) and reset on a season change, same as `known` above.
  const [overrides, setOverrides] = useState<{
    key: string
    byEpisode: Record<number, VideoQuality>
  }>({ key: seasonKey, byEpisode: {} })
  const episodeOverride = useMemo(
    () => (overrides.key === seasonKey ? overrides.byEpisode : {}),
    [overrides, seasonKey],
  )

  const effectiveQuality = (number: number): VideoQuality =>
    episodeOverride[number] ?? downloads.videoQuality

  /** Set (or, clicking the already-effective choice again, clear) one
   *  episode's override — clearing reverts it to following the season
   *  default, which is the more discoverable way to undo a mistaken pick
   *  than a separate reset control would be. */
  const setEpisodeQuality = (number: number, quality: VideoQuality) => {
    setOverrides((prev) => {
      const base = prev.key === seasonKey ? prev.byEpisode : {}
      if ((base[number] ?? downloads.videoQuality) === quality) {
        const { [number]: _drop, ...rest } = base
        return { key: seasonKey, byEpisode: rest }
      }
      return { key: seasonKey, byEpisode: { ...base, [number]: quality } }
    })
  }

  /** The subset of `episodeOverride` relevant to `ids` — what actually needs
   *  to travel to the backend as `episode_qualities`. An episode without an
   *  explicit override is simply absent, so it falls back server-side to the
   *  request's own `quality` (the season default) rather than being sent
   *  redundantly.
   *
   *  A hoisted function declaration, like `guardQualityFor` below, so the
   *  mutations above can call it without a use-before-definition warning. */
  function overridesFor(ids: string[]): Record<string, VideoQuality> {
    const out: Record<string, VideoQuality> = {}
    for (const id of ids) {
      const ep = episodeIds.find((e) => e.id === id)
      if (ep && ep.number in episodeOverride) out[id] = episodeOverride[ep.number]
    }
    return out
  }

  /** Per-episode verified concrete qualities (no "original", which every
   *  episode's own best stream always covers) for episode `number`, or null
   *  when that episode isn't authoritatively determined.
   *
   *  A successful probe whose providers are all `null`/unknown reports nothing
   *  (e.g. a hianime-only season) — that is "don't know", not "verified none",
   *  so it returns null (never a verdict, never blocks). */
  const determinedAt = (number: number): VideoQuality[] | null => {
    const providers = known[number]
    if (!providers) return null // unasked, loading, or failed — never a verdict
    if (!hasAuthoritativeSources(providers)) return null // all unknown
    return availableQualities(providers).filter((q) => q !== 'original')
  }

  /** Guard one download targeting `targetIds`: each episode's OWN effective
   *  quality (its override, or the season default) is checked against that
   *  SAME episode's own determination — never an intersection across the
   *  whole batch, which used to reject a perfectly good 1080p episode just
   *  because another episode in the selection hadn't verified 1080p yet. An
   *  episode that isn't authoritatively determined, or whose effective choice
   *  is "original", is never blocked — only a positive, per-episode proof of
   *  impossibility stops a request.
   *
   *  A hoisted function declaration so the mutations above can call it without
   *  a use-before-definition warning; its body only reads the consts it closes
   *  over, which are initialized by the time a mutation actually runs. */
  function guardQualityFor(targetIds: string[]) {
    for (const id of targetIds) {
      const ep = episodeIds.find((e) => e.id === id)
      if (!ep) continue
      const quality = effectiveQuality(ep.number)
      if (quality === 'original') continue
      const determined = determinedAt(ep.number)
      if (determined && !determined.includes(quality)) {
        throw new Error(m.anime.quality.unavailable(videoQualityLabel(quality, m)))
      }
    }
  }

  // Latest job state per episode id, for per-row progress / save links.
  const jobTracks = useMemo(() => {
    const map = new Map<string, { jobId: string; state: JobTrack }>()
    for (const entry of entries) {
      for (const t of entry.job?.tracks ?? []) {
        map.set(t.id, { jobId: entry.jobId, state: t })
      }
    }
    return map
  }, [entries])

  const running = entries.some((e) => !e.job?.finished)
  const doneTotal = entries.reduce((n, e) => n + (e.job?.done ?? 0), 0)
  const failedTotal = entries.reduce((n, e) => n + (e.job?.failed ?? 0), 0)
  const queuedTotal = entries.reduce((n, e) => n + (e.job?.total ?? e.tracks.length), 0)
  const settled = entries.reduce((n, e) => n + (e.job ? e.job.done + e.job.failed : 0), 0)
  const allFinished = entries.length > 0 && !running
  const allTracksDone = episodeIds.every((e) => jobTracks.get(e.id)?.state.status === 'done')
  const zipEntry = [...entries].reverse().find((e) => (e.job?.done ?? 0) > 0)

  const anyDownloading = start.isPending || startSelected.isPending || startEpisode.isPending

  return (
    <section className="overflow-hidden rounded-panel border border-ink-700 bg-ink-900">
      <div className="flex flex-wrap items-center gap-5 border-b border-ink-800 p-5 sm:p-6">
        {season.cover_url ? (
          <img
            src={season.cover_url}
            alt=""
            className="size-20 rounded-btn object-cover ring-1 ring-ink-700 sm:size-24"
          />
        ) : (
          <div className="grid size-20 place-items-center rounded-btn bg-ink-800 ring-1 ring-ink-700 sm:size-24">
            <Clapperboard className="size-8 text-ink-400" />
          </div>
        )}
        {/* basis keeps the title from being crushed on phones — the buttons
            wrap to their own row instead of truncating the name */}
        <div className="min-w-0 grow basis-40">
          <span className="text-micro font-semibold text-lime-flash">
            {m.anime.season(season.season)}
          </span>
          <h2
            className={clsx(
              'mt-1 truncate font-display text-2xl font-bold',
              faNumerals(season.title),
              startAlign,
            )}
            dir="auto"
          >
            {season.title}
          </h2>
          <p className="mt-1 text-mini text-ink-300">
            {season.status === 'RELEASING' && season.available_episodes > 0
              ? m.anime.airingAvailable(season.available_episodes, season.episodes)
              : m.anime.episodes(season.episodes)}
          </p>
        </div>

        <SubtitlePicker className="w-full sm:w-auto" />

        <div className="flex items-center gap-2">
          {zipEntry && (zipEntry.job?.total ?? zipEntry.tracks.length) > 1 && (
            <a
              href={jobZipUrl(zipEntry.jobId)}
              download
              className="flex animate-pop items-center gap-1.5 rounded-btn border border-ink-600 px-4 py-2.5 text-mini font-medium text-ink-100 transition duration-200 hover:border-ink-400 active:scale-[0.98]"
            >
              <Archive className="size-4" />
              ZIP ({m.app.num(zipEntry.job!.done)})
            </a>
          )}
          {selected.size > 0 ? (
            <>
              <button
                onClick={clearSelection}
                title={m.anime.clearSelection}
                aria-label={m.anime.clearSelection}
                className="grid size-10 place-items-center rounded-btn border border-ink-600 text-ink-300 transition duration-200 hover:border-ink-400 hover:text-ink-100 active:scale-95"
              >
                <X className="size-4" />
              </button>
              <button
                onClick={() => startSelected.mutate([...selected])}
                disabled={startSelected.isPending}
                className={clsx(
                  'flex animate-pop items-center gap-1.5 rounded-btn bg-lime-flash px-4 py-2.5 text-mini font-medium text-lime-ink',
                  'transition duration-200 hover:bg-lime-soft active:scale-[0.98]',
                  'disabled:cursor-not-allowed disabled:opacity-50',
                )}
              >
                {startSelected.isPending ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
                {m.anime.downloadSelected}
              </button>
            </>
          ) : (
            (running || !allTracksDone) && (
              <button
                onClick={() => start.mutate()}
                disabled={start.isPending || running}
                className={clsx(
                  'flex items-center gap-1.5 rounded-btn bg-lime-flash px-4 py-2.5 text-mini font-medium text-lime-ink',
                  'transition duration-200 hover:bg-lime-soft active:scale-[0.98]',
                  'disabled:cursor-not-allowed disabled:opacity-50',
                )}
              >
                {running || start.isPending ? (
                  <>
                    <LoaderCircle className="size-4 animate-spin" />
                    {entries.length > 0
                      ? `${m.app.num(settled)}/${m.app.num(queuedTotal)}`
                      : m.anime.starting}
                  </>
                ) : (
                  <>
                    <Download className="size-4" />
                    {m.anime.downloadSeason}
                  </>
                )}
              </button>
            )
          )}
        </div>
      </div>

      {/* The season-wide default. Every episode below follows it unless its
          own row is given an explicit override — the pills here are the
          common ladder plus whatever any already-probed episode has verified
          (`knownUnion`), never gated on a season-level probe (unreliable for
          this app's actual providers — see the module doc comment above). */}
      <div className="border-b border-ink-800 px-5 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-micro font-semibold text-ink-400">{m.anime.quality.label}</span>
          <QualityChips
            value={downloads.videoQuality}
            onChange={downloads.setVideoQuality}
            verified={knownUnion}
          />
        </div>
        <p className="mt-1.5 text-micro text-ink-500">{m.anime.quality.hint}</p>
      </div>

      {(start.isError || startSelected.isError || startEpisode.isError) && (
        <p
          role="alert"
          className="animate-fade-up border-b border-ink-800 bg-danger/10 px-5 py-3 text-mini text-danger"
        >
          {apiError(start.error ?? startSelected.error ?? startEpisode.error, m)}
        </p>
      )}

      {/* Selection bar, mirroring the music collection's. */}
      <div className="flex items-center justify-between gap-3 border-b border-ink-800 bg-ink-950/50 px-5 py-3 sm:py-2">
        <span className="min-w-0 text-xs text-ink-400 tabular-nums">
          {selected.size > 0
            ? m.anime.selectedOf(selected.size, episodeIds.length)
            : m.anime.episodes(episodeCount)}
        </span>
        <button
          onClick={selected.size === episodeIds.length ? clearSelection : selectAll}
          className="tap-target shrink-0 text-xs font-medium whitespace-nowrap text-lime-flash transition hover:text-lime-soft"
        >
          {selected.size === episodeIds.length ? m.anime.clearAll : m.anime.selectAll}
        </button>
      </div>

      <ol className="stagger">
        {episodeIds.map((ep, index) => {
          const tj = jobTracks.get(ep.id)
          const status = tj?.state.status
          const active =
            status === 'searching' ||
            status === 'downloading' ||
            status === 'tagging' ||
            status === 'retrying'
          const queuing = startEpisode.isPending && startEpisode.variables === ep.id
          // This episode's per-episode quality probe is active (selected, or
          // queuing a solo download) — show its availability line.
          const probing = selected.has(ep.id) || activeSolo === ep.id
          return (
            <li
              key={ep.id}
              style={{ '--i': index } as React.CSSProperties}
              className="group relative border-b border-ink-800 last:border-b-0"
            >
              <div className="flex items-center gap-4 px-5 py-3 transition-colors group-hover:bg-ink-800/40">
                <button
                  onClick={() => toggleSelect(ep.id)}
                  role="checkbox"
                  aria-checked={selected.has(ep.id)}
                  aria-label={m.anime.episodeLabel(ep.number)}
                  className={clsx(
                    'tap-target grid size-5 shrink-0 place-items-center rounded-[6px] border transition-all duration-150 active:scale-90',
                    selected.has(ep.id)
                      ? 'border-lime-flash bg-lime-flash text-lime-ink'
                      : 'border-ink-600 text-transparent pointer-fine:opacity-40 pointer-fine:group-hover:opacity-100 hover:border-ink-400',
                  )}
                >
                  {selected.has(ep.id) && (
                    <Check className="size-3 animate-pop" strokeWidth={3.5} />
                  )}
                </button>

                <span className="w-6 shrink-0 text-end font-display text-mini tabular-nums text-ink-600">
                  {m.app.num(ep.number)}
                </span>

                <div className={clsx('min-w-0 flex-1', startAlign)} dir="auto">
                  <p
                    className={clsx(
                      'truncate text-body font-medium transition-colors',
                      faNumerals(m.anime.episodeLabel(ep.number)),
                      status === 'done' ? 'text-ink-100' : 'text-ink-100',
                    )}
                  >
                    {m.anime.episodeLabel(ep.number)}
                  </p>
                  {probing && (
                    <EpisodeQualityRow
                      value={effectiveQuality(ep.number)}
                      onChange={(q) => setEpisodeQuality(ep.number, q)}
                      verified={determinedAt(ep.number)}
                      loading={!(ep.number in known) && !qualitiesQuery.isError}
                      undetermined={ep.number in known && determinedAt(ep.number) === null}
                      onRetry={() => qualitiesQuery.refetch()}
                    />
                  )}
                </div>

                <div className="flex shrink-0 items-center gap-3">
                  {status === 'error' ? (
                    <span
                      className="flex animate-pop items-center gap-1.5 text-mini text-danger"
                      title={tj?.state.error ?? undefined}
                    >
                      {m.track.failed}
                    </span>
                  ) : status === 'done' && tj ? (
                    <a
                      href={trackFileUrl(tj.jobId, ep.id)}
                      download
                      className="tap-target flex animate-pop items-center gap-1.5 rounded-ctl border border-ink-600 px-2.5 py-1.5 text-mini font-medium text-lime-flash transition duration-200 hover:border-lime-flash/50 hover:bg-lime-flash/10 active:scale-95"
                    >
                      <Check className="size-3.5" />
                      {tj.state.ext ?? 'mp4'}
                      <Download className="size-3.5" />
                    </a>
                  ) : active || status === 'queued' ? (
                    <span
                      className={clsx(
                        'text-mini text-ink-300 tabular-nums',
                        status !== 'downloading' && 'animate-breathe',
                      )}
                    >
                      {status === 'searching' && tj?.state.provider_progress
                        ? (() => {
                            const pp = tj.state.provider_progress!
                            if (pp.current) {
                              // A specific source is being tried — real backend
                              // progress, shown as it happens.
                              return m.anime.checkingSource(pp.checked, pp.total, pp.current)
                            }
                            if (pp.checked > 0) {
                              // A source just finished; how many are left.
                              return m.anime.searchingSources(pp.checked, pp.total)
                            }
                            return m.anime.searchingProviders
                          })()
                        : m.stages[status as keyof typeof m.stages]}
                      {status === 'downloading' &&
                        ` ${m.app.num(Math.round((tj?.state.progress ?? 0) * 100))}%`}
                    </span>
                  ) : (
                    <button
                      onClick={() => startEpisode.mutate(ep.id)}
                      disabled={queuing || anyDownloading}
                      title={queuing ? m.track.startingDownload : m.anime.downloadEpisode}
                      aria-label={m.anime.downloadFor(ep.number)}
                      aria-busy={queuing}
                      className={clsx(
                        'tap-target grid size-8 shrink-0 place-items-center rounded-ctl border transition duration-200 active:scale-90',
                        queuing
                          ? 'cursor-not-allowed border-lime-flash/40 text-lime-flash opacity-70'
                          : 'border-ink-700 text-ink-400 hover:border-lime-flash/50 hover:text-lime-flash pointer-fine:opacity-60 pointer-fine:group-hover:opacity-100',
                      )}
                    >
                      {queuing ? (
                        <LoaderCircle className="size-4 animate-spin" />
                      ) : (
                        <Download className="size-4" />
                      )}
                    </button>
                  )}
                </div>
              </div>

              {active && (
                <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-ink-800">
                  {status === 'downloading' ? (
                    <div
                      className="h-full bg-lime-flash transition-[width] duration-500 ease-out"
                      style={{ width: `${Math.max(3, (tj?.state.progress ?? 0) * 100)}%` }}
                    />
                  ) : (
                    <div className="h-full w-1/4 animate-sweep bg-lime-flash/70" />
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ol>

      {allFinished && (
        <p className="flex animate-fade-up items-center gap-2 border-t border-ink-800 px-5 py-3.5 text-mini text-ink-300">
          <Check className="size-4 shrink-0 text-lime-flash" />
          {m.anime.finished(doneTotal, queuedTotal)}
          {failedTotal > 0 && (
            <span className="text-danger">· {m.anime.failedCount(failedTotal)}</span>
          )}
        </p>
      )}
    </section>
  )
}

/** One episode's own quality control — this is the actual per-part selection
 *  the season default alone can't give: it renders a full `QualityChips` row
 *  (common ladder + custom) so the episode is choosable immediately, with
 *  whatever this specific episode's own probe has verified layered on top as
 *  checkmarks once it lands. Nothing here ever blocks a click — an in-flight
 *  or inconclusive probe still leaves every pill selectable, it just can't
 *  yet say which one is confirmed. */
function EpisodeQualityRow({
  value,
  onChange,
  verified,
  loading,
  undetermined,
  onRetry,
}: {
  value: VideoQuality
  onChange: (quality: VideoQuality) => void
  verified: VideoQuality[] | null
  loading: boolean
  undetermined: boolean
  onRetry: () => void
}) {
  const m = useMessages()
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
      <QualityChips value={value} onChange={onChange} verified={verified} size="sm" />
      {loading && (
        <LoaderCircle
          className="size-3 animate-spin text-ink-500"
          aria-label={m.anime.quality.checking}
        />
      )}
      {undetermined && (
        <button
          type="button"
          onClick={onRetry}
          title={m.anime.quality.undetermined}
          className="text-micro text-ink-500 underline decoration-dotted underline-offset-2 transition hover:text-lime-flash"
        >
          {m.anime.quality.retry}
        </button>
      )}
    </div>
  )
}
