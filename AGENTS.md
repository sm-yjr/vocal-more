## Development Focus

The active codebase is the **Python app** (`src/vocal_more/`). All new features, bug fixes, and improvements should target the Python codebase.

- **Python source**: `src/vocal_more/`
- **Tests**: `tests/`
- **Frontend assets**: `resources/` (HTML/CSS/JS for the floating capsule and settings UI, loaded by the Python app via WebView)

## Engineering Workflow

### Verification

- Install development dependencies with `uv sync --group dev`.
- Run the full test suite with `uv run python -m pytest -q`.
- Do not build a local DMG as part of routine development or feature verification. Run the test suite locally; when publishing a version, use the release CI workflow directly.
- Treat `pyproject.toml` as the version source of truth. The macOS bundle metadata and artifact names derive from it through `packaging/macos/read_version.py`; keep the editable `vocal-more` entry in `uv.lock` aligned.
- A version-only change should not rewrite unrelated registry metadata in `uv.lock`. If it does, verify the local `uv` version before accepting the diff.
- Read `docs/concurrency-runtime-model.md` before changing worker ownership, queues, shutdown behavior, or background runtimes.

### Licensing

- The project is licensed under `GPL-3.0-only`. Preserve both the root `LICENSE` file and the SPDX expression in `pyproject.toml`; do not change this to `GPL-3.0-or-later` without explicit authorization.
- Every official macOS distribution must include the project license in both `Vocal More.app/Contents/Resources/LICENSE.txt` and the DMG root. Keep third-party notices, such as `Sparkle-LICENSE.txt`, separate.

### macOS Releases

- Official releases are built by `.github/workflows/release.yml` from a version tag (`vX.Y.Z` or `X.Y.Z`) that exactly matches `pyproject.toml`.
- Do not consider a release complete until the workflow has passed tests, Developer ID signing, notarization and stapling, artifact verification, GitHub Release upload, and signed Sparkle appcast publication.
- Use `docs/release.md` for prerequisites and secret names. Never commit certificate material, notarization credentials, or the Sparkle private key.
- `VOCAL_MORE_ALLOW_UNSIGNED_DMG=1` is for local packaging checks only; never publish that unsigned artifact as an official release.

## Design Context

### Users
macOS power users — developers, writers, and bilingual (Chinese/English) professionals who need fast, accurate voice-to-text input. They use this app embedded in their daily workflow: writing code, drafting documents, or composing messages. The app should feel like a natural extension of macOS, always ready but never intrusive.

### Brand Personality
**Refined, Intelligent, Elegant** — The app exudes the quiet confidence of a premium tool. It doesn't shout for attention; it earns trust through polished details, smooth interactions, and thoughtful restraint.

### Aesthetic Direction
- **Visual tone**: Minimal, premium, Apple-native. The floating capsule UI draws directly from iPhone Dynamic Island — a dark pill shape hovering above content with subtle depth shadows and smooth animations.
- **References**: Apple Dynamic Island (interaction paradigm, shape language, dark-on-transparent), Raycast / Arc Browser (macOS-native polish, keyboard-first efficiency tools with refined aesthetics).
- **Anti-references**: Cluttered productivity apps, electron-style apps that feel foreign on macOS, overly colorful or playful UIs that break system coherence.
- **Theme**: Supports both light and dark mode, adapting to macOS system appearance. The floating capsule maintains its dark aesthetic regardless of system theme (like Dynamic Island), while any future settings UI or panels should respect system appearance.

### Color System
- **Capsule surface**: Solid black (`rgba(0,0,0,1)`) with subtle white border (`rgba(255,255,255,0.32)`)
- **Content on capsule**: White at varying opacities (0.4–0.9) for hierarchy
- **Semantic colors**: Apple system red (`rgb(255,59,48)`) for destructive/cancel, Apple system green (`rgb(52,199,89)`) for confirm/success
- **Depth**: Two-layer box shadow for floating effect — ambient glow + directional cast shadow
- **Future panels/settings**: Should use macOS system colors and adapt to light/dark mode

### Typography
System font stack only: `-apple-system, BlinkMacSystemFont, sans-serif`. No custom typefaces. This ensures the app feels like a native macOS component.

### Motion & Animation
- **Entrance/exit**: Cubic-bezier easing (`0.4, 0, 0.2, 1` — Material standard) with fade + scale + translate
- **Waveform**: 60fps requestAnimationFrame with Gaussian amplitude envelope, asymmetric smoothing (fast attack, slow decay)
- **Loading state**: Shimmer gradient animation on text
- **Progress**: Asymptotic approach (never false-promises completion)
- **Respect `prefers-reduced-motion`**: Reduce or disable animations for users who prefer reduced motion

### Design Principles
1. **Disappear into macOS** — The app should feel like a native system component, not a third-party tool. Use system conventions, system fonts, and system colors wherever possible.
2. **Quiet confidence** — Premium quality is expressed through restraint: precise spacing, smooth animations, and polished details rather than bold colors or flashy effects.
3. **Respect attention** — The floating capsule appears only when needed and communicates state changes through subtle, non-disruptive visual cues. Never interrupt the user's flow.
4. **Depth with purpose** — Use shadows, transparency, and layering to communicate spatial hierarchy. The capsule floats above content; interactive elements have clear affordances.
5. **Adaptive, not rigid** — Support system appearance preferences (light/dark mode, reduced motion) while maintaining a consistent brand identity. The capsule's dark aesthetic is a deliberate design choice that transcends system theme.

## Product Insights

### Low-Voice Input: The Core Usability Breakthrough

In open office environments, the biggest barrier to voice input is **social friction** — speaking at normal volume disturbs colleagues and exposes conversation content. This makes voice-to-text feel impractical despite its speed advantage.

The solution is a **high-gain + noise-control audio pipeline**:
- **Software gain up to +30dB** lets the user whisper (nearly inaudible to coworkers) while the app hears them clearly
- **High-pass filter (adjustable 50–500Hz)** removes low-frequency ambient noise (fans, AC, room rumble) that gets amplified along with the voice
- **Soft limiter (tanh)** prevents the high gain from producing harsh clipping distortion
- **Noise gate with hold time** silences true silence without chopping speech

This combination transforms voice input from a "quiet room only" tool into an everyday productivity tool usable in shared spaces. The insight: **gain is not about volume — it's about enabling a new, socially acceptable way to use voice input.**

When developing audio-related features, always consider the low-voice use case as the primary scenario, not an edge case.
