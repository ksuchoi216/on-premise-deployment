from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.infrastructure.database import get_db
from app.infrastructure.repositories import SQLAlchemyItemRepository
from app.application.use_cases import ItemService

router = APIRouter()

class ItemResponse(BaseModel):
    id: int
    name: str

def get_item_service(db: Session = Depends(get_db)) -> ItemService:
    repository = SQLAlchemyItemRepository(db)
    return ItemService(repository)

@router.get("/health")
def health():
    return {"status": "ok"}

@router.post("/items", response_model=ItemResponse)
def create_item(name: str, service: ItemService = Depends(get_item_service)):
    item = service.create_item(name=name)
    return ItemResponse(id=item.id, name=item.name)

@router.get("/items", response_model=List[ItemResponse])
def list_items(service: ItemService = Depends(get_item_service)):
    items = service.list_items()
    return [ItemResponse(id=item.id, name=item.name) for item in items]
