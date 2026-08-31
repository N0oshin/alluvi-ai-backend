"""Gemini vision client for the analysis pipeline.

Raw REST via httpx rather than the Google SDK: it is one POST, keeps
requirements.txt unchanged, and mirrors the shape the OpenRouter fallback
(Step 7) will use.

This module is deliberately dumb: one photo (or text description) in, one
validated ScanResult out, plus call metadata for scan_logs. Retries,
fallback models and caching belong to the caller.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.vision_scan import GEMINI_RESPONSE_SCHEMA, ScanResult
from app.services.vision.base import VisionAnalysisError

PROMPT_VERSION = "food_analysis_v1"
_PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"

API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

# The analysis copy can be smaller than the stored copy: identification
# survives downscaling, and tokens are billed per image tile.
ANALYSIS_MAX_EDGE_PX = 1024
ANALYSIS_JPEG_QUALITY = 80

# USD per 1M tokens — for the estimated_cost_usd column, so precision is
# nice-to-have, not load-bearing. Update if Google reprices.
# Gemini 3.5 Flash paid tier, Aug 2026 (thinking tokens bill as output).
COST_PER_1M_INPUT = 0.75
COST_PER_1M_OUTPUT = 4.50


@dataclass(slots=True)
class ScanMeta:
    """What scan_logs wants to know about the call."""

    model: str
    prompt_version: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    raw_output: dict


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# One client for the process: a fresh AsyncClient per scan pays a full TLS
# handshake to Google every call; a shared one keeps the connection warm.
_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=120.0)
    return _http_client


def resize_for_analysis(image_bytes: bytes) -> bytes:
    """Downscale to ANALYSIS_MAX_EDGE_PX and re-encode as JPEG."""
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGB")
    img.thumbnail((ANALYSIS_MAX_EDGE_PX, ANALYSIS_MAX_EDGE_PX))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=ANALYSIS_JPEG_QUALITY)
    return out.getvalue()


class GeminiScanClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model = model or settings.GEMINI_MODEL
        if not self.api_key:
            raise VisionAnalysisError("food.analysis_failed")

    async def scan(
        self,
        *,
        image_bytes: bytes | None = None,
        text_description: str | None = None,
        container: str | None = None,
    ) -> tuple[ScanResult, ScanMeta]:
        """One photo or text description -> validated ScanResult + metadata.

        Raises VisionAnalysisError on transport, refusal, or validation
        failure. Callers decide about retries and fallbacks.
        """
        if image_bytes is None and not text_description:
            raise ValueError("need image_bytes or text_description")

        parts: list[dict] = [{"text": _load_prompt()}]
        if container:
            parts.append(
                {"text": f"Container hint from the user: the food is in/on a {container}."}
            )
        if text_description:
            parts.append(
                {"text": f"Text mode — no photo. Meal description: {text_description}"}
            )
        if image_bytes is not None:
            # PIL decode/re-encode is CPU-bound; off the event loop so a big
            # photo doesn't stall every other in-flight request.
            resized = await asyncio.to_thread(resize_for_analysis, image_bytes)
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(resized).decode(),
                    }
                }
            )

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": GEMINI_RESPONSE_SCHEMA,
                "temperature": 0.2,
                # Thinking tokens count against this limit, hence the slack.
                "maxOutputTokens": 8192,
                # Gemini 3 flash models think by default, which can push a
                # scan past a minute. Identification doesn't need it.
                "thinkingConfig": {"thinkingLevel": "LOW"},
            },
        }

        started = time.perf_counter()
        try:
            resp = await get_http_client().post(
                API_URL.format(model=self.model),
                headers={"x-goog-api-key": self.api_key},
                json=body,
            )
        except httpx.HTTPError as exc:
            # Timeouts, resets, DNS — transport failures are analysis
            # failures too, not 500s.
            raise VisionAnalysisError("food.analysis_failed") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code != 200:
            raise VisionAnalysisError("food.analysis_failed") from httpx.HTTPStatusError(
                f"gemini {resp.status_code}: {resp.text[:500]}",
                request=resp.request,
                response=resp,
            )

        payload = resp.json()
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise VisionAnalysisError("food.analysis_failed") from exc

        try:
            result = ScanResult.model_validate_json(text)
        except ValidationError as exc:
            raise VisionAnalysisError("food.analysis_failed") from exc

        usage = payload.get("usageMetadata", {})
        in_tok = usage.get("promptTokenCount", 0)
        out_tok = usage.get("candidatesTokenCount", 0)
        meta = ScanMeta(
            model=self.model,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated_cost_usd=round(
                in_tok * COST_PER_1M_INPUT / 1e6 + out_tok * COST_PER_1M_OUTPUT / 1e6,
                6,
            ),
            raw_output=json.loads(text),
        )
        return result, meta
