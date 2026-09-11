from typing import List
from sqlalchemy.orm import Session
from app.application.interfaces import ItemRepository
from app.domain.item import ItemEntity
from app.infrastructure.models import ItemModel

class SQLAlchemyItemRepository(ItemRepository):
    def __init__(self, session: Session):
        self.session = session

    def add(self, item: ItemEntity) -> ItemEntity:
        db_item = ItemModel(name=item.name)
        self.session.add(db_item)
        self.session.commit()
        self.session.refresh(db_item)
        item.id = db_item.id
        return item

    def get_all(self) -> List[ItemEntity]:
        db_items = self.session.query(ItemModel).all()
        return [ItemEntity(id=db.id, name=db.name) for db in db_items]
