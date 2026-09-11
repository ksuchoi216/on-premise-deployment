from typing import List
from app.domain.item import ItemEntity
from app.application.interfaces import ItemRepository

class ItemService:
    def __init__(self, repository: ItemRepository):
        self.repository = repository

    def create_item(self, name: str) -> ItemEntity:
        item = ItemEntity(name=name)
        return self.repository.add(item)

    def list_items(self) -> List[ItemEntity]:
        return self.repository.get_all()
