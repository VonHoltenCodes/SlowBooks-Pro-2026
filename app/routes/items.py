from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.items import Item
from app.schemas.items import ItemCreate, ItemUpdate, ItemResponse
from app.routes._helpers import get_or_404

router = APIRouter(prefix="/api/items", tags=["items"])


@router.get("", response_model=list[ItemResponse])
def list_items(active_only: bool = False, item_type: str = None, search: str = None, db: Session = Depends(get_db)):
    q = db.query(Item)
    if active_only:
        q = q.filter(Item.is_active == True)
    if item_type:
        q = q.filter(Item.item_type == item_type)
    if search:
        q = q.filter(Item.name.ilike(f"%{search}%"))
    return q.order_by(Item.name).all()


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(item_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, Item, item_id)


@router.post("", response_model=ItemResponse, status_code=201)
def create_item(data: ItemCreate, db: Session = Depends(get_db)):
    item = Item(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/{item_id}", response_model=ItemResponse)
def update_item(item_id: int, data: ItemUpdate, db: Session = Depends(get_db)):
    item = get_or_404(db, Item, item_id)
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(item, key, val)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = get_or_404(db, Item, item_id)
    item.is_active = False
    db.commit()
    return {"message": "Item deactivated"}
