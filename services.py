# services.py
import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def parse_rate_con_with_ai(file_bytes: bytes, mime_type: str):
    """
    Sends the raw image or PDF directly to Gemini Vision
    and forces it to return a JSON object.
    """
    
    prompt = """
    You are an AI assistant for a trucking owner-operator. 
    Look at the attached document (Rate Confirmation) and extract these exact details.
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
        "notes": "String (Look for pickup numbers, seal numbers, or special instructions. null if none)"
    }
    """
    
    try:
        # Tell Gemini what kind of file we are handing it
        document_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        
        # Pass BOTH the prompt and the document to Gemini
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite',
            contents=[prompt, document_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        result = json.loads(response.text)
        return result
        
    except Exception as e:
        print(f"Gemini Vision Parsing Error: {e}")
        return None