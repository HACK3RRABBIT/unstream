import { useEffect, useState, type CSSProperties } from 'react'
import { Clapperboard, SearchX } from 'lucide-react'
import clsx from 'clsx'
import { translateAnime, type AnimeSearchPage, type AnimeSearchResult } from '../lib/api'
import { faNumerals, useLocale, useMessages, useStartAlign } from '../lib/i18n'
import type { Messages } from '../lib/locales/en'

interface Props {
  query: string
  results: AnimeSearchPage
  onPick: (result: AnimeSearchResult) => void
}

/** The fact-line under an anime title: "TV · 220 episodes · 2002", or
 *  "TV · airing · 6 of 12 episodes" for a show that's currently releasing. */
const facts = (item: AnimeSearchResult, m: Messages): string =>
  [
    item.season_count > 1 ? m.anime.seasons : item.format,
    item.status === 'RELEASING' && item.available_episodes > 0
      ? m.anime.airingAvailable(item.available_episodes, item.episodes)
      : item.episodes > 0
        ? m.anime.episodes(item.episodes)
        : null,
    item.year,
  ]
    .filter(Boolean)
    .join(' · ')

/** A summary block under the title, translated to the UI's language when that
 *  isn't English (AniList only stores an English synopsis). Fetched once per
 *  card and cached in memory; a failed translation keeps the English text. */
function Summary({ description }: { description: string | null }) {
  const { locale } = useLocale()
  const [text, setText] = useState<string | null>(null)

  useEffect(() => {
    if (!description) {
      setText(null)
      return
    }
    if (locale === 'en') {
      setText(description)
      return
    }
    let alive = true
    translateAnime(description, locale)
      .then((t) => {
        if (alive) setText(t)
      })
      .catch(() => {
        if (alive) setText(description) // fall back to English
      })
    return () => {
      alive = false
    }
  }, [description, locale])

  if (!text) return null
  return (
    <p dir="auto" className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-ink-400">
      {text}
    </p>
  )
}

function ResultCard({
  item,
  index,
  onPick,
}: {
  item: AnimeSearchResult
  index: number
  onPick: (result: AnimeSearchResult) => void
}) {
  const m = useMessages()
  const startAlign = useStartAlign()
  return (
    <li style={{ '--i': index } as CSSProperties} className="break-inside-avoid">
      <button
        onClick={() => onPick(item)}
        className={clsx('group w-full text-start focus-visible:outline-none', startAlign)}
        dir="auto"
      >
        <div className="relative overflow-hidden rounded-btn bg-ink-800 ring-1 ring-ink-700/60 transition duration-300 group-hover:-translate-y-1 group-hover:ring-ink-600 group-focus-visible:ring-2 group-focus-visible:ring-lime-flash aspect-[3/4]">
          {item.cover_url ? (
            <img
              src={item.cover_url}
              alt=""
              loading="lazy"
              className="size-full object-cover transition-transform duration-500 group-hover:scale-110"
            />
          ) : (
            <div className="grid size-full place-items-center">
              <Clapperboard className="size-6 text-ink-400" />
            </div>
          )}
          {item.format !== 'TV' && (
            <span className="absolute top-2 start-2 rounded-full bg-ink-950/80 px-2 py-0.5 text-micro font-semibold text-lime-flash backdrop-blur">
              {item.format}
            </span>
          )}
        </div>
        {/* Full title, not truncated — a long anime name must stay readable.
            `break-words` lets a very long word wrap instead of overflowing. */}
        <p
          className={clsx(
            'mt-2 text-mini font-medium leading-snug text-ink-100 break-words transition-colors group-hover:text-lime-flash',
            faNumerals(item.title),
          )}
        >
          {item.title}
        </p>
        <p className="mt-0.5 text-xs text-ink-400">{facts(item, m)}</p>
        <Summary description={item.description} />
      </button>
    </li>
  )
}

function Section({
  title,
  icon: Icon,
  items,
  onPick,
}: {
  title: string
  icon: typeof Clapperboard
  items: AnimeSearchResult[]
  onPick: Props['onPick']
}) {
  const m = useMessages()
  return (
    <div>
      <h3 className="flex items-center gap-2 px-5 pt-5 pb-2 text-micro font-semibold text-ink-400">
        <Icon className="size-3.5" />
        {title}
        <span className="tabular-nums text-ink-500">{m.app.num(items.length)}</span>
      </h3>
      <ul className="stagger grid grid-cols-2 gap-x-4 gap-y-5 px-5 pb-2 sm:grid-cols-3 md:grid-cols-4">
        {items.map((item, i) => (
          <ResultCard key={item.id} item={item} index={i} onPick={onPick} />
        ))}
      </ul>
    </div>
  )
}

/** Search results on the Anime tab — series grouped into franchises and
 *  movies kept apart, so the two are never confused. Picking a card opens
 *  the franchise (series) or the film (movie). */
export function AnimeSearchResults({ query, results, onPick }: Props) {
  const m = useMessages()

  if (results.series.length === 0 && results.movies.length === 0) {
    return (
      <section className="rounded-panel border border-ink-700 bg-ink-900 px-5 py-14 text-center">
        <SearchX className="mx-auto size-7 text-ink-600" />
        <p className="mt-3 text-body font-medium text-ink-100">
          {m.anime.noResultsBefore} <span dir="auto">{m.app.quote(query)}</span>{' '}
          {m.anime.noResultsAfter}
        </p>
        <p className="mx-auto mt-1.5 max-w-xs text-mini text-ink-400">{m.anime.searchLabel}</p>
      </section>
    )
  }

  return (
    <section className="overflow-hidden rounded-panel border border-ink-700 bg-ink-900">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pt-4">
        <h2 className="truncate text-body font-medium text-ink-100">
          {m.results.resultsFor}{' '}
          <span className="text-lime-flash" dir="auto">
            {query}
          </span>
        </h2>
        <p aria-live="polite" className="text-mini text-ink-400 tabular-nums">
          {m.app.num(results.series.length + results.movies.length)}
        </p>
      </div>

      {results.series.length > 0 && (
        <Section
          title={m.anime.series}
          icon={Clapperboard}
          items={results.series}
          onPick={onPick}
        />
      )}
      {results.movies.length > 0 && (
        <Section
          title={m.anime.movies}
          icon={Clapperboard}
          items={results.movies}
          onPick={onPick}
        />
      )}
    </section>
  )
}
