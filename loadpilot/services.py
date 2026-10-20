import json
import logging

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
logger = logging.getLogger(__name__)


def parse_rate_con_with_ai(files_data: list[tuple[bytes, str]], api_key: str) -> dict | None:
    """Sends raw document bytes (one or more pages) directly to Gemini Vision to extract metadata."""
    if not api_key:
        logger.error("Missing Gemini API Key. Extraction aborted.")
        return None

    prompt = """
    You are an AI assistant for a trucking owner-operator.
    Look at the attached document pages (Rate Confirmation) and extract these exact details.
    Analyze all pages provided to gather complete and accurate information.
    Return ONLY a valid JSON object matching this exact structure, with no other text:

    {
        "broker_name": "String (Name of the brokerage)",
        "broker_email": "String or null if not found",
        "load_reference": "String (Load number, PO number, or Order number)",
        "rate_amount": 0.0 (Float, numbers only, no commas or dollar signs),
        "pickup_location": "String (City, State)",
        "pickup_date": "YYYY-MM-DD",
        "delivery_location": "String (City, State)",
        "delivery_date": "YYYY-MM-DD",
        "loaded_miles": 0 (Integer, look for 'Miles' or 'Loaded Miles'. Return 0 if not found),
        "notes": "String (Look for pickup numbers and any instructions/comments/notes. null if none)"
    }
    """
    try:
        client = genai.Client(api_key=api_key)

        parts: list[types.Part] = [types.Part.from_text(text=prompt)]

        for file_bytes, mime_type in files_data:
            parts.append(
                types.Part.from_bytes(
                    data=file_bytes,
                    mime_type=mime_type,
                )
            )

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=types.Content(
                role="user",
                parts=parts,
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )

        response_text = response.text
        if not response_text:
            logger.error("Gemini Vision returned an empty or null response.")
            return None

        return json.loads(response_text)
    except Exception as e:
        logger.error(f"Gemini Vision Parsing Error: {e}", exc_info=True)
        return None
