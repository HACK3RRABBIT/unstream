import clsx from 'clsx'
import { type SubtitleLanguage } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useMessages } from '../lib/i18n'

/** The four subtitle choices — English, Persian (translated from English),
 *  English + Persian, or none. Persian is generated on the backend by
 *  translating the English track when the release has none of its own. */
const PRESETS: { key: 'eng' | 'fas' | 'both' | 'none'; langs: SubtitleLanguage[] }[] = [
  { key: 'eng', langs: ['eng'] },
  { key: 'fas', langs: ['fas'] },
  { key: 'both', langs: ['eng', 'fas'] },
  { key: 'none', langs: [] },
]

export function SubtitlePicker({ className }: { className?: string }) {
  const { subtitleLanguages, setSubtitleLanguages } = useDownloads()
  const m = useMessages()
  const isActive = (langs: SubtitleLanguage[]) =>
    langs.length === subtitleLanguages.length && langs.every((l) => subtitleLanguages.includes(l))

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span className="text-micro font-semibold text-ink-400">{m.anime.subtitles.label}</span>
      <div
        role="radiogroup"
        aria-label={m.anime.subtitles.label}
        className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
      >
        {PRESETS.map((preset) => {
          const active = isActive(preset.langs)
          return (
            <button
              key={preset.key}
              role="radio"
              aria-checked={active}
              title={m.anime.subtitles.presets.hints[preset.key]}
              onClick={() => setSubtitleLanguages(preset.langs)}
              className={clsx(
                'rounded-ctl px-2 py-1 text-micro font-medium transition duration-200 active:scale-95 pointer-coarse:px-2.5 pointer-coarse:py-2',
                active ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100',
              )}
            >
              {m.anime.subtitles.presets.labels[preset.key]}
            </button>
          )
        })}
      </div>
    </div>
  )
}
