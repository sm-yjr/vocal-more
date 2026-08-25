import { describe, expect, it } from "vitest"

import {
  buildCalibrationChanges,
  calibrationReducer,
  describeCalibrationChanges,
  gainToDb,
  initialCalibrationState,
  MAX_CALIBRATION_GAIN,
  MIN_CALIBRATION_GAIN,
  MIN_PHASE_SAMPLES,
  percentileDbfs,
  phaseDurationMs,
  recommendWhisperCalibration,
  type CalibrationResultOk,
  type CalibrationState,
} from "@/settings/whisper-calibration"

function rmsAtDbfs(dbfs: number): number {
  return 10 ** (dbfs / 20)
}

function samplesAt(dbfs: number, count = MIN_PHASE_SAMPLES + 2): number[] {
  // Strictly increasing (like distinct live samples) but negligibly spaced.
  return Array.from({ length: count }, (_, i) => rmsAtDbfs(dbfs + i * 0.0001))
}

const okResult: CalibrationResultOk = {
  status: "ok",
  noiseFloorDbfs: -55,
  whisperLevelDbfs: -34,
  recommendedGain: 20,
  recommendedCeilingDbfs: -12,
  gainClamped: false,
}

describe("percentileDbfs", () => {
  it("interpolates percentiles over the dBFS-sorted samples", () => {
    const samples = [rmsAtDbfs(-30), rmsAtDbfs(-20)]
    expect(percentileDbfs(samples, 0)).toBeCloseTo(-30, 3)
    expect(percentileDbfs(samples, 0.5)).toBeCloseTo(-25, 3)
    expect(percentileDbfs(samples, 1)).toBeCloseTo(-20, 3)
  })

  it("drops silent samples and reports null when nothing is left", () => {
    expect(percentileDbfs([0, 0], 0.5)).toBeNull()
    expect(percentileDbfs([], 0.5)).toBeNull()
    expect(percentileDbfs([0, rmsAtDbfs(-40)], 0.5)).toBeCloseTo(-40, 3)
  })
})

