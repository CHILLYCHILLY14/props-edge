from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ProviderError(RuntimeError):
    """A provider failed without leaking credentials into the error message."""


class JsonClient:
    def __init__(self, service: str, base_url: str, timeout: int = 25) -> None:
        self.service = service
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None, retries: int = 2) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        secrets = [str(value) for key, value in clean.items() if "key" in key.casefold() and value]
        query = urllib.parse.urlencode(clean, doseq=True)
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url += f"?{query}"
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "props-edge/1.0"},
        )
        last_error = "unknown provider error"
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")[:240]
                    for secret in secrets:
                        body = body.replace(secret, "[REDACTED]")
                except Exception:
                    pass
                last_error = f"HTTP {exc.code}" + (f": {body}" if body else "")
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = type(exc).__name__
                if attempt >= retries:
                    break
            time.sleep(1.5 * (2**attempt))
        raise ProviderError(f"{self.service} request failed ({last_error})")
