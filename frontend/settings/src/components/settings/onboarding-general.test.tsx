import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { GeneralSettings } from "@/components/settings/general-settings"
import { Onboarding } from "@/components/settings/onboarding"
import { getCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import { createSettingsStore } from "@/settings/store"
import type { SettingsMessage } from "@/settings/types"
import { useSettings } from "@/settings/use-settings"
import { makeInitData } from "@/test/fixtures"

function OnboardingHarness({
  store,
  language,
}: {
  store: SettingsStore
  language: string
}) {
  const snapshot = useSettings(store)
  return (
    <Onboarding
      store={store}
      snapshot={snapshot}
      copy={getCopy(language)}
    />
  )
}

function GeneralHarness({
  store,
  language,
}: {
  store: SettingsStore
  language: string
}) {
  const snapshot = useSettings(store)
  return (
    <GeneralSettings
      store={store}
      snapshot={snapshot}
      copy={getCopy(language)}
    />
  )
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

afterEach(() => {
  vi.useRealTimers()
})

describe("onboarding skip", () => {
  it("completes onboarding while Finish stays gated", async () => {
    const bridge = stubBridge()
    try {
      const data = makeInitData()
      data.config!.ui!.onboarding_completed = false
      const store = createSettingsStore(data)
      render(<OnboardingHarness store={store} language="zh" />)

      expect(screen.getByRole("button", { name: "完成设置" })).toBeDisabled()

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "跳过设置" }))
      })

      expect(store.getSnapshot().config.ui?.onboarding_completed).toBe(true)
      expect(bridge.posted).toContainEqual({
        action: "setConfig",
        key: "ui.onboarding_completed",
        value: true,
      })
      // Skipping also arms the "setup incomplete" reminder badge.
      expect(store.getSnapshot().config.ui?.onboarding_skipped).toBe(true)
      expect(bridge.posted).toContainEqual({
        action: "setConfig",
        key: "ui.onboarding_skipped",
        value: true,
      })
    } finally {
      bridge.restore()
    }
  })
})

describe("general settings rerun setup confirmation", () => {
  it("requires confirmation before rerunning setup", async () => {
    const bridge = stubBridge()
    try {
      const store = createSettingsStore(makeInitData())
      render(<GeneralHarness store={store} language="zh" />)

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "重新运行设置引导" }),
        )
      })

      expect(bridge.posted).toEqual([])

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "确认重新运行？" }),
        )
      })

      expect(store.getSnapshot().config.ui?.onboarding_completed).toBe(false)
      expect(bridge.posted).toContainEqual({
        action: "setConfig",
        key: "ui.onboarding_completed",
        value: false,
      })
    } finally {
      bridge.restore()
    }
  })

  it("cancel disarms the confirmation without posting", async () => {
    const bridge = stubBridge()
    try {
      const store = createSettingsStore(makeInitData())
      render(<GeneralHarness store={store} language="zh" />)

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "重新运行设置引导" }),
        )
      })

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "取消" }))
      })

      expect(bridge.posted).toEqual([])
      expect(store.getSnapshot().config.ui?.onboarding_completed).toBe(true)
      expect(
        screen.getByRole("button", { name: "重新运行设置引导" }),
      ).toBeVisible()
      expect(screen.getByRole("button", { name: "重新运行设置引导" })).toHaveFocus()
    } finally {
      bridge.restore()
    }
  })

  it("moves focus onto the confirm action and announces the swap", async () => {
    const bridge = stubBridge()
    try {
      const store = createSettingsStore(makeInitData())
      render(<GeneralHarness store={store} language="zh" />)

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "重新运行设置引导" }),
        )
      })

      // The confirm pair replaces the button that held focus; focus must
      // land on the new primary action, not <body>.
      expect(
        screen.getByRole("button", { name: "确认重新运行？" }),
      ).toHaveFocus()
      // The row is a live region so the swap is announced.
      expect(screen.getByRole("status")).toBeVisible()
    } finally {
      bridge.restore()
    }
  })

  it("auto-resets the armed confirmation after a few seconds", async () => {
    vi.useFakeTimers()
    const bridge = stubBridge()
    try {
      const store = createSettingsStore(makeInitData())
      render(<GeneralHarness store={store} language="zh" />)

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "重新运行设置引导" }),
        )
      })
      expect(
        screen.getByRole("button", { name: "确认重新运行？" }),
      ).toBeVisible()

      // Focus keeps the confirm reachable while the user is on it.
      await act(async () => {
        vi.advanceTimersByTime(5000)
      })
      expect(
        screen.getByRole("button", { name: "确认重新运行？" }),
      ).toBeVisible()

      // fireEvent.blur only dispatches the event; call the method so
      // document.activeElement actually returns to <body>.
      ;(
        screen.getByRole("button", { name: "确认重新运行？" }) as HTMLElement
      ).blur()
      await act(async () => {
        vi.advanceTimersByTime(5000)
      })

      expect(bridge.posted).toEqual([])
      expect(
        screen.getByRole("button", { name: "重新运行设置引导" }),
      ).toBeVisible()
    } finally {
      bridge.restore()
    }
  })
})

describe("general settings api key visibility labels", () => {
  it("labels the toggle with localized copy in both languages", async () => {
    const bridge = stubBridge()
    try {
      for (const [language, show, hide] of [
        ["zh", "显示", "隐藏"],
        ["en", "Show", "Hide"],
      ] as const) {
        const store = createSettingsStore(makeInitData())
        const { unmount } = render(
          <GeneralHarness store={store} language={language} />,
        )

        await act(async () => {
          fireEvent.click(screen.getByRole("button", { name: show }))
        })
        expect(screen.getByRole("button", { name: hide })).toBeVisible()
        unmount()
      }
    } finally {
      bridge.restore()
    }
  })
})
