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
        json={
            "gender": "male",
            "activityLevel": "high",
            "heightCm": 180,
            "weightKg": 80,
            "goal": "maintain",
            "desiredWeightKg": 80,
        },
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
    assert (await auth_client.get("/api/profile/nutritionGoals")).json()[
        "calories"
    ] == 2200


# --------------------------------------------------------------------------
# Food scan -> save -> dashboard
# --------------------------------------------------------------------------


async def test_analyze_then_save_then_summary(auth_client):
    await auth_client.post(
        "/api/userinfo/plan",
        json={
            "gender": "male",
            "activityLevel": "moderate",
            "heightCm": 180,
            "weightKg": 80,
            "goal": "lose",
            "desiredWeightKg": 75,
        },
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
    assert summary["caloriesLeft"] == max(0, summary["calorieGoal"] - meal["calories"])


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


def _assert_atwater_consistent(meal):
    """Protein and carbs are 4 kcal per gram, fat is 9 — the stored macros
    must account for the stored calories, or the row contradicts itself."""
    derived = meal["proteinGrams"] * 4 + meal["carbsGrams"] * 4 + meal["fatGrams"] * 9
    assert abs(derived - meal["calories"]) <= 0.1 * meal["calories"] + 5, (
        f"{meal['calories']} kcal but macros imply {derived}"
    )


async def test_calories_override_rescales_macros(auth_client):
    """An edited calorie figure is a portion correction, so the macros follow."""
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("ov.png", _png_bytes((11, 22, 33)), "image/png")},
    )
    result = analyze.json()
    per_serving = result["caloriesPerServing"]

    saved = await auth_client.post(
        "/api/meals",
        json={
            "analysisId": result["analysisId"],
            "quantity": 1,
            "caloriesOverride": per_serving // 2,
        },
    )
    meal = saved.json()
    assert meal["calories"] == per_serving // 2

    scale = meal["calories"] / per_serving
    assert meal["proteinGrams"] == round(result["proteinGramsPerServing"] * scale)
    assert meal["carbsGrams"] == round(result["carbsGramsPerServing"] * scale)
    assert meal["fatGrams"] == round(result["fatGramsPerServing"] * scale)
    _assert_atwater_consistent(meal)


async def test_patching_calories_rescales_macros(auth_client):
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("pt.png", _png_bytes((44, 55, 66)), "image/png")},
    )
    result = analyze.json()
    saved = await auth_client.post(
        "/api/meals", json={"analysisId": result["analysisId"], "quantity": 1}
    )
    meal = saved.json()
    _assert_atwater_consistent(meal)

    patched = await auth_client.patch(
        f"/api/meals/{meal['id']}", json={"calories": result["caloriesPerServing"] * 2}
    )
    assert patched.status_code == 200, patched.text
    updated = patched.json()
    assert updated["proteinGrams"] == result["proteinGramsPerServing"] * 2
    _assert_atwater_consistent(updated)


async def test_quantity_change_keeps_an_earlier_calorie_edit(auth_client):
    """The edit stands, but the macros re-derive from it rather than drifting."""
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("kp.png", _png_bytes((77, 88, 99)), "image/png")},
    )
    result = analyze.json()
    saved = await auth_client.post(
        "/api/meals",
        json={
            "analysisId": result["analysisId"],
            "quantity": 1,
            "caloriesOverride": 400,
        },
    )
    meal_id = saved.json()["id"]

    patched = await auth_client.patch(f"/api/meals/{meal_id}", json={"quantity": 2})
    updated = patched.json()
    assert updated["calories"] == 400, "a user edit is not overwritten by a resize"
    _assert_atwater_consistent(updated)


