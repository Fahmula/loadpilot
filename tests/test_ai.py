import base64
import json
from datetime import date

import httpx
import pytest

from loadpilot.ai import (
    PROVIDERS,
    AIConfig,
    AIError,
    Model,
    build_prompt,
    extract_rate_con,
    list_models,
    load_ai_config,
    normalize_extraction,
    parse_json_reply,
    suggest_model,
)

GEMINI, OPENAI, OPENROUTER = PROVIDERS["gemini"], PROVIDERS["openai"], PROVIDERS["openrouter"]

REPLY = {
    "broker_name": "Acme Logistics",
    "broker_email": None,
    "load_reference": "PO-1234",
    "rate_amount": "$2,500.00",
    "pickup_location": "Dallas, TX",
    "pickup_date": "2026-10-21",
    "delivery_location": "Atlanta, GA",
    "delivery_date": "10/23/2026",
    "loaded_miles": "781 mi",
    "notes": "PU# 5521",
}


class FakeAPI:
    """Records requests and answers them with a canned response."""

    def __init__(self, *responses: httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def config(provider, api_key="key-1234", extra=""):
    return AIConfig(provider=provider, api_key=api_key, key_source="saved", model="m", extra_instructions=extra)


# -----------------------------------------
# Listing and suggesting models
# -----------------------------------------
def test_list_gemini_models_filters_and_pages():
    api = FakeAPI(
        httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-9-flash",
                        "displayName": "Gemini 9 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]},
                    {"name": "models/gemini-9-flash-image", "supportedGenerationMethods": ["generateContent"]},
                ],
                "nextPageToken": "page2",
            },
        ),
        httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-9-pro",
                        "displayName": "Gemini 9 Pro",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                ]
            },
        ),
    )

    models = list_models(GEMINI, "key-1234", http=api.client())

    assert [m.id for m in models] == ["gemini-9-flash", "gemini-9-pro"]
    assert api.requests[0].headers["x-goog-api-key"] == "key-1234"
    assert api.requests[1].url.params["pageToken"] == "page2"


def test_list_openai_models_keeps_chat_models_only():
    ids = ["gpt-9-mini", "gpt-9", "gpt-9-2026-01-01", "gpt-9-audio", "text-embedding-3", "dall-e-3", "o5", "whisper-1"]
    api = FakeAPI(httpx.Response(200, json={"data": [{"id": i} for i in ids]}))

    models = list_models(OPENAI, "sk-test", http=api.client())

    assert [m.id for m in models] == ["gpt-9", "gpt-9-mini"]
    assert api.requests[0].headers["authorization"] == "Bearer sk-test"


def test_list_openrouter_models_keeps_vision_models():
    def model(model_id, inputs, outputs=("text",)):
        return {
            "id": model_id,
            "name": model_id,
            "architecture": {"input_modalities": inputs, "output_modalities": outputs},
        }

    api = FakeAPI(
        httpx.Response(
            200,
            json={
                "data": [
                    model("google/gemini-9-flash", ["text", "image", "file"]),
                    model("google/gemini-9-flash:batch", ["text", "image"]),
                    model("google/nano-banana", ["text", "image"], ["image", "text"]),
                    model("meta/text-only", ["text"]),
                ]
            },
        )
    )

    models = list_models(OPENROUTER, "or-key", http=api.client())

    assert [m.id for m in models] == ["google/gemini-9-flash"]
    assert models[0].group == "google"
    assert api.requests[0].headers["x-title"] == "LoadPilot"


def test_list_models_requires_a_key():
    with pytest.raises(AIError, match="API key first"):
        list_models(GEMINI, "")


@pytest.mark.parametrize(
    ("provider", "ids", "expected"),
    [
        (GEMINI, ["gemini-3-flash", "gemini-3.10-flash", "gemini-3.9-flash", "gemini-4-pro"], "gemini-3.10-flash"),
        (GEMINI, ["gemini-3-flash-lite", "gemini-4-pro"], "gemini-3-flash-lite"),
        (OPENAI, ["gpt-5-mini", "gpt-5.4-mini", "gpt-6-sol", "o3"], "gpt-5.4-mini"),
        (OPENAI, ["gpt-6", "gpt-7-sol"], "gpt-6"),
        (
            OPENROUTER,
            ["anthropic/claude-x", "google/gemini-3.8-flash", "google/gemini-3.8-flash-lite"],
            "google/gemini-3.8-flash",
        ),
        (OPENROUTER, ["anthropic/claude-x", "meta/llama"], "anthropic/claude-x"),
    ],
)
def test_suggest_model_picks_newest_preferred_family(provider, ids, expected):
    assert suggest_model(provider, [Model(i, i) for i in ids]) == expected


def test_suggest_model_with_no_models():
    assert suggest_model(GEMINI, []) is None


# -----------------------------------------
# Extraction requests
# -----------------------------------------
def test_extract_with_gemini_sends_inline_pages():
    reply = {"candidates": [{"content": {"parts": [{"text": json.dumps(REPLY)}]}}]}
    api = FakeAPI(httpx.Response(200, json=reply))

    result = extract_rate_con(config(GEMINI), "gemini-9-flash", [(b"%PDF", "application/pdf", "rc.pdf")], api.client())

    request = api.requests[0]
    assert request.url.path.endswith("/models/gemini-9-flash:generateContent")
    assert request.headers["x-goog-api-key"] == "key-1234"
    parts = api.body()["contents"][0]["parts"]
    assert "Return ONLY a JSON object" in parts[0]["text"]
    assert parts[1] == {"inline_data": {"mime_type": "application/pdf", "data": base64.b64encode(b"%PDF").decode()}}
    assert api.body()["generationConfig"]["responseMimeType"] == "application/json"
    assert result["rate_amount"] == 2500.0


