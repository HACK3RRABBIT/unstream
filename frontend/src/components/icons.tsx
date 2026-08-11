/** Icons not in lucide, drawn to its rules so they sit beside it: 24×24,
 *  stroked at 1.5 with round caps, `currentColor` throughout. Inlined because
 *  an `<img>` cannot inherit text colour.
 *
 *  Source: reicon-icons, outline set. */

interface IconProps {
  className?: string
}

/** Opens the settings sheet (reicon `outline/setting-42`). Sliders rather
 *  than a hamburger: it opens preferences, not navigation. */
export function SettingsIcon({ className }: IconProps) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M22 6.5H16" />
      <path d="M6 6.5H2" />
      <path d="M10 10C11.933 10 13.5 8.433 13.5 6.5C13.5 4.567 11.933 3 10 3C8.067 3 6.5 4.567 6.5 6.5C6.5 8.433 8.067 10 10 10Z" />
      <path d="M22 17.5H18" />
      <path d="M8 17.5H2" />
      <path d="M14 21C15.933 21 17.5 19.433 17.5 17.5C17.5 15.567 15.933 14 14 14C12.067 14 10.5 15.567 10.5 17.5C10.5 19.433 12.067 21 14 21Z" />
    </svg>
  )
}
