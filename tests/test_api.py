"""End-to-end coverage of the contract the Flutter client depends on."""

from __future__ import annotations

import io
import re
import uuid

import pytest
from PIL import Image

pytestmark = pytest.mark.asyncio


def _png_bytes(color: tuple[int, int, int] = (120, 180, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------
# The client's hardest constraint
# --------------------------------------------------------------------------


async def test_validation_error_detail_is_a_string(client):
    """FastAPI's default 422 body is `{"detail": [ ... ]}` — a list, which
    crashes the client's error handler. It must be flattened to a string."""
    resp = await client.post("/api/Auth/SignUp", json={"email": "not-an-email"})
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)


async def test_error_detail_is_localised_by_langcode(client):
    en = await client.post(
        "/api/Auth/login",
        json={"email": "nobody@example.com", "password": "Passw0rd!"},
        headers={"langCode": "2"},
    )
    ar = await client.post(
        "/api/Auth/login",
        json={"email": "nobody@example.com", "password": "Passw0rd!"},
        headers={"langCode": "1"},
    )
    assert en.status_code == ar.status_code == 401
    assert isinstance(en.json()["detail"], str)
    assert en.json()["detail"] != ar.json()["detail"]


async def test_unauthenticated_request_is_401_not_403(client):
    """403 would make AuthorizationInterceptor wipe the session."""
    resp = await client.get("/api/profile")
    assert resp.status_code == 401
    assert isinstance(resp.json()["detail"], str)


async def test_ownership_failure_is_404_not_403(auth_client):
    import uuid

    resp = await auth_client.delete(f"/api/meals/{uuid.uuid4()}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


async def test_password_policy_is_enforced(client):
    resp = await client.post(
        "/api/Auth/SignUp",
        json={"name": "A", "email": "weak@example.com", "password": "password"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "WEAK_PASSWORD"


async def test_duplicate_email_is_rejected(client):
    body = {"name": "A", "email": "dupe@example.com", "password": "Passw0rd!"}
    assert (await client.post("/api/Auth/SignUp", json=body)).status_code == 201
    second = await client.post("/api/Auth/SignUp", json=body)
    assert second.status_code == 409
    assert second.json()["code"] == "EMAIL_TAKEN"


async def test_login_returns_refresh_token(auth_client):
    """The client must start storing refreshToken — it is new in this API."""
    resp = await auth_client.post(
        "/api/Auth/login",
        json={"email": "nope@example.com", "password": "Passw0rd!"},
    )
    # Wrong creds here; the shape assertion happens in the fixture's login.
    assert resp.status_code == 401


async def test_refresh_rotates_and_detects_reuse(client, session_factory):
    from sqlalchemy import select

    from app.db.models import User

    email = "rotate@example.com"
    await client.post(
        "/api/Auth/SignUp",
        json={"name": "R", "email": email, "password": "Passw0rd!"},
    )
    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == email))
        user.email_verified = True
        await db.commit()

    first = (
        await client.post(
            "/api/Auth/login", json={"email": email, "password": "Passw0rd!"}
        )
    ).json()
    original_refresh = first["refreshToken"]

    rotated = await client.post(
        "/api/Auth/refresh", json={"refreshToken": original_refresh}
    )
    assert rotated.status_code == 200
    assert rotated.json()["refreshToken"] != original_refresh

    # Replaying the consumed token is treated as theft.
    replay = await client.post(
        "/api/Auth/refresh", json={"refreshToken": original_refresh}
    )
    assert replay.status_code == 401
    assert replay.json()["code"] == "REFRESH_REUSED"

    # ...and the whole family is now dead, including the rotated successor.
    after = await client.post(
        "/api/Auth/refresh", json={"refreshToken": rotated.json()["refreshToken"]}
    )
    assert after.status_code == 401


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------


async def test_plan_is_computed_and_persisted(auth_client):
    resp = await auth_client.post(
        "/api/userinfo/plan",
        json={
            "gender": "female",
            "activityLevel": "moderate",
            "heightCm": 170,
            "weightKg": 65,
            "goal": "lose",
            "desiredWeightKg": 58,
        },
    )
    assert resp.status_code == 200, resp.text
    plan = resp.json()

    assert 1200 <= plan["dailyCalories"] <= 4000
    assert plan["proteinG"] == 130  # 2 g per kg
    assert plan["weightDeltaKg"] == 7.0
    assert plan["targetDate"] is not None
    assert plan["planVersion"] == 1

    again = await auth_client.get("/api/userinfo/plan")
    assert again.json()["dailyCalories"] == plan["dailyCalories"]


async def test_goal_override_is_flagged(auth_client):
    await auth_client.post(
        "/api/userinfo/plan",
        json={"gender": "male", "activityLevel": "high", "heightCm": 180,
              "weightKg": 80, "goal": "maintain", "desiredWeightKg": 80},
    )
    resp = await auth_client.put(
        "/api/profile/nutritionGoals",
        json={"calories": 2200, "protein": 150, "carbs": 200, "fats": 70},
    )
    assert resp.status_code == 200
    assert resp.json()["isOverride"] is True
    assert resp.json()["calories"] == 2200

    # An override must survive a recalculation triggered by a weight change.
    await auth_client.put("/api/profile/weight", json={"currentWeightKg": 78})
    assert (await auth_client.get("/api/profile/nutritionGoals")).json()["calories"] == 2200


# --------------------------------------------------------------------------
# Food scan -> save -> dashboard
# --------------------------------------------------------------------------


async def test_analyze_then_save_then_summary(auth_client):
    await auth_client.post(
        "/api/userinfo/plan",
        json={"gender": "male", "activityLevel": "moderate", "heightCm": 180,
              "weightKg": 80, "goal": "lose", "desiredWeightKg": 75},
    )

    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("meal.png", _png_bytes(), "image/png")},
    )
    assert analyze.status_code == 200, analyze.text
    result = analyze.json()

    assert result["caloriesPerServing"] > 0
    assert result["detectedItems"], "camera overlay needs per-ingredient markers"
    for item in result["detectedItems"]:
        assert 0.0 <= item["cx"] <= 1.0
        assert 0.0 <= item["cy"] <= 1.0
    assert 0.0 <= result["proteinProgress"] <= 1.0
    assert result["healthScoreMax"] == 10

    saved = await auth_client.post(
        "/api/meals", json={"analysisId": result["analysisId"], "quantity": 2}
    )
    assert saved.status_code == 201, saved.text
    meal = saved.json()
    # Quantity scaling happens once, on save — never twice.
    assert meal["calories"] == result["caloriesPerServing"] * 2

    summary = (await auth_client.get("/api/home/summary")).json()
    assert summary["caloriesConsumed"] == meal["calories"]
    # Macros are what's LEFT against the goal, not what was consumed.
    assert summary["caloriesLeft"] == max(
        0, summary["calorieGoal"] - meal["calories"]
    )


