"""Export PDF Summary Report."""

from __future__ import annotations

import io

import pytest
from PIL import Image

pytestmark = pytest.mark.asyncio


def _png_bytes(color: tuple[int, int, int] = (90, 140, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, format="PNG")
    return buf.getvalue()


async def test_export_requires_auth(client):
    resp = await client.get("/api/profile/export/pdf")
    assert resp.status_code == 401


async def test_export_with_no_data_is_still_a_pdf(auth_client):
    """A fresh account (no meals, no weight) must not 500."""
    resp = await auth_client.get("/api/profile/export/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")


async def test_export_returns_downloadable_pdf(auth_client):
    await auth_client.put(
        "/api/profile/weight",
        json={"currentWeightKg": 72.5, "goalWeightKg": 68.0},
    )
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("meal.png", _png_bytes(), "image/png")},
    )
    saved = await auth_client.post(
        "/api/meals", json={"analysisId": analyze.json()["analysisId"], "quantity": 1}
    )
    assert saved.status_code == 201

    resp = await auth_client.get("/api/profile/export/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "alluvi-ai-summary-" in disposition
    # A report with content is meaningfully larger than the empty skeleton.
    assert len(resp.content) > 1500
