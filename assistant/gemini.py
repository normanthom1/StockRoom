"""The one place StockRoom talks to Google Gemini (generateContent and
batchEmbedContents over plain HTTPS, no SDK). Views call generate() and
stock/matching.py calls embed(); tests mock them.
"""

import base64
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
# Smaller than the model's 3072, so an item's cached vector stays a few KB; plenty for product names.
EMBEDDING_SIZE = 768
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
    # Low thinking: lookups and list-reading don't need it, and it took an
    # invoice import from 5-25s down to 2s. (thinkingLevel is Gemini 3's setting.)
    config = {"thinkingConfig": {"thinkingLevel": "low"}}
    if schema:
        config |= {"responseMimeType": "application/json", "responseSchema": schema}
    body = {"system_instruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": config}

    def answer(reply):
        text = "".join(part.get("text", "") for part in reply["candidates"][0]["content"]["parts"]).strip()
        if not text:
            raise ValueError("empty answer")
        return json.loads(text) if schema else text

    return _post(URL.format(model=settings.AI_MODEL), body, answer)


def embed(texts):
    """An embedding vector for each text, in order, for comparing how alike they mean."""
    model = f"models/{settings.AI_EMBEDDING_MODEL}"
    body = {
        "requests": [
            {"model": model, "content": {"parts": [{"text": text}]}, "taskType": "SEMANTIC_SIMILARITY",
             "outputDimensionality": EMBEDDING_SIZE}
            for text in texts
        ]
    }

    def vectors(reply):
        found = [embedding["values"] for embedding in reply["embeddings"]]
        if len(found) != len(texts):
            raise ValueError("wrong number of embeddings")
        return found

    return _post(EMBED_URL.format(model=settings.AI_EMBEDDING_MODEL), body, vectors)


def _post(url, body, read):
    """POST body to Gemini and return read(reply). Anything that goes wrong is a GeminiError."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": settings.AI_API_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return read(json.load(response))
    except urllib.error.HTTPError as e:
        logger.warning("Gemini HTTP %s: %s", e.code, e.read()[:500])
        raise GeminiError from e
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
        # KeyError/IndexError: a blocked reply has no candidates or parts. ValueError covers bad JSON.
        logger.warning("Gemini gave no usable answer: %r", e)
        raise GeminiError from e
