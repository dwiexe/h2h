from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = 'products'

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    operator: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    price: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='OPEN')
    provider_status: Mapped[str] = mapped_column(String(20), default='active')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class UserSession(Base):
    __tablename__ = 'user_sessions'

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    current_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    current_operator: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    current_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_destination: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    current_variant: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_action: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class Transaction(Base):
    __tablename__ = 'transactions'

    ref_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    invoice: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    product_code: Mapped[str] = mapped_column(String(64), index=True)
    destination: Mapped[str] = mapped_column(String(100))
    product_name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(100))
    operator: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    price: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    status_label: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    serial_number: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


engine: AsyncEngine = create_async_engine(settings.database_url, future=True, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_or_create_session(db: AsyncSession, chat_id: int) -> UserSession:
    session = await db.get(UserSession, chat_id)
    if session:
        return session
    session = UserSession(chat_id=chat_id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def pending_transactions(db: AsyncSession) -> list[Transaction]:
    result = await db.execute(select(Transaction).where(Transaction.status == 'pending'))
    return list(result.scalars().all())
