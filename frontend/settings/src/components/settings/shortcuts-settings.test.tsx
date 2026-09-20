import { act, fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { ShortcutsSettings } from "@/components/settings/shortcuts-settings"
import { getCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import { createSettingsStore } from "@/settings/store"
import type { CustomHotkey } from "@/settings/types"
import { useSettings } from "@/settings/use-settings"
import { makeInitData } from "@/test/fixtures"

function ShortcutsHarness({
  store,
  language,
}: {
  store: SettingsStore
  language: string
}) {
  const snapshot = useSettings(store)
  return (
    <ShortcutsSettings
      store={store}
      snapshot={snapshot}
      copy={getCopy(language)}
    />
  )
}

function renderShortcuts() {
  const store = createSettingsStore(makeInitData())
  render(<ShortcutsHarness store={store} language="zh" />)
  return { store }
}

function pressKey(code: string, key: string, repeat = false) {
  fireEvent(
    document,
    new KeyboardEvent("keydown", {
      code,
      key,
      repeat,
      bubbles: true,
      cancelable: true,
    }),
  )
}

function startCapture() {
  fireEvent.click(screen.getByRole("button", { name: "添加按键…" }))
}

function customKeys(store: SettingsStore): CustomHotkey[] {
  return (
    store.getSnapshot().config.hotkey?.custom_keys ?? []
  )
}

describe("shortcuts settings key capture", () => {
  it("cancels capture with Escape without registering it as a hotkey", () => {
    const { store } = renderShortcuts()
    startCapture()
    expect(
      screen.getByRole("button", { name: "请按下一个按键…" }),
    ).toBeVisible()

    // The capture state is announced through a live-region hint.
    expect(screen.getByRole("status")).toHaveTextContent(
      "正在监听按键，按 Esc 取消。",
    )

    act(() => {
      pressKey("Escape", "Escape")
    })

    expect(screen.getByRole("button", { name: "添加按键…" })).toBeVisible()
    expect(customKeys(store)).toEqual([])
  })

  it("requires a bare modifier to be pressed twice before registering", () => {
    const { store } = renderShortcuts()
    startCapture()

    act(() => {
      pressKey("MetaLeft", "Meta")
    })

    expect(customKeys(store)).toEqual([])
    expect(screen.getByText("再按一次同一修饰键确认")).toBeVisible()
    expect(
      screen.getByRole("button", { name: "请按下一个按键…" }),
    ).toBeVisible()

    act(() => {
      pressKey("MetaLeft", "Meta")
    })

    expect(customKeys(store)).toEqual([
      {
        key_code: 55,
        display_name: "Left Command",
        is_modifier: true,
        flag_mask: 0x100000,
      },
    ])
    expect(screen.getByRole("button", { name: "添加按键…" })).toBeVisible()
  })

  it("ignores key auto-repeat when confirming a bare modifier", () => {
    const { store } = renderShortcuts()
    startCapture()

    act(() => {
      pressKey("MetaLeft", "Meta")
    })
    // Holding the modifier produces auto-repeat keydowns; those must not
    // count as the second press of the double-press confirmation.
    act(() => {
      pressKey("MetaLeft", "Meta", true)
    })

    expect(customKeys(store)).toEqual([])
    expect(screen.getByText("再按一次同一修饰键确认")).toBeVisible()
  })

  it("resets the pending modifier when a different key is pressed", () => {
    const { store } = renderShortcuts()
    startCapture()

    act(() => {
      pressKey("MetaLeft", "Meta")
    })
    act(() => {
      pressKey("ShiftLeft", "Shift")
    })
    act(() => {
      pressKey("ShiftLeft", "Shift")
    })

    const keys = customKeys(store)
    expect(keys).toHaveLength(1)
    expect(keys[0]!.key_code).toBe(56)
    expect(keys[0]!.display_name).toBe("Left Shift")
  })

  it("registers a regular key immediately and ignores the earlier modifier", () => {
    const { store } = renderShortcuts()
    startCapture()

    act(() => {
      pressKey("MetaLeft", "Meta")
    })
    act(() => {
      pressKey("KeyA", "a")
    })

    expect(customKeys(store)).toEqual([
      {
        key_code: 0,
        display_name: "A",
        is_modifier: false,
        flag_mask: 0,
      },
    ])
    expect(screen.getByRole("button", { name: "添加按键…" })).toBeVisible()
  })
})
