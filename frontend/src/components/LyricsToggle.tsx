import { Captions, CaptionsOff } from 'lucide-react'
import clsx from 'clsx'
import { useDownloads } from '../lib/downloads'
import { useMessages } from '../lib/i18n'

/** Switch for whether new downloads embed lyrics in their tags.
 *
 *  A global preference like quality: it applies to every job started after
 *  it changes and is remembered across sessions; jobs already running keep
 *  the choice they started with.
 *
 *  A radiogroup of two segments rather than one chip that swaps its glyph, so
 *  it matches the quality and language strips beside it: both options are on
 *  screen and the lit one is the answer. A single chip made you read an icon
 *  and know which of the pair meant on — and a lone iOS-style switch would
 *  need colour to say "on", which in this header can only be lime, the one
 *  thing lime is not allowed to mark here (see QualityPicker).
 */
export function LyricsToggle({ className }: { className?: string }) {
  const { embedLyrics, setEmbedLyrics } = useDownloads()
  const m = useMessages()

  const options = [
    { on: true, Icon: Captions, hint: m.lyrics.embed.on },
    { on: false, Icon: CaptionsOff, hint: m.lyrics.embed.off },
  ]

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span className="text-micro font-semibold text-ink-400">{m.lyrics.embed.label}</span>
      <div
        role="radiogroup"
        aria-label={m.lyrics.embed.action}
        className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
      >
        {options.map(({ on, Icon, hint }) => {
          const active = on === embedLyrics
          return (
            <button
              key={String(on)}
              role="radio"
              aria-checked={active}
              // The glyph is the whole segment, so the sentence about what the
              // choice does is what a screen reader and a hover both get.
              aria-label={hint}
              title={hint}
              onClick={() => setEmbedLyrics(on)}
              className={clsx(
                // Matches a quality segment's box exactly, so the strips line
                // up on the same baseline at both pointer sizes.
                'grid size-[26px] place-items-center rounded-ctl transition duration-200 active:scale-95',
                'pointer-coarse:size-8',
                active ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100',
              )}
            >
              <Icon className="size-4" />
            </button>
          )
        })}
      </div>
    </div>
  )
}
