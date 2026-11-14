"""Gemini through its REST API: one prompt in, the reply text out. Standard library only.

The URL, the x-goog-api-key header and the body follow Google's quickstart REST example
(https://aistudio.google.com/docs?generate-content=true); the reply path follows the method
reference (https://aistudio.google.com/api-reference/generate-content/generate-content).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_S = 15.0


async def ask(prompt: str, key: str, model: str, timeout: float = TIMEOUT_S) -> str:
    """The model's text for prompt. Raises TimeoutError past timeout, RuntimeError on an HTTP or empty reply."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(_post, prompt, key, model, timeout), timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"Gemini did not answer within {timeout:g} s") from None


def _post(prompt: str, key: str, model: str, timeout: float) -> str:
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    request = urllib.request.Request(URL.format(model=model), body,
                                     {"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as e:
        # this text reaches the page, so a key the server echoes back is cut out
        detail = e.read(300).decode(errors="replace").replace(key, "***")
        raise RuntimeError(f"Gemini replied {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):  # a connect timeout arrives wrapped
            raise TimeoutError(f"Gemini did not answer within {timeout:g} s") from None
        raise
    try:
        return "".join(part.get("text", "") for part in reply["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):  # a blocked prompt comes back with no candidates
        raise RuntimeError(f"Gemini sent no text: {json.dumps(reply)[:300]}") from None
