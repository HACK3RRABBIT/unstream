import { useMemo, useState, type CSSProperties } from 'react'
import {
  ChevronLeft,
  Disc3,
  ListMusic,
  LoaderCircle,
  MicVocal,
  Music2,
  SearchX,
} from 'lucide-react'
import clsx from 'clsx'
import type { ResultKind, SearchResult, Source } from '../lib/api'
import { QuickDownload } from './QuickDownload'

interface Props {
  query: string
  results: SearchResult[]
  hasMore: boolean
  loadingMore: boolean
  onLoadMore: () => void
  onPick: (result: SearchResult) => void
}

const SOURCE_META: Record<Source, { label: string; dot: string }> = {
  deezer: { label: 'Deezer', dot: 'bg-[#a238ff]' },
  itunes: { label: 'Apple', dot: 'bg-[#fa5c73]' },
  youtube: { label: 'YouTube', dot: 'bg-[#ff4e45]' },
  soundcloud: { label: 'SoundCloud', dot: 'bg-[#ff7700]' },
}

/** `noun` is the singular — Persian counts and «…های بیشتر» need it, and it
 *  can't be derived from the plural label. */
const KINDS = [
  { kind: 'track', label: 'آهنگ‌ها', noun: 'آهنگ', icon: Music2, preview: 6 },
  { kind: 'artist', label: 'آرتیست‌ها', noun: 'آرتیست', icon: MicVocal, preview: 6 },
  { kind: 'album', label: 'آلبوم‌ها', noun: 'آلبوم', icon: Disc3, preview: 8 },
  { kind: 'playlist', label: 'پلی‌لیست‌ها', noun: 'پلی‌لیست', icon: ListMusic, preview: 4 },
] as const

type Tab = 'all' | ResultKind

/** Index handed to the CSS stagger (`.stagger > *` reads --i). */
const stagger = (i: number) => ({ '--i': i }) as CSSProperties

export function SourceBadge({ source }: { source: Source }) {
  const meta = SOURCE_META[source]
  if (!meta) return null
  return (
    <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-ink-700 px-2 py-0.5 text-micro font-medium tracking-wide text-ink-400">
      <span className={clsx('size-1.5 rounded-full', meta.dot)} />
      {meta.label}
    </span>
  )
}

