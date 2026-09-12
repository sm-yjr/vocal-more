// SPDX-License-Identifier: GPL-3.0-only
use crate::{catalog::CONTRACT, config::Config};
use regex::Regex;
use std::sync::LazyLock;

#[derive(Clone, Copy)]
pub enum PromptKind {
    System,
    Inline,
    Native,
}

fn builtin(category: &str, key: &str) -> &'static str {
    CONTRACT["prompt_presets"][category][key]
        .as_str()
        .unwrap_or("")
}
fn constant(name: &str) -> &'static str {
    CONTRACT["prompt_constants"][name].as_str().unwrap_or("")
}
fn setting<'a>(config: &'a Config, key: &str) -> &'a str {
    config.0["llm"][key].as_str().unwrap_or("")
}
fn custom<'a>(config: &'a Config, category: &str) -> Option<&'a str> {
    let item = &config.0["llm"]["prompt_overrides"][category];
    item["enabled"].as_bool().filter(|b| *b)?;
    item["prompt"]
        .as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
}
fn instruction<'a>(config: &'a Config, category: &str, key: &str) -> &'a str {
    custom(config, category).unwrap_or_else(|| builtin(category, key))
}

pub fn rules(config: &Config) -> String {
    let mut blocks = vec![
        constant("SPOKEN_TEXT_BASELINE").to_string(),
        format!(
            "输出类型要求：\n{}",
            instruction(config, "output_type", setting(config, "polish_mode"))
        ),
    ];
    let language = match setting(config, "output_language") {
        "zh" => Some("中文"),
        "en" => Some("英文"),
        _ => None,
    };
    if let Some(language) = language {
        blocks.push(format!("输出语言要求：\n1. 将整理后的全部输出翻译为{language}；这条要求优先于“保持用户原本的语言”\n2. 专有名词、代码、命令、路径、API 名、模型名和产品名保持原样，不翻译；用户词典中的术语尤其不得意译\n3. 只做翻译和整理，不要添加原文没有的内容"));
    }
    blocks.extend([
        format!(
            "润色强度要求：\n{}",
            instruction(config, "level", setting(config, "level"))
        ),
        format!(
            "语气要求：\n{}",
            instruction(config, "tone", setting(config, "tone"))
        ),
        format!(
            "表达人格要求：\n{}",
            instruction(config, "persona", setting(config, "persona"))
        ),
    ]);
    if config.0["llm"]["structured"] == true {
        blocks.push(format!(
            "结构化格式要求：\n{}",
            instruction(config, "structured", "enabled")
        ));
    }
    blocks.push(constant("COMMON_POLISH_RULES").into());
    if custom(config, "level").is_none() {
        blocks.push(
            CONTRACT["prompt_constants"]["POLISH_EXAMPLES"][setting(config, "level")]
                .as_str()
                .unwrap_or("")
                .into(),
        );
    }
    blocks.join("\n\n")
}

pub fn build_prompt(config: &Config, kind: PromptKind, dictionary: &str, context: &str) -> String {
    let key = match kind {
        PromptKind::Native => "native".into(),
        PromptKind::System => format!("{}_system", setting(config, "polish_mode")),
        PromptKind::Inline => format!("{}_inline", setting(config, "polish_mode")),
    };
    let template = CONTRACT["prompt_templates"][key].as_str().unwrap_or("");
    let rules = rules(config);
    let dictionary = if dictionary.is_empty() {
        String::new()
    } else {
        format!("\n\n{dictionary}")
    };
    let context = if context.trim().is_empty() {
        String::new()
    } else {
        format!(
            "\n\n当前使用场景（仅为本地映射出的抽象类别）：\n{}",
            context.trim()
        )
    };
    static PLACEHOLDER: LazyLock<Regex> =
        LazyLock::new(|| Regex::new("@(RULES|DICTIONARY|CONTEXT)@").unwrap());
    PLACEHOLDER
        .replace_all(template, |captures: &regex::Captures| match &captures[1] {
            "RULES" => rules.as_str(),
            "DICTIONARY" => dictionary.as_str(),
            _ => context.as_str(),
        })
        .into_owned()
}

static HEADING: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\s*#{1,6}\s*(.*?)\s*$").unwrap());
static HEADING_SPACING: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[\s_-]+").unwrap());
static INTERNAL_LINE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*(?:[-*]\s*)?(?:当前场景|格式保护|专有名词修正|润色强度要求|语气要求|表达人格要求|结构化格式要求)\s*[：:]").unwrap()
});
static INTERNAL_TEXT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"用户定义的专有名词词典|当前使用场景（仅为本地映射出的抽象类别）|严格执行以下映射修正",
    )
    .unwrap()
});

