import {
  Clipboard,
  FileText,
  Play,
  RotateCcw,
  Sparkles,
  Trash2,
  Volume2,
} from "lucide-react"
import { useEffect, useMemo, useRef } from "react"

import {
  InlineValue,
  SettingsCard,
  SettingsPage,
  SettingsRow,
} from "@/components/settings/settings-card"
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Spinner } from "@/components/ui/spinner"
import { sendAction } from "@/settings/actions"
import {
  formatCost,
  formatDuration,
  normalizeMeeting,
  recordingTimestamp,
} from "@/settings/history-utils"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type {
  MeetingMinutes,
  MeetingNotes,
  Recording,
  SettingsSnapshot,
} from "@/settings/types"

function displayTime(timestamp: string, language: string, copy: SettingsCopy) {
  const date = new Date(timestamp)
  if (Number.isNaN(date.getTime())) return "—"
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86_400_000)
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const locale = language === "en" ? "en-US" : "zh-CN"
  const time = date.toLocaleTimeString(locale, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
  if (day.getTime() === today.getTime()) return `${copy.today} ${time}`
  if (day.getTime() === yesterday.getTime()) {
    return `${copy.yesterday} ${time}`
  }
  return `${date.toLocaleDateString(locale, {
    month: "short",
    day: "numeric",
  })} ${time}`
}

function statusLabel(recording: Recording, copy: SettingsCopy) {
  if (recording.status === "success") return copy.success
  if (recording.status === "failed") return copy.failed
  if (recording.status === "retrying") return copy.retrying
  return copy.pending
}

function modeLabel(mode: string | undefined, copy: SettingsCopy) {
  if (mode === "meeting") return copy.meetingMode
  if (mode === "realtime_long") return copy.realtimeLong
  return copy.walkieTalkie
}

