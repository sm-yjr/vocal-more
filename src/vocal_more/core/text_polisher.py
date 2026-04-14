"""Text polishing helpers and second-stage LLM text polishing."""

from dataclasses import dataclass
from typing import Callable, Generator, Optional

import dashscope
from dashscope import Generation, MultiModalConversation

from ..config import LLMConfig, get_config, get_llm_model_info
from ..dictionary import normalize_terms


COMMON_POLISH_RULES = """通用要求：
1. 必须保持原意、事实、结论、时间、条件和行动项不变
2. 优先保持原本的信息顺序；除非原文明显混乱，否则不要重组结构
3. 只有当原文明确出现"第一/第二/第三/首先/其次/最后/有三点/包括"等结构信号时，才允许按原顺序整理成列表
4. 输出字数不应明显多于原文；如果你发现自己在扩写，说明偏离了方向

禁止行为：
- 不要改变事实和结论
- 不要补充原文没有的信息、观点、建议或评价（如"需重点关注""建议优化"等）
- 不要偏离指定的强度、语气和人格
- 不要在没有明确信号时强行拆成列表
- 不要把口语默认改写成书面语，除非对应强度明确允许
- 不要使用"该""其""上述""综上""隐患""不佳""予以"等书面腔词汇，用"这个""它""问题""不好""不太好"等日常表达"""


POLISH_EXAMPLES = {
    "minimal": """示例：
输入：嗯那个我想说一下就是我们的 API 响应时间最近变慢了然后用户那边有投诉
输出：嗯那个，我想说一下就是我们的 API 响应时间最近变慢了，然后用户那边有投诉。""",
    "balanced": """示例：
输入：嗯那个我想说一下就是我们的 API 响应时间最近变慢了然后用户那边有投诉
输出：我们的 API 响应时间最近变慢了，用户那边有投诉。""",
    "strong": """示例：
输入：嗯那个我想说一下就是我们的 API 响应时间最近变慢了然后用户那边有投诉
输出：API 响应时间最近变慢，用户有投诉。""",
}

LEVEL_INSTRUCTIONS = {
    "minimal": "默认尽量保留原句、原词和口语感。只允许修正明显错字、明显 ASR 误识别、补最基本标点；只有在严重影响理解时，才删除极少量明显口头填充或处理自我更正。不要主动书面化，不要主动压缩冗余，不要明显改写句式。",
    "balanced": "在不改变原意的前提下适度整理句子，让表达更顺、更清楚。删除口头填充词（嗯、啊、哎、哦、呃、那个、就是、就是说、然后就是 等），合并少量重复，但不要过度书面化。遇到自我更正（如'不对不对，应该是X'）时，直接使用更正后的说法，去掉更正过程。",
    "strong": "在不改变原意和关键信息的前提下，删除所有口头填充词（嗯、啊、哎、哦、呃、那个、就是、就是说、然后就是 等），大幅压缩冗余、合并重复，让表达更凝练。但凝练不等于书面化——保持说话人的自然语感，不要用公文腔。",
}

STRUCTURED_INSTRUCTIONS = """当内容存在结构化特征时（如并列要点、步骤、分层信息），用换行和编号让结构清晰可读：
- 存在多个并列要点或条目时，每条独占一行，用序号（1. 2. 3.）标明
- 条目之间用换行分隔，让每一点一目了然
- 不要使用 Markdown 语法（如 - 、> 、## 、**粗体** 等），因为输出可能粘贴到不支持 Markdown 渲染的地方
- 如果内容本身是单条连贯的表达，不要强行拆成列表
- 结构化只是让内容更好读——只在确实有结构时使用，不要为了格式化而格式化"""

TONE_INSTRUCTIONS = {
    "neutral": "保持自然、中性、克制的表达，不主动增加额外情绪色彩。",
    "gentle": "将生硬、直接、强硬的措辞软化为更温和、委婉的表达，但不要过度客套。",
    "direct": "将犹豫、绕弯、不够明确的表达整理为更直接、明确、利落的说法，但不要显得粗暴。",
}

