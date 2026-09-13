"""The one place StockRoom talks to Google Gemini (generateContent over plain
HTTPS, no SDK). Views call generate(); tests mock it.
"""

import base64
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Under gunicorn's 30s worker timeout, so a slow answer fails cleanly instead of killing the thread.
TIMEOUT_SECONDS = 25


class GeminiError(Exception):
    """No usable answer: network trouble, an API error, or a blocked or empty reply."""


def generate(system, turns, *, schema=None, attachment=None):
    """Ask Gemini. turns is [(role, text)] oldest first, role "user" or "model",
    ending with the user's question. attachment is (mime_type, bytes), sent
    with that last question. With a JSON schema, returns the parsed JSON;
    otherwise the answer text.
    """
    contents = [{"role": role, "parts": [{"text": text}]} for role, text in turns]
    if attachment:
        mime_type, data = attachment
        contents[-1]["parts"].append({"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode()}})
    body = {"system_instruction": {"parts": [{"text": system}]}, "contents": contents}
    if schema:
        body["generationConfig"] = {"responseMimeType": "application/json", "responseSchema": schema}

    request = urllib.request.Request(
        URL.format(model=settings.AI_MODEL),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.AI_API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            reply = json.load(response)
        text = "".join(part.get("text", "") for part in reply["candidates"][0]["content"]["parts"]).strip()
        if not text:
            raise ValueError("empty answer")
        return json.loads(text) if schema else text
    except urllib.error.HTTPError as e:
        logger.warning("Gemini HTTP %s: %s", e.code, e.read()[:500])
        raise GeminiError from e
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
        # KeyError/IndexError: a blocked reply has no candidates or parts. ValueError covers bad JSON.
        logger.warning("Gemini gave no usable answer: %r", e)
        raise GeminiError from e
