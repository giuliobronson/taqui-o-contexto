import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.core.config import get_settings


async def ensure_profile(session: AsyncSession, claims: dict[str, Any]) -> Profile:
    """Load the profile for a verified token, creating it on the first login.

    Name and avatar come from the Google data Supabase stores in user_metadata.
    E-mails listed in ADMIN_EMAILS are promoted to admin (never demoted here).
    """
    user_id = uuid.UUID(claims["sub"])
    email = (claims.get("email") or "").lower()
    metadata = claims.get("user_metadata") or {}
    should_be_admin = email in get_settings().admin_emails

    profile = await session.get(Profile, user_id)
    if profile is None:
        profile = Profile(
            id=user_id,
            email=email,
            display_name=metadata.get("full_name")
            or metadata.get("name")
            or email.split("@")[0]
            or "Jogador",
            avatar_url=metadata.get("avatar_url") or metadata.get("picture"),
            is_admin=should_be_admin,
        )
        session.add(profile)
        await session.commit()
    elif should_be_admin and not profile.is_admin:
        profile.is_admin = True
        await session.commit()
    return profile
