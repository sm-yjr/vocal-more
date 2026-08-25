import { Mic } from "lucide-react"
import { useEffect, useReducer, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBackdrop,
  DialogDescription,
  DialogFooter,
  DialogPopup,
  DialogPortal,
  DialogTitle,
} from "@/components/ui/dialog"
import { Progress } from "@/components/ui/progress"
import { sendAction, setConfig } from "@/settings/actions"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type { SettingsSnapshot } from "@/settings/types"
import {
  DEFAULT_WAVEFORM_CEILING_DBFS,
  rmsToDbfs,
  waveformLevelFromRms,
} from "@/settings/waveform-calibration"
import {
  buildCalibrationChanges,
  calibrationReducer,
  describeCalibrationChanges,
  initialCalibrationState,
  phaseDurationMs,
} from "@/settings/whisper-calibration"

const MEASURING_STATUSES = new Set(["starting", "recording", "stopping"])

export function WhisperCalibrationWizard({
  store,
  snapshot,
  copy,
  onClose,
  onMeasuringChange,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
  onClose: () => void
  onMeasuringChange: (measuring: boolean) => void
}) {
  const [state, dispatch] = useReducer(calibrationReducer, initialCalibrationState)
  const audio = snapshot.config.audio ?? {}
  const mic = snapshot.micTest
  const waveformCeilingDbfs =
    typeof audio.waveform_ceiling_dbfs === "number"
      ? audio.waveform_ceiling_dbfs
      : DEFAULT_WAVEFORM_CEILING_DBFS

  // The level stream is post-gain, so the recommendation must be anchored to
  // the gain actually in force during the measurement. Apple AGC (automatic
  // mode) forces the recorder gain to 1.0 regardless of the saved value.
  const currentGain =
    audio.gain_mode === "manual" && typeof audio.gain === "number"
      ? Math.max(audio.gain, 0.001)
      : 1.0
  const currentGainRef = useRef(currentGain)
  currentGainRef.current = currentGain

  const statusRef = useRef(state.status)
  statusRef.current = state.status
  const onMeasuringChangeRef = useRef(onMeasuringChange)
  onMeasuringChangeRef.current = onMeasuringChange

  useEffect(() => {
    onMeasuringChange(state.status !== "idle")
  }, [state.status, onMeasuringChange])

  // Ask the backend to open the microphone whenever a phase is starting.
  useEffect(() => {
    if (state.status === "starting") {
      sendAction("startMicTest")
    }
  }, [state.status, state.phase])

  // End each phase on schedule; the backend's 5 s auto-stop sits behind this
  // timer as a safety net only.
  useEffect(() => {
    if (state.status !== "recording") return
    const timer = window.setTimeout(() => {
      dispatch({ type: "stopPhase" })
      sendAction("stopMicTest")
    }, phaseDurationMs(state.phase))
    return () => window.clearTimeout(timer)
  }, [state.status, state.phase])

  // Fold the mic-test lifecycle callbacks into the calibration state machine.
  useEffect(() => {
    if (mic.state === "recording" && state.status === "starting") {
      dispatch({ type: "micStarted" })
    } else if (
      mic.state === "done" &&
      (state.status === "recording" || state.status === "stopping")
    ) {
      dispatch({ type: "micCompleted", currentGain: currentGainRef.current })
    } else if (mic.state === "error" && MEASURING_STATUSES.has(state.status)) {
      dispatch({ type: "micFailed", message: mic.error ?? "" })
    }
  }, [mic.state, mic.error, state.status])

  // Sample the level stream while a phase is recording.
  useEffect(() => {
    if (
      state.status === "recording" &&
      mic.state === "recording" &&
      mic.level > 0
    ) {
      dispatch({ type: "sample", level: mic.level })
    }
  }, [mic.level, mic.state, state.status])

  const [timeProgress, setTimeProgress] = useState(0)
  useEffect(() => {
    if (state.status !== "recording") {
      setTimeProgress(0)
      return
    }
    const startedAt = Date.now()
    const duration = phaseDurationMs(state.phase)
    const timer = window.setInterval(() => {
      setTimeProgress(Math.min(100, ((Date.now() - startedAt) / duration) * 100))
    }, 100)
    return () => window.clearInterval(timer)
  }, [state.status, state.phase])

  // A closed wizard must leave no mic-test session behind: stop an in-flight
  // recording and clear the finished state so it cannot auto-play later.
  useEffect(() => {
    return () => {
      if (MEASURING_STATUSES.has(statusRef.current)) {
        sendAction("stopMicTest")
      }
      store.resetMicTest()
      onMeasuringChangeRef.current(false)
    }
  }, [store])

  function startCalibration() {
    store.resetMicTest()
    dispatch({ type: "start" })
  }

  function handleClose() {
    if (MEASURING_STATUSES.has(state.status)) {
      sendAction("stopMicTest")
    }
    onClose()
  }

  function applyRecommendation() {
    const result = state.result
    if (!result || result.status !== "ok") return
    for (const { key, value } of buildCalibrationChanges(result, audio)) {
      setConfig(store, key, value)
    }
    onClose()
  }

  const micDbfs = rmsToDbfs(mic.level)
  const levelReadout = Number.isFinite(micDbfs)
    ? `${micDbfs.toFixed(1)} dBFS`
    : "≤ −60 dBFS"

  let body
  if (state.status === "idle") {
    body = (
      <>
        <DialogTitle>{copy.whisperCalibration}</DialogTitle>
        <DialogDescription>{copy.whisperCalibrationIntro}</DialogDescription>
        <p className="rounded-lg border bg-muted/40 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
          「{copy.whisperCalibrationSentence}」
        </p>
        <DialogFooter>
          <Button variant="ghost" onClick={handleClose}>
            {copy.cancel}
          </Button>
          <Button onClick={startCalibration}>
            <Mic data-icon="inline-start" />
            {copy.whisperCalibrationStart}
          </Button>
        </DialogFooter>
      </>
    )
  } else if (MEASURING_STATUSES.has(state.status)) {
    const quiet = state.phase === "quiet"
    const progressValue = quiet
      ? timeProgress
      : Math.round(
          waveformLevelFromRms(mic.level, waveformCeilingDbfs) * 100,
        )
    body = (
      <>
        <DialogTitle>
          {quiet
            ? copy.whisperCalibrationStepOne
            : copy.whisperCalibrationStepTwo}
        </DialogTitle>
        <DialogDescription>
          {quiet
            ? copy.whisperCalibrationQuietHint
            : copy.whisperCalibrationWhisperHint}
        </DialogDescription>
        {quiet ? null : (
          <p className="rounded-lg border bg-muted/40 px-3 py-2.5 text-center text-sm leading-relaxed">
            「{copy.whisperCalibrationSentence}」
          </p>
        )}
        <div className="flex items-center gap-3">
          <Progress
            aria-label={copy.whisperCalibration}
            className="flex-1"
            value={progressValue}
          />
          <span className="w-24 shrink-0 text-right text-xs text-muted-foreground">
            {levelReadout}
          </span>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={handleClose}>
            {copy.cancel}
          </Button>
        </DialogFooter>
      </>
    )
  } else if (state.status === "result" && state.result?.status === "ok") {
    const result = state.result
    const changes = describeCalibrationChanges(result, audio, copy)
    body = (
      <>
        <DialogTitle>{copy.whisperCalibrationResultTitle}</DialogTitle>
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-lg border px-3 py-2">
            <p className="text-xs text-muted-foreground">{copy.whisperLevel}</p>
            <p className="text-lg font-semibold">
              {result.whisperLevelDbfs.toFixed(1)} dBFS
            </p>
          </div>
          <div className="rounded-lg border px-3 py-2">
            <p className="text-xs text-muted-foreground">{copy.noiseFloor}</p>
            <p className="text-lg font-semibold">
              {result.noiseFloorDbfs.toFixed(1)} dBFS
            </p>
          </div>
        </div>
        <div className="flex flex-col gap-1.5 rounded-lg border px-3 py-2.5">
          {changes.map((change) => (
            <div
              key={change.label}
              className="flex items-center justify-between gap-3 text-xs"
            >
              <span className="text-muted-foreground">{change.label}</span>
              <span className="font-medium">{change.value}</span>
            </div>
          ))}
        </div>
        {result.gainClamped ? (
          <DialogDescription>
            {copy.whisperCalibrationGainLimited}
          </DialogDescription>
        ) : null}
        <DialogFooter>
          <Button variant="ghost" onClick={handleClose}>
            {copy.cancel}
          </Button>
          <Button onClick={applyRecommendation}>
            {copy.applyRecommendation}
          </Button>
        </DialogFooter>
      </>
    )
  } else {
    const message =
      state.status === "error"
        ? copy.whisperCalibrationFailed
        : state.result?.status === "low-snr"
          ? copy.whisperCalibrationLowSnr
          : copy.whisperCalibrationInsufficient
    body = (
      <>
        <DialogTitle>{copy.whisperCalibration}</DialogTitle>
        <DialogDescription>{message}</DialogDescription>
        {state.status === "error" && state.error ? (
          <DialogDescription>{state.error}</DialogDescription>
        ) : null}
        <DialogFooter>
          <Button variant="ghost" onClick={handleClose}>
            {copy.cancel}
          </Button>
          <Button onClick={startCalibration}>
            <Mic data-icon="inline-start" />
            {copy.whisperCalibrationRetry}
          </Button>
        </DialogFooter>
      </>
    )
  }

  return (
    <Dialog
      open
      onOpenChange={(nextOpen) => {
        if (!nextOpen) handleClose()
      }}
    >
      <DialogPortal>
        <DialogBackdrop />
        <DialogPopup>{body}</DialogPopup>
      </DialogPortal>
    </Dialog>
  )
}
