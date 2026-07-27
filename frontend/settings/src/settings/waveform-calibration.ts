export const WAVEFORM_NOISE_FLOOR_DBFS = -60
export const DEFAULT_WAVEFORM_CEILING_DBFS = -6
export const MIN_WAVEFORM_CEILING_DBFS = -30
export const MAX_WAVEFORM_CEILING_DBFS = 0

export function rmsToDbfs(rms: number): number {
  if (!Number.isFinite(rms) || rms <= 0) return Number.NEGATIVE_INFINITY
  return 20 * Math.log10(rms)
}

export function waveformLevelFromRms(
  rms: number,
  ceilingDbfs = DEFAULT_WAVEFORM_CEILING_DBFS,
): number {
  const dbfs = rmsToDbfs(rms)
  if (dbfs <= WAVEFORM_NOISE_FLOOR_DBFS) return 0
  const ceiling = Math.max(
    MIN_WAVEFORM_CEILING_DBFS,
    Math.min(MAX_WAVEFORM_CEILING_DBFS, ceilingDbfs),
  )
  return Math.max(
    0,
    Math.min(
      1,
      (dbfs - WAVEFORM_NOISE_FLOOR_DBFS) /
        (ceiling - WAVEFORM_NOISE_FLOOR_DBFS),
    ),
  )
}
