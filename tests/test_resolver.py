import asyncio
import json
from types import SimpleNamespace

import pytest

from app.concepts import is_sam_friendly
from app.reasoner.base import ReasonerError, Result, parse_json_reply, with_retries
from app.reasoner.meta import MetaApiReasoner, to_responses_input
from app.reasoner.openai_compat import OpenAICompatReasoner, to_chat_messages
from app.resolver import SCHEMA, clean, Resolution, resolve_concept, semantic_find
from app.world_model import WorldModel

W, H = 480, 640


def run(coro):
    return asyncio.run(coro)


class FakeReasoner:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    async def respond(self, messages, **kw):
        self.calls.append((messages, kw))
        return Result(text="", parsed=self.replies.pop(0), input_tokens=10, output_tokens=5, latency_ms=1.0)


def room():
    wm = WorldModel()
    s = wm.start_scan()
    f = wm.add_frame(s, 1.0, 0.0, 0.0, W, H, roll=0.0)
    wm.observe(f, [{"label": "charger", "box": [200, 300, 240, 340]}, {"label": "laptop", "box": [280, 300, 340, 340]}])
    return wm


def test_sam_friendly():
    assert is_sam_friendly("water bottle") and is_sam_friendly("remote control")
    assert not is_sam_friendly("the red mug next to the laptop")
    assert not is_sam_friendly("keys, wallet")
    assert not is_sam_friendly("cable without plug")
    assert not is_sam_friendly("")


def test_clean_filters_and_promotes():
    r = clean(Resolution(concept="the thing I charge my laptop with", alternates=["Charger", "charging cable", "power adapter"], confidence=0.9))
    assert r.concept == "charger" and r.alternates == ["charging cable", "power adapter"] and r.confidence <= 0.5
    assert clean(Resolution(concept="a, b", alternates=["not a thing"], confidence=0.9)).concept == ""
    ok = clean(Resolution(concept="The Charger", alternates=["charger", "adapter"], confidence=0.8))
    assert ok.concept == "charger" and ok.alternates == ["adapter"] and ok.confidence == 0.8


def test_schema_is_strict_compatible():
    assert SCHEMA["additionalProperties"] is False
    assert set(SCHEMA["required"]) == {"concept", "alternates", "confidence"}


def test_resolve_concept_passes_known_labels():
    fake = FakeReasoner({"concept": "charger", "alternates": ["power adapter"], "confidence": 0.9})
    res, usage = run(resolve_concept(fake, "the thing I charge my laptop with", ["laptop", "mug"]))
    assert res.concept == "charger" and usage["input_tokens"] == 10
    msgs, kw = fake.calls[0]
    assert "Known labels in this room: laptop, mug" in msgs[1]["content"] and kw["schema"] == SCHEMA


def test_semantic_find_hits_world_model():
    wm = room()
    fake = FakeReasoner({"concept": "power adapter", "alternates": ["charger"], "confidence": 0.8})
    r = run(semantic_find(fake, wm, "the thing I charge my laptop with"))
    assert r["found"]["object"]["label"] == "charger" and not r["needs_live_look"] and not r["skipped_llm"]
    assert "laptop" in r["found"]["speech"]


def test_semantic_find_skips_model_for_exact_label():
    wm = room()
    fake = FakeReasoner()
    r = run(semantic_find(fake, wm, "The Laptop"))
    assert r["skipped_llm"] and fake.calls == [] and r["found"]["object"]["label"] == "laptop"


def test_semantic_find_not_in_memory_asks_for_live_look():
    wm = room()
    r = run(semantic_find(FakeReasoner({"concept": "scissors", "alternates": [], "confidence": 0.9}), wm, "something to cut paper"))
    assert r["found"] is None and r["needs_live_look"] and r["resolution"]["concept"] == "scissors"