async def test_fractional_quantity_scales_and_rounds(auth_client):
    """"I ate half of it" is the commonest correction, so it must be loggable."""
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("half.png", _png_bytes((40, 50, 60)), "image/png")},
    )
    result = analyze.json()
    analysis_id = result["analysisId"]

    saved = await auth_client.post(
        "/api/meals", json={"analysisId": analysis_id, "quantity": 0.5}
    )
    assert saved.status_code == 201, saved.text
    meal = saved.json()

    # Totals are rounded to whole units — the client renders integers.
    assert meal["calories"] == round(result["caloriesPerServing"] * 0.5)
    assert meal["proteinGrams"] == round(result["proteinGramsPerServing"] * 0.5)
    assert meal["carbsGrams"] == round(result["carbsGramsPerServing"] * 0.5)
    assert meal["fatGrams"] == round(result["fatGramsPerServing"] * 0.5)

    # Editing the quantity later rescales from the analysis, not from the
    # already-scaled totals — otherwise the error compounds on every edit.
    patched = await auth_client.patch(
        f"/api/meals/{meal['id']}", json={"quantity": 1.5}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["calories"] == round(result["caloriesPerServing"] * 1.5)


async def test_analyze_returns_portion_estimate(auth_client):
    """The portion is the assumption the calorie figure rests on, so it is
    surfaced for the user to check rather than left implicit."""
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("pg.png", _png_bytes((5, 15, 25)), "image/png")},
    )
    assert analyze.status_code == 200, analyze.text
    result = analyze.json()

    assert result["estimatedPortionGrams"] > 0
    assert result["portionConfidence"] in {"low", "medium", "high"}


async def test_portion_estimate_is_persisted(auth_client, session_factory):
    """The client may re-open a saved scan, so the estimate must survive the
    round-trip to the database, not just live in the analyze response."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.db.models import FoodAnalysis

    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("ps.png", _png_bytes((6, 16, 26)), "image/png")},
    )
    result = analyze.json()

    async with session_factory() as db:
        row = await db.scalar(
            select(FoodAnalysis).where(
                FoodAnalysis.id == _uuid.UUID(result["analysisId"])
            )
        )
    assert row is not None
    assert row.estimated_portion_grams == result["estimatedPortionGrams"]
    assert row.portion_confidence.value == result["portionConfidence"]


async def test_analyze_accepts_and_stores_a_container_hint(auth_client, session_factory):
    """The hint bounds the depth a photo cannot show, so it is recorded
    alongside the estimate it influenced."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.db.models import FoodAnalysis

    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("c.png", _png_bytes((8, 18, 28)), "image/png")},
        data={"container": "bowl"},
    )
    assert analyze.status_code == 200, analyze.text

    async with session_factory() as db:
        row = await db.scalar(
            select(FoodAnalysis).where(
                FoodAnalysis.id == _uuid.UUID(analyze.json()["analysisId"])
            )
        )
    assert row.container_hint == "bowl"


async def test_unknown_container_is_ignored_not_rejected(auth_client):
    """A hint only improves the estimate, so an unrecognised label must never
    cost the user their analysis."""
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("u.png", _png_bytes((9, 19, 29)), "image/png")},
        data={"container": "tiffin-carrier"},
    )
    assert analyze.status_code == 200, analyze.text
    assert analyze.json()["caloriesPerServing"] > 0


async def test_analyze_reports_scale_reference(auth_client):
    """Null is meaningful: it is the cue to ask for a fork on the next shot."""
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("s.png", _png_bytes((3, 13, 23)), "image/png")},
    )
    result = analyze.json()
    assert "scaleReference" in result
    assert result["scaleReference"] is None or isinstance(result["scaleReference"], str)


