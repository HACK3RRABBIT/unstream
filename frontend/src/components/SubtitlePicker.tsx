import clsx from 'clsx'
import { SUBTITLE_LANGUAGES } from '../lib/api'
import { useDownloads } from '../lib/downloads'
import { useMessages } from '../lib/i18n'

/** Subtitle language for anime episodes — English, Persian (when the release
 *  has it), or none. Works like the music lyrics toggle: a global preference,
 *  persisted, applied to every anime job. The track is muxed into the mp4 so
 *  a player can toggle it. */
export function SubtitlePicker({ className }: { className?: string }) {
  const { subtitleLanguage, setSubtitleLanguage } = useDownloads()
  const m = useMessages()

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span className="text-micro font-semibold text-ink-400">{m.anime.subtitles.label}</span>
      <div
        role="radiogroup"
        aria-label={m.anime.subtitles.label}
        className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
      >
        {SUBTITLE_LANGUAGES.map((lang) => {
          const active = lang === subtitleLanguage
          return (
            <button
              key={lang}
              role="radio"
              aria-checked={active}
              title={m.anime.subtitles.hints[lang]}
              onClick={() => setSubtitleLanguage(lang)}
              className={clsx(
                'rounded-ctl px-2 py-1 text-micro font-medium transition duration-200 active:scale-95 pointer-coarse:px-2.5 pointer-coarse:py-2',
                active ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100',
              )}
            >
              {m.anime.subtitles.languages[lang]}
            </button>
          )
        })}
      </div>
    </div>
  )
}
