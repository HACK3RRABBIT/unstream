import type { CSSProperties } from 'react'
import { Clapperboard, ListVideo } from 'lucide-react'
import clsx from 'clsx'
import type { AnimeDetail, AnimeSeason } from '../lib/api'
import { faNumerals, useDirectional, useMessages, useStartAlign } from '../lib/i18n'

interface Props {
  anime: AnimeDetail
  /** Open a season's episode list. */
  onOpenSeason: (anime: AnimeDetail, season: AnimeSeason) => void
}

/** The franchise page: the seed anime's header plus every season as a row.
 *  Phase 1 shows seasons and episode counts (the download surface is Phase 2),
 *  so clicking a season currently has nowhere to go — it is wired here so the
 *  Phase 2 season view plugs in without touching navigation again. */
export function AnimeView({ anime, onOpenSeason }: Props) {
  const m = useMessages()
  const startAlign = useStartAlign()
  const { ForwardChevron, forwardNudge } = useDirectional()

  return (
    <section className="overflow-hidden rounded-panel border border-ink-700 bg-ink-900">
      <div className="flex flex-wrap items-center gap-5 border-b border-ink-800 p-5 sm:p-6">
        {anime.cover_url ? (
          <img
            src={anime.cover_url}
            alt=""
            className="size-20 rounded-btn object-cover ring-1 ring-ink-700 sm:size-24"
          />
        ) : (
          <div className="grid size-20 place-items-center rounded-btn bg-ink-800 ring-1 ring-ink-700 sm:size-24">
            <Clapperboard className="size-8 text-ink-400" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <span className="text-micro font-semibold text-lime-flash">{m.anime.seasons}</span>
          <h2
            className={clsx(
              'mt-1 truncate font-display text-2xl font-bold',
              faNumerals(anime.title),
            )}
            dir="auto"
          >
            {anime.title}
          </h2>
          <p className="mt-1 text-mini text-ink-300">
            {m.anime.episodes(anime.seasons.reduce((n, s) => n + s.episodes, 0))}
          </p>
        </div>
      </div>

      {anime.description && (
        <div className="border-b border-ink-800 px-5 py-4">
          <p dir="auto" className="line-clamp-3 text-mini leading-relaxed text-ink-300">
            {anime.description.replace(/<br\s*\/?>/gi, ' ').replace(/<[^>]+>/g, '')}
          </p>
        </div>
      )}

      <div className="px-5 pt-4 pb-1">
        <h3 className="flex items-center gap-2 text-micro font-semibold text-ink-400">
          <ListVideo className="size-3.5" />
          {m.anime.seasons}
        </h3>
      </div>

      <ul className="stagger pb-2">
        {anime.seasons.map((season, i) => (
          <li
            key={season.media_id}
            style={{ '--i': i } as CSSProperties}
            className="group flex items-center gap-3 pe-5 transition-colors hover:bg-ink-800/60 focus-within:bg-ink-800/60"
          >
            <button
              onClick={() => onOpenSeason(anime, season)}
              className="flex min-w-0 flex-1 items-center gap-4 py-2.5 ps-5 text-start focus-visible:outline-none"
            >
              {season.cover_url ? (
                <img
                  src={season.cover_url}
                  alt=""
                  loading="lazy"
                  className="size-10 shrink-0 rounded-ctl object-cover"
                />
              ) : (
                <div className="grid size-10 shrink-0 place-items-center rounded-ctl bg-ink-800">
                  <Clapperboard className="size-4 text-ink-400" />
                </div>
              )}
              <div className={clsx('min-w-0 flex-1', startAlign)} dir="auto">
                <p
                  className={clsx(
                    'truncate text-body font-medium text-ink-100',
                    faNumerals(season.title),
                  )}
                >
                  {season.title}
                </p>
                <p className="truncate text-mini text-ink-400">
                  {m.anime.season(season.season)} ·{' '}
                  {season.status === 'RELEASING' && season.available_episodes > 0
                    ? m.anime.airingAvailable(season.available_episodes, season.episodes)
                    : m.anime.episodes(season.episodes)}
                </p>
              </div>
            </button>
            <ForwardChevron
              className={clsx(
                'size-4 shrink-0 text-ink-600 transition-transform duration-200',
                forwardNudge,
              )}
            />
          </li>
        ))}
      </ul>
    </section>
  )
}
