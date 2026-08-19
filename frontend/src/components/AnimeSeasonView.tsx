import { useMemo, useState } from 'react'
import { useMutation, useQueries, useQuery } from '@tanstack/react-query'
import { Archive, Check, Clapperboard, Download, LoaderCircle, X } from 'lucide-react'
import clsx from 'clsx'
import {
  apiError,
  getAnimeEpisodeQualities,
  getAnimeSources,
  jobZipUrl,
  trackFileUrl,
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
import { availableQualities, hasAuthoritativeSources } from './VideoQualityPicker'
import { videoQualityLabel } from '../lib/api'

interface Props {
  anime: AnimeDetail
  season: AnimeSeason
}

/** The season's episodes with individual or selected-subset downloads.

 *  Mirrors the music CollectionView's structure — the same selection bar,
 *  mutation + toast download actions, per-row live progress (stage label,
 *  percentage while downloading, error / done-with-save-link), a ZIP button
 *  once a job has files, and a finished banner. Quality is set globally in
 *  the header (VideoQualityPicker) like music's bitrate. */
export function AnimeSeasonView({ anime, season }: Props) {
  const m = useMessages()
  const startAlign = useStartAlign()
  const downloads = useDownloads()
  const { push } = useToast()
  const entries = downloads
    .entriesForUrl(`anime://${anime.id}/${season.season}`)
    .filter((e) => !e.expired)

  // Per-source capability for this season, from the backend's /sources probe —
  // the source of truth for which qualities are verified available. The quality
  // picker renders from it; a failed probe degrades to no capability data
  // (render all qualities normally) rather than hiding options that might work.
  const sourcesQuery = useQuery({
    queryKey: ['anime-sources', anime.id, season.season],
    queryFn: () => getAnimeSources(anime.id, season.season),
    staleTime: 5 * 60 * 1000,
  })

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
      guardQualityFor(episodeIds.map((e) => e.id))
      return downloads.startAnime(
        { id: anime.id, title: anime.title, coverUrl: anime.cover_url },
        season,
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
  const episodeQualities = useQueries({
    queries: episodeIds.map((ep) => ({
      queryKey: ['anime-episode-qualities', anime.id, season.season, ep.number],
      queryFn: () => getAnimeEpisodeQualities(anime.id, season.season, ep.number),
      staleTime: 2 * 60 * 1000,
      enabled: selected.has(ep.id) || activeSolo === ep.id,
    })),
  })

  // The capability probe already knows which resolutions this season can
  // actually serve. Refuse a download that is provably impossible (every
  // probed source lacks the requested quality) instead of letting the job
  // fail in the backend — a null-unknown source (Nyaa/hianime) never hides
  // a possibility.
  const sources = sourcesQuery.data?.providers ?? null
  const availability = useMemo(() => availableQualities(sources), [sources])

  /** Per-episode verified concrete qualities (no "original", which every
   *  episode's own best stream always covers) for the query at index `i`, or
   *  null when that episode isn't authoritatively determined.
   *
   *  A successful query whose providers are all `null`/unknown reports nothing
   *  (e.g. a hianime-only season) — that is "don't know", not "verified none",
   *  so it returns null (never a verdict, never blocks). */
  const determinedAt = (i: number): VideoQuality[] | null => {
    const q = episodeQualities[i]
    if (!q.data) return null // disabled, loading, or failed — never a verdict
    if (!hasAuthoritativeSources(q.data.providers)) return null // all unknown
    return availableQualities(q.data.providers).filter((q) => q !== 'original')
  }

  /** Guard one download targeting `targetIds`: the season-level /sources gate
   *  always applies (it never blocks on unknown), and when EVERY target episode
   *  is authoritatively determined the requested quality must be in their
   *  INTERSECTION. Any undetermined/loading/failed episode — or "original" —
   *  is never blocked.

   *  A hoisted function declaration so the mutations above can call it without
   *  a use-before-definition warning; its body only reads the consts it closes
   *  over, which are initialized by the time a mutation actually runs. */
  function guardQualityFor(targetIds: string[]) {
    if (targetIds.length === 0) return
    const chosen = downloads.videoQuality
    if (sources && hasAuthoritativeSources(sources) && !availability.includes(chosen)) {
      throw new Error(m.anime.quality.unavailable(videoQualityLabel(chosen, m)))
    }
    if (chosen === 'original') return
    const sets = targetIds.map((id) => determinedAt(episodeIds.findIndex((e) => e.id === id)))
    if (sets.every((s) => s !== null)) {
      const first = sets[0]!
      const intersection = first.filter((q) => sets.every((s) => s!.includes(q)))
      if (!intersection.includes(chosen)) {
        throw new Error(m.anime.quality.unavailable(videoQualityLabel(chosen, m)))
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
                    <EpisodeAvailability
                      query={episodeQualities[index]}
                      mChecking={m.anime.quality.checking}
                      mUndetermined={m.anime.quality.undetermined}
                      mRetry={m.anime.quality.retry}
                      qLabel={(q) => videoQualityLabel(q, m)}
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

/** One episode's per-probing availability state: "Checking…" while the query
 *  runs, then the verified concrete qualities as chips, or "Couldn't
 *  determine" + a retry. Never a fake list — a null/unknown query shows only
 *  the undetermined line. `original` is always possible (every source serves
 *  its own best) so it's never listed here. */
function EpisodeAvailability({
  query,
  mChecking,
  mUndetermined,
  mRetry,
  qLabel,
}: {
  query: {
    data?: { providers: AnimeSource[] } | undefined
    isError: boolean
    isPending: boolean
    refetch: () => void
  }
  mChecking: string
  mUndetermined: string
  mRetry: string
  qLabel: (quality: VideoQuality) => string
}) {
  if (query.isPending) {
    return (
      <span className="mt-0.5 flex animate-breathe items-center gap-1.5 text-micro text-ink-400">
        <LoaderCircle className="size-3 animate-spin" />
        {mChecking}
      </span>
    )
  }
  if (query.isError || !query.data?.providers) {
    return (
      <button
        type="button"
        onClick={() => query.refetch()}
        className="mt-0.5 flex items-center gap-1.5 text-micro text-ink-400 transition hover:text-lime-flash"
      >
        <span>{mUndetermined}</span>
        <span className="font-medium underline-offset-2 underline">{mRetry}</span>
      </button>
    )
  }
  const chips = availableQualities(query.data.providers).filter((q) => q !== 'original')
  if (chips.length === 0) return null // authoritatively none served — no chips
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-1 text-micro text-ink-400">
      {chips.map((q) => (
        <span
          key={q}
          className="rounded-ctl border border-ink-700 bg-ink-800 px-1.5 py-0.5 font-medium tabular-nums text-ink-300"
        >
          {qLabel(q)}
        </span>
      ))}
    </span>
  )
}
