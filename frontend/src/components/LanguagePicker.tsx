import clsx from 'clsx'
import { LOCALES, useLocale, useMessages } from '../lib/i18n'

/** Language switch for the header, and the only control that can change the
 *  document's direction.
 *
 *  A radiogroup rather than a two-state switch, even though there are two
 *  languages today: adding one is meant to be a line in `LOCALES` plus a
 *  dictionary, and a control that flips between exactly two would be a third
 *  thing to rewrite. At two options it reads as a toggle anyway.
 *
 *  Each language is labelled in its own script, so the option you want stays
 *  legible in a language you do not have selected — the whole job of this
 *  control. Kept neutral: lime marks the primary action on a surface, and in
 *  the header that is never this.
 */
export function LanguagePicker({ className }: { className?: string }) {
  const { locale, setLocale } = useLocale()
  const m = useMessages()

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span className="text-micro font-semibold text-ink-400">{m.language.label}</span>
      <div
        role="radiogroup"
        aria-label={m.language.picker}
        className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
      >
        {LOCALES.map((option) => {
          const active = option.code === locale
          return (
            <button
              key={option.code}
              role="radio"
              aria-checked={active}
              // The abbreviation is what fits; the full endonym is what it
              // means, so the label a screen reader and a hover both get is
              // the unabbreviated one.
              aria-label={option.label}
              title={option.label}
              lang={option.code}
              onClick={() => setLocale(option.code)}
              className={clsx(
                // Matches a quality segment's box exactly, so the two strips
                // sit on one baseline at both pointer sizes.
                'rounded-ctl px-2 py-1 text-micro font-medium transition duration-200 active:scale-95 pointer-coarse:px-2.5 pointer-coarse:py-2',
                active ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100',
              )}
            >
              {option.short}
            </button>
          )
        })}
      </div>
    </div>
  )
}