describe("recommendWhisperCalibration", () => {
  it("recommends the gain that lifts the whisper to the −14 dBFS target", () => {
    const result = recommendWhisperCalibration({
      quietSamples: samplesAt(-55),
      whisperSamples: samplesAt(-34),
      currentGain: 2,
    })

    expect(result.status).toBe("ok")
    if (result.status !== "ok") return
    expect(result.noiseFloorDbfs).toBeCloseTo(-55, 2)
    expect(result.whisperLevelDbfs).toBeCloseTo(-34, 2)
    // 2 × 10^((−14 − (−34)) / 20) = 20
    expect(result.recommendedGain).toBeCloseTo(20, 1)
    expect(result.gainClamped).toBe(false)
    // Whisper lands at −14 dBFS after compensation; ceiling sits 2 dB above.
    expect(result.recommendedCeilingDbfs).toBe(-12)
  })

  it("clamps the recommendation at the 50× safe ceiling", () => {
    const result = recommendWhisperCalibration({
      quietSamples: samplesAt(-70),
      whisperSamples: samplesAt(-60),
      currentGain: 1,
    })

    expect(result.status).toBe("ok")
    if (result.status !== "ok") return
    expect(result.recommendedGain).toBe(MAX_CALIBRATION_GAIN)
    expect(result.gainClamped).toBe(true)
    // −60 dBFS + 20·log10(50) ≈ −26 dBFS → ceiling −24.
    expect(result.recommendedCeilingDbfs).toBe(-24)
  })

  it("never attenuates an already loud whisper below unity gain", () => {
    const result = recommendWhisperCalibration({
      quietSamples: samplesAt(-50),
      whisperSamples: samplesAt(-10),
      currentGain: 1,
    })

    expect(result.status).toBe("ok")
    if (result.status !== "ok") return
    expect(result.recommendedGain).toBe(MIN_CALIBRATION_GAIN)
    expect(result.gainClamped).toBe(true)
    expect(result.recommendedCeilingDbfs).toBe(-8)
  })

  it("keeps the ceiling inside the −30..0 dBFS waveform range", () => {
    const loud = recommendWhisperCalibration({
      quietSamples: samplesAt(-55),
      whisperSamples: samplesAt(-1),
      currentGain: 1,
    })
    expect(loud.status).toBe("ok")
    if (loud.status === "ok") {
      expect(loud.recommendedCeilingDbfs).toBe(0)
    }
  })

  it("refuses to recommend when a phase captured too few samples", () => {
    const short = samplesAt(-55, MIN_PHASE_SAMPLES - 1)
    expect(
      recommendWhisperCalibration({
        quietSamples: short,
        whisperSamples: samplesAt(-34),
        currentGain: 2,
      }).status,
    ).toBe("insufficient-samples")
    expect(
      recommendWhisperCalibration({
        quietSamples: samplesAt(-55),
        whisperSamples: [],
        currentGain: 2,
      }).status,
    ).toBe("insufficient-samples")
  })

  it("treats an all-silent phase as insufficient data", () => {
    const result = recommendWhisperCalibration({
      quietSamples: new Array(MIN_PHASE_SAMPLES + 2).fill(0),
      whisperSamples: samplesAt(-34),
      currentGain: 2,
    })
    expect(result.status).toBe("insufficient-samples")
    expect(result.noiseFloorDbfs).toBeNull()
  })

  it("requires the whisper to stand 6 dB above the noise floor", () => {
    expect(
      recommendWhisperCalibration({
        quietSamples: samplesAt(-40),
        whisperSamples: samplesAt(-36),
        currentGain: 2,
      }).status,
    ).toBe("low-snr")

    const borderline = recommendWhisperCalibration({
      quietSamples: samplesAt(-40),
      whisperSamples: samplesAt(-34),
      currentGain: 2,
    })
    expect(borderline.status).toBe("ok")
  })

  it("survives a non-positive current gain", () => {
    const result = recommendWhisperCalibration({
      quietSamples: samplesAt(-55),
      whisperSamples: samplesAt(-34),
      currentGain: 0,
    })
    expect(result.status).toBe("ok")
    if (result.status !== "ok") return
    expect(result.recommendedGain).toBeGreaterThanOrEqual(1)
    expect(Number.isFinite(result.recommendedGain)).toBe(true)
  })
})

describe("buildCalibrationChanges", () => {
  it("writes manual gain, high-pass floor, and the waveform ceiling", () => {
    const changes = buildCalibrationChanges(okResult, {
      highpass_freq: 280,
      soft_limiter: true,
    })
    expect(changes).toEqual([
      { key: "audio.gain_mode", value: "manual" },
      { key: "audio.gain", value: 20 },
      { key: "audio.highpass_filter", value: true },
      { key: "audio.waveform_ceiling_dbfs", value: -12 },
    ])
  })

  it("raises a low cutoff and re-enables a disabled limiter", () => {
    const changes = buildCalibrationChanges(okResult, {
      highpass_freq: 200,
      soft_limiter: false,
    })
    expect(changes).toContainEqual({ key: "audio.highpass_freq", value: 220 })
    expect(changes).toContainEqual({ key: "audio.soft_limiter", value: true })
  })

  it("defaults a missing cutoff to 200 Hz and bumps it", () => {
    const changes = buildCalibrationChanges(okResult, {})
    expect(changes).toContainEqual({ key: "audio.highpass_freq", value: 220 })
  })
})

describe("describeCalibrationChanges", () => {
  const copy = {
    gainControl: "Gain control",
    manualSoftwareGain: "Manual software gain",
    softwareGain: "Software gain",
    highpass: "High-pass filter",
    cutoff: "Cutoff frequency",
    waveformCalibration: "Waveform full-scale level",
    limiter: "Soft limiter",
    on: "On",
  }

  it("labels every change the apply step will write", () => {
    const rows = describeCalibrationChanges(
      okResult,
      { highpass_freq: 200, soft_limiter: false },
      copy,
    )
    expect(rows).toEqual([
      { label: "Gain control", value: "Manual software gain" },
      { label: "Software gain", value: "+26 dB" },
      { label: "High-pass filter", value: "On" },
      { label: "Cutoff frequency", value: "220 Hz" },
      { label: "Waveform full-scale level", value: "-12 dBFS" },
      { label: "Soft limiter", value: "On" },
    ])
  })
})

