import { Link2, Search, X } from 'lucide-react'
import type { RecentSearch } from '../lib/recent'

interface Props {
  items: RecentSearch[]
  onPick: (input: string) => void
  onClear: () => void
}

/** The last few things the user asked for, as chips under the hero — which
 *  otherwise offers nothing to click. Worth most on a phone, where retyping an
 *  album name is the expensive part. */
export function RecentSearches({ items, onPick, onClear }: Props) {
  if (items.length === 0) return null

  return (
    <div className="mt-6 animate-fade-up [animation-delay:280ms]">
      <div className="flex items-center gap-2">
        <h2 className="text-micro font-semibold text-ink-400">آخرین جستجوها</h2>
        <button
          onClick={onClear}
          className="tap-target grid size-5 place-items-center rounded text-ink-600 transition hover:text-ink-300"
          aria-label="پاک کردن آخرین جستجوها"
          title="پاک کردن آخرین جستجوها"
        >
          <X className="size-3.5" />
        </button>
      </div>
      {/* One scrolling row on a phone, wrapped rows from `sm` up. Six chips
          that wrap on a 390px screen are five stacked rows pushing the form
          and everything under it down the page; sideways, they cost one.
          The scrollbar is hidden — the chips clipping at the edge is the
          affordance, and a visible bar under two of them reads as chrome. */}
      <ul className="mt-2.5 flex snap-x snap-mandatory gap-2 overflow-x-auto pb-1 [scrollbar-width:none] sm:flex-wrap sm:overflow-visible sm:pb-0 [&::-webkit-scrollbar]:hidden">
        {items.map((item) => (
          <li key={item.input} className="shrink-0 snap-start sm:shrink">
            <button
              onClick={() => onPick(item.input)}
              dir="auto"
              title={item.input}
              className="tap-target flex max-w-[68vw] items-center gap-1.5 rounded-btn border border-ink-700 bg-ink-900 px-3 py-2 text-mini text-ink-300 transition duration-200 hover:border-ink-600 hover:text-ink-100 active:scale-[0.98] sm:max-w-[15rem]"
            >
              {item.isLink ? (
                <Link2 className="size-3.5 shrink-0 text-ink-600" />
              ) : (
                <Search className="size-3.5 shrink-0 text-ink-600" />
              )}
              <span className="truncate">{item.isLink ? linkLabel(item.input) : item.input}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** A pasted URL is unreadable at chip width; its host and path are not. */
function linkLabel(url: string): string {
  try {
    const { hostname, pathname } = new URL(url)
    return `${hostname.replace(/^www\./, '')}${pathname}`
  } catch {
    return url
  }
}
