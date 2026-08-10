from sqlalchemy import Boolean, String, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import BaseModel


class User(BaseModel):
    """平台用户"""
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    role: Mapped[str] = mapped_column(String(20), default="viewer", comment="admin | analyst | auditor | viewer")

    __table_args__ = (
        Index("idx_users_email_active", "email", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"
