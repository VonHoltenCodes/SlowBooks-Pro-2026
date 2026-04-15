from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.items import Item
from app.schemas.items import ItemCreate, ItemResponse, ItemUpdate
from app.services.auth import require_permissions

router = APIRouter(prefix="/api/items", tags=["items"])


@router.get("", response_model=list[ItemResponse])
def list_items(
    active_only: bool = False,
    item_type: str = None,
    search: str = None,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("items.view")),
):
    q = db.query(Item)
    if active_only:
        q = q.filter(Item.is_active == True)
    if item_type:
        q = q.filter(Item.item_type == item_type)
    if search:
        q = q.filter(Item.name.ilike(f"%{search}%"))
    return q.order_by(Item.name).all()


@router.get("/{item_id}", response_model=ItemResponse)
def get_item(
    item_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("items.view")),
):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@router.post("", response_model=ItemResponse, status_code=201)
def create_item(
    data: ItemCreate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("items.manage")),
):
    item = Item(**data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/{item_id}", response_model=ItemResponse)
def update_item(
    item_id: int,
    data: ItemUpdate,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("items.manage")),
):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    for key, val in data.model_dump(exclude_unset=True).items():
        setattr(item, key, val)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}")
def delete_item(
    item_id: int,
    db: Session = Depends(get_db),
    auth=Depends(require_permissions("items.manage")),
):
    item = db.query(Item).filter(Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.is_active = False
    db.commit()
    return {"message": "Item deactivated"}
