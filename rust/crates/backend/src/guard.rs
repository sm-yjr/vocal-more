// SPDX-License-Identifier: GPL-3.0-only
//! Deterministic output guards that catch AI "answering" drift on dictated
//! questions and short-silence ASR hallucinations before text is pasted.

use crate::config::Config;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DriftReason {
    AssistantReplyPrefix,
    RunawayExpansion,
}

impl DriftReason {
    pub fn warning(&self) -> &'static str {
        match self {
            Self::AssistantReplyPrefix => {
                "Detected assistant-style reply drift; fell back to faithful transcription."
            }
            Self::RunawayExpansion => {
                "Detected abnormal output expansion; fell back to faithful transcription."
            }
        }
    }
}

const ASSISTANT_PREFIXES: &[&str] = &[
    "好的，为您",
    "好的，以下是",
    "好的，关于",
    "当然可以",
    "根据您的问题",
    "关于您的问题",
    "为您解答",
    "很抱歉，我无法",
    "抱歉，我无法",
    "作为人工智能",
    "作为一个ai",
    "作为一个 ai",
    "我可以帮您",
    "以下是关于",
    "sure, here",
    "certainly, here",
    "of course, here",
    "here is the answer",
    "here's the answer",
    "as an ai",
    "i'm sorry, i cannot",
    "i am sorry, i cannot",
    "i cannot assist",
    "to answer your question",
];

const SILENCE_HALLUCINATIONS: &[&str] = &[
    "字幕由",
    "字幕制作",
    "感谢观看",
    "谢谢观看",
    "谢谢收看",
    "请不吝点赞",
    "订阅转发",
    "中文字幕",
    "subtitles by",
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "amara.org",
];

fn meaningful_len(text: &str) -> usize {
    text.chars()
        .filter(|ch| ch.is_alphanumeric() || is_cjk(*ch))
        .count()
}

fn is_cjk(ch: char) -> bool {
    matches!(
        ch as u32,
        0x3400..=0x4DBF | 0x4E00..=0x9FFF | 0xF900..=0xFAFF | 0x3040..=0x30FF | 0xAC00..=0xD7AF
    )
}

fn starts_with_assistant_prefix(candidate: &str, reference: &str) -> bool {
    let cand = candidate.trim().to_lowercase();
    let ref_lower = reference.trim().to_lowercase();
    ASSISTANT_PREFIXES
        .iter()
        .any(|prefix| cand.starts_with(prefix) && !ref_lower.starts_with(prefix))
}

/// Checks whether a semantic / polished dictation output drifted into answering
/// the user's question or hallucinating extra content instead of faithfully
/// transcribing `reference`.
///
/// Never triggers in `prompt` mode (which intentionally expands instructions)
/// or when `reference` is too short to anchor a comparison.
pub fn detect_answer_drift(
    candidate: &str,
    reference: &str,
    config: &Config,
) -> Option<DriftReason> {
    if config.get("llm.polish_mode") == "prompt" {
        return None;
    }
    let cand_len = meaningful_len(candidate);
    let ref_len = meaningful_len(reference);
    if ref_len < 4 || cand_len <= ref_len {
        return None;
    }

    // 1. Explicit assistant reply prefix absent from what the user spoke,
    //    combined with noticeable expansion.
    if starts_with_assistant_prefix(candidate, reference) && cand_len >= ref_len + 8 {
        return Some(DriftReason::AssistantReplyPrefix);
    }

    // 2. Extreme runaway expansion in normal dictation (e.g. answering a short
    //    question with a multi-sentence paragraph). Exempt explicit translation
    //    (zh->en expands character count by ~3x) and strong/structured modes.
    let same_language = config.get("llm.output_language") == "auto";
    let allows_heavy_rewrite =
        config.get("llm.structured") == true || config.get("llm.level") == "strong";
    if same_language
        && !allows_heavy_rewrite
        && ref_len >= 6
        && cand_len >= 60
        && cand_len >= ref_len.saturating_mul(4)
    {
        return Some(DriftReason::RunawayExpansion);
    }
    None
}

/// Rejects classic short-silence ASR hallucinations (e.g. subtitle watermarks
/// or physically impossible long output from an accidental sub-0.65s tap).
pub fn is_short_audio_hallucination(text: &str, audio_seconds: f64) -> bool {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return false;
    }
    let lower = trimmed.to_lowercase();
    if audio_seconds < 1.2
        && SILENCE_HALLUCINATIONS
            .iter()
            .any(|phrase| lower.contains(phrase))
    {
        return true;
    }
    // Physically impossible to articulate >= 28 meaningful characters in < 0.65s.
    audio_seconds < 0.65 && meaningful_len(trimmed) >= 28
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn catches_assistant_reply_to_dictated_question() {
        let cfg = Config::default();
        let ref_text = "这个接口为什么会返回 500 错误？";
        let answered = "好的，为您解答：这个接口返回 500 错误通常是因为服务端未捕获异常。";
        assert_eq!(
            detect_answer_drift(answered, ref_text, &cfg),
            Some(DriftReason::AssistantReplyPrefix)
        );
    }

    #[test]
    fn allows_legitimate_dictation_and_user_spoken_prefix() {
        let cfg = Config::default();
        assert_eq!(
            detect_answer_drift(
                "这个接口为什么会返回 500 错误？",
                "这个接口为什么返回 500 错误",
                &cfg
            ),
            None
        );
        // If the user actually dictated "当然可以...", never falsely trigger.
        assert_eq!(
            detect_answer_drift(
                "当然可以，我们明天下午三点再同步一下这个方案。",
                "当然可以我们明天下午三点再同步一下这个方案",
                &cfg
            ),
            None
        );
    }

    #[test]
    fn exempts_prompt_mode_and_explicit_translation() {
        let mut prompt_cfg = Config::default();
        prompt_cfg
            .apply_update("llm.polish_mode", &json!("prompt"))
            .unwrap();
        assert_eq!(
            detect_answer_drift(
                "好的，以下是整理后的结构化 AI 提示词：请帮我分析日志并定位 500 错误根因。",
                "帮我分析一下日志",
                &prompt_cfg
            ),
            None
        );

        let mut en_cfg = Config::default();
        en_cfg
            .apply_update("llm.output_language", &json!("en"))
            .unwrap();
        let zh_ref = "请帮我检查今天的接口延迟报告";
        let en_out = "Please help me review today's API latency benchmark report carefully before we ship the release.";
        assert_eq!(detect_answer_drift(en_out, zh_ref, &en_cfg), None);
    }

    #[test]
    fn catches_runaway_expansion_and_short_audio_hallucination() {
        let cfg = Config::default();
        let ref_text = "今天天气怎么样";
        let essay = "今天北京天气晴朗，气温大约在十五到二十二摄氏度之间，空气质量良好，微风二级，非常适合外出散步或办公通勤。建议您早晚适当增添外套，注意温差变化以免着凉感冒，祝您今天工作顺利！";
        assert_eq!(
            detect_answer_drift(essay, ref_text, &cfg),
            Some(DriftReason::RunawayExpansion)
        );

        assert!(is_short_audio_hallucination(
            "字幕由 Amara.org 社区提供",
            0.8
        ));
        assert!(is_short_audio_hallucination("Thank you for watching!", 0.5));
        assert!(is_short_audio_hallucination(
            "这是一段在半秒空白录音中凭空生成的超长幻觉字幕文本内容，显然不可能在半秒内说完",
            0.4
        ));
        assert!(!is_short_audio_hallucination("原始的rust文本", 0.2));
        assert!(!is_short_audio_hallucination("感谢观看今天的演示", 2.5));
    }
}
