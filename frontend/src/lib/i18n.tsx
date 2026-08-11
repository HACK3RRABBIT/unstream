import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, type LucideIcon } from 'lucide-react'
import en, { type Messages } from './locales/en'
import fa from './locales/fa'

export type Dir = 'ltr' | 'rtl'

export interface LocaleDefinition {
  /** BCP 47 tag; goes straight into `<html lang>`. */
  code: string
  /** Endonym — a language is listed the way its own speakers write it. */
  label: string
  dir: Dir
  messages: Messages
}

/** Every language the UI ships. Adding one is this list plus a dictionary
 *  file: copy `locales/en.ts`, translate it, register it here. Nothing else
 *  in the app enumerates locales, and `Messages` makes a half-translated
 *  dictionary a compile error rather than a run-time blank. */
export const LOCALES: LocaleDefinition[] = [
  { code: 'en', label: 'English', dir: 'ltr', messages: en },
  { code: 'fa', label: 'فارسی', dir: 'rtl', messages: fa },
]

/** Physical alignment of the UI's own start edge — `text-right` under RTL.
 *
 *  For blocks that carry `dir="auto"`: content there decides direction, which
 *  is what keeps a mixed-script title shaped correctly *and* truncating at its
 *  own end. But direction also drives `text-align: start`, so an English title
 *  in an RTL row would align to the row's end — the edge that moves with the
 *  source badge, leaving a ragged column. Alignment is therefore stated
 *  physically, from the UI's direction rather than the text's, which is the one
 *  thing about the pair that must not vary row to row. */
export function useStartAlign(): string {
  return useLocale().dir === 'rtl' ? 'text-right' : 'text-left'
}

/** Persian/Arabic script, used to judge which numerals a *piece of text* wants. */
const PERSIAN_SCRIPT = /[؀-ۿ]/

/** Class for text whose digits should follow its own script rather than the
 *  UI's language — provider content: track titles, artist and album names,
 *  subtitles, a pasted query.
 *
 *  A Persian title reads wrong with Latin digits ("24 ساعت"), and an English
 *  one reads wrong with Persian ("۲۴K Magic"). Neither the document's language
 *  nor its direction can decide that, because the two mix freely inside one
 *  list — so the string decides, the same way `dir="auto"` lets it decide
 *  direction. Belongs on exactly the nodes that already carry `dir="auto"`.
 *
 *  Returns undefined for Latin text: the default family already has it right,
 *  and no class means no second font to download. */
export const faNumerals = (text: string): string | undefined =>
  PERSIAN_SCRIPT.test(text) ? 'font-fa' : undefined

/** Last resort if nothing is configured: Farsi, the language the interface was
 *  written in (docs/DESIGN.md#farsi-only). Deployments override it with
 *  `UNSTREAM_DEFAULT_LOCALE`; this is only what you get having set nothing. */
const FALLBACK_LOCALE = 'fa'

const STORAGE_KEY = 'unstream:locale'

declare global {
  interface Window {
    /** Written by `/config.js`, which the frontend container generates from
     *  its environment at start-up. Absent if that file didn't load. */
    __UNSTREAM_CONFIG__?: { defaultLocale?: string }
  }
}

const known = (code: string | null | undefined): string | null =>
  code && LOCALES.some((l) => l.code === code) ? code : null

/** Retargets an existing meta tag. Deliberately does not create one: index.html
 *  stays the single place the document's head is declared, so a tag that isn't
 *  there is a missing line in the HTML rather than something to paper over. */
