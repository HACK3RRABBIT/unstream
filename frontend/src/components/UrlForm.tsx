import { useEffect, useState, type RefObject } from 'react'
import { ArrowLeft, LoaderCircle, Search } from 'lucide-react'
import clsx from 'clsx'
import { isCatalogUrl } from '../lib/api'

interface Props {
  loading: boolean
  onSubmit: (input: string) => void
  className?: string
  inputRef?: RefObject<HTMLInputElement | null>
  /** Bumped by the parent each time a keyboard shortcut focuses this form —
   *  the value is meaningless, the change is the signal. */
  focusPulse?: number
}

export function UrlForm({ loading, onSubmit, className, inputRef, focusPulse = 0 }: Props) {
  const [input, setInput] = useState('')
  const isUrl = isCatalogUrl(input)

  const [pulsing, setPulsing] = useState(false)
  useEffect(() => {
    if (focusPulse === 0) return
    setPulsing(true)
    const timer = setTimeout(() => setPulsing(false), 600)
    return () => clearTimeout(timer)
  }, [focusPulse])

  return (
    <form
      className={clsx(
        'relative flex items-center gap-2 rounded-panel border border-ink-700 bg-ink-900 p-2 ps-4',
        'transition duration-200 focus-within:border-lime-flash/60',
        'focus-within:ring-4 focus-within:ring-lime-flash/10',
        className,
      )}
      onSubmit={(e) => {
        e.preventDefault()
        if (input.trim() && !loading) onSubmit(input.trim())
      }}
    >
      {/* The pulse gets its own element: `animate-*` sets the `animation`
          shorthand, so sharing a node with the caller's entrance animation
          would clobber it — and replay it once the pulse finished. */}
      {pulsing && (
        <span
          aria-hidden
          className="pointer-events-none absolute -inset-px animate-focus-pulse rounded-panel"
        />
      )}
      <Search
        className={clsx(
          'size-4 shrink-0 transition-colors duration-200',
          input.trim() ? 'text-lime-flash' : 'text-ink-400',
        )}
      />
      <input
        ref={inputRef}
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        dir="auto"
        placeholder="آهنگ، آلبوم یا آرتیست جستجو کن — یا لینک اسپاتیفای / دیزر / یوتیوب / ساندکلاد رو پیست کن"
        spellCheck={false}
        autoFocus
        className="min-w-0 flex-1 bg-transparent text-body text-ink-100 placeholder:text-ink-600 focus:outline-none"
      />
      <button
        type="submit"
        disabled={loading || !input.trim()}
        className={clsx(
          'group flex shrink-0 items-center gap-1.5 rounded-btn px-4 py-2.5 text-mini font-medium',
          'bg-lime-flash text-lime-ink transition duration-200 active:scale-[0.98]',
          'hover:bg-lime-soft',
          'disabled:cursor-not-allowed disabled:opacity-40',
        )}
      >
        {loading ? (
          <>
            <LoaderCircle className="size-4 animate-spin" />
            {isUrl ? 'در حال باز کردن…' : 'در حال جستجو…'}
          </>
        ) : (
          <>
            {isUrl ? 'باز کن' : 'جستجو'}
            <ArrowLeft className="size-4 transition-transform duration-200 group-hover:-translate-x-0.5" />
          </>
        )}
      </button>
    </form>
  )
}
