import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.models import DeviceToken, Meal, User
from app.db.session import SessionLocal

EMAIL = "streaktest@example.com"


async def main() -> None:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == EMAIL))
        if user is None:
            user = User(email=EMAIL, name="Streak Test", timezone="UTC")
            db.add(user)
            await db.flush()

        # a claimed device token, so the scheduler considers this user
        if not await db.scalar(
            select(DeviceToken).where(DeviceToken.user_id == user.id)
        ):
            db.add(
                DeviceToken(token=f"fake-test-token-{uuid.uuid4()}", user_id=user.id)
            )

        # meal yesterday, nothing today (UTC — matches user.timezone above)
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)

        def make_meal(day):
            return Meal(
                user_id=user.id,
                title="Test dinner",
                calories=500,
                protein_g=30,
                carbs_g=50,
                fat_g=20,
                eaten_at=datetime.now(timezone.utc),
                eaten_on=day,
            )

        for day in (yesterday, today):
            if not await db.scalar(
                select(Meal.id).where(Meal.user_id == user.id, Meal.eaten_on == day)
            ):
                db.add(make_meal(day))

        await db.commit()
        print(f"Seeded {EMAIL} — streak at risk (logged {yesterday}, nothing {today})")


asyncio.run(main())
