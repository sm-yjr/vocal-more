import { useState } from "react"

import {
  InlineValue,
  SettingsCard,
  SettingsPage,
  SettingsRow,
} from "@/components/settings/settings-card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group"
import { sendAction, setConfig } from "@/settings/actions"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type {
  PromptOverride,
  SettingsSnapshot,
} from "@/settings/types"

type PromptCategory =
  | "output_type"
  | "level"
  | "structured"
  | "tone"
  | "persona"

function sliderNumber(value: number | readonly number[]): number {
  return typeof value === "number" ? value : (value[0] ?? 0)
}

function promptPresetKey(
  category: PromptCategory,
  llm: SettingsSnapshot["config"]["llm"],
): string {
  if (category === "output_type") return llm?.polish_mode ?? "dictation"
  if (category === "level") return llm?.level ?? "minimal"
  if (category === "structured") return "enabled"
  if (category === "tone") return llm?.tone ?? "neutral"
  return llm?.persona ?? "default"
}

export function PolishSettings({
  store,
  snapshot,
  copy,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const llm = snapshot.config.llm ?? {}
  const appContext = snapshot.config.context_personalization ?? {}
  const advanced = snapshot.config.ui?.advanced_settings === true
  const enabled = snapshot.config.enable_polish !== false
  const [category, setCategory] =
    useState<PromptCategory>("output_type")
  const asrModel = snapshot.asrModels.find(
    (model) => model.id === snapshot.config.asr?.model,
  )
  const inlinePolish = asrModel?.handles_inline_polish === true
  const selectedLlm = snapshot.llmModels.find(
    (model) => model.id === llm.model,
  )
  const llmEnabled = enabled && !inlinePolish
  const overrides = llm.prompt_overrides ?? {}
  const override = overrides[category] ?? {
    enabled: false,
    prompt: "",
  }
  const preset =
    snapshot.polishPromptPresets[category]?.[
      promptPresetKey(category, llm)
    ] ?? ""
  const contextCounts = snapshot.contextProfile.counts
  const contextSummary = [
    `${copy.contextDevelopment} ${contextCounts.development ?? 0}`,
    `${copy.contextTerminal} ${contextCounts.terminal ?? 0}`,
    `${copy.contextMessaging} ${contextCounts.messaging ?? 0}`,
    `${copy.contextWriting} ${contextCounts.writing ?? 0}`,
    `${copy.contextGeneral} ${contextCounts.general ?? 0}`,
  ].join(" · ")

  function setLlm(key: string, value: unknown) {
    setConfig(store, `llm.${key}`, value)
  }

  function setPromptOverride(next: PromptOverride) {
    setLlm("prompt_overrides", {
      ...overrides,
      [category]: next,
    })
  }

  return (
    <SettingsPage title={copy.polish}>
      <SettingsCard>
        <SettingsRow
          label={copy.enablePolish}
          description={copy.enablePolishHint}
        >
          <Switch
            checked={enabled}
            onCheckedChange={(checked) =>
              setConfig(store, "enable_polish", checked)
            }
          />
        </SettingsRow>
      </SettingsCard>

      <SettingsCard
        title={copy.contextPersonalization}
        description={copy.contextPersonalizationHint}
      >
        <SettingsRow
          label={copy.contextAdaptation}
          description={copy.contextPrivacyBoundary}
        >
          <Switch
            checked={appContext.enabled !== false}
            onCheckedChange={(checked) =>
              setConfig(
                store,
                "context_personalization.enabled",
                checked,
              )
            }
          />
        </SettingsRow>
        <SettingsRow
          label={copy.contextExcludedApps}
          description={copy.contextExcludedAppsHint}
          htmlFor="context-excluded-apps"
        >
          <Input
            id="context-excluded-apps"
            className="h-8 w-64"
            value={(appContext.excluded_bundle_ids ?? []).join(", ")}
            onChange={(event) =>
              setConfig(
                store,
                "context_personalization.excluded_bundle_ids",
                event.target.value
                  .split(",")
                  .map((value) => value.trim())
                  .filter(Boolean),
              )
            }
          />
        </SettingsRow>
        <SettingsRow
          label={copy.contextActivity}
          description={copy.contextActivityHint}
        >
          <div className="flex max-w-72 flex-col items-end gap-1.5">
            <InlineValue>{contextSummary}</InlineValue>
            <Button
              variant="outline"
              size="sm"
              disabled={snapshot.contextProfile.total === 0}
              onClick={() => sendAction("resetContextProfile")}
            >
              {copy.contextReset}
            </Button>
          </div>
        </SettingsRow>
      </SettingsCard>

      <SettingsCard>
        <SettingsRow label={copy.outputType} htmlFor="polish-mode">
          <NativeSelect
            id="polish-mode"
            className="h-8 w-44"
            disabled={!enabled}
            value={llm.polish_mode ?? "dictation"}
            onChange={(event) =>
              setLlm("polish_mode", event.target.value)
            }
          >
            <NativeSelectOption value="dictation">
              {copy.dictation}
            </NativeSelectOption>
            <NativeSelectOption value="prompt">
              {copy.prompt}
            </NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
        <SettingsRow label={copy.level} htmlFor="polish-level">
          <NativeSelect
            id="polish-level"
            className="h-8 w-44"
            disabled={!enabled}
            value={llm.level ?? "minimal"}
            onChange={(event) => setLlm("level", event.target.value)}
          >
            <NativeSelectOption value="minimal">
              {copy.minimal}
            </NativeSelectOption>
            <NativeSelectOption value="balanced">
              {copy.balanced}
            </NativeSelectOption>
            <NativeSelectOption value="strong">
              {copy.strong}
            </NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
        <SettingsRow
          label={copy.structured}
          description={copy.structuredHint}
        >
          <Switch
            disabled={!enabled}
            checked={llm.structured === true}
            onCheckedChange={(checked) =>
              setLlm("structured", checked)
            }
          />
        </SettingsRow>
        <SettingsRow label={copy.tone} htmlFor="polish-tone">
          <NativeSelect
            id="polish-tone"
            className="h-8 w-44"
            disabled={!enabled}
            value={llm.tone ?? "neutral"}
            onChange={(event) => setLlm("tone", event.target.value)}
          >
            <NativeSelectOption value="neutral">
              {copy.neutral}
            </NativeSelectOption>
            <NativeSelectOption value="gentle">
              {copy.gentle}
            </NativeSelectOption>
            <NativeSelectOption value="direct">
              {copy.direct}
            </NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
        <SettingsRow label={copy.persona} htmlFor="polish-persona">
          <NativeSelect
            id="polish-persona"
            className="h-8 w-44"
            disabled={!enabled}
            value={llm.persona ?? "default"}
            onChange={(event) =>
              setLlm("persona", event.target.value)
            }
          >
            <NativeSelectOption value="default">
              {copy.defaultPersona}
            </NativeSelectOption>
            <NativeSelectOption value="technical">
              {copy.technical}
            </NativeSelectOption>
            <NativeSelectOption value="bilingual">
              {copy.bilingual}
            </NativeSelectOption>
            <NativeSelectOption value="professional">
              {copy.professional}
            </NativeSelectOption>
            <NativeSelectOption value="chat">{copy.chat}</NativeSelectOption>
          </NativeSelect>
        </SettingsRow>
      </SettingsCard>

      {advanced ? <SettingsCard
        title={copy.customPrompts}
        description={copy.customPromptsHint}
      >
        <div className="flex flex-col gap-3 p-3">
          <div className="flex items-center justify-between gap-3 max-[600px]:flex-col max-[600px]:items-stretch">
            <ToggleGroup
              value={[category]}
              variant="outline"
              size="sm"
              spacing={0}
              onValueChange={(values) => {
                const next = values[values.length - 1] as
                  | PromptCategory
                  | undefined
                if (next) setCategory(next)
              }}
            >
              <ToggleGroupItem value="output_type">
                {copy.output}
              </ToggleGroupItem>
              <ToggleGroupItem value="level">
                {copy.level}
              </ToggleGroupItem>
              <ToggleGroupItem value="structured">
                {copy.structure}
              </ToggleGroupItem>
              <ToggleGroupItem value="tone">{copy.tone}</ToggleGroupItem>
              <ToggleGroupItem value="persona">
                {copy.persona}
              </ToggleGroupItem>
            </ToggleGroup>
            <NativeSelect
              aria-label={copy.customPrompts}
              className="h-8 w-36 max-[600px]:w-full"
              disabled={!enabled}
              value={override.enabled ? "custom" : "system"}
              onChange={(event) => {
                const custom = event.target.value === "custom"
                setPromptOverride({
                  enabled: custom,
                  prompt: override.prompt || preset,
                })
              }}
            >
              <NativeSelectOption value="system">
                {copy.systemPreset}
              </NativeSelectOption>
              <NativeSelectOption value="custom">
                {copy.custom}
              </NativeSelectOption>
            </NativeSelect>
          </div>
          <Textarea
            aria-label={copy.customPrompts}
            className="min-h-32 resize-y font-mono text-xs leading-relaxed"
            disabled={!enabled}
            readOnly={!override.enabled}
            value={override.enabled ? override.prompt : preset}
            onChange={(event) =>
              setPromptOverride({
                enabled: true,
                prompt: event.target.value,
              })
            }
          />
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {override.enabled
                ? copy.promptCustomHint
                : copy.promptSystemHint}
            </p>
            {override.enabled ? (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  setPromptOverride({ enabled: true, prompt: preset })
                }
              >
                {copy.reloadPreset}
              </Button>
            ) : null}
          </div>
        </div>
      </SettingsCard> : null}

      {advanced ? <SettingsCard>
        <SettingsRow label={copy.llmModel} htmlFor="llm-model">
          <NativeSelect
            id="llm-model"
            className="h-8 w-52"
            disabled={!llmEnabled}
            value={llm.model ?? "qwen3.5-plus"}
            onChange={(event) => setLlm("model", event.target.value)}
          >
            {snapshot.llmModels.map((model) => (
              <NativeSelectOption key={model.id} value={model.id}>
                {model.display_name}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        </SettingsRow>
        <SettingsRow label={copy.temperature}>
          <div className="flex w-52 items-center gap-3">
            <Slider
              min={0}
              max={1}
              step={0.1}
              disabled={!llmEnabled}
              value={
                typeof llm.temperature === "number"
                  ? llm.temperature
                  : 0
              }
              onValueChange={(value) =>
                setLlm("temperature", sliderNumber(value))
              }
            />
            <InlineValue>
              {(llm.temperature ?? 0).toFixed(1)}
            </InlineValue>
          </div>
        </SettingsRow>
        <SettingsRow
          label={copy.thinking}
          description={
            inlinePolish
              ? copy.thinkingLocked
              : selectedLlm?.supports_thinking === false
                ? copy.thinkingUnsupported
                : copy.thinkingHint
          }
        >
          <Switch
            disabled={
              !llmEnabled || selectedLlm?.supports_thinking === false
            }
            checked={
              llmEnabled &&
              selectedLlm?.supports_thinking !== false &&
              llm.enable_thinking === true
            }
            onCheckedChange={(checked) =>
              setLlm("enable_thinking", checked)
            }
          />
        </SettingsRow>
      </SettingsCard> : null}
    </SettingsPage>
  )
}
