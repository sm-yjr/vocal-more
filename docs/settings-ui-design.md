# Settings UI design specification

This document is the visual and interaction contract for the React settings
window. It complements the executable Python/JavaScript bridge contract; it
does not replace it.

## Reference screens

- `docs/design/settings-ui/audio-concept.png`
- `docs/design/settings-ui/history-concept.png`

The concepts establish hierarchy, density, component geometry, and action
priority. Existing application copy and behavior remain authoritative when a
generated label differs from `resources/settings/settings.html`.

## Surface and container model

- Logical viewport: 680 × 480 px, resizable down to 520 × 380 px.
- Shell: fixed 160 px navigation rail plus one independently scrollable
  content pane.
- Navigation order: General, Audio, Recognition, Polish, Shortcuts,
  Dictionary, History.
- Content uses open sections and separators. Cards are reserved for prompt
  editing, history summaries, recordings, and learning-review records.
- The window must remain useful at its minimum size. The rail may compact to
  icons below 600 px, but all pages and controls remain reachable.

## Design tokens

- Font: `-apple-system, BlinkMacSystemFont, sans-serif`; no downloadable font.
- Base control text: 13 px/1.4; helper text: 11–12 px; page title: 20 px/1.2.
- Spacing scale: 4, 8, 12, 16, 20, 24 px.
- Radius: 6 px for controls, 8 px for cards, full radius only for switches and
  status badges.
- Border: one physical pixel using the semantic border token.
- Accent: macOS system blue. Destructive and success states use Apple system
  red and green through semantic tokens.
- Motion: 150–200 ms state transitions. Disable nonessential transforms and
  scrolling animation under `prefers-reduced-motion`.
- Light and dark appearances follow `prefers-color-scheme`. The floating
  capsule is outside this specification and remains dark.

## Component families

- Navigation: shadcn `Tabs` with one `TabsList` and seven `TabsTrigger`
  children.
- Forms: `FieldGroup`, `Field`, `FieldLabel`, `FieldDescription`,
  `FieldSeparator`, and `FieldSet` where controls form a semantic group.
- Controls: `Input`, `NativeSelect`, `Textarea`, `Switch`, `Slider`,
  `Checkbox`, and `ToggleGroup`.
- Actions and feedback: `Button`, `Alert`, `Badge`, `Progress`, `Empty`,
  `Spinner`, and `Separator`.
- Structured content: full `Card` composition for prompt editing, history
  summaries and recording rows.
- Icons: one consistent Lucide outline family. Button icons use `data-icon`;
  destructive actions have an accessible text label even when visually
  icon-only.

## Allowed primary-screen copy

Audio:

- Audio
- Input Device
- System Default
- Presets
- Quiet Room
- Office Whisper
- Noisy Space
- Start with a preset, then fine-tune if needed.
- Software Gain
- High-Pass Filter
- Cutoff Frequency
- Soft Limiter
- Test Recording
- Test

History:

- History
- Total cost
- Recognition
- Polish
- Play
- Stop
- Retry
- Meeting Notes
- Copy
- Success
- Failed
- Pending

Translations in the legacy settings page remain the source for all other
English and Chinese strings. No new marketing copy, profile UI, search UI,
metrics, or navigation is permitted.

## Interaction invariants

- `window._initData` is consumed synchronously before the first render.
- Every Python-to-JavaScript global currently invoked by
  `SettingsWindow` remains callable with the same arguments.
- `collectFormState()` remains synchronous and returns the same serializable
  shape.
- Every JavaScript-to-Python action keeps its current name and payload shape.
- Slider drag state is not overwritten by backend synchronization.
- Device refresh, custom hotkey capture, prompt overrides, dictionary review,
  recording retry/play/copy/delete, meeting-note generation, and microphone
  testing retain their current behavior.

## Visual QA ledger

Implementation captures:

- `docs/design/settings-ui/actual-audio-680x480.png`
- `docs/design/settings-ui/actual-audio-520x380.png`

Comparison against the Audio and History concepts:

1. **Shell and navigation:** the implementation keeps the seven-item vertical
   rail, one-pixel divider, quiet inactive icons, and filled selected state.
   The 160 px rail matches the reference proportion; at the minimum width it
   contracts to 128 px without hiding labels.
2. **Typography:** page titles, row labels, and helper copy preserve the
   concept's three-level hierarchy. The implementation deliberately uses the
   macOS system stack rather than the concept renderer's approximate font.
3. **Color and depth:** neutral system surfaces, low-contrast borders, and
   system-blue focus/selection states match the concepts in both appearances.
   No decorative gradients, remote assets, or extra brand colors were added.
4. **Geometry:** controls use compact shadcn dimensions and related settings
   are grouped in 12 px-radius cards. Cards replace some of the concept's open
   separator groups so dense controls remain legible and clickable at 520 px.
5. **Action hierarchy:** primary actions use a filled accent treatment;
   secondary actions are outlined or ghost buttons. All icon-only actions
   retain accessible labels and use one Lucide outline family.
6. **Responsive behavior:** the 680 × 480 capture keeps two-column setting
   rows. At 520 × 380, rows stack their label and control regions, preset
   buttons remain three equal columns, and the content pane scrolls without
   horizontal clipping.
7. **Motion and platform behavior:** short control transitions remain, while
   smooth scrolling and nonessential animation are disabled under
   `prefers-reduced-motion`. Light/dark appearance follows the system; the
   separate floating capsule is intentionally unchanged.
