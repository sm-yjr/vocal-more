import {
  MAX_WAVEFORM_CEILING_DBFS,
  MIN_WAVEFORM_CEILING_DBFS,
  rmsToDbfs,
} from "@/settings/waveform-calibration"

/**
 * Guided low-voice calibration.
 *
 * The microphone test stream reports RMS *after* the active software gain
 * (see AudioRecorder._audio_callback), so every level measured here is
 * expressed at the gain in force while recording. Recommending a new gain is
 * therefore a pure rescale of the measured whisper level:
 *
 *   recommended = current × 10^((target − measured) / 20)
 *
 * The target (−14 dBFS) leaves comfortable headroom below full scale for
 * consonant peaks while lifting a whisper well above the noise floor, and the
 * gain is clamped to [1.0, 50.0]: calibration prefers under-gain over any
 * risk of clipping, and never attenuates an already loud whisper below unity.
 */

export const CALIBRATION_TARGET_DBFS = -14
export const MIN_CALIBRATION_GAIN = 1.0
export const MAX_CALIBRATION_GAIN = 50.0
export const CEILING_HEADROOM_DB = 2
export const MIN_CALIBRATION_HIGHPASS_HZ = 220

/** Minimum RMS samples per phase before a recommendation is trustworthy. */
export const MIN_PHASE_SAMPLES = 8

/** Whisper must stand at least this far above the noise floor to calibrate. */
export const MIN_SIGNAL_HEADROOM_DB = 6

/**
 * Phase durations stay below the Python mic-test auto-stop (5 s) so the
 * wizard, not the backend, always owns the phase boundary.
 */
export const QUIET_PHASE_MS = 3000
export const WHISPER_PHASE_MS = 4500

export type CalibrationPhase = "quiet" | "whisper"

export function phaseDurationMs(phase: CalibrationPhase): number {
  return phase === "quiet" ? QUIET_PHASE_MS : WHISPER_PHASE_MS
}

export interface CalibrationInput {
  /** RMS samples (linear, post-gain) captured while the room was silent. */
  quietSamples: readonly number[]
  /** RMS samples (linear, post-gain) captured while the user whispered. */
  whisperSamples: readonly number[]
  /** Effective linear gain in force during the measurement. */
  currentGain: number
}

export interface CalibrationResultOk {
  status: "ok"
  noiseFloorDbfs: number
  whisperLevelDbfs: number
  recommendedGain: number
  recommendedCeilingDbfs: number
  /** True when the raw recommendation hit the 1.0 or 50.0 gain clamp. */
  gainClamped: boolean
}

export interface CalibrationResultUnavailable {
  status: "insufficient-samples" | "low-snr"
  noiseFloorDbfs: number | null
  whisperLevelDbfs: number | null
}

export type CalibrationResult =
  | CalibrationResultOk
  | CalibrationResultUnavailable

/**
 * Linear-interpolated percentile of the samples' dBFS values.
 * Zero / silent samples map to −∞ and are dropped; returns null when no
 * finite sample remains.
 */
export function percentileDbfs(
  samples: readonly number[],
  percentile: number,
): number | null {
  const dbfs = samples
    .map(rmsToDbfs)
    .filter((value) => Number.isFinite(value))
    .sort((a, b) => a - b)
  if (dbfs.length === 0) return null
  const rank = (dbfs.length - 1) * Math.min(Math.max(percentile, 0), 1)
  const lower = Math.floor(rank)
  const upper = Math.ceil(rank)
  if (lower === upper) return dbfs[lower]!
  const fraction = rank - lower
  return dbfs[lower]! * (1 - fraction) + dbfs[upper]! * fraction
}

export function recommendWhisperCalibration(
  input: CalibrationInput,
): CalibrationResult {
  const { quietSamples, whisperSamples } = input
  const currentGain = Math.max(Number(input.currentGain) || 1, 0.001)

  const noiseFloorDbfs = percentileDbfs(quietSamples, 0.5)
  const whisperLevelDbfs = percentileDbfs(whisperSamples, 0.9)

  if (
    quietSamples.length < MIN_PHASE_SAMPLES ||
    whisperSamples.length < MIN_PHASE_SAMPLES ||
    noiseFloorDbfs === null ||
    whisperLevelDbfs === null
  ) {
    return {
      status: "insufficient-samples",
      noiseFloorDbfs,
      whisperLevelDbfs,
    }
  }

  if (whisperLevelDbfs < noiseFloorDbfs + MIN_SIGNAL_HEADROOM_DB) {
    return { status: "low-snr", noiseFloorDbfs, whisperLevelDbfs }
  }

  const rawGain =
    currentGain *
    10 ** ((CALIBRATION_TARGET_DBFS - whisperLevelDbfs) / 20)
  const gainClamped =
    rawGain < MIN_CALIBRATION_GAIN || rawGain > MAX_CALIBRATION_GAIN
  const recommendedGain =
    Math.round(
      Math.min(Math.max(rawGain, MIN_CALIBRATION_GAIN), MAX_CALIBRATION_GAIN) *
        100,
    ) / 100

  // Re-project the measured whisper level onto the recommended gain so the
  // waveform full-scale follows what the capsule will actually see.
  const compensatedDbfs =
    whisperLevelDbfs + 20 * Math.log10(recommendedGain / currentGain)
  const recommendedCeilingDbfs = Math.min(
    Math.max(
      Math.round(compensatedDbfs + CEILING_HEADROOM_DB),
      MIN_WAVEFORM_CEILING_DBFS,
    ),
    MAX_WAVEFORM_CEILING_DBFS,
  )

  return {
    status: "ok",
    noiseFloorDbfs,
    whisperLevelDbfs,
    recommendedGain,
    recommendedCeilingDbfs,
    gainClamped,
  }
}

