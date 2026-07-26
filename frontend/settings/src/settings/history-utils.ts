import type {
  MeetingNotes,
  MeetingSegment,
  Recording,
} from "@/settings/types"

export function formatDuration(
  seconds: number,
  minuteSuffix: string,
  secondSuffix: string,
): string {
  if (seconds < 60) return `${Math.round(seconds)}${secondSuffix}`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return `${minutes}${minuteSuffix}${remainder ? ` ${remainder}${secondSuffix}` : ""}`
}

export function formatCost(value: unknown): string {
  const amount = Number(value || 0)
  if (!(amount > 0)) return "¥0"
  const decimals = amount < 0.01 ? 6 : amount < 1 ? 4 : 2
  return `¥${amount.toFixed(decimals).replace(/0+$/, "").replace(/\.$/, "")}`
}

export function recordingTimestamp(recording: Recording): string {
  return recording.timestamp?.toString() ?? recording.created_at ?? ""
}

export function formatMeetingTimestamp(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return ""
  const total = Math.floor(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const remainder = total % 60
  const pad = (value: number) => String(value).padStart(2, "0")
  return hours
    ? `${pad(hours)}:${pad(minutes)}:${pad(remainder)}`
    : `${pad(minutes)}:${pad(remainder)}`
}

function repairMeetingJson(text: string): string {
  return text
    .replace(
      /,\s*"((?:\\.|[^"\\])*)"\s*}/g,
      ', "text": "$1"}',
    )
    .replace(/,(\s*[}\]])/g, "$1")
}

function parseEmbeddedMeeting(text: string): MeetingNotes | null {
  for (const candidate of [text, repairMeetingJson(text)]) {
    try {
      return JSON.parse(candidate) as MeetingNotes
    } catch {
      // Try the repaired candidate next.
    }
  }
  return null
}

function normalizeSpeaker(value: unknown): string {
  const text = String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_")
  return ["speaker_2", "speaker2", "2", "b", "speaker_b"].includes(
    text,
  )
    ? "speaker_2"
    : "speaker_1"
}

function coerceSeconds(value: unknown): number {
  if (typeof value === "number") return Math.max(0, value)
  const text = String(value ?? "").trim()
  if (!text) return Number.NaN
  if (text.includes(":")) {
    const parts = text.split(":").map(Number)
    if (parts.some((part) => !Number.isFinite(part))) return Number.NaN
    if (parts.length === 2) return Math.max(0, parts[0] * 60 + parts[1])
    if (parts.length === 3) {
      return Math.max(0, parts[0] * 3600 + parts[1] * 60 + parts[2])
    }
    return Number.NaN
  }
  const number = Number(text.replace(/seconds?|secs?|s$/i, "").trim())
  return Number.isFinite(number) ? Math.max(0, number) : Number.NaN
}

function normalizeSegments(
  segments: MeetingSegment[],
  speakers: unknown[] | undefined,
): MeetingSegment[] {
  const labels: Record<string, string> = {}
  for (const raw of speakers ?? []) {
    if (!raw || typeof raw !== "object") continue
    const speaker = raw as Record<string, unknown>
    const id = normalizeSpeaker(speaker.id ?? speaker.speaker)
    labels[id] = String(
      speaker.label ??
        speaker.name ??
        (id === "speaker_2" ? "Speaker 2" : "Speaker 1"),
    )
  }
  return segments
    .filter((segment) => segment && typeof segment === "object")
    .map((segment) => {
      const raw = segment as MeetingSegment & Record<string, unknown>
      const speaker = normalizeSpeaker(raw.speaker ?? raw.speaker_id)
      const start = coerceSeconds(raw.start_seconds ?? raw.start)
      return {
        ...segment,
        speaker,
        speaker_label:
          labels[speaker] ??
          (speaker === "speaker_2" ? "Speaker 2" : "Speaker 1"),
        text: String(raw.text ?? raw.content ?? "").trim(),
        ...(Number.isFinite(start)
          ? {
              start_seconds: start,
              timestamp: formatMeetingTimestamp(start),
            }
          : {}),
      }
    })
    .filter((segment) => Boolean(segment.text))
}

export function normalizeMeeting(meeting: MeetingNotes): MeetingNotes {
  const segments = Array.isArray(meeting.segments)
    ? meeting.segments
    : []
  if (segments.length !== 1) return meeting
  const text = String(segments[0]?.text ?? "").trim()
  if (!text.startsWith("{") || !text.includes('"segments"')) return meeting
  const parsed = parseEmbeddedMeeting(text)
  if (!parsed || !Array.isArray(parsed.segments)) return meeting
  return {
    ...meeting,
    speakers: Array.isArray(parsed.speakers)
      ? parsed.speakers
      : meeting.speakers,
    speaker_count: parsed.speaker_count ?? meeting.speaker_count,
    segments: normalizeSegments(
      parsed.segments,
      parsed.speakers ?? meeting.speakers,
    ),
  }
}
