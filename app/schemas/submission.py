import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictStr

WIDGET_PUBLIC_ID_PATTERN = r"^wgt_[a-z0-9]{10}$"


class SubmissionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    widget_id: StrictStr = Field(pattern=WIDGET_PUBLIC_ID_PATTERN)
    fields: dict[StrictStr, StrictStr] = Field(max_length=20)
    website: StrictStr = Field(default="", max_length=500)  # honeypot


class SubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    widget_id: uuid.UUID
    data: dict
    origin: str | None
    country: str | None
    city: str | None
    geo_provider: str | None
    created_at: datetime


class SubmissionPage(BaseModel):
    items: list[SubmissionOut]
    next_before: datetime | None = None