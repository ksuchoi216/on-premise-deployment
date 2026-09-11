from abc import ABC, abstractmethod
from typing import List
from app.domain.item import ItemEntity

class ItemRepository(ABC):
    @abstractmethod
    def add(self, item: ItemEntity) -> ItemEntity:
        pass

    @abstractmethod
    def get_all(self) -> List[ItemEntity]:
        pass