/**
 * The exact config writes "Apply recommendation" performs, in order. Kept
 * pure so the UI can show the full change list before writing anything.
 */
export function buildCalibrationChanges(
  result: CalibrationResultOk,
  audio: {
    highpass_freq?: unknown
    soft_limiter?: unknown
  },
): Array<{ key: string; value: unknown }> {
  const changes: Array<{ key: string; value: unknown }> = [
    { key: "audio.gain_mode", value: "manual" },
    { key: "audio.gain", value: result.recommendedGain },
    { key: "audio.highpass_filter", value: true },
  ]
  const highpassFreq =
    typeof audio.highpass_freq === "number" ? audio.highpass_freq : 200
  if (highpassFreq < MIN_CALIBRATION_HIGHPASS_HZ) {
    changes.push({
      key: "audio.highpass_freq",
      value: MIN_CALIBRATION_HIGHPASS_HZ,
    })
  }
  changes.push({
    key: "audio.waveform_ceiling_dbfs",
    value: result.recommendedCeilingDbfs,
  })
  if (audio.soft_limiter === false) {
    changes.push({ key: "audio.soft_limiter", value: true })
  }
  return changes
}

export function gainToDb(gain: number): number {
  return Math.round(20 * Math.log10(Math.max(gain, 0.001)))
}

export interface CalibrationChangeCopy {
  gainControl: string
  manualSoftwareGain: string
  softwareGain: string
  highpass: string
  cutoff: string
  waveformCalibration: string
  limiter: string
  on: string
}

/**
 * Human-readable rows for every write "Apply recommendation" performs, so the
 * confirmation screen never applies a setting it has not shown.
 */
export function describeCalibrationChanges(
  result: CalibrationResultOk,
  audio: { highpass_freq?: unknown; soft_limiter?: unknown },
  copy: CalibrationChangeCopy,
): Array<{ label: string; value: string }> {
  return buildCalibrationChanges(result, audio).map(({ key, value }) => {
    switch (key) {
      case "audio.gain_mode":
        return { label: copy.gainControl, value: copy.manualSoftwareGain }
      case "audio.gain": {
        const db = gainToDb(value as number)
        return {
          label: copy.softwareGain,
          value: `${db >= 0 ? "+" : ""}${db} dB`,
        }
      }
      case "audio.highpass_filter":
        return { label: copy.highpass, value: copy.on }
      case "audio.highpass_freq":
        return { label: copy.cutoff, value: `${value as number} Hz` }
      case "audio.waveform_ceiling_dbfs":
        return { label: copy.waveformCalibration, value: `${value as number} dBFS` }
      case "audio.soft_limiter":
        return { label: copy.limiter, value: copy.on }
      default:
        return { label: key, value: String(value) }
    }
  })
}

export type CalibrationStatus =
  | "idle"
  | "starting"
  | "recording"
  | "stopping"
  | "result"
  | "error"

export interface CalibrationState {
  status: CalibrationStatus
  phase: CalibrationPhase
  quietSamples: number[]
  whisperSamples: number[]
  result: CalibrationResult | null
  error: string | null
}

export const initialCalibrationState: CalibrationState = {
  status: "idle",
  phase: "quiet",
  quietSamples: [],
  whisperSamples: [],
  result: null,
  error: null,
}

export type CalibrationEvent =
  | { type: "start" }
  | { type: "micStarted" }
  | { type: "sample"; level: number }
  | { type: "stopPhase" }
  | { type: "micCompleted"; currentGain: number }
  | { type: "micFailed"; message: string }
  | { type: "cancel" }

/**
 * Two-phase measurement state machine. Side effects (startMicTest /
 * stopMicTest messages, phase timers) live in the component; this reducer is
 * pure and only advances on events that match the current status, so stray
 * callbacks from a stale mic-test session can never corrupt a run.
 */
export function calibrationReducer(
  state: CalibrationState,
  event: CalibrationEvent,
): CalibrationState {
  switch (event.type) {
    case "start":
      return {
        status: "starting",
        phase: "quiet",
        quietSamples: [],
        whisperSamples: [],
        result: null,
        error: null,
      }
    case "micStarted":
      if (state.status !== "starting") return state
      return { ...state, status: "recording" }
    case "sample":
      if (state.status !== "recording") return state
      return state.phase === "quiet"
        ? { ...state, quietSamples: [...state.quietSamples, event.level] }
        : { ...state, whisperSamples: [...state.whisperSamples, event.level] }
    case "stopPhase":
      if (state.status !== "recording") return state
      return { ...state, status: "stopping" }
    case "micCompleted":
      if (state.status !== "recording" && state.status !== "stopping") {
        return state
      }
      if (state.phase === "quiet") {
        return { ...state, status: "starting", phase: "whisper" }
      }
      return {
        ...state,
        status: "result",
        result: recommendWhisperCalibration({
          quietSamples: state.quietSamples,
          whisperSamples: state.whisperSamples,
          currentGain: event.currentGain,
        }),
      }
    case "micFailed":
      if (
        state.status === "idle" ||
        state.status === "result" ||
        state.status === "error"
      ) {
        return state
      }
      return { ...state, status: "error", error: event.message }
    case "cancel":
      return initialCalibrationState
    default:
      return state
  }
}
