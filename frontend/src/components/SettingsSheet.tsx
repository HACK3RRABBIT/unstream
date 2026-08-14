import { X } from 'lucide-react'
import { useMessages } from '../lib/i18n'
import { LyricsToggle } from './LyricsToggle'
import { QualityPicker } from './QualityPicker'
import { VideoQualityPicker } from './VideoQualityPicker'
import { SubtitlePicker } from './SubtitlePicker'
import { LanguagePicker } from './LanguagePicker'
import { Sheet } from './Sheet'
import type { AppTab } from './TabSwitch'

/** The header's preferences, on the layouts too narrow to hold them.
 *
 *  The same components the wide header uses, not phone-only copies of them —
 *  otherwise the next quality option would have to be added twice. Which set
 *  shows follows the active tab: music gets lyrics + audio quality, anime
 *  gets video quality + subtitles. `w-full justify-between` is all they need
 *  to become rows. */
export function SettingsSheet({ tab, onClose }: { tab: AppTab; onClose: () => void }) {
  const m = useMessages()

  return (
    <Sheet label={m.settings.label} onClose={onClose} width="sm:w-[min(24rem,calc(100vw-2.5rem))]">
      <header className="flex shrink-0 items-center gap-3 border-b border-ink-800 px-5 py-3.5">
        <h2 className="flex-1 font-display text-body font-bold text-ink-100">{m.settings.label}</h2>
        <button
          onClick={onClose}
          aria-label={m.settings.close}
          className="tap-target grid size-9 shrink-0 place-items-center rounded-btn text-ink-400 transition duration-200 hover:bg-ink-800 hover:text-ink-100 active:scale-90"
        >
          <X className="size-4" />
        </button>
      </header>

      <div className="min-h-0 flex-auto overflow-y-auto overscroll-contain px-5 py-2">
        {/* `flex-wrap`: the quality strip at coarse-pointer sizes nearly fills
            a 320px phone beside its label, and a row that cannot fit must drop
            to a second line rather than scroll sideways. */}
        <div className="divide-y divide-ink-800">
          {tab === 'music' ? (
            <>
              <div className="py-4">
                <LyricsToggle className="w-full flex-wrap justify-between gap-y-3" />
              </div>
              <div className="py-4">
                <QualityPicker className="w-full flex-wrap justify-between gap-y-3" />
              </div>
            </>
          ) : (
            <>
              <div className="py-4">
                <VideoQualityPicker className="w-full flex-wrap justify-between gap-y-3" />
              </div>
              <div className="py-4">
                <SubtitlePicker className="w-full flex-wrap justify-between gap-y-3" />
              </div>
            </>
          )}
          <div className="py-4">
            <LanguagePicker className="w-full flex-wrap justify-between gap-y-3" />
          </div>
        </div>
      </div>
    </Sheet>
  )
}
