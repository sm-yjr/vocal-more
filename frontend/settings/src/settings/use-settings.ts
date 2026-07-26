import { useSyncExternalStore } from "react"

import type { SettingsStore } from "@/settings/store"

export function useSettings(store: SettingsStore) {
  return useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  )
}
