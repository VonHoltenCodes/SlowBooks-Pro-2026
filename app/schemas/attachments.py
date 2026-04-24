from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AttachmentResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    filename: str
    file_path: str
    mime_type: Optional[str]
    file_size: Optional[int]
    uploaded_at: datetime

    model_config = {"from_attributes": True}
