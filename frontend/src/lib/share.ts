/** Whether this browser can share actual files, not just links. Chrome on
 *  desktop implements share() but not file sharing, so the probe needs a real
 *  File to be worth anything. */
export function canShareFiles(): boolean {
  if (typeof navigator === 'undefined' || !navigator.canShare || !navigator.share) return false
  try {
    return navigator.canShare({ files: [new File([], 'probe.mp3', { type: 'audio/mpeg' })] })
  } catch {
    return false
  }
}

export type ShareOutcome = 'shared' | 'cancelled' | 'unsupported'

/** Fetch a finished track and offer it to the OS share sheet.
 *
 *  Exists for iOS Safari, which regularly ignores `<a download>` and plays the
 *  audio instead, leaving no way to keep the file. 'unsupported' means the
 *  caller should fall back to its download link. */
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
    const name = (err as Error)?.name
    if (name === 'AbortError') return 'cancelled' // the user tapped cancel
    // Safari decided the fetch above outlived the tap that started it.
    if (name === 'NotAllowedError') return 'unsupported'
    throw err
  }
}

export function shareFilename(title: string, ext: string): string {
  const safe = title.replace(/[/\\?%*:|"<>]/g, '-').trim() || 'track'
  return `${safe}.${ext}`
}
