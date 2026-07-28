import { beforeEach, describe, expect, it, vi } from "vitest"

import { commitPreviewConfig, previewConfig } from "@/settings/actions"
import { postSettingsMessage } from "@/settings/python-bridge"
import type { SettingsStore } from "@/settings/store"

vi.mock("@/settings/python-bridge", () => ({
  postSettingsMessage: vi.fn(),
}))

function makeStore(): SettingsStore {
  return {
    setConfig: vi.fn(),
    collectFormState: vi.fn(() => ({})),
  } as unknown as SettingsStore
}

describe("audio slider preview actions", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    vi.clearAllTimers()
  })

  it("coalesces bridge previews while keeping local slider state immediate", () => {
    const store = makeStore()

    previewConfig(store, "audio.gain", 2)
    previewConfig(store, "audio.gain", 3)
    previewConfig(store, "audio.gain", 4)

    expect(store.setConfig).toHaveBeenCalledTimes(3)
    expect(postSettingsMessage).not.toHaveBeenCalled()

    vi.advanceTimersByTime(50)

    expect(postSettingsMessage).toHaveBeenCalledOnce()
    expect(postSettingsMessage).toHaveBeenCalledWith({
      action: "previewConfig",
      key: "audio.gain",
      value: 4,
    })
  })

  it("cancels a pending preview and persists the final committed value", () => {
    const store = makeStore()

    previewConfig(store, "audio.highpass_freq", 240)
    commitPreviewConfig(store, "audio.highpass_freq", 250)
    vi.advanceTimersByTime(50)

    expect(postSettingsMessage).toHaveBeenCalledOnce()
    expect(postSettingsMessage).toHaveBeenCalledWith({
      action: "setConfig",
      key: "audio.highpass_freq",
      value: 250,
    })
  })
})
