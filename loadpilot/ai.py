"""Rate confirmation extraction through a choice of AI providers.

Gemini is called through its native REST API. OpenAI and OpenRouter share the
OpenAI chat-completions format, so they use the same code with a different URL.
Models are listed live from each provider; nothing here pins a model name.
"""

import base64
import json
import logging
import os
import re
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime

import httpx

logger = logging.getLogger("loadpilot.ai")

REQUEST_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    env_var: str
    key_url: str
    base_url: str
    # Ordered regexes for the suggested default; the first group is the version number, highest wins
    suggestions: tuple[str, ...]


PROVIDERS = {
    "gemini": Provider(
        key="gemini",
        label="Google Gemini",
        env_var="GEMINI_API_KEY",
        key_url="https://aistudio.google.com/apikey",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        suggestions=(r"^gemini-(\d+(?:\.\d+)?)-flash$", r"^gemini-(\d+(?:\.\d+)?)-flash-lite$"),
    ),
    "openai": Provider(
        key="openai",
        label="OpenAI",
        env_var="OPENAI_API_KEY",
        key_url="https://platform.openai.com/api-keys",
        base_url="https://api.openai.com/v1",
        suggestions=(r"^gpt-(\d+(?:\.\d+)?)-mini$", r"^gpt-(\d+(?:\.\d+)?)$"),
    ),
    "openrouter": Provider(
        key="openrouter",
        label="OpenRouter",
        env_var="OPENROUTER_API_KEY",
        key_url="https://openrouter.ai/settings/keys",
        base_url="https://openrouter.ai/api/v1",
        suggestions=(r"^google/gemini-(\d+(?:\.\d+)?)-flash$", r"^openai/gpt-(\d+(?:\.\d+)?)-mini$"),
    ),
}
DEFAULT_PROVIDER = "gemini"

# Identifies LoadPilot on OpenRouter's dashboard
OPENROUTER_HEADERS = {"HTTP-Referer": "https://github.com/Fahmula/loadpilot", "X-Title": "LoadPilot"}

# Model families that can't read a rate con (speech, embeddings, image generation, ...)
GEMINI_EXCLUDED = ("embedding", "aqa", "image", "tts", "live", "audio", "veo", "imagen")
OPENAI_EXCLUDED = ("audio", "realtime", "transcribe", "tts", "search", "image", "instruct", "embedding", "codex")
OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")
DATED_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")

EXTRACTION_FIELDS = """{
    "broker_name": "String (name of the brokerage)",
    "broker_email": "String or null if not found",
    "load_reference": "String (load number, PO number, or order number)",
    "rate_amount": 0.0 (number only, no commas or dollar signs; the total pay for the load),
    "pickup_location": "String (City, ST)",
    "pickup_date": "YYYY-MM-DD",
    "delivery_location": "String (City, ST)",
    "delivery_date": "YYYY-MM-DD",
    "loaded_miles": 0 (integer; look for 'Miles' or 'Loaded Miles', 0 if not found),
    "notes": "String (pickup/delivery numbers, appointment times and instructions; null if none)"
}"""


class AIError(Exception):
    """A failure whose message is safe and useful to show the user."""


@dataclass(frozen=True)
class Model:
    id: str
    name: str

    @property
    def group(self) -> str:
        """Vendor for OpenRouter ids like "google/gemini-3.8-flash"; one group otherwise."""
        return self.id.split("/", 1)[0] if "/" in self.id else "All models"


@dataclass
class AIConfig:
    provider: Provider
    api_key: str
    key_source: str | None  # "saved", "env" or None
    model: str
    extra_instructions: str


# -----------------------------------------
# CONFIGURATION
# -----------------------------------------
async def load_ai_config(db, provider_key: str | None = None) -> AIConfig:
    """Reads the active provider (or the one given) with its key, model and extra instructions."""
    settings = await db.get_all_settings()
    provider_key = provider_key or settings.get("ai_provider") or DEFAULT_PROVIDER
    provider = PROVIDERS.get(provider_key, PROVIDERS[DEFAULT_PROVIDER])

    saved_key = str(settings.get(f"ai_api_key_{provider.key}") or "")
    env_key = os.getenv(provider.env_var, "")
    return AIConfig(
        provider=provider,
        api_key=saved_key or env_key,
        key_source="saved" if saved_key else "env" if env_key else None,
        model=str(settings.get(f"ai_model_{provider.key}") or ""),
        extra_instructions=str(settings.get("ai_extra_instructions") or ""),
    )


