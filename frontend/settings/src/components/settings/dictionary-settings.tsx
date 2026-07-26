import { Plus, X } from "lucide-react"
import { useState, type FormEvent } from "react"

import {
  SettingsCard,
  SettingsPage,
  SettingsRow,
} from "@/components/settings/settings-card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { sendAction, setConfig } from "@/settings/actions"
import type { SettingsCopy } from "@/settings/i18n"
import type { SettingsStore } from "@/settings/store"
import type { SettingsSnapshot } from "@/settings/types"

export function DictionarySettings({
  store,
  snapshot,
  copy,
}: {
  store: SettingsStore
  snapshot: SettingsSnapshot
  copy: SettingsCopy
}) {
  const learning = snapshot.config.dictionary_learning ?? {}
  const [term, setTerm] = useState("")
  const [aliases, setAliases] = useState("")

  function addEntry(event: FormEvent) {
    event.preventDefault()
    const cleanTerm = term.trim()
    if (!cleanTerm) return
    sendAction("addDictEntry", {
      term: cleanTerm,
      aliases: aliases
        .split(/[,，\n]+/)
        .map((alias) => alias.trim())
        .filter(Boolean),
    })
    setTerm("")
    setAliases("")
  }

  return (
    <SettingsPage
      title={copy.dictionary}
      description={copy.dictionaryHint}
    >
      <SettingsCard>
        <SettingsRow
          label={copy.automaticLearning}
          description={copy.privacyHint}
        >
          <Switch
            checked={learning.enabled === true}
            onCheckedChange={(checked) =>
              setConfig(store, "dictionary_learning.enabled", checked)
            }
          />
        </SettingsRow>
        <SettingsRow
          label={copy.excludedApps}
          description={copy.excludedAppsHint}
          htmlFor="excluded-apps"
          className="items-start"
        >
          <Input
            id="excluded-apps"
            className="h-8 w-72 text-xs"
            value={(learning.excluded_bundle_ids ?? []).join(", ")}
            placeholder="com.1password.1password, com.apple.Terminal"
            onChange={(event) =>
              setConfig(
                store,
                "dictionary_learning.excluded_bundle_ids",
                event.target.value
                  .split(/[,，\n]+/)
                  .map((value) => value.trim())
                  .filter(Boolean),
              )
            }
          />
        </SettingsRow>
      </SettingsCard>

      <SettingsCard title={copy.learningActivity}>
        {snapshot.dictionaryLearningRecords.length ? (
          snapshot.dictionaryLearningRecords.map((record) => (
            <SettingsRow
              key={record.id}
              label={`${record.term ?? "—"}${record.aliases?.length ? ` ← ${record.aliases.join(", ")}` : ""}`}
            >
              <Badge variant="outline">
                {record.status === "review" ||
                record.status === "pending"
                  ? copy.needsReview
                  : record.status === "reverted"
                    ? copy.reverted
                    : copy.learned}
              </Badge>
              {record.status === "review" ||
              record.status === "pending" ? (
                <>
                  <Button
                    size="sm"
                    onClick={() =>
                      sendAction("approveDictionaryLearning", {
                        id: record.id,
                      })
                    }
                  >
                    {copy.add}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      sendAction("rejectDictionaryLearning", {
                        id: record.id,
                      })
                    }
                  >
                    {copy.ignore}
                  </Button>
                </>
              ) : record.status === "applied" ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    sendAction("undoDictionaryLearning", {
                      id: record.id,
                    })
                  }
                >
                  {copy.undo}
                </Button>
              ) : null}
            </SettingsRow>
          ))
        ) : (
          <p className="p-4 text-xs text-muted-foreground">
            {copy.noLearning}
          </p>
        )}
      </SettingsCard>

      <SettingsCard title={copy.customTerms}>
        <div className="flex min-h-14 flex-wrap items-center gap-2 border-b p-3">
          {snapshot.dictionary.length ? (
            snapshot.dictionary.map((entry) => (
              <Badge
                key={entry.term}
                variant="secondary"
                className="gap-1 py-1"
              >
                <span>{entry.term}</span>
                {entry.aliases?.length ? (
                  <span className="font-normal text-muted-foreground">
                    ({entry.aliases.join(", ")})
                  </span>
                ) : null}
                <button
                  type="button"
                  className="rounded-sm text-muted-foreground hover:text-destructive"
                  aria-label={`${copy.remove} ${entry.term}`}
                  onClick={() =>
                    sendAction("removeDictEntry", { term: entry.term })
                  }
                >
                  <X className="size-3" />
                </button>
              </Badge>
            ))
          ) : (
            <span className="text-xs text-muted-foreground">
              {copy.noTerms}
            </span>
          )}
        </div>
        <form
          className="grid grid-cols-[1fr_1fr_auto] items-end gap-2 p-3 max-[600px]:grid-cols-1"
          onSubmit={addEntry}
        >
          <label className="flex flex-col gap-1.5 text-xs font-medium">
            {copy.term}
            <Input
              aria-label={copy.term}
              className="h-8"
              value={term}
              placeholder={copy.termPlaceholder}
              onChange={(event) => setTerm(event.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1.5 text-xs font-medium">
            {copy.aliases}
            <Input
              aria-label={copy.aliases}
              className="h-8"
              value={aliases}
              placeholder={copy.aliasesPlaceholder}
              onChange={(event) => setAliases(event.target.value)}
            />
          </label>
          <Button type="submit" size="sm">
            <Plus data-icon="inline-start" />
            {copy.addTerm}
          </Button>
        </form>
        <div className="border-t p-3">
          <Button
            size="sm"
            variant="outline"
            onClick={() => sendAction("openDictFile")}
          >
            {copy.openDictionary}
          </Button>
        </div>
      </SettingsCard>
    </SettingsPage>
  )
}