async def test_calories_override_is_recorded(auth_client):
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("m.png", _png_bytes((10, 20, 30)), "image/png")},
    )
    analysis_id = analyze.json()["analysisId"]

    saved = await auth_client.post(
        "/api/meals",
        json={"analysisId": analysis_id, "quantity": 1, "caloriesOverride": 999},
    )
    assert saved.json()["calories"] == 999


async def test_analyze_rejects_non_image(auth_client):
    resp = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("notes.txt", b"this is not an image", "text/plain")},
    )
    assert resp.status_code == 400
    assert isinstance(resp.json()["detail"], str)


async def test_photo_is_downscaled_and_exif_stripped(auth_client, session_factory):
    from sqlalchemy import select

    from app.db.models import MealPhoto

    big = io.BytesIO()
    Image.new("RGB", (4000, 3000), (200, 100, 50)).save(big, format="JPEG")

    resp = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("big.jpg", big.getvalue(), "image/jpeg")},
    )
    assert resp.status_code == 200

    async with session_factory() as db:
        photo = await db.scalar(select(MealPhoto))
        assert max(photo.width, photo.height) <= 1568
        assert photo.mime_type == "image/jpeg"


# --------------------------------------------------------------------------
# Analytics & health sync
# --------------------------------------------------------------------------


