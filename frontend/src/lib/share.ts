/** Handing a finished file to the OS share sheet.
 *
 *  `<a download>` is the right control on a desktop and an unreliable one on
 *  iOS Safari, which frequently ignores the attribute and opens the audio in a
 *  player instead — leaving no obvious way to keep the file. navigator.share
 *  with a File drops it straight into Files, Telegram or WhatsApp, which is
 *  what someone on a phone wanted from a download app in the first place.
 */

/** Whether this browser can share actual files (not just links). Chrome on
 *  desktop implements share() but not file sharing, so probing `canShare`
 *  with a real File is the only honest test — and it needs a File, so the
 *  check is done against a throwaway one. */
export function canShareFiles(): boolean {
  if (typeof navigator === 'undefined' || !navigator.canShare || !navigator.share) return false
  try {
    return navigator.canShare({ files: [new File([], 'probe.mp3', { type: 'audio/mpeg' })] })
  } catch {
    return false
  }
}

export type ShareOutcome = 'shared' | 'cancelled' | 'unsupported'

/** Fetch a finished track and offer it to the share sheet.
 *
 *  Returns 'unsupported' when the caller should fall back to the download
 *  link, and 'cancelled' when the user dismissed the sheet — which is a
 *  normal outcome, not a failure to report. Anything else throws. */
export async function shareTrackFile(
  url: string,
  filename: string,
  mimeType = 'audio/mpeg',
): Promise<ShareOutcome> {
  if (!canShareFiles()) return 'unsupported'

  const response = await fetch(url)
  if (!response.ok) throw new Error(String(response.status))
  const blob = await response.blob()
  const file = new File([blob], filename, { type: blob.type || mimeType })
  if (!navigator.canShare({ files: [file] })) return 'unsupported'

  try {
    await navigator.share({ files: [file] })
    return 'shared'
  } catch (err) {
    // AbortError is the user tapping "cancel". NotAllowedError is Safari
    // deciding the fetch above outlived the tap that started it — the
    // download link is still there, so treat it as "use the other route".
    const name = (err as Error)?.name
    if (name === 'AbortError') return 'cancelled'
    if (name === 'NotAllowedError') return 'unsupported'
    throw err
  }
}

/** A filename the OS will accept, built from the track title we already show. */
export function shareFilename(title: string, ext: string): string {
  const safe = title.replace(/[/\\?%*:|"<>]/g, '-').trim() || 'track'
  return `${safe}.${ext}`
}
