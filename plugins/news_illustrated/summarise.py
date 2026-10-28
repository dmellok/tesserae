"""A short, grounded summary of a story, written by Groq.

The prompt forbids inventing anything not in the headline and text, and
changes shape with how much text there is: a full article gets the
configured prompt, a thin one a shorter summary from that text only, and a
bare headline one or two sentences restating it. When Groq is not set up or
fails, the story still gets text: the first words of the source.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import net, text

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT_S = 30
# gpt-oss is a reasoning model: reasoning tokens count toward max_tokens, so
# the cap is far above the length of the summary itself.
MAX_TOKENS = 1024
# Shorter than this (characters) is "thin": summarise briefly, from the text only.
THIN_TEXT_CHARS = 400
# Shorter than this is no text at all: restate the headline.
EMPTY_TEXT_CHARS = 40
DEFAULT_INSTRUCTION = (
    "Summarize this news story in exactly {words} words. Write as flowing prose only, "
    "no bullet points, no headers, no markdown."
)
GROUNDING = (
    "Use ONLY facts stated in the headline and content below. Never invent or guess details, "
    "numbers, names, quotes, causes or outcomes that are not in the text. Do not mention that "
    "the text is short or missing."
)


@dataclass(frozen=True)
class Prompt:
    text: str
    kind: str  # "full", "thin" or "headline"


def build_prompt(title: str, body: str, words: int, template: str = "") -> Prompt:
    """The prompt for this much source text."""
    body = body.strip()
    instruction = (template or DEFAULT_INSTRUCTION).replace("{words}", str(words))
    if len(body) < EMPTY_TEXT_CHARS:
        return Prompt(
            f"Write one or two plain-language sentences (at most {min(words, 25)} words) "
            "explaining this news headline. Only the headline is available: restate what it "
            "says without adding any facts, numbers, names or context that are not in the "
            "headline itself. No bullet points, no markdown.\n\n"
            f"Headline: {title}",
            "headline",
        )
    if len(body) < THIN_TEXT_CHARS:
        return Prompt(
            f"{instruction}\n\n{GROUNDING} The content is short, so keep the summary short "
            f"(at most {min(words, 40)} words) instead of padding it out."
            f"\n\nHeadline: {title}\n\nContent:\n{body}",
            "thin",
        )
    return Prompt(f"{instruction}\n\n{GROUNDING}\n\nHeadline: {title}\n\nContent:\n{body}", "full")


def payload(prompt: str, model: str) -> dict[str, object]:
    """The chat-completions request body."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": "low",
        "include_reasoning": False,
        "temperature": 0.4,
    }


def summarise(
    title: str,
    body: str,
    *,
    api_key: str,
    model: str,
    words: int,
    template: str,
    log: Callable[[str], None],
    usage: Callable[[dict[str, int]], None] = lambda used: None,
    off: bool = False,
) -> str:
    """The summary, normalised for the panel. Never empty. ``usage`` hears
    the tokens Groq says the call used ({"tokens", "in", "out"})."""
    prompt = build_prompt(title, body, words, template)
    if prompt.kind == "headline":
        log("No article text, summarising from the headline only")
    elif prompt.kind == "thin":
        log("Thin article text, short grounded summary")
    if api_key:
        reply = net.post_json(
            GROQ_URL,
            payload(prompt.text, model),
            timeout=TIMEOUT_S,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        answer = reply.json()
        content = _content(answer)
        used = tokens_used(answer)
        if used:
            usage(used)
        if reply.ok and content:
            spent = f", Groq {used['tokens']:,} tokens" if used else ""
            log(f"Summary: about {len(content.split())} words{spent}")
            return text.normalise(content)
        log(f"Groq failed: {_error(answer) or reply.reason()}")
    else:
        log(
            "Summaries are off, using the start of the source text"
            if off
            else "No Groq key, using the start of the source text"
        )
    if prompt.kind == "headline":
        return title
    return text.normalise(text.first_words(body, words))


def tokens_used(answer: object) -> dict[str, int]:
    """Groq's (OpenAI-style) ``usage``: {"tokens", "in", "out"}; {} without one."""
    got = answer.get("usage") if isinstance(answer, dict) else None
    if not isinstance(got, dict):
        return {}
    out: dict[str, int] = {}
    for mine, theirs in (
        ("tokens", "total_tokens"),
        ("in", "prompt_tokens"),
        ("out", "completion_tokens"),
    ):
        value = got.get(theirs)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[mine] = value
    return out if "tokens" in out else {}


def _content(answer: object) -> str:
    """The reply text: a string, or a list of ``{"type": "text", "text": ...}``
    parts. Anything else (an object, numbers) is not a summary."""
    try:
        content = answer["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list) and all(
        isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
        for p in content
    ):
        return " ".join(p["text"] for p in content).strip()
    return ""


def _error(answer: object) -> str:
    if isinstance(answer, dict) and isinstance(answer.get("error"), dict):
        return " ".join(str(answer["error"].get("message") or "").split())[:200]
    return ""
