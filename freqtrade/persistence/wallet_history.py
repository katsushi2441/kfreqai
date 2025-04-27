from datetime import datetime
from typing import ClassVar

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from freqtrade.persistence.base import ModelBase, SessionType


class WalletBalance(ModelBase):
    """
    Daily wallet state tracking with minimal fields
    """

    __tablename__ = "wallet_balance"
    session: ClassVar[SessionType]

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(25), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        # Ensure one record per currency per day
        UniqueConstraint("timestamp", "currency", name="unique_wallet_daily"),
    )
