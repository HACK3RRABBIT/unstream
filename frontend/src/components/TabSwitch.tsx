import { AudioLines, Clapperboard } from 'lucide-react'
import clsx from 'clsx'
import { useMessages } from '../lib/i18n'

export type AppTab = 'music' | 'anime'

/** Music / Anime switch in the header. Navigation, not a preference — it
 *  stays visible on phones (unlike the sheet-bound preferences), and switching
 *  resets the view stack to the tab's landing. */
export function TabSwitch({ tab, onChange }: { tab: AppTab; onChange: (tab: AppTab) => void }) {
  const m = useMessages()
  return (
    <div
      role="tablist"
      aria-label=""
      className="flex items-center gap-0.5 rounded-ctl border border-ink-800 bg-ink-900 p-0.5"
    >
      {(
        [
          { key: 'music', label: m.tabs.music, icon: AudioLines },
          { key: 'anime', label: m.tabs.anime, icon: Clapperboard },
        ] as const
      ).map(({ key, label, icon: Icon }) => {
        const active = tab === key
        return (
          <button
            key={key}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(key)}
            className={clsx(
              'flex items-center gap-1.5 rounded-ctl px-3 py-1.5 text-mini font-medium transition duration-200 active:scale-[0.97] pointer-coarse:py-2',
              active ? 'bg-ink-700 text-ink-100' : 'text-ink-400 hover:text-ink-100',
            )}
          >
            <Icon className="size-4" strokeWidth={2} />
            {label}
          </button>
        )
      })}
    </div>
  )
}