def build_prompt(extra_instructions: str = "", today: date | None = None) -> str:
    today = today or date.today()
    prompt = f"""You are an assistant for a trucking owner-operator.
Read the attached rate confirmation (every page provided) and extract the load details.
Today's date is {today.isoformat()}. If a date on the document has no year, assume the next upcoming one.
Return ONLY a JSON object with exactly these fields and no other text:

{EXTRACTION_FIELDS}"""
    if extra_instructions.strip():
        prompt += f"\n\nAdditional instructions from the driver:\n{extra_instructions.strip()}"
    return prompt


# -----------------------------------------
# MODELS
# -----------------------------------------
def list_models(provider: Provider, api_key: str, http: httpx.Client | None = None) -> list[Model]:
    """Models from the provider that can read images, sorted by id."""
    if not api_key:
        raise AIError(f"Enter your {provider.label} API key first.")

    with _client(http) as client:
        if provider.key == "gemini":
            models = _list_gemini_models(client, provider, api_key)
        elif provider.key == "openai":
            models = _list_openai_models(client, provider, api_key)
        else:
            models = _list_openrouter_models(client, provider, api_key)
    return sorted(models, key=lambda m: m.id)


def _list_gemini_models(client: httpx.Client, provider: Provider, api_key: str) -> list[Model]:
    models, page_token = [], None
    while True:
        params = {"pageSize": 1000, **({"pageToken": page_token} if page_token else {})}
        data = _request(
            client, provider, "GET", f"{provider.base_url}/models", _headers(provider, api_key), params=params
        )
        for item in data.get("models", []):
            model_id = item.get("name", "").removeprefix("models/")
            if "generateContent" in item.get("supportedGenerationMethods", []) and not any(
                word in model_id for word in GEMINI_EXCLUDED
            ):
                models.append(Model(model_id, item.get("displayName") or model_id))
        page_token = data.get("nextPageToken")
        if not page_token:
            return models


def _list_openai_models(client: httpx.Client, provider: Provider, api_key: str) -> list[Model]:
    data = _request(client, provider, "GET", f"{provider.base_url}/models", _headers(provider, api_key))
    return [
        Model(item["id"], item["id"])
        for item in data.get("data", [])
        if item.get("id", "").startswith(OPENAI_PREFIXES)
        and not any(word in item["id"] for word in OPENAI_EXCLUDED)
        and not DATED_SNAPSHOT.search(item["id"])
    ]


def _list_openrouter_models(client: httpx.Client, provider: Provider, api_key: str) -> list[Model]:
    data = _request(client, provider, "GET", f"{provider.base_url}/models", _headers(provider, api_key))
    models = []
    for item in data.get("data", []):
        architecture = item.get("architecture") or {}
        reads_images = "image" in architecture.get("input_modalities", [])
        draws_images = "image" in architecture.get("output_modalities", [])
        if reads_images and not draws_images and not item["id"].endswith(":batch"):
            models.append(Model(item["id"], item.get("name") or item["id"]))
    return models


def _version(match: re.Match) -> tuple[int, ...]:
    if not match.groups() or match.group(1) is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def suggest_model(provider: Provider, models: list[Model]) -> str | None:
    """The newest model matching the provider's preferred families, else the first model."""
    for pattern in provider.suggestions:
        matches = [(m, re.match(pattern, m.id)) for m in models]
        matches = [(m, match) for m, match in matches if match]
        if matches:
            return max(matches, key=lambda pair: _version(pair[1]))[0].id
    return models[0].id if models else None


# -----------------------------------------
# EXTRACTION
# -----------------------------------------
def extract_rate_con(
    config: AIConfig, model: str, files: list[tuple[bytes, str, str]], http: httpx.Client | None = None
) -> dict:
    """Sends the rate con pages (bytes, mime type, filename) to the AI and returns normalized load fields."""
    if not config.api_key:
        raise AIError(f"Add your {config.provider.label} API key in Settings to scan rate cons.")
    if not model:
        raise AIError("Choose an AI model in Settings.")

    prompt = build_prompt(config.extra_instructions)
    with _client(http) as client:
        if config.provider.key == "gemini":
            reply = _extract_gemini(client, config, model, prompt, files)
        else:
            reply = _extract_openai_compatible(client, config, model, prompt, files)
    return normalize_extraction(parse_json_reply(reply))


