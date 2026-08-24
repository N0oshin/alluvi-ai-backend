"""Provider selection. Swapping providers is one env var."""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.vision.base import VisionProvider
from app.services.vision.stub import StubVisionProvider


@lru_cache
def get_vision_provider() -> VisionProvider:
    if settings.VISION_PROVIDER == "claude":
        from app.services.vision.claude import ClaudeVisionProvider

        return ClaudeVisionProvider()
    if settings.VISION_PROVIDER == "pipeline":
        from app.services.vision.pipeline import PipelineVisionProvider

        return PipelineVisionProvider()
    return StubVisionProvider()