TECHNICAL_PERSONA_INSTRUCTIONS = """1. 保护代码、命令、API 名、函数名、库名、版本号、路径等技术标识符——不翻译、不替换、不改写
2. 补充标点时不要破坏代码片段、命令参数或路径格式
3. 可以让技术描述更清楚，但不要改变技术含义"""

BILINGUAL_PERSONA_INSTRUCTIONS = """1. 中文与英文、中文与数字之间加一个半角空格（如"使用 Docker 部署""共 3 个节点"）
2. 英文术语、产品名、命令名保持英文原样，不硬翻
3. 英文句段内用半角标点，中文语境用全角标点
4. 让双语内容读起来自然顺畅"""

PROFESSIONAL_PERSONA_INSTRUCTIONS = """1. 确保结论、行动项、时间和责任人等关键信息容易定位
2. 去掉不必要的口语铺垫（如"我觉得吧""其实也不一定"），直接进入正题
3. 表达保持朴实自然——目标是"说清楚"，不是"书面化"；不要用公文套话"""

CHAT_PERSONA_INSTRUCTIONS = """1. 保留聊天口语感，适合 IM/Slack/微信
2. 短句、直接、好读，不要官腔
3. 原文已像聊天消息时尽量少改"""

PERSONA_INSTRUCTIONS = {
    "default": "保持通用写作风格，不附加特定职业或沟通身份。",
    "technical": TECHNICAL_PERSONA_INSTRUCTIONS,
    "bilingual": BILINGUAL_PERSONA_INSTRUCTIONS,
    "professional": PROFESSIONAL_PERSONA_INSTRUCTIONS,
    "chat": CHAT_PERSONA_INSTRUCTIONS,
}


def _build_polish_rule_block(llm_config: LLMConfig) -> str:
    blocks = [
        f"润色强度要求：\n{LEVEL_INSTRUCTIONS[llm_config.level]}",
        f"语气要求：\n{TONE_INSTRUCTIONS[llm_config.tone]}",
        f"表达人格要求：\n{PERSONA_INSTRUCTIONS[llm_config.persona]}",
    ]
    if llm_config.structured:
        blocks.append(f"结构化格式要求：\n{STRUCTURED_INSTRUCTIONS}")
    blocks.append(COMMON_POLISH_RULES)
    blocks.append(POLISH_EXAMPLES[llm_config.level])
    return "\n\n".join(blocks)


def build_polish_system_prompt(llm_config: Optional[LLMConfig] = None) -> str:
    """Build the shared polish system prompt for second-stage LLM calls."""
    llm_config = llm_config or get_config().llm
    return f"""你是一个中文听写整理助手。

你需要根据给定的润色强度、语气和表达人格来整理文本。
在任何情况下都必须保持原意，不补充原文没有的信息。

{_build_polish_rule_block(llm_config)}

请直接输出处理后的文本，不要添加任何解释或说明。"""


def build_omni_inline_polish_instructions(llm_config: Optional[LLMConfig] = None) -> str:
    """Build the shared prompt used when Omni directly returns the final text."""
    from ..dictionary import get_dictionary

    llm_config = llm_config or get_config().llm
    dictionary_block = get_dictionary().format_for_prompt()
    extra = f"\n\n{dictionary_block}" if dictionary_block else ""
    return f"""你是一个中文听写整理助手。

你会收到用户口述的音频内容。请先准确理解用户说的话，再直接输出最终整理后的文本。
你不是在回答问题，也不是在和用户对话；你的唯一任务是把用户刚才说出的内容整理成最终可直接使用的文本。

{_build_polish_rule_block(llm_config)}{extra}

请只输出最终整理后的文本，不要解释过程，不要添加前缀，不要复述任务。"""


def should_polish_text(
    llm_config: Optional[LLMConfig],
    original_text: str,
    normalized_text: str,
) -> bool:
    """Decide whether the text needs polish based on config and text length."""
    llm_config = llm_config or get_config().llm
    if llm_config.polish_mode == "always":
        return True

    text = normalized_text.strip()
    if len(text) < 2:
        return False

    if len(text) <= 4:
        return False

    return True



