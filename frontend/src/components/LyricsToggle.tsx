import clsx from 'clsx'
import { useDownloads } from '../lib/downloads'
import { MusicNoteIcon } from '../icons'

/** Switch for whether new downloads embed lyrics in their tags.
 *
 *  A global preference like quality: it applies to every job started after
 *  it changes and is remembered across sessions; jobs already running keep
 *  the choice they started with.
 *
 *  The lime knob is the "on" state, not a primary action — same rule as the
 *  quality picker next to it. The switch is forced `ltr` so the knob's
 *  translate never depends on the page's direction. The music note carries
 *  the meaning on phones, where the text label is hidden.
 */
export function LyricsToggle({ className }: { className?: string }) {
  const { embedLyrics, setEmbedLyrics } = useDownloads()
  const on = embedLyrics

  return (
    <div className={clsx('flex items-center gap-2', className)}>
      <span className={clsx('hidden sm:inline text-micro font-semibold', on ? 'text-ink-300' : 'text-ink-400')}>
        متن آهنگ
      </span>
      <button
        role="switch"
        dir="ltr"
        aria-checked={on}
        aria-label="ذخیره‌ی متن آهنگ داخل فایل دانلودی"
        onClick={() => setEmbedLyrics(!on)}
        title={on ? 'متن آهنگ داخل فایل دانلودی ذخیره میشه' : 'متن آهنگ داخل فایل دانلودی ذخیره نمیشه'}
        className={clsx(
          'flex h-6 w-11 shrink-0 items-center rounded-full border p-0.5 transition-colors duration-200',
          'pointer-coarse:h-7 pointer-coarse:w-13',
          on ? 'border-lime-flash/40 bg-ink-700' : 'border-ink-700 bg-ink-800',
        )}
      >
        <span
          className={clsx(
            'size-5 rounded-full shadow transition-transform duration-200 pointer-coarse:size-6',
            on ? 'translate-x-5 bg-lime-flash pointer-coarse:translate-x-6' : 'translate-x-0 bg-ink-400',
          )}
        >
          <span className="grid h-full place-items-center text-ink-400">
            <MusicNoteIcon className={clsx('size-2.5', on && 'text-lime-ink')} />
          </span>
        </span>
      </button>
    </div>
  )
}
