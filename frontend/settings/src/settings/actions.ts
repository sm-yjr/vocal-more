import { postSettingsMessage } from "@/settings/python-bridge"
import type { SettingsStore } from "@/settings/store"

let syncTimer: number | null = null

function queueFormSync(store: SettingsStore): void {
  if (syncTimer !== null) window.clearTimeout(syncTimer)
  syncTimer = window.setTimeout(() => {
    syncTimer = null
    postSettingsMessage({
      action: "syncFormState",
      state: store.collectFormState(),
    })
  }, 120)
}

export function setConfig(
  store: SettingsStore,
  key: string,
  value: unknown,
): void {
  store.setConfig(key, value)
  postSettingsMessage({ action: "setConfig", key, value })
  queueFormSync(store)
}

export function setAsrModel(
  store: SettingsStore,
  model: string,
  backend: string,
): void {
  store.setConfig("asr.model", model)
  store.setConfig("asr.backend", backend)
  postSettingsMessage({ action: "setAsrModel", model, backend })
  queueFormSync(store)
}

export function setActiveHotkeys(
  store: SettingsStore,
  hotkeys: string[],
): void {
  store.setConfig("hotkey.active_hotkeys", hotkeys)
  postSettingsMessage({ action: "setActiveHotkeys", hotkeys })
  queueFormSync(store)
}

export function setDevice(
  store: SettingsStore,
  device: string | null,
): void {
  store.setConfig("audio.input_device", device)
  postSettingsMessage({ action: "setDevice", device })
  queueFormSync(store)
}

export function setCustomKey(
  store: SettingsStore,
  customKey: unknown,
): void {
  store.setConfig("hotkey.custom_key", customKey)
  postSettingsMessage({
    action: "setConfig",
    key: "hotkey.custom_key",
    value: customKey,
  })
  queueFormSync(store)
}

export function setCustomKeys(
  store: SettingsStore,
  customKeys: unknown[],
): void {
  store.setConfig("hotkey.custom_keys", customKeys)
  postSettingsMessage({
    action: "setConfig",
    key: "hotkey.custom_keys",
    value: customKeys,
  })
  queueFormSync(store)
}

export function sendAction(
  action: string,
  data: Record<string, unknown> = {},
): void {
  postSettingsMessage({ action, ...data })
}
