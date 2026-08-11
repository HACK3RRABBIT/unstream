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
 *  Built as a chip in the quality picker's shell rather than as a switch,
 *  for two reasons. The header's control vocabulary is already "a label, then
 *  a bordered strip of chips", and a lone iOS-style switch reads as a
 *  different app. And a switch's "on" needs colour to say so, which in this
 *  header can only be lime — the one thing lime is not allowed to mark here
 *  (see QualityPicker). The two caption glyphs carry the state instead, so it
 *  survives being the only control on a phone, where the label is hidden.
 */
export function LyricsToggle({ className }: { className?: string }) {
  const { embedLyrics, setEmbedLyrics } = useDownloads()
  const m = useMessages()

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span className="hidden text-micro font-semibold text-ink-400 sm:inline">
        {m.lyrics.embed.label}
      </span>
      <div className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5">
        <button
          aria-pressed={embedLyrics}
          aria-label={m.lyrics.embed.action}
          title={embedLyrics ? m.lyrics.embed.on : m.lyrics.embed.off}
          onClick={() => setEmbedLyrics(!embedLyrics)}
          className={clsx(
            // Matches a quality segment's box exactly, so the two strips line
            // up on the same baseline at both pointer sizes.
            'grid size-[26px] place-items-center rounded-ctl transition duration-200 active:scale-95',
            'pointer-coarse:size-8',
            embedLyrics ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100',
          )}
        >
          {/* Keyed so the glyph swap re-runs `pop` — the chip is small and the
              two icons are close cousins; without it the change is easy to
              miss on a tap. */}
          {embedLyrics ? (
            <Captions key="on" className="size-4 animate-pop" />
          ) : (
            <CaptionsOff key="off" className="size-4 animate-pop" />
          )}
        </button>
      </div>
    </div>
  )
}
