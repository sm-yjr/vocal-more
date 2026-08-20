import { postSettingsMessage } from "@/settings/python-bridge"
import type { SettingsStore } from "@/settings/store"

const previewTimers = new Map<string, number>()

export function setConfig(
  store: SettingsStore,
  key: string,
  value: unknown
): void {
  store.setConfig(key, value)
  postSettingsMessage({ action: "setConfig", key, value })
}

export function previewConfig(
  store: SettingsStore,
  key: string,
  value: unknown
): void {
  store.setConfig(key, value)
  const pending = previewTimers.get(key)
  if (pending !== undefined) window.clearTimeout(pending)
  previewTimers.set(
    key,
    window.setTimeout(() => {
      previewTimers.delete(key)
      postSettingsMessage({ action: "previewConfig", key, value })
    }, 50)
  )
}

export function commitPreviewConfig(
  store: SettingsStore,
  key: string,
  value: unknown
): void {
  const pending = previewTimers.get(key)
  if (pending !== undefined) {
    window.clearTimeout(pending)
    previewTimers.delete(key)
  }
  setConfig(store, key, value)
}

export function setAsrModel(
  store: SettingsStore,
  model: string,
  backend: string
): void {
  store.setConfig("asr.model", model)
  store.setConfig("asr.backend", backend)
  postSettingsMessage({ action: "setAsrModel", model, backend })
}

export function setActiveHotkeys(
  store: SettingsStore,
  hotkeys: string[]
): void {
  store.setConfig("hotkey.active_hotkeys", hotkeys)
  postSettingsMessage({ action: "setActiveHotkeys", hotkeys })
}

export function setDevice(store: SettingsStore, device: string | null): void {
  store.setConfig("audio.input_device", device)
  postSettingsMessage({ action: "setDevice", device })
}

export function setCustomKey(store: SettingsStore, customKey: unknown): void {
  store.setConfig("hotkey.custom_key", customKey)
  postSettingsMessage({
    action: "setConfig",
    key: "hotkey.custom_key",
    value: customKey,
  })
}

export function setCustomKeys(
  store: SettingsStore,
  customKeys: unknown[]
): void {
  store.setConfig("hotkey.custom_keys", customKeys)
  postSettingsMessage({
    action: "setConfig",
    key: "hotkey.custom_keys",
    value: customKeys,
  })
}

export function setCommandKey(
  store: SettingsStore,
  commandKey: unknown,
): void {
  store.setConfig("hotkey.command_key", commandKey)
  postSettingsMessage({
    action: "setConfig",
    key: "hotkey.command_key",
    value: commandKey,
  })
}

export function sendAction(
  action: string,
  data: Record<string, unknown> = {}
): void {
  postSettingsMessage({ action, ...data })
}
