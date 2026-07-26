import { Command, X } from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import {
  SettingsCard,
  SettingsPage,
  SettingsRow,
} from "@/components/settings/settings-card"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { setActiveHotkeys, setCustomKeys } from "@/settings/actions"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type {
  CustomHotkey,
  SettingsSnapshot,
} from "@/settings/types"

const MAC_KEY_CODES: Record<string, number> = {
  KeyA: 0,
  KeyS: 1,
  KeyD: 2,
  KeyF: 3,
  KeyH: 4,
  KeyG: 5,
  KeyZ: 6,
  KeyX: 7,
  KeyC: 8,
  KeyV: 9,
  IntlBackslash: 10,
  KeyB: 11,
  KeyQ: 12,
  KeyW: 13,
  KeyE: 14,
  KeyR: 15,
  KeyY: 16,
  KeyT: 17,
  Digit1: 18,
  Digit2: 19,
  Digit3: 20,
  Digit4: 21,
  Digit6: 22,
  Digit5: 23,
  Equal: 24,
  Digit9: 25,
  Digit7: 26,
  Minus: 27,
  Digit8: 28,
  Digit0: 29,
  BracketRight: 30,
  KeyO: 31,
  KeyU: 32,
  BracketLeft: 33,
  KeyI: 34,
  KeyP: 35,
  Enter: 36,
  KeyL: 37,
  KeyJ: 38,
  Quote: 39,
  KeyK: 40,
  Semicolon: 41,
  Backslash: 42,
  Comma: 43,
  Slash: 44,
  KeyN: 45,
  KeyM: 46,
  Period: 47,
  Tab: 48,
  Space: 49,
  Backquote: 50,
  Backspace: 51,
  Escape: 53,
  F17: 64,
  NumpadDecimal: 65,
  NumpadMultiply: 67,
  NumpadAdd: 69,
  NumLock: 71,
  NumpadDivide: 75,
  NumpadEnter: 76,
  NumpadSubtract: 78,
  F18: 79,
  F19: 80,
  NumpadEqual: 81,
  Numpad0: 82,
  Numpad1: 83,
  Numpad2: 84,
  Numpad3: 85,
  Numpad4: 86,
  Numpad5: 87,
  Numpad6: 88,
  Numpad7: 89,
  F20: 90,
  Numpad8: 91,
  Numpad9: 92,
  F5: 96,
  F6: 97,
  F7: 98,
  F3: 99,
  F8: 100,
  F9: 101,
  F11: 103,
  F13: 105,
  F16: 106,
  F14: 107,
  F10: 109,
  F12: 111,
  F15: 113,
  Help: 114,
  Home: 115,
  PageUp: 116,
  Delete: 117,
  F4: 118,
  End: 119,
  F2: 120,
  PageDown: 121,
  F1: 122,
  ArrowLeft: 123,
  ArrowRight: 124,
  ArrowDown: 125,
  ArrowUp: 126,
}

const MODIFIERS: Record<
  string,
  Pick<CustomHotkey, "key_code" | "display_name" | "flag_mask">
> = {
  MetaLeft: {
    key_code: 55,
    display_name: "Left Command",
    flag_mask: 0x100000,
  },
  MetaRight: {
    key_code: 54,
    display_name: "Right Command",
    flag_mask: 0x100000,
  },
  ShiftLeft: {
    key_code: 56,
    display_name: "Left Shift",
    flag_mask: 0x20000,
  },
  ShiftRight: {
    key_code: 60,
    display_name: "Right Shift",
    flag_mask: 0x20000,
  },
  AltLeft: {
    key_code: 58,
    display_name: "Left Option",
    flag_mask: 0x80000,
  },
  AltRight: {
    key_code: 61,
    display_name: "Right Option",
    flag_mask: 0x80000,
  },
  ControlLeft: {
    key_code: 59,
    display_name: "Left Control",
    flag_mask: 0x40000,
  },
  ControlRight: {
    key_code: 62,
    display_name: "Right Control",
    flag_mask: 0x40000,
  },
  CapsLock: {
    key_code: 57,
    display_name: "Caps Lock",
    flag_mask: 0x10000,
  },
}

function hotkeyForEvent(event: KeyboardEvent): CustomHotkey | null {
  const modifier = MODIFIERS[event.code]
  if (modifier) {
    return { ...modifier, is_modifier: true }
  }
  const keyCode = MAC_KEY_CODES[event.code]
  if (keyCode === undefined) return null
  return {
    key_code: keyCode,
    display_name:
      event.code === "Space"
        ? "Space"
        : event.key.length === 1
          ? event.key.toUpperCase()
          : event.key,
    is_modifier: false,
    flag_mask: 0,
  }
}

export function ShortcutsSettings({
  store,
  snapshot,
  copy,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const hotkey = snapshot.config.hotkey ?? {}
  const active = hotkey.active_hotkeys ?? ["fn"]
  const customKeys = useMemo(
    () =>
      hotkey.custom_keys ??
      (hotkey.custom_key ? [hotkey.custom_key] : []),
    [hotkey.custom_key, hotkey.custom_keys],
  )
  const [capturing, setCapturing] = useState(false)

  useEffect(() => {
    if (!capturing) return
    const onKeyDown = (event: KeyboardEvent) => {
      event.preventDefault()
      event.stopPropagation()
      const next = hotkeyForEvent(event)
      setCapturing(false)
      if (
        next &&
        customKeys.length < 8 &&
        !customKeys.some((item) => item.key_code === next.key_code)
      ) {
        setCustomKeys(store, [...customKeys, next])
      }
    }
    document.addEventListener("keydown", onKeyDown, true)
    return () => document.removeEventListener("keydown", onKeyDown, true)
  }, [capturing, customKeys, store])

  return (
    <SettingsPage title={copy.shortcuts}>
      <SettingsCard title={copy.builtInHotkeys}>
        <SettingsRow label={copy.fnKey}>
          <Switch
            checked={active.includes("fn")}
            onCheckedChange={(checked) =>
              setActiveHotkeys(
                store,
                checked
                  ? [...new Set([...active, "fn"])]
                  : active.filter((name) => name !== "fn"),
              )
            }
          />
        </SettingsRow>
      </SettingsCard>

      <SettingsCard
        title={copy.customKey}
        description={copy.shortcutGestureHint}
      >
        <div className="space-y-3 p-4">
          {customKeys.length ? (
            <div className="grid gap-2">
              {customKeys.map((item) => (
                <div
                  key={item.key_code}
                  className="flex h-11 items-center rounded-lg border bg-muted/30 px-3"
                >
                  <Command className="mr-2 size-4 text-muted-foreground" />
                  <span className="flex-1 font-mono text-sm font-medium">
                    {item.display_name}
                  </span>
                  <Button
                    aria-label={`${copy.remove} ${item.display_name}`}
                    size="icon"
                    variant="ghost"
                    onClick={() =>
                      setCustomKeys(
                        store,
                        customKeys.filter(
                          (candidate) =>
                            candidate.key_code !== item.key_code,
                        ),
                      )
                    }
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex h-11 items-center justify-center rounded-lg border border-dashed bg-muted/20 text-sm text-muted-foreground">
              {copy.notSet}
            </div>
          )}
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-muted-foreground">
              {copy.customKeyLimit}
            </span>
            <Button
              disabled={!capturing && customKeys.length >= 8}
              variant={capturing ? "secondary" : "default"}
              onClick={() => setCapturing((value) => !value)}
            >
              {capturing ? copy.pressKey : copy.addKey}
            </Button>
          </div>
        </div>
      </SettingsCard>
    </SettingsPage>
  )
}