function TrackList({
  items,
  downloadable,
  onPick,
}: {
  items: SearchResult[]
  downloadable?: boolean
  onPick: Props['onPick']
}) {
  return (
    <ul className="stagger">
      {items.map((item, i) => (
        <li
          key={item.dedup_key}
          style={stagger(i)}
          className="group flex items-center gap-3 pe-5 transition-colors hover:bg-ink-800/60 focus-within:bg-ink-800/60"
        >
          <button
            onClick={() => onPick(item)}
            className="flex min-w-0 flex-1 items-center gap-4 py-2.5 ps-5 text-start focus-visible:outline-none"
          >
            {item.cover_url ? (
              <img
                src={item.cover_url}
                alt=""
                loading="lazy"
                className="size-10 shrink-0 rounded-ctl object-cover transition-transform duration-300 group-hover:scale-105"
              />
            ) : (
              <div className="grid size-10 shrink-0 place-items-center rounded-ctl bg-ink-800">
                <Music2 className="size-4 text-ink-400" />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-body font-medium text-ink-100" dir="auto">
                {item.name}
              </p>
              {item.subtitle && (
                <p className="truncate text-mini text-ink-400" dir="auto">
                  {item.subtitle}
                </p>
              )}
            </div>
            <SourceBadge source={item.source} />
          </button>
          {downloadable && <QuickDownload result={item} />}
        </li>
      ))}
    </ul>
  )
}

function CardGrid({
  items,
  round,
  onPick,
}: {
  items: SearchResult[]
  round?: boolean
  onPick: Props['onPick']
}) {
  return (
    <ul
      className={clsx(
        'stagger grid gap-x-4 gap-y-5 px-5 pb-4',
        round
          ? 'grid-cols-3 sm:grid-cols-4 md:grid-cols-6'
          : 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4',
      )}
    >
      {items.map((item, i) => (
        <li key={item.dedup_key} style={stagger(i)}>
          <button
            onClick={() => onPick(item)}
            className="group w-full text-start focus-visible:outline-none"
          >
            <div
              className={clsx(
                'relative overflow-hidden bg-ink-800 ring-1 ring-ink-700/60 transition duration-300',
                'group-hover:-translate-y-1 group-hover:ring-ink-600',
                'group-focus-visible:ring-2 group-focus-visible:ring-lime-flash',
                round ? 'aspect-square rounded-full' : 'aspect-square rounded-btn',
              )}
            >
              {item.cover_url ? (
                <img
                  src={item.cover_url}
                  alt=""
                  loading="lazy"
                  className="size-full object-cover transition-transform duration-500 group-hover:scale-110"
                />
              ) : (
                <div className="grid size-full place-items-center">
                  {round ? (
                    <MicVocal className="size-6 text-ink-400" />
                  ) : (
                    <Disc3 className="size-6 text-ink-400" />
                  )}
                </div>
              )}
            </div>
            <p
              className={clsx(
                'mt-2 truncate text-mini font-medium text-ink-100 transition-colors group-hover:text-lime-flash',
                round && 'text-center',
              )}
              dir="auto"
            >
              {item.name}
            </p>
            {item.subtitle && (
              <p
                className={clsx('truncate text-xs text-ink-400', round && 'text-center')}
                dir="auto"
              >
                {item.subtitle}
              </p>
            )}
          </button>
        </li>
      ))}
    </ul>
  )
}

export function SearchResults({ query, results, hasMore, loadingMore, onLoadMore, onPick }: Props) {
  const [tab, setTab] = useState<Tab>('all')

  const grouped = useMemo(() => {
    const map = new Map<ResultKind, SearchResult[]>()
    for (const r of results) {
      const list = map.get(r.kind) ?? []
      list.push(r)
      map.set(r.kind, list)
    }
    return map
  }, [results])

  if (results.length === 0) {
    return (
      <section className="rounded-panel border border-ink-700 bg-ink-900 px-5 py-14 text-center">
        <SearchX className="mx-auto size-7 text-ink-600" />
        <p className="mt-3 text-body font-medium text-ink-100">
          چیزی برای <span dir="auto">«{query}»</span> پیدا نشد
        </p>
        <p className="mx-auto mt-1.5 max-w-xs text-mini text-ink-400">
          فقط اسم آرتیست رو امتحان کن، املا رو یه چک بکن، یا به‌جاش لینک آلبوم رو پیست کن.
        </p>
      </section>
    )
  }

  const sections = KINDS.filter(({ kind }) => (grouped.get(kind) ?? []).length > 0)
  // Null on the "All" tab — that view doesn't page.
  const activeKind = KINDS.find(({ kind }) => kind === tab) ?? null

  return (
    <section className="overflow-hidden rounded-panel border border-ink-700 bg-ink-900">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pt-4">
        <h2 className="truncate text-body font-medium text-ink-100">
          نتایج برای{' '}
          <span className="text-lime-flash" dir="auto">
            {query}
          </span>
        </h2>
        <p aria-live="polite" className="text-mini text-ink-400 tabular-nums">
          {results.length} نتیجه{hasMore && ' تا الان'}
        </p>
      </div>

      <nav
        aria-label="فیلتر نتایج"
        className="flex flex-wrap items-center gap-1.5 border-b border-ink-800 px-4 py-3"
      >
        {(
          [
            { kind: 'all', label: 'همه', count: results.length },
            ...sections.map(({ kind, label }) => ({
              kind,
              label,
              count: grouped.get(kind)!.length,
            })),
          ] as { kind: Tab; label: string; count: number }[]
        ).map(({ kind, label, count }) => (
          <button
            key={kind}
            onClick={() => setTab(kind)}
            aria-pressed={tab === kind}
            className={clsx(
              'flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-mini font-medium transition duration-200 active:scale-[0.97]',
              tab === kind
                ? 'bg-lime-flash text-lime-ink'
                : 'text-ink-300 hover:bg-ink-800 hover:text-ink-100',
            )}
          >
            {label}
            <span
              className={clsx(
                'text-micro tabular-nums',
                tab === kind ? 'text-lime-ink/70' : 'text-ink-400',
              )}
            >
              {count}
            </span>
          </button>
        ))}
      </nav>

      {sections
        .filter(({ kind }) => tab === 'all' || tab === kind)
        .map(({ kind, label, icon: Icon, preview }) => {
          const items = grouped.get(kind)!
          const shown = tab === 'all' ? items.slice(0, preview) : items
          const hidden = items.length - shown.length
          return (
            <div key={kind} className="border-b border-ink-800 last:border-b-0">
              <div className="flex items-baseline justify-between px-5 pt-4 pb-2">
                <h3 className="flex items-center gap-2 text-micro font-semibold text-ink-400">
                  <Icon className="size-3.5" />
                  {label}
                </h3>
                {hidden > 0 && (
                  <button
                    onClick={() => setTab(kind)}
                    className="group flex items-center gap-0.5 text-xs font-medium text-lime-flash transition hover:text-lime-soft"
                  >
                    نمایش همه‌ی {items.length} تا
                    <ChevronLeft className="size-3.5 transition-transform duration-200 group-hover:-translate-x-0.5" />
                  </button>
                )}
              </div>
              {kind === 'track' || kind === 'playlist' ? (
                <div className="pb-2">
                  <TrackList items={shown} downloadable={kind === 'track'} onPick={onPick} />
                </div>
              ) : (
                <CardGrid items={shown} round={kind === 'artist'} onPick={onPick} />
              )}
            </div>
          )
        })}

      {/* Paging is deliberately manual: one page is a fan-out across four
          providers, so it only runs when the user asks. "All" is a summary of
          what's already loaded — you go deeper from inside a category. */}
      {activeKind && (
        <div className="flex items-center justify-center px-5 py-4">
          {loadingMore ? (
            <span className="flex items-center gap-2 text-mini text-ink-400">
              <LoaderCircle className="size-3.5 animate-spin text-lime-flash" />
              در حال جستجوی عمیق‌تر…
            </span>
          ) : hasMore ? (
            <button
              onClick={onLoadMore}
              className="rounded-full border border-ink-700 px-4 py-1.5 text-mini font-medium text-ink-300 transition duration-200 hover:border-ink-600 hover:text-ink-100 active:scale-[0.97]"
            >
              {activeKind.noun}‌های بیشتر
            </button>
          ) : (
            <span className="text-mini text-ink-400">
              همین بود — {grouped.get(activeKind.kind)?.length ?? 0} {activeKind.noun} از همه‌ی
              منبع‌ها.
            </span>
          )}
        </div>
      )}
    </section>
  )
}
