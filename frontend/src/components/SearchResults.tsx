import { Disc3, ListMusic, Music2 } from 'lucide-react'
import type { SearchResult } from '../lib/api'

interface Props {
  results: SearchResult[]
  onPick: (result: SearchResult) => void
}

const SECTIONS = [
  { kind: 'track', label: 'Songs', icon: Music2 },
  { kind: 'album', label: 'Albums', icon: Disc3 },
  { kind: 'playlist', label: 'Playlists', icon: ListMusic },
] as const

export function SearchResults({ results, onPick }: Props) {
  if (results.length === 0) {
    return (
      <section className="rounded-2xl border border-ink-700 bg-ink-900 px-5 py-10 text-center">
        <p className="text-sm text-ink-300">No results — try different keywords.</p>
      </section>
    )
  }

  return (
    <section className="overflow-hidden rounded-2xl border border-ink-700 bg-ink-900">
      {SECTIONS.map(({ kind, label, icon: Icon }) => {
        const items = results.filter((r) => r.kind === kind)
        if (items.length === 0) return null
        return (
          <div key={kind} className="border-b border-ink-800 last:border-b-0">
            <h3 className="flex items-center gap-2 px-5 pt-4 pb-1 text-[11px] font-semibold tracking-[0.14em] text-ink-400 uppercase">
              <Icon className="size-3.5" />
              {label}
            </h3>
            <ul className="pb-2">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    onClick={() => onPick(item)}
                    className="flex w-full items-center gap-4 px-5 py-2.5 text-left transition-colors hover:bg-ink-800/60 focus-visible:bg-ink-800/60 focus-visible:outline-none"
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
                        <Icon className="size-4 text-ink-400" />
                      </div>
                    )}
                    <div className="min-w-0">
                      <p className="truncate text-[15px] font-medium text-ink-100">
                        {item.name}
                      </p>
                      {item.subtitle && (
                        <p className="truncate text-[13px] text-ink-400">
                          {item.subtitle}
                        </p>
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </section>
  )
}
