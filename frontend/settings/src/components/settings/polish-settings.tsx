import { useState } from "react"

import {
  SettingsCard,
  SettingsPage,
  SettingsRow,
} from "@/components/settings/settings-card"
import { Button } from "@/components/ui/button"
import {
  NativeSelect,
  NativeSelectOption,
} from "@/components/ui/native-select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  ToggleGroup,
  ToggleGroupItem,
} from "@/components/ui/toggle-group"
import { setConfig } from "@/settings/actions"
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
  const advanced = snapshot.config.ui?.advanced_settings === true
  const nativeAsr = snapshot.asrModels.find(
    (model) => model.id === snapshot.config.asr?.model,
  )?.pipeline === "native_asr"
  const enabled = snapshot.config.enable_polish !== false && !nativeAsr
  const [category, setCategory] =
    useState<PromptCategory>("output_type")
  const overrides = llm.prompt_overrides ?? {}
  const override = overrides[category] ?? {
    enabled: false,
    prompt: "",
  }
  const preset =
    snapshot.polishPromptPresets[category]?.[
      promptPresetKey(category, llm)
    ] ?? ""

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
          description={nativeAsr ? copy.nativeAsrHint : copy.enablePolishHint}
        >
          <Switch
            disabled={nativeAsr}
            checked={enabled}
            onCheckedChange={(checked) =>
              setConfig(store, "enable_polish", checked)
            }
          />
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
        <SettingsRow
          label={copy.outputLanguage}
          description={copy.outputLanguageHint}
          htmlFor="polish-output-language"
        >
          <NativeSelect
            id="polish-output-language"
            className="h-8 w-44"
            disabled={!enabled}
            value={llm.output_language ?? "auto"}
            onChange={(event) =>
              setLlm("output_language", event.target.value)
            }
          >
            <NativeSelectOption value="auto">
              {copy.outputLanguageAuto}
            </NativeSelectOption>
            <NativeSelectOption value="zh">
              {copy.outputLanguageZh}
            </NativeSelectOption>
            <NativeSelectOption value="en">
              {copy.outputLanguageEn}
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

    </SettingsPage>
  )
}
