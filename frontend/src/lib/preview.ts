import { useSyncExternalStore } from 'react'

/** One shared Audio element for 30-second previews — starting a preview
 *  stops the previous one, like any music UI. */

let currentId: string | null = null
let loading = false
let audio: HTMLAudioElement | null = null

const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function ensureAudio(): HTMLAudioElement {
  if (!audio) {
    audio = new Audio()
    audio.addEventListener('ended', () => {
      currentId = null
      emit()
    })
    audio.addEventListener('error', () => {
      currentId = null
      loading = false
      emit()
    })
    audio.addEventListener('waiting', () => {
      loading = true
      emit()
    })
    audio.addEventListener('playing', () => {
      loading = false
      emit()
    })
  }
  return audio
}

export function togglePreview(id: string, url: string) {
  const el = ensureAudio()
  if (currentId === id) {
    el.pause()
    currentId = null
    emit()
    return
  }
  currentId = id
  loading = true
  emit()
  // Resume a paused clip instead of restarting it; anything else reloads.
  if (el.src !== url) el.src = url
  el.play().catch(() => {
    currentId = null
    loading = false
    emit()
  })
}

const subscribe = (listener: () => void) => {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function usePlayingPreviewId(): string | null {
  return useSyncExternalStore(subscribe, () => currentId)
}

export function usePreviewLoading(): boolean {
  return useSyncExternalStore(subscribe, () => loading)
}