def _extract_gemini(client, config: AIConfig, model: str, prompt: str, files) -> str:
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}} for data, mime, _ in files
    ]
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    url = f"{config.provider.base_url}/models/{model}:generateContent"
    data = _request(client, config.provider, "POST", url, _headers(config.provider, config.api_key), json=body)

    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no response")
        raise AIError(f"Gemini didn't return a result ({reason}).")
    return "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", []))


def _extract_openai_compatible(client, config: AIConfig, model: str, prompt: str, files) -> str:
    content = [{"type": "text", "text": prompt}]
    for data, mime, filename in files:
        data_url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        if mime.startswith("image/"):
            content.append({"type": "image_url", "image_url": {"url": data_url}})
        else:
            content.append({"type": "file", "file": {"filename": filename or "ratecon.pdf", "file_data": data_url}})
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }
    url = f"{config.provider.base_url}/chat/completions"
    data = _request(client, config.provider, "POST", url, _headers(config.provider, config.api_key), json=body)

    try:
        message = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise AIError(f"{config.provider.label} returned an unexpected response.") from e
    if isinstance(message, list):  # some providers return content parts
        message = "".join(part.get("text", "") for part in message if isinstance(part, dict))
    return message or ""


def parse_json_reply(reply: str) -> dict:
    """Pulls the JSON object out of a model reply, tolerating code fences or extra text."""
    text = reply.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise AIError("The AI reply didn't contain any load details. Try again or enter the load manually.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise AIError("The AI reply wasn't valid JSON. Try again or enter the load manually.") from e
    if not isinstance(data, dict):
        raise AIError("The AI reply wasn't in the expected format.")
    return data


def _clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"null", "none", "n/a"} else text


def _clean_number(value) -> float | None:
    cleaned = re.sub(r"[^\d.\-]", "", str(value or ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _clean_date(value) -> str:
    text = _clean_text(value)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def normalize_extraction(data: dict) -> dict:
    """Coerces the AI's answer into what the load form expects, blanking anything unusable."""
    rate = _clean_number(data.get("rate_amount"))
    miles = _clean_number(data.get("loaded_miles"))
    return {
        "broker_name": _clean_text(data.get("broker_name")),
        "broker_email": _clean_text(data.get("broker_email")),
        "load_reference": _clean_text(data.get("load_reference")),
        "rate_amount": rate if rate and rate > 0 else "",
        "pickup_location": _clean_text(data.get("pickup_location")),
        "pickup_date": _clean_date(data.get("pickup_date")),
        "delivery_location": _clean_text(data.get("delivery_location")),
        "delivery_date": _clean_date(data.get("delivery_date")),
        "loaded_miles": int(miles) if miles and miles > 0 else 0,
        "notes": _clean_text(data.get("notes")),
    }


# -----------------------------------------
# HTTP
# -----------------------------------------
def _client(http: httpx.Client | None):
    """Uses the injected client (tests) without closing it, or a fresh one per call."""
    return nullcontext(http) if http is not None else httpx.Client(timeout=REQUEST_TIMEOUT)


def _headers(provider: Provider, api_key: str) -> dict[str, str]:
    if provider.key == "gemini":
        return {"x-goog-api-key": api_key}
    headers = {"Authorization": f"Bearer {api_key}"}
    if provider.key == "openrouter":
        headers.update(OPENROUTER_HEADERS)
    return headers


def _request(client: httpx.Client, provider: Provider, method: str, url: str, headers: dict, **kwargs) -> dict:
    try:
        response = client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as e:
        raise AIError(f"Couldn't reach {provider.label}. Check your connection and try again.") from e

    if response.is_success:
        try:
            return response.json()
        except ValueError as e:
            raise AIError(f"{provider.label} returned an unreadable response.") from e

    detail = _error_detail(response)
    logger.warning(f"{provider.label} request failed ({response.status_code}): {detail}")
    status = response.status_code
    if status in (401, 403):
        raise AIError(f"{provider.label} rejected the API key. Check it in Settings.")
    if status == 402:
        raise AIError(f"Your {provider.label} account is out of credits.")
    if status == 404:
        raise AIError(f"{provider.label} couldn't find that model. Pick another in Settings.")
    if status == 429:
        raise AIError(f"{provider.label} rate limit or quota reached. Wait a minute or check your billing.")
    raise AIError(f"{provider.label} error {status}: {detail[:200]}")


def _error_detail(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
    except ValueError:
        return response.text
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or response.text)