@dataclass
class PolishResult:
    """Text polishing result."""

    original_text: str
    polished_text: str
    normalized_text: str = ""
    used_llm: bool = False


class TextPolisher:
    """Text polisher using DashScope Qwen3.5-Plus."""

    def __init__(self):
        """Initialize the text polisher."""
        self.config = get_config()
        if self.config.api_key:
            dashscope.api_key = self.config.api_key

    def _build_messages(self, text: str) -> list[dict]:
        """Build system + user messages for the LLM call."""
        return [
            {"role": "system", "content": build_polish_system_prompt(self.config.llm)},
            {"role": "user", "content": text},
        ]

    def should_polish(self, original_text: str, normalized_text: str) -> bool:
        """Decide whether the text needs LLM polishing."""
        return should_polish_text(self.config.llm, original_text, normalized_text)

    def _call_generation(self, messages: list[dict], stream: bool = False):
        info = get_llm_model_info(self.config.llm.model)
        api_name = info.get("api") if info else "generation"
        print(
            f"[Polisher] Calling model={self.config.llm.model}, "
            f"api={api_name}, thinking={self.config.llm.enable_thinking}, "
            f"temperature={self.config.llm.temperature}"
        )
        if info and info.get("api") == "multimodal_conversation":
            # MultiModalConversation expects content as list of dicts
            mm_messages = [
                {"role": m["role"], "content": [{"text": m["content"]}]}
                for m in messages
            ]
            return MultiModalConversation.call(
                model=self.config.llm.model,
                messages=mm_messages,
                enable_thinking=self.config.llm.enable_thinking,
                temperature=self.config.llm.temperature,
                max_tokens=self.config.llm.max_tokens,
                stream=stream,
                incremental_output=True,
            )

        return Generation.call(
            model=self.config.llm.model,
            messages=messages,
            temperature=self.config.llm.temperature,
            max_tokens=self.config.llm.max_tokens,
            enable_thinking=self.config.llm.enable_thinking,
            result_format="message",
            stream=stream,
            incremental_output=True,
        )

    def _extract_response_text(self, response) -> str:
        choice = response.output.choices[0].message.content
        if isinstance(choice, str):
            return choice

        chunks = []
        for item in choice or []:
            if isinstance(item, dict) and "text" in item:
                chunks.append(str(item["text"]))
            elif isinstance(item, str):
                chunks.append(item)
        return "".join(chunks)

    def _prepare(self, text: str) -> tuple[str, Optional[PolishResult]]:
        """Normalize text and decide if LLM is needed. Returns (normalized, skip_result)."""
        if not text.strip():
            return text, PolishResult(original_text=text, polished_text=text, normalized_text=text)

        normalized = normalize_terms(text)
        if not self.should_polish(text, normalized):
            return normalized, PolishResult(
                original_text=text, polished_text=normalized,
                normalized_text=normalized, used_llm=False,
            )
        return normalized, None

    def polish(self, text: str) -> PolishResult:
        """Polish the text."""
        normalized, skip = self._prepare(text)
        if skip:
            return skip

        response = self._call_generation(self._build_messages(normalized))
        if response.status_code == 200:
            return PolishResult(
                original_text=text, polished_text=self._extract_response_text(response).strip(),
                normalized_text=normalized, used_llm=True,
            )
        raise Exception(f"API error: {response.code} - {response.message}")

    def polish_stream(
        self,
        text: str,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> Generator[str, None, PolishResult]:
        """Polish text with streaming output."""
        normalized, skip = self._prepare(text)
        if skip:
            if normalized.strip() and on_chunk:
                on_chunk(normalized)
            if normalized.strip():
                yield normalized
            return skip

        messages = self._build_messages(normalized)
        responses = self._call_generation(messages, stream=True)

        full_text = ""
        for response in responses:
            if response.status_code == 200:
                chunk = self._extract_response_text(response)
                full_text += chunk
                if on_chunk:
                    on_chunk(chunk)
                yield chunk
            else:
                raise Exception(f"API error: {response.code} - {response.message}")

        return PolishResult(
            original_text=text,
            polished_text=full_text.strip(),
            normalized_text=normalized,
            used_llm=True,
        )
