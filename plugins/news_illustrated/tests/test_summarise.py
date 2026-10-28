"""The Groq request: its shape, the grounding rules per amount of text, and
the fallbacks when Groq is missing or failing."""

from __future__ import annotations

from plugins.news_illustrated import summarise
from plugins.news_illustrated.tests.fakes import FakeNet, ok_json

ARTICLE = "The council voted on Tuesday to approve the new tram line. " * 20
TEMPLATE = "Summarize this news story in about {words} words."


def _answer(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _run(body: str = ARTICLE, key: str = "gsk_test", words: int = 70) -> tuple[str, list[str]]:
    log: list[str] = []
    out = summarise.summarise(
        "Tram line approved",
        body,
        api_key=key,
        model="openai/gpt-oss-120b",
        words=words,
        template=TEMPLATE,
        log=log.append,
    )
    return out, log


def test_request_shape(fake_net: FakeNet) -> None:
    fake_net.on(summarise.GROQ_URL, ok_json(_answer("A new tram line‑was approved.")))
    out, _log = _run()
    assert out == "A new tram line-was approved."
    (call,) = fake_net.calls
    assert call.method == "POST" and call.url == "https://api.groq.com/openai/v1/chat/completions"
    assert call.headers["Authorization"] == "Bearer gsk_test"
    assert call.headers["Content-Type"] == "application/json"
    assert call.timeout == summarise.TIMEOUT_S
    body = call.json()
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["max_tokens"] == 1024
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False
    assert body["temperature"] == 0.4
    prompt = body["messages"][0]["content"]
    assert body["messages"][0]["role"] == "user"
    assert prompt.startswith("Summarize this news story in about 70 words.")
    assert "Use ONLY facts stated in the headline and content below." in prompt
    assert "Headline: Tram line approved" in prompt and "Content:\nThe council voted" in prompt


def test_thin_text_asks_for_a_short_grounded_summary() -> None:
    prompt = summarise.build_prompt("T", "x" * 100, 70, TEMPLATE)
    assert prompt.kind == "thin"
    assert "at most 40 words" in prompt.text and "Use ONLY facts" in prompt.text
    assert summarise.build_prompt("T", "x" * 100, 30, TEMPLATE).text.count("at most 30 words")


def test_no_text_restates_the_headline_only() -> None:
    prompt = summarise.build_prompt("Tram line approved", "  short ", 70, TEMPLATE)
    assert prompt.kind == "headline"
    assert "at most 25 words" in prompt.text
    assert prompt.text.endswith("Headline: Tram line approved")
    assert "Content:" not in prompt.text


def test_default_instruction_when_no_template() -> None:
    prompt = summarise.build_prompt("T", ARTICLE, 50, "")
    assert prompt.text.startswith("Summarize this news story in exactly 50 words.")


def test_groq_error_falls_back_to_the_opening_words(fake_net: FakeNet) -> None:
    fake_net.on(summarise.GROQ_URL, ok_json({"error": {"message": "model decommissioned"}}, 400))
    out, log = _run(words=5)
    assert out == "The council voted on Tuesday"
    assert "Groq failed: model decommissioned" in log


def test_empty_answer_falls_back(fake_net: FakeNet) -> None:
    fake_net.on(summarise.GROQ_URL, ok_json(_answer("  ")))
    assert _run(words=3)[0] == "The council voted"


def test_no_key_makes_no_request_and_falls_back(fake_net: FakeNet) -> None:
    out, log = _run(key="", words=4)
    assert out == "The council voted on"
    assert fake_net.calls == []
    assert "No Groq key, using the start of the source text" in log


def test_headline_only_fallback_is_the_headline(fake_net: FakeNet) -> None:
    assert _run(body="", key="")[0] == "Tram line approved"


def test_only_text_content_counts_as_a_summary(fake_net: FakeNet) -> None:
    fake_net.on(
        summarise.GROQ_URL,
        ok_json(_answer({"text": "an object"})),  # type: ignore[arg-type]
        ok_json({"choices": [{"message": {"content": [{"type": "text", "text": "Parts."}]}}]}),
    )
    assert _run(words=3)[0] == "The council voted"
    assert _run(words=3)[0] == "Parts."


def test_groq_usage_is_reported_and_logged(fake_net: FakeNet) -> None:
    fake_net.on(
        summarise.GROQ_URL,
        ok_json(
            {
                **_answer("A short summary."),
                "usage": {"prompt_tokens": 1100, "completion_tokens": 212, "total_tokens": 1312},
            }
        ),
    )
    heard: list[dict[str, int]] = []
    log: list[str] = []
    out = summarise.summarise(
        "T",
        ARTICLE,
        api_key="k",
        model="m",
        words=50,
        template="",
        log=log.append,
        usage=heard.append,
    )
    assert out == "A short summary."
    assert heard == [{"tokens": 1312, "in": 1100, "out": 212}]
    assert "Summary: about 3 words, Groq 1,312 tokens" in log


def test_tokens_used_reads_only_real_counts() -> None:
    assert summarise.tokens_used({"usage": {"total_tokens": 5, "prompt_tokens": True}}) == {
        "tokens": 5
    }
    assert summarise.tokens_used({"usage": {"prompt_tokens": 5}}) == {}
    assert summarise.tokens_used({"usage": "lots"}) == {}
    assert summarise.tokens_used(None) == {}


def test_summaries_off_says_so(fake_net: FakeNet) -> None:
    log: list[str] = []
    out = summarise.summarise(
        "T", ARTICLE, api_key="", model="m", words=4, template="", log=log.append, off=True
    )
    assert out == "The council voted on"
    assert fake_net.calls == []
    assert "Summaries are off, using the start of the source text" in log
