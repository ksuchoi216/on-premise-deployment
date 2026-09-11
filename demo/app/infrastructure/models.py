from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database import Base

class ItemModel(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
