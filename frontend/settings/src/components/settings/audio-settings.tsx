import { Mic, RefreshCw, Square } from "lucide-react"
import { useEffect } from "react"

import {
  InlineValue,
  SettingsCard,
  SettingsPage,
  SettingsRow,
} from "@/components/settings/settings-card"
import { Button } from "@/components/ui/button"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Progress } from "@/components/ui/progress"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { sendAction, setConfig, setDevice } from "@/settings/actions"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type { SettingsSnapshot } from "@/settings/types"

const AUDIO_PRESETS = {
  whisper: {
    gain: 8,
    highpass_filter: true,
    highpass_freq: 220,
    soft_limiter: true,
  },
  normal: {
    gain: 4,
    highpass_filter: true,
    highpass_freq: 200,
    soft_limiter: true,
  },
  noisy: {
    gain: 6,
    highpass_filter: true,
    highpass_freq: 280,
    soft_limiter: true,
  },
} as const

function gainToDb(gain: number): number {
  return Math.round(20 * Math.log10(Math.max(gain, 0.001)))
}

function dbToGain(db: number): number {
  return 10 ** (db / 20)
}

function sliderNumber(value: number | readonly number[]): number {
  return typeof value === "number" ? value : (value[0] ?? 0)
}

export function AudioSettings({
  store,
  snapshot,
  copy,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const audio = snapshot.config.audio ?? {}
  const gainDb = gainToDb(
    typeof audio.gain === "number" ? audio.gain : 2,
  )
  const mic = snapshot.micTest

  useEffect(() => {
    if (mic.state !== "recording") return
    const timer = window.setTimeout(
      () => sendAction("stopMicTest"),
      5500,
    )
    return () => window.clearTimeout(timer)
  }, [mic.state])

  function applyPreset(name: keyof typeof AUDIO_PRESETS) {
    for (const [key, value] of Object.entries(AUDIO_PRESETS[name])) {
      setConfig(store, `audio.${key}`, value)
    }
  }

  return (
    <SettingsPage
      title={copy.audio}
      description={copy.softwareGainHint}
    >
      <SettingsCard>
        <SettingsRow label={copy.inputDevice} htmlFor="audio-device">
          <NativeSelect
            id="audio-device"
            aria-label={copy.inputDevice}
            className="h-8 w-64"
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
                {device.is_default ? ` (${copy.defaultSuffix})` : ""}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={copy.refresh}
            onClick={() => sendAction("refreshDevices")}
          >
            <RefreshCw />
          </Button>
        </SettingsRow>
      </SettingsCard>

      <SettingsCard
        title={copy.presets}
        description={copy.presetsHint}
      >
        <div className="grid grid-cols-3 gap-2 p-3">
          {(
            [
              ["whisper", copy.whisper],
              ["normal", copy.normal],
              ["noisy", copy.noisy],
            ] as const
          ).map(([name, label]) => (
            <Button
              key={name}
              variant="outline"
              className="justify-start"
              onClick={() => applyPreset(name)}
            >
              {label}
            </Button>
          ))}
        </div>
      </SettingsCard>

      <SettingsCard>
        <SettingsRow
          label={copy.softwareGain}
          description={copy.softwareGainHint}
        >
          <div className="flex w-64 items-center gap-3">
            <Slider
              aria-label={copy.softwareGain}
              min={-6}
              max={30}
              step={1}
              value={gainDb}
              onValueChange={(value) =>
                setConfig(
                  store,
                  "audio.gain",
                  dbToGain(sliderNumber(value)),
                )
              }
            />
            <InlineValue>
              {gainDb >= 0 ? "+" : ""}
              {gainDb} dB
            </InlineValue>
          </div>
        </SettingsRow>
        <SettingsRow
          label={copy.highpass}
          description={copy.highpassHint}
        >
          <Switch
            checked={audio.highpass_filter !== false}
            onCheckedChange={(checked) =>
              setConfig(store, "audio.highpass_filter", checked)
            }
          />
        </SettingsRow>
        <SettingsRow label={copy.cutoff}>
          <div className="flex w-64 items-center gap-3">
            <Slider
              aria-label={copy.cutoff}
              min={50}
              max={500}
              step={10}
              disabled={audio.highpass_filter === false}
              value={
                typeof audio.highpass_freq === "number"
                  ? audio.highpass_freq
                  : 200
              }
              onValueChange={(value) =>
                setConfig(
                  store,
                  "audio.highpass_freq",
                  sliderNumber(value),
                )
              }
            />
            <InlineValue>
              {audio.highpass_freq ?? 200} Hz
            </InlineValue>
          </div>
        </SettingsRow>
        <SettingsRow
          label={copy.limiter}
          description={copy.limiterHint}
        >
          <Switch
            checked={audio.soft_limiter !== false}
            onCheckedChange={(checked) =>
              setConfig(store, "audio.soft_limiter", checked)
            }
          />
        </SettingsRow>
      </SettingsCard>

      <SettingsCard title={copy.testRecording}>
        <div className="flex min-h-16 items-center gap-3 p-3">
          {mic.state === "recording" ? (
            <>
              <Progress
                aria-label={copy.testRecording}
                className="flex-1"
                value={Math.round(mic.level * 100)}
              />
              <Button
                size="sm"
                variant="destructive"
                onClick={() => sendAction("stopMicTest")}
              >
                <Square data-icon="inline-start" />
                {copy.stop}
              </Button>
            </>
          ) : (
            <>
              {mic.state === "done" && mic.playbackBase64 ? (
                <audio
                  className="h-8 flex-1"
                  controls
                  src={`data:audio/wav;base64,${mic.playbackBase64}`}
                />
              ) : (
                <p className="flex-1 text-xs text-muted-foreground">
                  {mic.error || copy.softwareGainHint}
                </p>
              )}
              <Button
                size="sm"
                onClick={() => {
                  store.resetMicTest()
                  sendAction("startMicTest")
                }}
              >
                <Mic data-icon="inline-start" />
                {mic.state === "done" ? copy.retest : copy.test}
              </Button>
            </>
          )}
        </div>
      </SettingsCard>
    </SettingsPage>
  )
}
