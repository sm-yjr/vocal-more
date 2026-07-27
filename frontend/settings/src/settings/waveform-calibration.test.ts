import { describe, expect, it } from "vitest"

import {
  rmsToDbfs,
  waveformLevelFromRms,
} from "@/settings/waveform-calibration"

function rmsAtDbfs(dbfs: number): number {
  return 10 ** (dbfs / 20)
}

describe("waveform calibration", () => {
  it.each([
    [-61.8, 0],
    [-38.7, 21.3 / 54],
    [-29.1, 30.9 / 54],
    [-9.6, 50.4 / 54],
  ])("maps %s dBFS to the measured display level", (dbfs, expected) => {
    expect(waveformLevelFromRms(rmsAtDbfs(dbfs))).toBeCloseTo(
      expected,
      3,
    )
  })

  it("reports RMS in dBFS for the calibration readout", () => {
    expect(rmsToDbfs(rmsAtDbfs(-29.1))).toBeCloseTo(-29.1, 3)
  })
})
