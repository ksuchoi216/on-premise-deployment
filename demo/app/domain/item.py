from dataclasses import dataclass
from typing import Optional

@dataclass
class ItemEntity:
    name: str
    id: Optional[int] = None
