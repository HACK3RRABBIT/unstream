import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Languages } from 'lucide-react'
import clsx from 'clsx'
import { LOCALES, useLocale, useMessages } from '../lib/i18n'

/** Language switch for the header, and the only control that can change the
 *  document's direction.
 *
 *  A custom listbox rather than a native `<select>`: the OS popup renders in
 *  its own light-ish chrome, anchored to the platform's rules rather than the
 *  control, which reads as a foreign object dropped on a dark page. This owns
 *  its own panel — same surface, border and shadow as the downloads panel —
 *  anchored under the trigger's trailing edge, which also means it stays put
 *  when the whole layout mirrors.
 *
 *  Kept deliberately neutral: lime marks the primary action on a surface, and
 *  in the header that is never this. Each language is listed in its own script,
 *  so the option you want is readable in a language you don't have selected.
 */
export function LanguagePicker({ className }: { className?: string }) {
  const { locale, setLocale } = useLocale()
  const m = useMessages()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement | null>(null)
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([])

  const active = LOCALES.findIndex((l) => l.code === locale)

  // Opening moves focus into the list, so the keyboard can drive it and a
  // blur-free close (Escape, outside click) has somewhere to return to.
  useEffect(() => {
    if (open) optionRefs.current[active]?.focus()
  }, [open, active])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false)
    }
    // Capture + stopPropagation: App's global Escape navigates back, which is
    // not what "close this menu" means while the menu is up.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
        return
      }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const focused = optionRefs.current.findIndex((el) => el === document.activeElement)
        const step = e.key === 'ArrowDown' ? 1 : -1
        const next = (focused + step + LOCALES.length) % LOCALES.length
        optionRefs.current[next]?.focus()
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKey, true)
    }
  }, [open])

  const choose = (code: string) => {
    setLocale(code)
    setOpen(false)
  }

  return (
    <div ref={root} className={clsx('relative', className)}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={m.language.picker}
        title={m.language.label}
        className={clsx(
          'flex items-center gap-1.5 rounded-ctl border border-ink-800 bg-ink-900 px-2 py-1',
          'text-micro font-medium text-ink-300 transition duration-200 active:scale-95',
          'hover:text-ink-100 pointer-coarse:py-2',
          open && 'text-ink-100',
        )}
      >
        <Languages className="size-3.5 shrink-0 text-ink-400" />
        <span lang={LOCALES[active]?.code}>{LOCALES[active]?.label}</span>
        <ChevronDown
          className={clsx(
            'size-3 shrink-0 text-ink-400 transition-transform',
            open && 'rotate-180',
          )}
        />
      </button>

      {open && (
        // Pinned to the trigger's trailing edge, so it mirrors with the layout
        // instead of drifting off the header in RTL.
        <ul
          role="listbox"
          aria-label={m.language.picker}
          className={clsx(
            'absolute end-0 top-full z-50 mt-1.5 min-w-36 animate-fade-up',
            'rounded-btn border border-ink-700 bg-ink-900 p-1 shadow-2xl shadow-black/60',
          )}
        >
          {LOCALES.map((option, i) => {
            const selected = option.code === locale
            return (
              <li key={option.code} role="option" aria-selected={selected}>
                <button
                  ref={(el) => {
                    optionRefs.current[i] = el
                  }}
                  onClick={() => choose(option.code)}
                  className={clsx(
                    'flex w-full items-center gap-2 rounded-ctl px-2 py-1.5 text-start text-mini transition',
                    'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-lime-flash',
                    selected
                      ? 'bg-ink-800 font-medium text-ink-100'
                      : 'text-ink-300 hover:bg-ink-800/60 hover:text-ink-100',
                  )}
                >
                  {/* The tick keeps its slot when absent, so the labels align. */}
                  <Check
                    className={clsx(
                      'size-3.5 shrink-0',
                      selected ? 'text-lime-flash' : 'text-transparent',
                    )}
                  />
                  {/* `lang`/`dir` go on the label, not the row: on the row they
                      would flip the flex axis per option, putting one entry's
                      tick on the opposite side from the next one's and leaving
                      the labels unable to line up. The row keeps the menu's own
                      direction; only the word inside is shaped by its script. */}
                  <span lang={option.code} dir={option.dir}>
                    {option.label}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
