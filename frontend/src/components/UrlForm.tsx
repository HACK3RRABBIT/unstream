import { useEffect, useRef, useState, type RefObject } from 'react'
import { LoaderCircle, Search } from 'lucide-react'
import clsx from 'clsx'
import { isCatalogUrl } from '../lib/api'
import { useDirectional, useLocale, useMessages } from '../lib/i18n'

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
  const m = useMessages()
  const { dir } = useLocale()
  const { Forward, forwardNudge } = useDirectional()

  // Focus on arrival is a desktop courtesy and a mobile ambush — on a phone it
  // summons the keyboard over the hero. `autoFocus` has no way to ask, so it's
  // done here instead.
  const ownInputRef = useRef<HTMLInputElement | null>(null)
  const inputEl = inputRef ?? ownInputRef
  useEffect(() => {
    if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
      inputEl.current?.focus()
    }
    // Mount-only: later focus goes through `focusPulse` and the / shortcut.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
        ref={inputEl}
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        dir="auto"
        placeholder={m.form.placeholder}
        spellCheck={false}
        className={clsx(
          'min-w-0 flex-1 bg-transparent text-body text-ink-100 placeholder:text-ink-600 focus:outline-none',
          // `dir="auto"` shapes the query by its own script — necessary, or a
          // Latin query in an RTL field comes out reversed. But it also decides
          // where the line starts, which would drag a query to the far side of
          // the form. So alignment is pinned to the *UI's* side physically:
          // `text-start` would resolve against dir="auto" and defeat the point.
          // An empty `dir="auto"` field falls back to LTR, so RTL locales also
          // need the placeholder's own direction stated.
          dir === 'rtl' ? 'text-right placeholder-shown:[direction:rtl]' : 'text-left',
        )}
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
            {isUrl ? m.form.opening : m.form.searching}
          </>
        ) : (
          <>
            {isUrl ? m.form.open : m.form.search}
            <Forward className={clsx('size-4 transition-transform duration-200', forwardNudge)} />
          </>
        )}
      </button>
    </form>
  )
}
