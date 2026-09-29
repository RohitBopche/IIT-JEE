"""Minimal Gemini REST client: structured JSON output, rate limiting, retry on 429/5xx."""
import base64
import json
import os
import re
import threading
import time

import requests

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")


class GeminiError(RuntimeError):
    pass


class RateLimiter:
    """Allow at most `rpm` calls per rolling minute across threads."""

    def __init__(self, rpm):
        self.interval = 60.0 / max(rpm, 1)
        self.lock = threading.Lock()
        self.next_ok = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            delay = self.next_ok - now
            self.next_ok = max(now, self.next_ok) + self.interval
        if delay > 0:
            time.sleep(delay)


class Gemini:
    def __init__(self, model=DEFAULT_MODEL, rpm=int(os.environ.get("GEMINI_RPM", "10")), max_retries=8):
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise GeminiError("GEMINI_API_KEY is not set")
        self.key = key
        self.model = model
        self.limiter = RateLimiter(rpm)
        self.max_retries = max_retries
        self.session = requests.Session()

    def generate_json(self, parts, schema, system=None):
        """parts: list of str (text) or bytes (PNG). Returns parsed JSON."""
        req_parts = []
        for p in parts:
            if isinstance(p, bytes):
                req_parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(p).decode()}})
            else:
                req_parts.append({"text": p})
        body = {
            "contents": [{"role": "user", "parts": req_parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                "maxOutputTokens": 65536,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        last_err = None
        for attempt in range(self.max_retries):
            self.limiter.wait()
            try:
                r = self.session.post(
                    API.format(model=self.model),
                    headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
                    json=body,
                    timeout=600,
                )
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(min(2 ** attempt * 5, 120))
                continue
            if r.status_code == 200:
                data = r.json()
                try:
                    cand = data["candidates"][0]
                    text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
                    return json.loads(text), cand.get("finishReason"), data.get("usageMetadata", {})
                except (KeyError, IndexError, json.JSONDecodeError) as e:
                    last_err = f"bad response: {e}: {json.dumps(data)[:500]}"
                    time.sleep(5)
                    continue
            last_err = f"HTTP {r.status_code}: {r.text[:500]}"
            if r.status_code in (429, 500, 502, 503, 504):
                m = re.search(r'"retryDelay":\s*"(\d+)s"', r.text)
                wait = int(m.group(1)) + 2 if m else min(2 ** attempt * 10, 300)
                if r.status_code == 429 and "PerDay" in r.text:
                    raise GeminiError("daily quota exhausted: " + r.text[:300])
                print(f"    gemini {r.status_code}, retry in {wait}s", flush=True)
                time.sleep(wait)
                continue
            raise GeminiError(last_err)
        raise GeminiError(f"gave up after {self.max_retries} attempts: {last_err}")
