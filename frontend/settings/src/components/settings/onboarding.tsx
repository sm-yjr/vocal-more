import {
  CircleAlert,
  CircleCheck,
  ExternalLink,
  KeyRound,
  Mic2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Square,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Progress } from "@/components/ui/progress"
import { sendAction, setConfig, setDevice } from "@/settings/actions"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type { SettingsSnapshot } from "@/settings/types"

const WHISPER_PRESET = {
  gain: 8,
  highpass_filter: true,
  highpass_freq: 220,
  soft_limiter: true,
} as const

function readiness(
  snapshot: SettingsSnapshot,
  key: string,
): boolean {
  return snapshot.environmentChecks.some(
    (check) => check.key === key && check.status === "ok",
  )
}

function Status({
  ready,
  copy,
}: {
  ready: boolean
  copy: SettingsCopy
}) {
  return (
    <span
      className={
        ready
          ? "inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400"
          : "inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
      }
    >
      {ready ? (
        <CircleCheck className="size-3.5" />
      ) : (
        <CircleAlert className="size-3.5" />
      )}
      {ready ? copy.ready : copy.needsAttention}
    </span>
  )
}

export function Onboarding({
  store,
  snapshot,
  copy,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const config = snapshot.config
  const audio = config.audio ?? {}
  const mic = snapshot.micTest
  const apiReady = Boolean(config.api_key?.trim())
  const deviceReady =
    snapshot.devices.length > 0 && readiness(snapshot, "input_device")
  const accessibilityReady = readiness(snapshot, "accessibility")
  const hotkeyReady = readiness(snapshot, "hotkey_listener")
  const firstRecordingReady =
    mic.state === "done" && Boolean(mic.playbackBase64)
  const canFinish =
    apiReady &&
    deviceReady &&
    accessibilityReady &&
    hotkeyReady &&
    firstRecordingReady

  function applyWhisperPreset() {
    for (const [key, value] of Object.entries(WHISPER_PRESET)) {
      setConfig(store, `audio.${key}`, value)
    }
  }

  return (
    <main className="h-svh min-h-[480px] overflow-y-auto bg-background px-6 py-5">
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        <header className="space-y-1">
          <div className="flex items-center gap-2 text-primary">
            <Sparkles className="size-5" />
            <h1 className="text-xl font-semibold tracking-tight">
              {copy.welcomeTitle}
            </h1>
          </div>
          <p className="text-sm text-muted-foreground">
            {copy.welcomeDescription}
          </p>
        </header>

        <section className="grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-medium">{copy.setupConnection}</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  {copy.setupConnectionHint}
                </p>
              </div>
              <Status ready={apiReady} copy={copy} />
            </div>
            <div className="flex gap-2">
              <Input
                aria-label={copy.apiKey}
                type="password"
                value={config.api_key ?? ""}
                placeholder="sk-…"
                autoComplete="off"
                spellCheck={false}
                onChange={(event) =>
                  setConfig(store, "api_key", event.target.value)
                }
              />
              <Button
                variant="outline"
                size="icon"
                aria-label={copy.getApiKey}
                onClick={() =>
                  sendAction("openExternal", {
                    url: "https://dashscope.console.aliyun.com/apiKey",
                  })
                }
              >
                <ExternalLink />
              </Button>
            </div>
          </div>

          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-1.5 text-sm font-medium">
                  <ShieldCheck className="size-4" />
                  {copy.setupPermission}
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  {copy.setupPermissionHint}
                </p>
              </div>
              <Status
                ready={accessibilityReady && hotkeyReady}
                copy={copy}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => sendAction("openAccessibilitySettings")}
              >
                <KeyRound data-icon="inline-start" />
                {copy.openAccessibility}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => sendAction("refreshEnvironment")}
              >
                <RefreshCw data-icon="inline-start" />
                {copy.refreshStatus}
              </Button>
            </div>
          </div>

          <div className="rounded-xl border bg-card p-4 shadow-sm md:col-span-2">
            <div className="mb-3 flex items-start justify-between gap-3">
              <div>
                <h2 className="flex items-center gap-1.5 text-sm font-medium">
                  <Mic2 className="size-4" />
                  {copy.setupMicrophone}
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  {copy.setupMicrophoneHint}
                </p>
              </div>
              <Status
                ready={deviceReady && firstRecordingReady}
                copy={copy}
              />
            </div>
            <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
              <NativeSelect
                aria-label={copy.inputDevice}
                value={audio.input_device ?? ""}
                onChange={(event) =>
                  setDevice(store, event.target.value || null)
                }
              >
                <NativeSelectOption value="">
                  {copy.systemDefault}
                </NativeSelectOption>
                {snapshot.devices.map((device) => (
                  <NativeSelectOption key={device.name} value={device.name}>
                    {device.name}
                  </NativeSelectOption>
                ))}
              </NativeSelect>
              <Button variant="outline" onClick={applyWhisperPreset}>
                {copy.whisper}
              </Button>
              {mic.state === "recording" ? (
                <Button
                  variant="destructive"
                  onClick={() => sendAction("stopMicTest")}
                >
                  <Square data-icon="inline-start" />
                  {copy.stop}
                </Button>
              ) : (
                <Button
                  onClick={() => {
                    store.resetMicTest()
                    sendAction("startMicTest")
                  }}
                >
                  <Mic2 data-icon="inline-start" />
                  {copy.startSpeaking}
                </Button>
              )}
            </div>
            {mic.state === "recording" ? (
              <Progress
                className="mt-3"
                aria-label={copy.startSpeaking}
                value={Math.round(mic.level * 100)}
              />
            ) : null}
            {mic.playbackBase64 ? (
              <audio
                className="mt-3 h-8 w-full"
                controls
                src={`data:audio/wav;base64,${mic.playbackBase64}`}
              />
            ) : null}
            {mic.error ? (
              <p className="mt-2 text-xs text-destructive">{mic.error}</p>
            ) : null}
          </div>
        </section>

        <footer className="flex items-center justify-between gap-4 border-t pt-4">
          <p className="text-xs text-muted-foreground">
            {copy.setupCompleteHint}
          </p>
          <Button
            disabled={!canFinish}
            onClick={() =>
              setConfig(store, "ui.onboarding_completed", true)
            }
          >
            {copy.finishSetup}
          </Button>
        </footer>
      </div>
    </main>
  )
}
