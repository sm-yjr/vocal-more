## Development Focus

The active codebase is the **Rust workspace** (`rust/`). Since 0.6.0 the macOS app is a Rust desktop program; new features, bug fixes and improvements target Rust.

- **Desktop app** (`rust/crates/desktop/`): GPUI Kit settings window (`src/settings/`), native AppKit capsule (`src/capsule/`), menu bar, hotkeys, paste and other platform adapters (`src/platform/`), Geist theme (`src/theme.rs`, `assets/`)
- **Business backend** (`rust/crates/backend/`, `rust/crates/core/`), embedded directly by the desktop app
- **Build and run**: `bash script/build_and_run.sh` (isolated dev data, hotkeys off); toolchain and checks are in `rust/README.md` (Rust 1.98.1, as in CI)
- **Tests**: `cargo test` in `rust/`, including the Metal UI acceptance `--features ui-test --test settings_rendering` and the native termination acceptance binary; packaging/release checks stay in Python under `tests/`
- **Legacy**: the Python app (`src/vocal_more/`, WebView `resources/`, `frontend/`) is the 0.5.x reference path only; change it only for release maintenance or when a Rust parity test reads it

## Design Context

### Users
macOS power users — developers, writers, and bilingual (Chinese/English) professionals who need fast, accurate voice-to-text input. They use this app embedded in their daily workflow: writing code, drafting documents, or composing messages. The app should feel like a natural extension of macOS, always ready but never intrusive.

### Brand Personality
**Refined, Intelligent, Elegant** — The app exudes the quiet confidence of a premium tool. It doesn't shout for attention; it earns trust through polished details, smooth interactions, and thoughtful restraint.

### Aesthetic Direction
- **Visual tone**: Minimal, precise, high-contrast monochrome. The settings window follows the **Vercel Geist design system**; the floating capsule keeps its iPhone Dynamic Island form — a dark pill hovering above content with subtle depth shadows and smooth animations.
- **References**: Vercel Geist (settings window: gray scale, 1px borders, typography, components — https://vercel.com/geist), Apple Dynamic Island (capsule interaction paradigm, shape language, dark-on-transparent).
- **Anti-references**: Cluttered productivity apps, colorful icon tiles or gradients, overly playful UIs, heavy shadows.
- **Theme**: Settings follow the macOS light/dark appearance using Geist Light/Dark (`rust/crates/desktop/assets/themes/geist.json`, applied by `rust/crates/desktop/src/theme.rs`). The floating capsule stays dark regardless of system theme (like Dynamic Island). The menu bar is a native NSMenu: text-only items (macOS 27 menus draw no item icons), concise, and worded like the settings window; only the status icon is a monochrome template image.

### Color System
- **Capsule surface**: Solid black (`rgba(0,0,0,1)`) with subtle white border (`rgba(255,255,255,0.32)`)
- **Content on capsule**: White at varying opacities (0.4–0.9) for hierarchy
- **Semantic colors**: Apple system red (`rgb(255,59,48)`) for destructive/cancel, Apple system green (`rgb(52,199,89)`) for confirm/success
- **Depth**: Two-layer box shadow for floating effect — ambient glow + directional cast shadow
- **Settings window (Geist tokens)**: gray-100…1000 scale from Geist (light `background #fff`, `border gray-400 #eaeaea`, text `gray-1000 #171717` / secondary `gray-900 #4d4d4d`; dark `background #000`, `border #2e2e2e`, text `#ededed` / `#a0a0a0`). Primary actions and switches use gray-1000 (black in light, near-white in dark); blue-700 only for focus rings and links; red/amber/green-700 only for status. Change colors in `geist.json`, not in view code.

### Typography
- **Settings window**: Geist Sans for UI text, Geist Mono for values, numbers and versions. The fonts are embedded from `rust/crates/desktop/assets/fonts/` (OFL-1.1, notice added by `packaging/macos/rust_notices.py`); CJK text falls back to the system font. Base size 14px, field descriptions 13px, page titles 24px semibold.
- **Capsule**: system font stack (`-apple-system, BlinkMacSystemFont, sans-serif`).

### Motion & Animation
- **Entrance/exit**: Cubic-bezier easing (`0.4, 0, 0.2, 1` — Material standard) with fade + scale + translate
- **Waveform**: 60fps requestAnimationFrame with Gaussian amplitude envelope, asymmetric smoothing (fast attack, slow decay)
- **Loading state**: Shimmer gradient animation on text
- **Progress**: Asymptotic approach (never false-promises completion)
- **Respect `prefers-reduced-motion`**: Reduce or disable animations for users who prefer reduced motion

### Design Principles
1. **Geist consistency** — In the settings window use the Geist palette, 6px control radius, 12px card radius, 1px borders, monochrome icons and no decorative shadows. Respect macOS behaviors (appearance, keyboard, reduced motion) even where the look is Geist.
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