function setMeta(attr: 'name' | 'property', key: string, content: string) {
  const el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`)
  if (el) el.content = content
}

/** Rewrites everything about the document that carries a language: the two
 *  `<html>` attributes, the title, the description, the social cards, the
 *  PWA manifest and the structured-data block.
 *
 *  The head is not just decoration here — it is what a shared link and an
 *  install prompt show, so leaving it at the build's language would have an
 *  English reader sharing a Farsi title. What a non-JS crawler sees is still
 *  index.html's static copy; that one is the default language's by definition. */
function applyDocumentLocale(definition: LocaleDefinition) {
  const { code, dir, messages } = definition
  const { meta, app } = messages

  const root = document.documentElement
  root.lang = code
  root.dir = dir

  document.title = meta.title
  setMeta('name', 'description', meta.description)
  setMeta('name', 'apple-mobile-web-app-title', app.name)
  setMeta('property', 'og:site_name', app.name)
  setMeta('property', 'og:locale', meta.ogLocale)
  setMeta('property', 'og:title', meta.title)
  setMeta('property', 'og:description', meta.description)
  setMeta('name', 'twitter:title', meta.title)
  setMeta('name', 'twitter:description', meta.description)

  const manifest = document.head.querySelector<HTMLLinkElement>('link[rel="manifest"]')
  if (manifest) manifest.href = meta.manifest

  const jsonLd = document.head.querySelector<HTMLScriptElement>(
    'script[type="application/ld+json"]',
  )
  if (jsonLd) {
    try {
      const data: unknown = JSON.parse(jsonLd.textContent ?? '')
      jsonLd.textContent = JSON.stringify(
        { ...(data as object), name: app.name, description: meta.description, inLanguage: code },
        null,
        2,
      )
    } catch {
      // Unparseable — the build's own block is better than a broken one.
    }
  }
}

/** The language a first-time visitor gets.
 *
 *  Runtime env first, then build-time env, then the fallback. Runtime is the
 *  one that matters: the published images are prebuilt, so a `VITE_`-style
 *  build variable could only be changed by rebuilding them — `/config.js` is
 *  written from the environment when the container starts, which is what makes
 *  `UNSTREAM_DEFAULT_LOCALE` in a `.env` actually take effect.
 *
 *  Deliberately *not* the browser's `Accept-Language`: a configured default is
 *  a decision the person running the instance made, and having it silently
 *  lose to a visitor's OS setting would make it untestable. Visitors override
 *  it with the picker, and that choice is what gets remembered. */
export function defaultLocale(): string {
  return (
    known(window.__UNSTREAM_CONFIG__?.defaultLocale) ??
    known(import.meta.env.VITE_DEFAULT_LOCALE) ??
    FALLBACK_LOCALE
  )
}

const definitionFor = (code: string | null | undefined): LocaleDefinition =>
  LOCALES.find((l) => l.code === code) ?? LOCALES.find((l) => l.code === defaultLocale())!

/** The visitor's own saved choice, or the instance's configured default. */
function initialLocale(): string {
  try {
    const saved = known(localStorage.getItem(STORAGE_KEY))
    if (saved) return saved
  } catch {
    // private mode / storage disabled — the configured default still applies
  }
  return defaultLocale()
}

interface LocaleContextValue {
  locale: string
  setLocale: (code: string) => void
  dir: Dir
  m: Messages
}

const LocaleContext = createContext<LocaleContextValue | null>(null)

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState(initialLocale)
  const definition = definitionFor(locale)

  // `<html lang>` and `<html dir>` are the source of truth for text shaping,
  // logical properties (ps-/pe-/ms-/start-/end-) and the sweep animation's
  // direction, so the document has to carry the choice — a class wouldn't do.
  // Layout effect, not effect: this runs before paint, so a locale whose
  // direction differs from the build's default never flashes the wrong way.
  useLayoutEffect(() => {
    applyDocumentLocale(definition)
  }, [definition])

  const setLocale = useCallback((code: string) => {
    setLocaleState(definitionFor(code).code)
    try {
      localStorage.setItem(STORAGE_KEY, code)
    } catch {
      // Not persisting is survivable; the session still honours the choice.
    }
  }, [])

  const value = useMemo(
    () => ({ locale: definition.code, setLocale, dir: definition.dir, m: definition.messages }),
    [definition, setLocale],
  )

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
}

export function useLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext)
  if (!ctx) throw new Error('useLocale must be used inside <LocaleProvider>')
  return ctx
}

/** The active dictionary on its own — the common case. */
export const useMessages = (): Messages => useLocale().m

/** Icons and hover nudges that mean "onward" and "back". Lucide's arrows are
 *  drawn, not mirrored by the browser, and a physical `translate-x` can't
 *  follow the inline axis either — so both are chosen here rather than
 *  hard-coded at each call site. */
export function useDirectional(): {
  Back: LucideIcon
  Forward: LucideIcon
  ForwardChevron: LucideIcon
  backNudge: string
  forwardNudge: string
} {
  const { dir } = useLocale()
  const rtl = dir === 'rtl'
  return {
    Back: rtl ? ArrowRight : ArrowLeft,
    Forward: rtl ? ArrowLeft : ArrowRight,
    ForwardChevron: rtl ? ChevronLeft : ChevronRight,
    backNudge: rtl ? 'group-hover:translate-x-0.5' : 'group-hover:-translate-x-0.5',
    forwardNudge: rtl ? 'group-hover:-translate-x-0.5' : 'group-hover:translate-x-0.5',
  }
}

/** A backend-composed subtitle ("by X · 40 tracks"), rendered in the active
 *  language. Done here at render time rather than when the response lands, so
 *  switching language re-translates results already on screen. */
export function localizeSubtitle(subtitle: string, m: Messages): string {
  return subtitle
    .split(' · ')
    .map((part) => m.subtitle(part))
    .join(' · ')
}