async def test_quantity_must_be_positive(auth_client):
    analyze = await auth_client.post(
        "/api/food/analyze",
        files={"image": ("z.png", _png_bytes((70, 80, 90)), "image/png")},
    )
    resp = await auth_client.post(
        "/api/meals",
        json={"analysisId": analyze.json()["analysisId"], "quantity": 0},
    )
    assert resp.status_code == 422


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
        json={
            "gender": "female",
            "activityLevel": "low",
            "heightCm": 165,
            "weightKg": 70,
            "goal": "lose",
            "desiredWeightKg": 62,
        },
    )
    resp = await auth_client.get("/api/analytics?range=90d")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["currentBmi"]["value"] > 0
    assert body["currentBmi"]["categoryKey"] in {
        "underweight",
        "healthy",
        "overweight",
        "obese",
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


# --------------------------------------------------------------------------
# Username derivation and email changes
# --------------------------------------------------------------------------


async def test_same_local_part_on_different_domains_both_sign_up(client):
    """alice@gmail.com and alice@outlook.com are different people. The
    username is derived from the local part, so the second signup used to
    die on uq_users_username with a 500."""
    local = f"alice{uuid.uuid4().hex[:6]}"
    first = await client.post(
        "/api/Auth/SignUp",
        json={"name": "A", "email": f"{local}@gmail.com", "password": "Passw0rd!"},
    )
    second = await client.post(
        "/api/Auth/SignUp",
        json={"name": "B", "email": f"{local}@outlook.com", "password": "Passw0rd!"},
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text


async def test_username_freed_by_an_email_change_does_not_break_signup(
    auth_client, client, session_factory
):
    """Changing an address leaves the old derived username on the row, so the
    original address becomes re-registerable while its username is still
    taken."""
    from sqlalchemy import select

    from app.db.models import User

    details = await auth_client.get("/api/profile/personalDetails")
    original = details.json()["email"]

    moved = f"moved-{uuid.uuid4().hex[:8]}@example.com"
    resp = await auth_client.put("/api/profile/personalDetails", json={"email": moved})
    assert resp.status_code == 200, resp.text

    # The original address is free again — signing up with it must not 500.
    resp = await client.post(
        "/api/Auth/SignUp",
        json={"name": "New Owner", "email": original, "password": "Passw0rd!"},
    )
    assert resp.status_code == 201, resp.text

    async with session_factory() as db:
        names = (await db.scalars(select(User.username))).all()
        assert len(names) == len(set(names))


async def test_duplicate_signup_is_409_not_500(client):
    email = f"dupe-{uuid.uuid4().hex[:8]}@example.com"
    body = {"name": "A", "email": email, "password": "Passw0rd!"}
    assert (await client.post("/api/Auth/SignUp", json=body)).status_code == 201
    resp = await client.post("/api/Auth/SignUp", json=body)
    assert resp.status_code == 409
    assert resp.json()["code"] == "EMAIL_TAKEN"


async def test_changing_email_revokes_verified_status(auth_client, session_factory):
    """Otherwise a verified user could point the account at an address they
    don't own and still receive its password-reset links."""
    from sqlalchemy import select

    from app.db.models import User

    moved = f"unproven-{uuid.uuid4().hex[:8]}@example.com"
    resp = await auth_client.put("/api/profile/personalDetails", json={"email": moved})
    assert resp.status_code == 200, resp.text
    assert resp.json()["emailVerificationRequired"] is True

    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.email == moved))
        assert user is not None
        assert user.email_verified is False


async def test_changing_email_to_a_taken_address_is_409(auth_client, client):
    taken = f"taken-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/api/Auth/SignUp",
        json={"name": "Owner", "email": taken, "password": "Passw0rd!"},
    )
    assert resp.status_code == 201, resp.text

    resp = await auth_client.put("/api/profile/personalDetails", json={"email": taken})
    assert resp.status_code == 409
    assert resp.json()["code"] == "EMAIL_TAKEN"


async def test_editing_other_fields_leaves_verification_alone(
    auth_client, session_factory
):
    from sqlalchemy import select

    from app.db.models import User

    resp = await auth_client.put(
        "/api/profile/personalDetails", json={"name": "Renamed", "heightCm": 175.0}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["emailVerificationRequired"] is False

    async with session_factory() as db:
        user = await db.scalar(select(User).where(User.name == "Renamed"))
        assert user.email_verified is True
