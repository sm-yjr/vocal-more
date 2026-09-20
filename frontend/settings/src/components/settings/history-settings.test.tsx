import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { HistorySettings } from "@/components/settings/history-settings"
import { getCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import { createSettingsStore } from "@/settings/store"
import type { SettingsInitData } from "@/settings/types"
import { useSettings } from "@/settings/use-settings"
import { makeInitData } from "@/test/fixtures"

function HistoryHarness({
  store,
  language,
}: {
  store: SettingsStore
  language: string
}) {
  const snapshot = useSettings(store)
  return (
    <HistorySettings
      store={store}
      snapshot={snapshot}
      copy={getCopy(language)}
    />
  )
}

function renderHistory(data = makeInitData()) {
  const store = createSettingsStore(data)
  render(<HistoryHarness store={store} language="zh" />)
  return { store }
}

function stubClipboard(writeText: ReturnType<typeof vi.fn>) {
  const descriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard")
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  })
  return () => {
    if (descriptor) Object.defineProperty(navigator, "clipboard", descriptor)
    else delete (navigator as unknown as Record<string, unknown>).clipboard
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe("history settings copy feedback", () => {
  it("prefers the async clipboard API and swaps the button to a transient copied state", async () => {
    vi.useFakeTimers()
    const writeText = vi.fn(() => Promise.resolve())
    const restoreClipboard = stubClipboard(writeText)
    try {
      const { store } = renderHistory()

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "复制 Hello Vocal More." }),
        )
      })
      expect(writeText).toHaveBeenCalledWith("Hello Vocal More.")
      expect(
        screen.getByRole("button", { name: "已复制 Hello Vocal More." }),
      ).toBeVisible()
      expect(
        screen.queryByRole("button", { name: "复制 Hello Vocal More." }),
      ).not.toBeInTheDocument()
      expect(store.getSnapshot().copiedRecordingId).toBe("rec-1")

      act(() => {
        vi.advanceTimersByTime(1500)
      })
      expect(
        screen.getByRole("button", { name: "复制 Hello Vocal More." }),
      ).toBeVisible()
      expect(store.getSnapshot().copiedRecordingId).toBeNull()
    } finally {
      restoreClipboard()
    }
  })

  it("falls back to execCommand when the clipboard API is unavailable", async () => {
    const execCommand = vi.fn(() => true)
    const originalExecCommand = document.execCommand
    document.execCommand = execCommand
    try {
      const { store } = renderHistory()

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "复制 Hello Vocal More." }),
        )
      })
      expect(execCommand).toHaveBeenCalledWith("copy")
      expect(store.getSnapshot().copiedRecordingId).toBe("rec-1")
      expect(
        screen.getByRole("button", { name: "已复制 Hello Vocal More." }),
      ).toBeVisible()
    } finally {
      document.execCommand = originalExecCommand
    }
  })

  it("falls back to execCommand when the clipboard write is rejected", async () => {
    const writeText = vi.fn(() => Promise.reject(new Error("denied")))
    const restoreClipboard = stubClipboard(writeText)
    const execCommand = vi.fn(() => true)
    const originalExecCommand = document.execCommand
    document.execCommand = execCommand
    try {
      renderHistory()

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "复制 Hello Vocal More." }),
        )
      })
      expect(writeText).toHaveBeenCalledWith("Hello Vocal More.")
      expect(execCommand).toHaveBeenCalledWith("copy")
      expect(
        screen.getByRole("button", { name: "已复制 Hello Vocal More." }),
      ).toBeVisible()
    } finally {
      document.execCommand = originalExecCommand
      restoreClipboard()
    }
  })

  it("does not show the copied state when both clipboard paths fail", async () => {
    const writeText = vi.fn(() => Promise.reject(new Error("denied")))
    const restoreClipboard = stubClipboard(writeText)
    const execCommand = vi.fn(() => false)
    const originalExecCommand = document.execCommand
    document.execCommand = execCommand
    try {
      const { store } = renderHistory()

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "复制 Hello Vocal More." }),
        )
      })

      expect(writeText).toHaveBeenCalledWith("Hello Vocal More.")
      expect(execCommand).toHaveBeenCalledWith("copy")
      expect(store.getSnapshot().copiedRecordingId).toBeNull()
      expect(
        screen.getByRole("button", { name: "复制 Hello Vocal More." }),
      ).toBeVisible()
      expect(
        screen.queryByRole("button", { name: "已复制 Hello Vocal More." }),
      ).not.toBeInTheDocument()
      expect(screen.getByRole("alert")).toHaveTextContent(
        "复制失败，请手动选择文本复制。",
      )
    } finally {
      document.execCommand = originalExecCommand
      restoreClipboard()
    }
  })

  it("does not show the copied state when execCommand fails without the async API", async () => {
    const execCommand = vi.fn(() => false)
    const originalExecCommand = document.execCommand
    document.execCommand = execCommand
    try {
      const restoreClipboard = stubClipboard(
        vi.fn(() => Promise.resolve()) as ReturnType<typeof vi.fn>,
      )
      // Remove the clipboard object entirely to force the legacy path.
      Object.defineProperty(navigator, "clipboard", {
        value: undefined,
        configurable: true,
      })
      const { store } = renderHistory()

      await act(async () => {
        fireEvent.click(
          screen.getByRole("button", { name: "复制 Hello Vocal More." }),
        )
      })

      expect(execCommand).toHaveBeenCalledWith("copy")
      expect(store.getSnapshot().copiedRecordingId).toBeNull()
      expect(
        screen.queryByRole("button", { name: "已复制 Hello Vocal More." }),
      ).not.toBeInTheDocument()
      restoreClipboard()
    } finally {
      document.execCommand = originalExecCommand
    }
  })
})

describe("history settings meeting labels", () => {
  function meetingData(language: string): SettingsInitData {
    const data = makeInitData()
    data.config!.ui!.language = language
    data.recordings![0]!.meeting = {
      status: "success",
      segments: [{ timestamp: "00:01", text: "Hello meeting." }],
    }
    return data
  }

  it("labels an unlabeled segment with the localized speaker fallback", () => {
    for (const [language, expected] of [
      ["zh", "发言人"],
      ["en", "Speaker"],
    ] as const) {
      const store = createSettingsStore(meetingData(language))
      const { unmount } = render(
        <HistoryHarness store={store} language={language} />,
      )
      expect(screen.getByText(expected)).toBeVisible()
      unmount()
    }
  })
})

it("cleans up and restores focus when the legacy clipboard throws", async () => {
  const restoreClipboard = stubClipboard(vi.fn(() => Promise.reject(new Error("denied"))))
  const originalExecCommand = document.execCommand
  document.execCommand = vi.fn(() => { throw new Error("clipboard unavailable") })
  try {
    const { store } = renderHistory()
    const button = screen.getByRole("button", { name: "复制 Hello Vocal More." })
    button.focus()
    await act(async () => { fireEvent.click(button) })

    expect(store.getSnapshot().copiedRecordingId).toBeNull()
    expect(screen.getByRole("alert")).toHaveTextContent("复制失败")
    expect(document.querySelector("textarea")).toBeNull()
    expect(button).toHaveFocus()
  } finally {
    document.execCommand = originalExecCommand
    restoreClipboard()
  }
})
