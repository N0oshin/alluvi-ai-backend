"""
the Claude (Haiku 4.5) emergency scan client
Used only when Gemini fails twice (see resilient.py). Same prompt file and
the same Pydantic validation as the Gemini client,
Distinct from the legacy ClaudeVisionProvider (claude.py), which implements
the old single-model contract; this one speaks the pipeline's ScanResult.
"""

from __future__ import annotations

import base64
import json
import time

from pydantic import ValidationError

from app.core.config import settings
from app.schemas.vision_scan import GEMINI_RESPONSE_SCHEMA, ScanResult
from app.services.vision.base import VisionAnalysisError
from app.services.vision.gemini import (
    PROMPT_VERSION,
    ScanMeta,
    _load_prompt,
    resize_for_analysis,
)

# USD per 1M tokens (Haiku 4.5). Informational, for the cost column.
COST_PER_1M_INPUT = 1.00
COST_PER_1M_OUTPUT = 5.00


class ClaudeScanClient:
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.ANTHROPIC_API_KEY
        self.model = model or settings.ANTHROPIC_FALLBACK_MODEL
        if not self.api_key:
            raise VisionAnalysisError("food.analysis_failed")

    async def scan(
        self,
        *,
        image_bytes: bytes | None = None,
        text_description: str | None = None,
        container: str | None = None,
    ) -> tuple[ScanResult, ScanMeta]:
        import anthropic

        if image_bytes is None and not text_description:
            raise ValueError("need image_bytes or text_description")

        # Claude has no constrained decoding, so the schema rides in the
        # prompt and Pydantic is the sole enforcement gate.
        system = (
            _load_prompt()
            + "\n\nRespond with ONLY a JSON object (no prose, no markdown "
            "fences) matching this schema:\n" + json.dumps(GEMINI_RESPONSE_SCHEMA)
        )

        content: list[dict] = []
        if container:
            content.append(
                {
                    "type": "text",
                    "text": f"Container hint from the user: the food is in/on a {container}.",
                }
            )
        if text_description:
            content.append(
                {
                    "type": "text",
                    "text": f"Text mode — no photo. Meal description: {text_description}",
                }
            )
        if image_bytes is not None:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(
                            resize_for_analysis(image_bytes)
                        ).decode(),
                    },
                }
            )
        if not any(c["type"] == "text" for c in content):
            content.append({"type": "text", "text": "Analyse this meal photo."})

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        started = time.perf_counter()
        try:
            msg = await client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.AnthropicError as exc:
            raise VisionAnalysisError("food.analysis_failed") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        raw = "".join(b.text for b in msg.content if b.type == "text").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        try:
            result = ScanResult.model_validate_json(raw)
        except ValidationError as exc:
            raise VisionAnalysisError("food.analysis_failed") from exc

        meta = ScanMeta(
            model=self.model,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            estimated_cost_usd=round(
                msg.usage.input_tokens * COST_PER_1M_INPUT / 1e6
                + msg.usage.output_tokens * COST_PER_1M_OUTPUT / 1e6,
                6,
            ),
            raw_output=json.loads(raw),
        )
        return result, meta
