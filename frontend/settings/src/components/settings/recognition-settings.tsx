import {
  InlineValue,
  SettingsCard,
  SettingsPage,
  SettingsRow,
} from "@/components/settings/settings-card"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Input } from "@/components/ui/input"
import { setAsrModel, setConfig } from "@/settings/actions"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type { SettingsSnapshot } from "@/settings/types"

function backendLabel(backend: string | undefined, copy: SettingsCopy) {
  if (backend === "realtime_ws") return copy.backendRealtime
  if (backend === "short_file") return copy.backendShort
  if (backend === "omni_offline") return copy.backendOmni
  return "—"
}

function validWorkspaceEndpoint(value: string): boolean {
  return /^wss:\/\/[a-z0-9.-]+\.maas\.aliyuncs\.com\/api-ws\/v1\/realtime\/?$/i.test(
    value.trim(),
  )
}

export function RecognitionSettings({
  store,
  snapshot,
  copy,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const asr = snapshot.config.asr ?? {}
  const selected = snapshot.asrModels.find(
    (model) => model.id === asr.model,
  )
  const configuredEndpoint = asr.realtime_url ?? ""
  const [endpointMode, setEndpointMode] = useState<"public" | "workspace">(
    configuredEndpoint ? "workspace" : "public",
  )
  const [endpointDraft, setEndpointDraft] = useState(configuredEndpoint)

  useEffect(() => {
    setEndpointDraft(configuredEndpoint)
    setEndpointMode(configuredEndpoint ? "workspace" : "public")
  }, [configuredEndpoint])

  function commitWorkspaceEndpoint() {
    const endpoint = endpointDraft.trim().replace(/\/$/, "")
    if (!validWorkspaceEndpoint(endpoint)) return
    setEndpointDraft(endpoint)
    setConfig(store, "asr.realtime_url", endpoint)
  }

  return (
    <SettingsPage title={copy.recognition}>
      <SettingsCard>
        <SettingsRow label={copy.asrModel} htmlFor="asr-model">
          <NativeSelect
            id="asr-model"
            aria-label={copy.asrModel}
            className="h-8 w-64"
            value={asr.model ?? ""}
            onChange={(event) => {
              const model = snapshot.asrModels.find(
                (candidate) => candidate.id === event.target.value,
              )
              if (model?.id && model.transport) {
                setAsrModel(store, model.id, model.transport)
              }
            }}
          >
            {snapshot.asrModels.map((model, index) =>
              model.separator ? (
                <NativeSelectOption
                  key={`separator-${index}`}
                  disabled
                  value={`separator-${index}`}
                >
                  {model.display_name}
                </NativeSelectOption>
              ) : (
                <NativeSelectOption key={model.id} value={model.id}>
                  {model.display_name}
                </NativeSelectOption>
              ),
            )}
          </NativeSelect>
        </SettingsRow>
        <SettingsRow
          label={copy.backend}
          description={copy.backendHint}
        >
          <InlineValue>
            {backendLabel(selected?.transport ?? asr.backend, copy)}
          </InlineValue>
        </SettingsRow>
        <SettingsRow label={copy.language} htmlFor="asr-language">
          <NativeSelect
            id="asr-language"
            className="h-8 w-40"
            value={asr.language ?? "auto"}
            onChange={(event) =>
              setConfig(store, "asr.language", event.target.value)
            }
          >
            <NativeSelectOption value="auto">{copy.auto}</NativeSelectOption>
            <NativeSelectOption value="zh">{copy.chinese}</NativeSelectOption>
            <NativeSelectOption value="en">{copy.english}</NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
        <SettingsRow
          label={copy.realtimeEndpoint}
          description={copy.realtimeEndpointHint}
          htmlFor="realtime-endpoint-mode"
        >
          <NativeSelect
            id="realtime-endpoint-mode"
            className="h-8 w-52"
            value={endpointMode}
            onChange={(event) => {
              const mode = event.target.value === "workspace"
                ? "workspace"
                : "public"
              setEndpointMode(mode)
              if (mode === "public") {
                setConfig(store, "asr.realtime_url", "")
              }
            }}
          >
            <NativeSelectOption value="public">
              {copy.publicEndpoint}
            </NativeSelectOption>
            <NativeSelectOption value="workspace">
              {copy.workspaceEndpoint}
            </NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
        {endpointMode === "workspace" ? (
          <SettingsRow
            label={copy.workspaceEndpointUrl}
            description={copy.workspaceEndpointHint}
            htmlFor="workspace-endpoint-url"
          >
            <Input
              id="workspace-endpoint-url"
              className="h-8 w-80 font-mono text-xs"
              value={endpointDraft}
              placeholder="wss://WORKSPACE.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
              spellCheck={false}
              aria-invalid={
                endpointDraft.length > 0 &&
                !validWorkspaceEndpoint(endpointDraft)
              }
              onChange={(event) => setEndpointDraft(event.target.value)}
              onBlur={commitWorkspaceEndpoint}
              onKeyDown={(event) => {
                if (event.key === "Enter") commitWorkspaceEndpoint()
              }}
            />
          </SettingsRow>
        ) : null}
      </SettingsCard>
    </SettingsPage>
  )
}
import { useEffect, useState } from "react"
