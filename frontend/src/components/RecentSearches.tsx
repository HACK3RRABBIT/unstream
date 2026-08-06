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
      <ul className="mt-2.5 flex flex-wrap gap-2">
        {items.map((item) => (
          <li key={item.input}>
            <button
              onClick={() => onPick(item.input)}
              dir="auto"
              title={item.input}
              className="flex max-w-[15rem] items-center gap-1.5 rounded-btn border border-ink-700 bg-ink-900 px-3 py-2 text-mini text-ink-300 transition duration-200 hover:border-ink-600 hover:text-ink-100 active:scale-[0.98]"
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
