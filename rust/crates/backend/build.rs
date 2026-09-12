// SPDX-License-Identifier: GPL-3.0-only
fn main() {
    let manifest = std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let project = manifest.join("../../../pyproject.toml");
    println!("cargo:rerun-if-changed={}", project.display());
    let source =
        std::fs::read_to_string(project).expect("read product version from pyproject.toml");
    let mut in_project = false;
    let mut version = None;
    for line in source.lines().map(str::trim) {
        if line.starts_with('[') {
            in_project = line == "[project]";
        }
        if in_project
            && let Some((key, value)) = line.split_once('=')
            && key.trim() == "version"
        {
            version = value
                .trim()
                .strip_prefix('"')
                .and_then(|s| s.strip_suffix('"'));
            break;
        }
    }
    let version = version.expect("[project].version must be an explicit quoted version");
    assert!(
        !version.is_empty()
            && version
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b".-+".contains(&b)),
        "invalid product version"
    );
    println!("cargo:rustc-env=VOCAL_MORE_PRODUCT_VERSION={version}");
}