@pytest.mark.parametrize("provider", [OPENAI, OPENROUTER])
def test_extract_with_openai_compatible_sends_images_and_pdfs(provider):
    api = FakeAPI(httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(REPLY)}}]}))
    pages = [(b"png-bytes", "image/png", "page1.png"), (b"%PDF", "application/pdf", "page2.pdf")]

    result = extract_rate_con(config(provider), "some/model", pages, api.client())

    request = api.requests[0]
    assert str(request.url) == f"{provider.base_url}/chat/completions"
    assert request.headers["authorization"] == "Bearer key-1234"
    body = api.body()
    assert body["model"] == "some/model"
    assert body["response_format"] == {"type": "json_object"}
    text, image, pdf = body["messages"][0]["content"]
    assert text["type"] == "text"
    assert image == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"png-bytes").decode()},
    }
    assert pdf["type"] == "file" and pdf["file"]["filename"] == "page2.pdf"
    assert pdf["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert result["load_reference"] == "PO-1234"


def test_extract_accepts_content_parts_reply():
    content = [{"type": "text", "text": "```json\n"}, {"type": "text", "text": json.dumps(REPLY) + "\n```"}]
    api = FakeAPI(httpx.Response(200, json={"choices": [{"message": {"content": content}}]}))

    assert (
        extract_rate_con(config(OPENAI), "gpt-9", [(b"x", "image/png", "a.png")], api.client())["notes"] == "PU# 5521"
    )


def test_extra_instructions_reach_the_prompt():
    api = FakeAPI(httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(REPLY)}}]}))

    extract_rate_con(config(OPENAI, extra="Shipper Ref is the pickup number"), "gpt-9", [], api.client())

    assert "Shipper Ref is the pickup number" in api.body()["messages"][0]["content"][0]["text"]


def test_extract_requires_key_and_model():
    with pytest.raises(AIError, match="API key in Settings"):
        extract_rate_con(config(GEMINI, api_key=""), "m", [])
    with pytest.raises(AIError, match="Choose an AI model"):
        extract_rate_con(config(GEMINI), "", [])


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "rejected the API key"),
        (402, "out of credits"),
        (404, "couldn't find that model"),
        (429, "rate limit or quota"),
        (500, "error 500: boom"),
    ],
)
def test_http_errors_become_friendly_messages(status, message):
    api = FakeAPI(httpx.Response(status, json={"error": {"message": "boom"}}))

    with pytest.raises(AIError, match=message):
        extract_rate_con(config(OPENAI), "gpt-9", [], api.client())


def test_network_errors_become_friendly_messages():
    def unreachable(request):
        raise httpx.ConnectError("no route to host")

    client = httpx.Client(transport=httpx.MockTransport(unreachable))

    with pytest.raises(AIError, match="Couldn't reach OpenRouter"):
        list_models(OPENROUTER, "key", http=client)


def test_gemini_blocked_response():
    api = FakeAPI(httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))

    with pytest.raises(AIError, match="SAFETY"):
        extract_rate_con(config(GEMINI), "gemini-9", [], api.client())


# -----------------------------------------
# Parsing the reply
# -----------------------------------------
def test_parse_json_reply_tolerates_fences_and_chatter():
    assert parse_json_reply('Sure! ```json\n{"a": 1}\n``` Hope that helps') == {"a": 1}


@pytest.mark.parametrize("reply", ["no json here", "{not: valid}", "[1, 2]"])
def test_parse_json_reply_rejects_bad_replies(reply):
    with pytest.raises(AIError):
        parse_json_reply(reply)


def test_normalize_extraction_cleans_values():
    data = normalize_extraction(REPLY)

    assert data["rate_amount"] == 2500.0
    assert data["loaded_miles"] == 781
    assert data["delivery_date"] == "2026-10-23"
    assert data["broker_email"] == ""


def test_normalize_extraction_blanks_unusable_values():
    data = normalize_extraction(
        {"rate_amount": "TBD", "loaded_miles": None, "pickup_date": "next Tuesday", "notes": "null"}
    )

    assert data["rate_amount"] == ""
    assert data["loaded_miles"] == 0
    assert data["pickup_date"] == ""
    assert data["notes"] == ""
    assert data["broker_name"] == ""


def test_build_prompt_includes_date_and_extra_instructions():
    prompt = build_prompt("Use Shipper Ref", today=date(2026, 10, 20))

    assert "Today's date is 2026-10-20" in prompt
    assert prompt.endswith("Use Shipper Ref")
    assert "Additional instructions" not in build_prompt("  ")


# -----------------------------------------
# Configuration
# -----------------------------------------
async def test_load_ai_config_prefers_saved_key_over_environment(db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

    from_env = await load_ai_config(db, "openai")
    await db.save_setting("ai_api_key_openai", "sk-saved")
    saved = await load_ai_config(db, "openai")

    assert (from_env.api_key, from_env.key_source) == ("sk-env", "env")
    assert (saved.api_key, saved.key_source) == ("sk-saved", "saved")


async def test_load_ai_config_defaults_to_gemini(db, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    ai = await load_ai_config(db)

    assert ai.provider.key == "gemini"
    assert (ai.api_key, ai.key_source, ai.model) == ("", None, "")


async def test_load_ai_config_uses_saved_provider(db):
    await db.save_setting("ai_provider", "openrouter")
    await db.save_setting("ai_model_openrouter", "google/gemini-9-flash")

    ai = await load_ai_config(db)

    assert ai.provider.key == "openrouter"
    assert ai.model == "google/gemini-9-flash"