function formatStorageBytes(bytes: number | undefined): string {
  const value = Math.max(0, Number(bytes ?? 0))
  if (value < 1024) return `${Math.round(value)} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

function BillingSummary({
  recordings,
  copy,
}: {
  recordings: Recording[]
  copy: SettingsCopy
}) {
  const totals = useMemo(
    () =>
      recordings.reduce(
        (sum, recording) => {
          const billing = recording.billing ?? {}
          sum.total += Number(billing.total_cost_cny ?? 0)
          sum.asr += Number(billing.asr_cost_cny ?? 0)
          sum.polish += Number(billing.polish_cost_cny ?? 0)
          return sum
        },
        { total: 0, asr: 0, polish: 0 },
      ),
    [recordings],
  )
  return (
    <div className="grid grid-cols-3 gap-2">
      {[
        [copy.totalCost, totals.total],
        [copy.asrCost, totals.asr],
        [copy.polishCost, totals.polish],
      ].map(([label, value]) => (
        <SettingsCard key={String(label)}>
          <div className="p-3">
            <div className="text-[11px] text-muted-foreground">{label}</div>
            <div className="mt-1 text-base font-semibold tabular-nums">
              {formatCost(value)}
            </div>
          </div>
        </SettingsCard>
      ))}
    </div>
  )
}

function Minutes({
  minutes,
  copy,
}: {
  minutes: MeetingMinutes
  copy: SettingsCopy
}) {
  if (minutes.status === "pending") {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Spinner /> {copy.summarizing}
      </div>
    )
  }
  if (minutes.status === "failed") {
    return (
      <p className="text-xs text-destructive">
        {minutes.error || copy.meetingFailed}
      </p>
    )
  }
  return (
    <div className="flex flex-col gap-2">
      {minutes.summary ? (
        <p className="text-xs leading-relaxed">{minutes.summary}</p>
      ) : null}
      {[
        [copy.keyPoints, minutes.key_points],
        [copy.actionItems, minutes.action_items],
      ].map(([label, items]) =>
        Array.isArray(items) && items.length ? (
          <div key={String(label)}>
            <div className="text-[11px] font-medium text-muted-foreground">
              {label}
            </div>
            <ul className="mt-1 list-disc pl-4 text-xs leading-relaxed">
              {items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
    </div>
  )
}

function MeetingView({
  raw,
  copy,
}: {
  raw: MeetingNotes
  copy: SettingsCopy
}) {
  const meeting = normalizeMeeting(raw)
  if (meeting.status === "failed") {
    return (
      <p className="text-xs text-destructive">
        {meeting.error || copy.meetingFailed}
      </p>
    )
  }
  if (
    meeting.status === "transcribing" ||
    meeting.status === "summarizing"
  ) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Spinner />
        {meeting.status === "summarizing"
          ? copy.summarizing
          : copy.transcribing}
      </div>
    )
  }
  const minutes =
    meeting.minutes ??
    (meeting.summary || meeting.key_points || meeting.action_items
      ? {
          status: "success",
          summary: meeting.summary,
          key_points: meeting.key_points,
          action_items: meeting.action_items,
        }
      : null)
  return (
    <div className="flex flex-col gap-3">
      {meeting.segments?.length ? (
        <div>
          <div className="mb-2 text-[11px] font-medium text-muted-foreground">
            {copy.speakerTranscript}
          </div>
          <div className="flex flex-col gap-2">
            {meeting.segments.map((segment, index) => (
              <div
                key={`${segment.timestamp}-${index}`}
                className="grid grid-cols-[38px_1fr] gap-2 text-xs"
              >
                <span className="font-mono text-[10px] text-muted-foreground">
                  {segment.timestamp ?? ""}
                </span>
                <div>
                  <span className="font-medium">
                    {segment.speaker_label ?? segment.speaker ?? "Speaker"}
                  </span>
                  <p className="mt-0.5 leading-relaxed">{segment.text}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : meeting.transcript ? (
        <p className="text-xs leading-relaxed">{meeting.transcript}</p>
      ) : null}
      {minutes ? (
        <div className="rounded-lg border bg-muted/20 p-3">
          <div className="mb-2 text-[11px] font-semibold">{copy.minutes}</div>
          <Minutes minutes={minutes} copy={copy} />
        </div>
      ) : null}
    </div>
  )
}

function RecordingCard({
  recording,
  store,
  snapshot,
  copy,
}: {
  recording: Recording
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const language = snapshot.config.ui?.language ?? "zh"
  const model = snapshot.asrModels.find(
    (item) => item.id === recording.asr_model,
  )
  const duration =
    typeof recording.duration_seconds === "number"
      ? recording.duration_seconds
      : recording.duration ?? 0
  const retrying = recording.status === "retrying"
  const meetingGenerating =
    recording.meeting_status === "generating" ||
    ["transcribing", "summarizing"].includes(
      recording.meeting?.status ?? "",
    )
  const accessibleText = recording.transcript || recording.id

  function stageDelete() {
    const previous = store.commitRecordingDeletion()
    if (previous) sendAction("deleteRecording", { id: previous })
    store.stopAudio()
    store.stageRecordingDeletion(recording.id)
  }

  function copyTranscript() {
    if (!recording.transcript) return
    const textarea = document.createElement("textarea")
    textarea.value = recording.transcript
    textarea.style.position = "fixed"
    textarea.style.opacity = "0"
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand?.("copy")
    textarea.remove()
    store.copiedFeedback(recording.id)
  }

  return (
    <Card
      id={`rec-${recording.id}`}
      className="gap-0 py-0 shadow-none"
      data-focused={snapshot.focusRecordingId === recording.id || undefined}
    >
      <CardHeader className="flex-row items-start justify-between gap-3 px-4 py-3 max-[600px]:flex-col">
        <div className="min-w-0">
          <div className="text-xs font-medium">
            {displayTime(recordingTimestamp(recording), language, copy)}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Badge variant="secondary">
              {modeLabel(recording.mode, copy)}
            </Badge>
            <Badge variant="outline">
              {model?.display_name ?? recording.asr_model ?? copy.asrModel}
            </Badge>
            <Badge variant="outline">
              {formatDuration(
                duration,
                copy.durationMinutes,
                copy.durationSeconds,
              )}
            </Badge>
            <Badge
              variant={
                recording.status === "failed"
                  ? "destructive"
                  : "outline"
              }
            >
              {statusLabel(recording, copy)}
            </Badge>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1 max-[600px]:self-end">
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={retrying}
            aria-label={`${copy.play} ${accessibleText}`}
            onClick={() => {
              if (snapshot.playingRecordingId === recording.id) {
                store.stopAudio()
              } else {
                sendAction("playRecording", { id: recording.id })
              }
            }}
          >
            {snapshot.playingRecordingId === recording.id ? (
              <Volume2 />
            ) : (
              <Play />
            )}
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={retrying}
            aria-label={`${copy.retry} ${accessibleText}`}
            onClick={() => {
              store.retryStarted(recording.id)
              sendAction("retryTranscription", { id: recording.id })
            }}
          >
            <RotateCcw />
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={retrying || meetingGenerating}
            aria-label={`${copy.meetingNotes} ${accessibleText}`}
            onClick={() => {
              store.meetingNotesStarted(recording.id)
              sendAction("generateMeetingNotes", { id: recording.id })
            }}
          >
            <Sparkles />
          </Button>
          {recording.status === "success" && recording.transcript ? (
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={`${copy.copy} ${accessibleText}`}
              onClick={copyTranscript}
            >
              <Clipboard />
            </Button>
          ) : null}
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={retrying}
            className="text-muted-foreground hover:text-destructive"
            aria-label={`${copy.deleteAria} ${accessibleText}`}
            onClick={stageDelete}
          >
            <Trash2 />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="border-t px-4 py-3">
        {retrying ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Spinner /> {copy.retrying}
          </div>
        ) : recording.meeting ? (
          <MeetingView raw={recording.meeting} copy={copy} />
        ) : recording.status === "failed" ? (
          <p className="text-xs text-destructive">
            {recording.error || copy.transcriptFailed}
          </p>
        ) : (
          <p className="text-xs leading-relaxed">
            {recording.transcript || copy.pending}
          </p>
        )}
        {recording.billing &&
        Number(recording.billing.total_cost_cny ?? 0) > 0 ? (
          <div className="mt-2 text-[10px] text-muted-foreground">
            {copy.cost}{" "}
            {formatCost(recording.billing.total_cost_cny)}
            {recording.billing.estimated ? ` · ${copy.estimated}` : ""}
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

export function HistorySettings({
  store,
  snapshot,
  copy,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const pendingId =
    snapshot.pendingRecordingDeletion?.recording.id ?? null
  const storage = snapshot.recordingStorage
  const storageSummary = copy.historyStorageSummary
    .replace(
      "{compressed}",
      String(storage.compressed_count ?? 0),
    )
    .replace("{total}", String(storage.recording_count ?? 0))
    .replace(
      "{saved}",
      formatStorageBytes(storage.bytes_saved),
    )

  useEffect(() => {
    if (!pendingId) return
    const timer = window.setTimeout(() => {
      const id = store.commitRecordingDeletion()
      if (id) sendAction("deleteRecording", { id })
    }, 5000)
    return () => window.clearTimeout(timer)
  }, [pendingId, store])

  useEffect(() => {
    const id = snapshot.playingRecordingId
    if (!id) return
    if (snapshot.playbackBase64 === null) {
      return () => sendAction("stopRecording", { id })
    }
    audioRef.current?.pause()
    const audio = new Audio(
      `data:audio/wav;base64,${snapshot.playbackBase64}`,
    )
    audioRef.current = audio
    audio.onended = () => store.stopAudio()
    void audio.play()
    return () => {
      audio.pause()
      audio.removeAttribute("src")
      audio.load()
      if (audioRef.current === audio) audioRef.current = null
    }
  }, [
    snapshot.playbackBase64,
    snapshot.playingRecordingId,
    store,
  ])

  useEffect(() => {
    if (!snapshot.copiedRecordingId) return
    const timer = window.setTimeout(() => store.clearCopiedFeedback(), 1500)
    return () => window.clearTimeout(timer)
  }, [snapshot.copiedRecordingId, store])

  return (
    <SettingsPage title={copy.historyTitle}>
      <SettingsCard
        title={copy.historyCompression}
        description={copy.historyCompressionHint}
      >
        <SettingsRow
          label={copy.historyStorage}
          description={
            snapshot.recordingCompactionError
              ? `${copy.historyCompressionError}: ${snapshot.recordingCompactionError}`
              : undefined
          }
        >
          <div className="flex flex-col items-end gap-1.5">
            <InlineValue>{storageSummary}</InlineValue>
            <Button
              size="sm"
              variant="outline"
              disabled={
                snapshot.recordingCompacting ||
                (storage.recording_count ?? 0) <= 3
              }
              onClick={() => sendAction("compactRecordingHistory")}
            >
              {snapshot.recordingCompacting ? (
                <>
                  <Spinner /> {copy.historyCompressionRunning}
                </>
              ) : (
                copy.historyCompressionAction
              )}
            </Button>
          </div>
        </SettingsRow>
      </SettingsCard>

      {snapshot.pendingRecordingDeletion ? (
        <Alert>
          <AlertDescription>{copy.deletedPending}</AlertDescription>
          <AlertAction>
            <Button
              size="sm"
              variant="outline"
              onClick={() => store.undoRecordingDeletion()}
            >
              {copy.undoDelete}
            </Button>
          </AlertAction>
        </Alert>
      ) : null}

      {snapshot.recordings.length ? (
        <>
          <BillingSummary recordings={snapshot.recordings} copy={copy} />
          <div className="flex flex-col gap-2">
            {snapshot.recordings.map((recording) => (
              <RecordingCard
                key={recording.id}
                recording={recording}
                store={store}
                snapshot={snapshot}
                copy={copy}
              />
            ))}
          </div>
        </>
      ) : (
        <SettingsCard>
          <Empty className="min-h-64">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <FileText />
              </EmptyMedia>
              <EmptyTitle>{copy.noRecordings}</EmptyTitle>
              <EmptyDescription>{copy.noRecordingsHint}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        </SettingsCard>
      )}
    </SettingsPage>
  )
}