describe("gainToDb", () => {
  it("expresses linear gain in decibels", () => {
    expect(gainToDb(1)).toBe(0)
    expect(gainToDb(20)).toBe(26)
    expect(gainToDb(0.5)).toBe(-6)
  })
})

describe("phaseDurationMs", () => {
  it("keeps both phases under the 5 s backend auto-stop", () => {
    expect(phaseDurationMs("quiet")).toBeLessThan(5000)
    expect(phaseDurationMs("whisper")).toBeLessThan(5000)
    expect(phaseDurationMs("whisper")).toBeGreaterThan(
      phaseDurationMs("quiet"),
    )
  })
})

describe("calibrationReducer", () => {
  function run(
    events: Parameters<typeof calibrationReducer>[1][],
    from: CalibrationState = initialCalibrationState,
  ): CalibrationState {
    return events.reduce(calibrationReducer, from)
  }

  const startedQuiet: CalibrationState = {
    ...initialCalibrationState,
    status: "recording",
    phase: "quiet",
  }

  it("starts a fresh two-phase run and ignores stray lifecycle events", () => {
    const state = run([{ type: "start" }])
    expect(state).toMatchObject({
      status: "starting",
      phase: "quiet",
      quietSamples: [],
      whisperSamples: [],
      result: null,
      error: null,
    })

    expect(run([{ type: "micStarted" }])).toBe(initialCalibrationState)
    expect(run([{ type: "stopPhase" }])).toBe(initialCalibrationState)
    expect(run([{ type: "micCompleted", currentGain: 2 }])).toBe(
      initialCalibrationState,
    )
    expect(run([{ type: "micFailed", message: "x" }])).toBe(
      initialCalibrationState,
    )
  })

  it("collects samples only while recording the matching phase", () => {
    let state = run([{ type: "start" }, { type: "micStarted" }])
    state = calibrationReducer(state, { type: "sample", level: 0.02 })
    state = calibrationReducer(state, { type: "sample", level: 0.03 })
    expect(state.quietSamples).toEqual([0.02, 0.03])
    expect(state.whisperSamples).toEqual([])

    const stopping = calibrationReducer(state, { type: "stopPhase" })
    expect(stopping.status).toBe("stopping")
    expect(
      calibrationReducer(stopping, { type: "sample", level: 0.04 })
        .quietSamples,
    ).toEqual([0.02, 0.03])
  })

  it("advances from the quiet phase into the whisper phase", () => {
    let state: CalibrationState = {
      ...startedQuiet,
      quietSamples: [0.001, 0.002],
    }
    state = calibrationReducer(state, { type: "stopPhase" })
    state = calibrationReducer(state, { type: "micCompleted", currentGain: 2 })
    expect(state).toMatchObject({
      status: "starting",
      phase: "whisper",
      quietSamples: [0.001, 0.002],
    })
  })

  it("accepts an early backend auto-stop while still recording", () => {
    const state = run(
      [
        { type: "start" },
        { type: "micStarted" },
        { type: "micCompleted", currentGain: 2 },
      ],
    )
    expect(state.phase).toBe("whisper")
    expect(state.status).toBe("starting")
  })

  it("computes the recommendation when the whisper phase completes", () => {
    let state: CalibrationState = {
      status: "recording",
      phase: "whisper",
      quietSamples: samplesAt(-55),
      whisperSamples: samplesAt(-34),
      result: null,
      error: null,
    }
    state = calibrationReducer(state, {
      type: "micCompleted",
      currentGain: 2,
    })
    expect(state.status).toBe("result")
    expect(state.result?.status).toBe("ok")
  })

  it("records microphone failures and recovers through retry", () => {
    let state = calibrationReducer(startedQuiet, {
      type: "micFailed",
      message: "busy",
    })
    expect(state).toMatchObject({ status: "error", error: "busy" })

    state = calibrationReducer(state, { type: "start" })
    expect(state.status).toBe("starting")
    expect(state.error).toBeNull()
  })

  it("cancels back to the initial state", () => {
    expect(calibrationReducer(startedQuiet, { type: "cancel" })).toBe(
      initialCalibrationState,
    )
  })
})