def test_semantic_find_unresolvable_query():
    r = run(semantic_find(FakeReasoner({"concept": "", "alternates": [], "confidence": 0.0}), room(), "what is the meaning of life"))
    assert r["found"] is None and not r["needs_live_look"]


# ---------------------------------------------------------------- reasoner plumbing

def test_message_conversion():
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": [{"type": "text", "text": "t"}, {"type": "image", "data_url": "data:image/jpeg;base64,AA"}]}]
    assert to_responses_input(msgs)[1]["content"] == [{"type": "input_text", "text": "t"}, {"type": "input_image", "image_url": "data:image/jpeg;base64,AA"}]
    assert to_chat_messages(msgs)[1]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA"}}
    with pytest.raises(ValueError):
        to_responses_input([{"role": "user", "content": [{"type": "audio"}]}])


def test_parse_json_reply():
    assert parse_json_reply('{"a": 1}') == {"a": 1}
    for bad in ("nope", "[1]"):
        with pytest.raises(ReasonerError):
            parse_json_reply(bad)


class StatusError(Exception):
    def __init__(self, status, retry_after=None):
        self.status_code = status
        self.response = SimpleNamespace(headers={"retry-after": retry_after} if retry_after else {})


def test_retries_honour_retry_after_then_succeed():
    sleeps, n = [], 0

    async def fake_sleep(s):
        sleeps.append(s)

    async def call():
        nonlocal n
        n += 1
        if n == 1:
            raise StatusError(429, "3")
        if n == 2:
            raise StatusError(503)
        return "ok"

    assert run(with_retries(call, sleep=fake_sleep, rand=lambda: 1.0)) == "ok"
    assert sleeps == [3.0, 1.0]  # Retry-After first, then base_delay*2 with full jitter


def test_retries_do_not_retry_client_errors_and_give_up():
    async def bad():
        raise StatusError(400)

    with pytest.raises(StatusError):
        run(with_retries(bad, sleep=lambda s: asyncio.sleep(0)))
    calls = 0

    async def always_429():
        nonlocal calls
        calls += 1
        raise StatusError(429)

    with pytest.raises(StatusError):
        run(with_retries(always_429, attempts=3, sleep=lambda s: asyncio.sleep(0)))
    assert calls == 3


class FakeResponses:
    def __init__(self, text):
        self.text, self.kwargs = text, None

    async def create(self, **kw):
        self.kwargs = kw
        return SimpleNamespace(output_text=self.text, output=[], usage=SimpleNamespace(input_tokens=7, output_tokens=3))


def test_meta_reasoner_builds_request_and_parses():
    fake = SimpleNamespace(responses=FakeResponses('{"concept": "mug", "alternates": [], "confidence": 1}'))
    r = run(MetaApiReasoner(client=fake, model="muse-spark-1.3").respond([{"role": "user", "content": "hi"}], schema={"type": "object"}, effort="low"))
    kw = fake.responses.kwargs
    assert kw["model"] == "muse-spark-1.3" and kw["reasoning"] == {"effort": "low"}
    assert kw["text"]["format"]["type"] == "json_schema" and kw["text"]["format"]["strict"] is True
    assert r.parsed["concept"] == "mug" and (r.input_tokens, r.output_tokens) == (7, 3)


def test_meta_reasoner_refuses_contributor_mode():
    with pytest.raises(ValueError):
        MetaApiReasoner(client=object(), model="muse-spark-1.3-contributor")


def test_openai_compat_reasoner_builds_request_and_parses():
    msg = SimpleNamespace(content='{"x": 1}', tool_calls=None)
    comp = SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2))
    seen = {}

    async def create(**kw):
        seen.update(kw)
        return comp

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    r = run(OpenAICompatReasoner(client=fake, model="glimmer").respond([{"role": "user", "content": "hi"}], schema={"type": "object"}))
    assert seen["response_format"]["type"] == "json_schema" and seen["model"] == "glimmer"
    assert r.parsed == {"x": 1} and r.input_tokens == 4