fn forbidden(heading: &str) -> bool {
    let normalized = HEADING_SPACING
        .replace_all(heading.trim().trim_end_matches([':', '：', '?', '？']), " ")
        .to_lowercase();
    normalized.starts_with("open question")
        || [
            "question",
            "questions",
            "clarifying question",
            "clarifying questions",
            "missing information",
            "information needed",
            "stop rule",
            "stop rules",
            "待确认",
            "待确认事项",
            "待确认信息",
            "待补充",
            "待补充信息",
            "澄清问题",
            "需要确认的信息",
            "需要用户确认",
        ]
        .contains(&normalized.as_str())
}

pub fn sanitize_prompt(text: &str) -> String {
    let mut lines = Vec::new();
    let mut skipping = false;
    for raw in text.lines() {
        let line = raw.trim_end();
        if let Some(heading) = HEADING.captures(line) {
            skipping = forbidden(&heading[1]);
        }
        if skipping || INTERNAL_LINE.is_match(line) || INTERNAL_TEXT.is_match(line) {
            continue;
        }
        lines.push(line);
    }
    let mut filtered = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        if !HEADING.is_match(lines[i]) {
            filtered.push(lines[i]);
            i += 1;
            continue;
        }
        let mut end = i + 1;
        while end < lines.len() && !HEADING.is_match(lines[end]) {
            end += 1;
        }
        if lines[i + 1..end].iter().any(|l| !l.trim().is_empty()) {
            filtered.extend_from_slice(&lines[i..end]);
        }
        i = end;
    }
    let mut compact = Vec::new();
    for line in filtered {
        if line.trim().is_empty() {
            if !compact.is_empty() && compact.last() != Some(&"") {
                compact.push("");
            }
        } else {
            compact.push(line);
        }
    }
    compact.join("\n").trim().into()
}

static PROTECTED: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"`[^`]*`|\S*[/\\]\S*|www\.\S*").unwrap());

fn bilingual_segment(text: &str) -> String {
    fn cjk(ch: char) -> bool {
        ('\u{3400}'..='\u{4dbf}').contains(&ch) || ('\u{4e00}'..='\u{9fff}').contains(&ch)
    }
    let mut result = String::new();
    let mut previous: Option<char> = None;
    for mut ch in text.chars() {
        if ('０'..='９').contains(&ch) || ('Ａ'..='Ｚ').contains(&ch) || ('ａ'..='ｚ').contains(&ch)
        {
            ch = char::from_u32(ch as u32 - 0xfee0).unwrap();
        }
        if let Some(prev) = previous {
            if prev == ch && "。！？，、".contains(ch) {
                continue;
            }
            if (cjk(prev) && ch.is_ascii_alphanumeric())
                || (prev.is_ascii_alphanumeric() && cjk(ch))
            {
                result.push(' ');
            }
        }
        result.push(ch);
        previous = Some(ch);
    }
    result
}

pub fn bilingual(text: &str) -> String {
    let mut output = String::new();
    let mut cursor = 0;
    for segment in PROTECTED.find_iter(text) {
        output.push_str(&bilingual_segment(&text[cursor..segment.start()]));
        output.push_str(segment.as_str());
        cursor = segment.end();
    }
    output.push_str(&bilingual_segment(&text[cursor..]));
    output
}

static LIST_MARKERS: LazyLock<Vec<fancy_regex::Regex>> = LazyLock::new(|| {
    [
    r"(?<!^)(?<!\n)(?:(?<=\S)\s+|(?<=[：:])\s*)((?:\d{1,2}|[一二三四五六七八九十]+)[.、．]\s*(?=\D))",
    r"(?<!^)(?<!\n)(?<=\S)\s+([•·]\s*)", r"\s+(-\s+)", r"\s+(\*\s+)",
].into_iter().map(|r| fancy_regex::Regex::new(r).unwrap()).collect()
});

pub fn structured(text: &str, config: &Config) -> String {
    if setting(config, "polish_mode") == "prompt" {
        return sanitize_prompt(text);
    }
    if config.0["llm"]["structured"] != true {
        return text.trim().into();
    }
    text.lines()
        .flat_map(|raw| {
            let mut line = raw.trim().to_string();
            for (i, pattern) in LIST_MARKERS.iter().enumerate() {
                if (i == 2 && line.matches(" - ").count() < 2)
                    || (i == 3 && line.matches(" * ").count() < 2)
                {
                    continue;
                }
                if let Ok(next) = pattern.try_replacen(&line, 0, "\n$1") {
                    line = next.into_owned();
                }
            }
            line.split('\n')
                .map(|s| s.trim().to_string())
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>()
        .join("\n")
        .trim()
        .into()
}
