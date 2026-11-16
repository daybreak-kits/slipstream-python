from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from l01_core.config import Settings
from l01_core.ids import new_id
from l01_core.timeutil import now_ms
from l02_infra.crypto import hash_password, verify_password
from l04_repo import orm, repo


async def authenticate_admin(session: AsyncSession, username: str, password: str) -> orm.AdminUserORM | None:
    """Return the admin when the credentials match, otherwise None."""
    admin = await repo.get_admin_by_username(session, username)
    if admin is None or not verify_password(password, admin.password_hash):
        return None
    return admin


async def ensure_default_admin(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> orm.AdminUserORM | None:
    """Seed settings.admin_username once, when no admin account exists yet."""
    async with session_factory() as session:
        existing = await repo.count_admins(session)
        if existing:
            return None
        admin = orm.AdminUserORM(
            id=new_id(),
            username=settings.admin_username,
            password_hash=hash_password(settings.admin_password),
            created_at=now_ms(),
            updated_at=now_ms(),
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        return admin


async def change_admin_password(
    session: AsyncSession, admin: orm.AdminUserORM, current_password: str, new_password: str
) -> bool:
    """Change the password; return False when current_password is wrong."""
    if not verify_password(current_password, admin.password_hash):
        return False
    admin.password_hash = hash_password(new_password)
    admin.updated_at = now_ms()
    await session.commit()
    return True
