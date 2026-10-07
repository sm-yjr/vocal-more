// SPDX-License-Identifier: GPL-3.0-only
//! Product version from pyproject.toml: `[project].version` (PEP 440, `X.Y.Z`
//! or `X.Y.ZbN`) plus `[tool.vocal-more].build`, shown as `X.Y.Z+B` or
//! `X.Y.Z-beta.N+B` — the same text packaging writes to `VocalMoreVersion`.
fn main() {
    let manifest = std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let project = manifest.join("../../../pyproject.toml");
    println!("cargo:rerun-if-changed={}", project.display());
    let source =
        std::fs::read_to_string(project).expect("read product version from pyproject.toml");
    let mut section = "";
    let mut version = None;
    let mut build = None;
    for line in source.lines().map(str::trim) {
        if line.starts_with('[') {
            section = line;
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        match (section, key.trim()) {
            ("[project]", "version") => {
                version = value
                    .trim()
                    .strip_prefix('"')
                    .and_then(|s| s.strip_suffix('"'));
            }
            ("[tool.vocal-more]", "build") => build = value.trim().parse::<u32>().ok(),
            _ => {}
        }
    }
    let version = version.expect("[project].version must be an explicit quoted version");
    let build = build
        .filter(|build| *build > 0)
        .expect("[tool.vocal-more].build must be a positive integer");
    let (base, beta) = match version.split_once('b') {
        Some((base, number)) => (base, Some(number)),
        None => (version, None),
    };
    let numeric = |text: &str| !text.is_empty() && text.bytes().all(|b| b.is_ascii_digit());
    assert!(
        base.split('.').count() == 3 && base.split('.').all(numeric) && beta.is_none_or(numeric),
        "[project].version must be X.Y.Z or X.Y.ZbN (alpha releases are retired)"
    );
    let display = match beta {
        Some(number) => format!("{base}-beta.{number}+{build}"),
        None => format!("{base}+{build}"),
    };
    println!("cargo:rustc-env=VOCAL_MORE_PRODUCT_VERSION={display}");
}
