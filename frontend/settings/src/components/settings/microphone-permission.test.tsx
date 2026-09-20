import { act, fireEvent, render, screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { AudioSettings } from "@/components/settings/audio-settings"
import { Onboarding } from "@/components/settings/onboarding"
import { getCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import { createSettingsStore } from "@/settings/store"
import type { SettingsMessage } from "@/settings/types"
import { useSettings } from "@/settings/use-settings"
import { makeInitData } from "@/test/fixtures"

function OnboardingHarness({ store }: { store: SettingsStore }) {
  const snapshot = useSettings(store)
  return <Onboarding store={store} snapshot={snapshot} copy={getCopy("zh")} />
}

function AudioHarness({ store }: { store: SettingsStore }) {
  const snapshot = useSettings(store)
  return <AudioSettings store={store} snapshot={snapshot} copy={getCopy("zh")} />
}

function stubBridge() {
  const posted: SettingsMessage[] = []
  window.webkit = {
    messageHandlers: {
      settings: {
        postMessage: (message: SettingsMessage) => {
          posted.push(message)
        },
      },
    },
  }
  return {
    posted,
    restore() {
      delete window.webkit
    },
  }
}

function withMicrophonePermission(
  status: "ok" | "error" | "unknown",
  details: string,
) {
  const data = makeInitData()
  data.environment_checks = [
    ...(data.environment_checks ?? []),
    { key: "microphone_permission", status, details },
  ]
  return data
}

describe("onboarding microphone permission card", () => {
  it("shows the denied status and posts the open action", async () => {
    const bridge = stubBridge()
    try {
      const store = createSettingsStore(
        withMicrophonePermission("error", "denied"),
      )
      render(<OnboardingHarness store={store} />)

      const card = screen
        .getByText("授予麦克风权限")
        .closest("div.rounded-xl") as HTMLElement
      expect(within(card).getByText("需要处理")).toBeVisible()

      await act(async () => {
        fireEvent.click(
          within(card).getByRole("button", { name: "打开麦克风设置" }),
        )
      })

      expect(bridge.posted).toContainEqual({
        action: "openMicrophoneSettings",
      })
    } finally {
      bridge.restore()
    }
  })

  it("keeps Finish available while microphone permission is missing", async () => {
    const bridge = stubBridge()
    try {
      const store = createSettingsStore(
        withMicrophonePermission("error", "denied"),
      )
      render(<OnboardingHarness store={store} />)

      await act(async () => {
        store.micTestComplete()
      })

      expect(
        screen.getByRole("button", { name: "完成设置" }),
      ).toBeEnabled()
    } finally {
      bridge.restore()
    }
  })

  it("shows the granted status when permission is authorized", () => {
    const bridge = stubBridge()
    try {
      const store = createSettingsStore(
        withMicrophonePermission("ok", "authorized"),
      )
      render(<OnboardingHarness store={store} />)

      const card = screen
        .getByText("授予麦克风权限")
        .closest("div.rounded-xl") as HTMLElement
      expect(within(card).getByText("已就绪")).toBeVisible()
    } finally {
      bridge.restore()
    }
  })
})

describe("audio page microphone permission row", () => {
  it("offers the open action when permission is denied", async () => {
    const bridge = stubBridge()
    try {
      const data = makeInitData()
      data.audio_input_status!.microphone_permission = "denied"
      const store = createSettingsStore(data)
      render(<AudioHarness store={store} />)

      expect(screen.getByText("已在系统设置中拒绝")).toBeVisible()

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "打开麦克风设置" }),
        )
      })

      expect(bridge.posted).toContainEqual({
        action: "openMicrophoneSettings",
      })
    } finally {
      bridge.restore()
    }
  })

  it("hides the open action unless permission is denied or restricted", () => {
    const bridge = stubBridge()
    try {
      for (const permission of ["authorized", "not_determined"] as const) {
        const data = makeInitData()
        data.audio_input_status!.microphone_permission = permission
        const store = createSettingsStore(data)
        const { unmount } = render(<AudioHarness store={store} />)

        expect(
          screen.queryByRole("button", { name: "打开麦克风设置" }),
        ).toBeNull()
        unmount()
      }
    } finally {
      bridge.restore()
    }
  })
})