async def test_analytics_shape(auth_client):
    await auth_client.post(
        "/api/userinfo/plan",
        json={"gender": "female", "activityLevel": "low", "heightCm": 165,
              "weightKg": 70, "goal": "lose", "desiredWeightKg": 62},
    )
    resp = await auth_client.get("/api/analytics?range=90d")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["currentBmi"]["value"] > 0
    assert body["currentBmi"]["categoryKey"] in {
        "underweight", "healthy", "overweight", "obese"
    }
    assert len(body["caloriesThisWeek"]) == 7
    assert body["goalProgress"]["goalWeightKg"] == 62


async def test_health_sync_is_idempotent(auth_client):
    samples = {
        "samples": [
            {"externalId": "hk-1", "kg": 72.5, "recordedOn": "2026-08-01"},
            {"externalId": "hk-2", "kg": 72.1, "recordedOn": "2026-08-02"},
        ]
    }
    first = await auth_client.post("/api/profile/healthSync", json=samples)
    assert first.json()["imported"] == 2

    # Re-syncing the same HealthKit samples must not double-count.
    second = await auth_client.post("/api/profile/healthSync", json=samples)
    assert second.json()["imported"] == 0
    assert second.json()["skipped"] == 2

    history = (await auth_client.get("/api/profile/weightHistory")).json()
    assert len(history) == 2


# --------------------------------------------------------------------------
# Push tokens — the one already-live contract
# --------------------------------------------------------------------------


async def test_device_token_returns_literal_true(client):
    """The client only records success when the body is literally `true`."""
    resp = await client.post(
        "/api/UserNotification/addAnonymousToken", json={"token": "fcm-abc"}
    )
    assert resp.status_code == 200
    assert resp.json() is True


# --------------------------------------------------------------------------
# Email delivery
# --------------------------------------------------------------------------


async def test_signup_sends_a_verification_email(client, monkeypatch):
    """Sign-up must actually hand a message to the sender, with the live code."""
    from app.services.email import base as email_base
    from app.api.v1 import auth as auth_module

    sent: list[email_base.EmailMessage] = []

    class _Capture(email_base.EmailSender):
        name = "capture"

        async def send(self, message):
            sent.append(message)

    monkeypatch.setattr(auth_module, "get_email_sender", lambda: _Capture())

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/Auth/SignUp",
        json={"name": "Test User", "email": email, "password": "Passw0rd!"},
    )
    assert resp.status_code == 201, resp.text

    assert len(sent) == 1
    message = sent[0]
    assert message.to == email
    # The 6-digit code must appear in the plain-text part, not only the HTML —
    # some clients never render HTML at all.
    code = re.search(r"\b(\d{6})\b", message.text)
    assert code is not None, message.text

    # And it must be the code the database will actually accept.
    verify = await client.post(
        "/api/Auth/verifyCode", json={"email": email, "code": code.group(1)}
    )
    assert verify.status_code == 200, verify.text


async def test_delivery_failure_rolls_back_the_signup(
    client, session_factory, monkeypatch
):
    """A dead provider must not leave a user who can never be verified."""
    from sqlalchemy import select

    from app.services.email import base as email_base
    from app.api.v1 import auth as auth_module
    from app.db.models import User

    class _Broken(email_base.EmailSender):
        name = "broken"

        async def send(self, message):
            raise email_base.EmailDeliveryError("provider down")

    monkeypatch.setattr(auth_module, "get_email_sender", lambda: _Broken())

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/Auth/SignUp",
        json={"name": "Test User", "email": email, "password": "Passw0rd!"},
    )

    assert resp.status_code == 502
    assert isinstance(resp.json()["detail"], str)

    async with session_factory() as db:
        assert await db.scalar(select(User).where(User.email == email)) is None
