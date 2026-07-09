import { useMemo, useState } from 'react'
import { ChevronRight, Disc3, ListMusic, MicVocal, Music2 } from 'lucide-react'
import clsx from 'clsx'
import type { ResultKind, SearchResult, Source } from '../lib/api'
import { QuickDownload } from './QuickDownload'

interface Props {
  results: SearchResult[]
  onPick: (result: SearchResult) => void
}

const SOURCE_META: Record<Source, { label: string; dot: string }> = {
  deezer: { label: 'Deezer', dot: 'bg-[#a238ff]' },
  spotify: { label: 'Spotify', dot: 'bg-[#1db954]' },
  itunes: { label: 'Apple', dot: 'bg-[#fa5c73]' },
  youtube: { label: 'YouTube', dot: 'bg-[#ff4e45]' },
  soundcloud: { label: 'SoundCloud', dot: 'bg-[#ff7700]' },
}

const KINDS = [
  { kind: 'track', label: 'Songs', icon: Music2, preview: 5 },
  { kind: 'artist', label: 'Artists', icon: MicVocal, preview: 6 },
  { kind: 'album', label: 'Albums', icon: Disc3, preview: 4 },
  { kind: 'playlist', label: 'Playlists', icon: ListMusic, preview: 4 },
] as const

type Tab = 'all' | ResultKind

export function SourceBadge({ source }: { source: Source }) {
  const meta = SOURCE_META[source]
  if (!meta) return null
  return (
    <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-ink-700 px-2 py-0.5 text-[10px] font-medium tracking-wide text-ink-400">
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
    <ul>
      {items.map((item) => (
        <li
          key={`${item.source}-${item.id}`}
          className="flex items-center gap-3 pr-5 transition-colors hover:bg-ink-800/60 focus-within:bg-ink-800/60"
        >
          <button
            onClick={() => onPick(item)}
            className="flex min-w-0 flex-1 items-center gap-4 py-2.5 pl-5 text-left focus-visible:outline-none"
          >
            {item.cover_url ? (
              <img
                src={item.cover_url}
                alt=""
                loading="lazy"
                className="size-10 shrink-0 rounded-md object-cover"
              />
            ) : (
              <div className="grid size-10 shrink-0 place-items-center rounded-md bg-ink-800">
                <Music2 className="size-4 text-ink-400" />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-[15px] font-medium text-ink-100">
                {item.name}
              </p>
              {item.subtitle && (
                <p className="truncate text-[13px] text-ink-400">{item.subtitle}</p>
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
        'grid gap-x-4 gap-y-5 px-5 pb-4',
        round
          ? 'grid-cols-3 sm:grid-cols-4 md:grid-cols-6'
          : 'grid-cols-2 sm:grid-cols-3 md:grid-cols-4',
      )}
    >
      {items.map((item) => (
        <li key={`${item.source}-${item.id}`}>
          <button
            onClick={() => onPick(item)}
            className="group w-full text-left focus-visible:outline-none"
          >
            <div
              className={clsx(
                'relative overflow-hidden bg-ink-800 transition group-hover:brightness-110 group-focus-visible:ring-2 group-focus-visible:ring-lime-flash',
                round ? 'aspect-square rounded-full' : 'aspect-square rounded-xl',
              )}
            >
              {item.cover_url ? (
                <img
                  src={item.cover_url}
                  alt=""
                  loading="lazy"
                  className="size-full object-cover"
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
                'mt-2 truncate text-[13.5px] font-medium text-ink-100',
                round && 'text-center',
              )}
            >
              {item.name}
            </p>
            {item.subtitle && (
              <p
                className={clsx(
                  'truncate text-xs text-ink-400',
                  round && 'text-center',
                )}
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

export function SearchResults({ results, onPick }: Props) {
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
      <section className="rounded-2xl border border-ink-700 bg-ink-900 px-5 py-10 text-center">
        <p className="text-sm text-ink-300">No results — try different keywords.</p>
      </section>
    )
  }

  const sections = KINDS.filter(({ kind }) => (grouped.get(kind) ?? []).length > 0)

  return (
    <section className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-900">
      <nav
        aria-label="Result filters"
        className="flex flex-wrap items-center gap-1.5 border-b border-ink-800 px-4 py-3"
      >
        {(
          [
            { kind: 'all', label: 'All', count: results.length },
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
            className={clsx(
              'flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-medium transition',
              tab === kind
                ? 'bg-lime-flash text-lime-ink'
                : 'text-ink-300 hover:bg-ink-800 hover:text-ink-100',
            )}
          >
            {label}
            <span
              className={clsx(
                'text-[11px] tabular-nums',
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
                <h3 className="flex items-center gap-2 text-[11px] font-semibold tracking-[0.14em] text-ink-400 uppercase">
                  <Icon className="size-3.5" />
                  {label}
                </h3>
                {hidden > 0 && (
                  <button
                    onClick={() => setTab(kind)}
                    className="flex items-center gap-0.5 text-xs font-medium text-lime-flash transition hover:text-lime-soft"
                  >
                    Show all {items.length}
                    <ChevronRight className="size-3.5" />
                  </button>
                )}
              </div>
              {kind === 'track' || kind === 'playlist' ? (
                <div className="pb-2">
                  <TrackList
                    items={shown}
                    downloadable={kind === 'track'}
                    onPick={onPick}
                  />
                </div>
              ) : (
                <CardGrid items={shown} round={kind === 'artist'} onPick={onPick} />
              )}
            </div>
          )
        })}
    </section>
  )
}
