import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")
RESERVED_FIELD_NAMES = {"website", "widget_id"}

WidgetType = Literal["signup_form", "cta_popover"]


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=FIELD_NAME_PATTERN)
    label: str = Field(min_length=1, max_length=80)
    type: Literal["text", "email", "textarea"] = "text"
    required: bool = False
    max_length: int = Field(default=255, ge=1, le=2000)

    @field_validator("name")
    @classmethod
    def not_reserved(cls, value: str) -> str:
        if value in RESERVED_FIELD_NAMES:
            raise ValueError(f"'{value}' is a reserved field name")
        return value


class DisplayOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: Literal["inline", "bottom-right", "bottom-left", "center"] = "inline"
    theme: Literal["light", "dark"] = "light"


def _unique_names(fields: list[FieldDef] | None) -> list[FieldDef] | None:
    if fields is not None:
        names = [f.name for f in fields]
        if len(names) != len(set(names)):
            raise ValueError("field names must be unique")
    return fields


def _normalise_origins(origins: list[str] | None) -> list[str] | None:
    if origins is None:
        return None
    cleaned = []
    for origin in origins:
        if not ORIGIN_RE.match(origin):
            raise ValueError(f"invalid origin '{origin}' (expected scheme://host[:port])")
        cleaned.append(origin.lower())
    return cleaned


class WidgetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WidgetType
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    button_text: str = Field(default="Submit", min_length=1, max_length=40)
    fields: list[FieldDef] = Field(min_length=1, max_length=20)
    display_options: DisplayOptions = Field(default_factory=DisplayOptions)
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)
    is_active: bool = True

    @field_validator("fields")
    @classmethod
    def check_fields(cls, value: list[FieldDef]) -> list[FieldDef]:
        return _unique_names(value)

    @field_validator("allowed_origins")
    @classmethod
    def check_origins(cls, value: list[str]) -> list[str]:
        return _normalise_origins(value)


class WidgetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: WidgetType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    button_text: str | None = Field(default=None, min_length=1, max_length=40)
    fields: list[FieldDef] | None = Field(default=None, min_length=1, max_length=20)
    display_options: DisplayOptions | None = None
    allowed_origins: list[str] | None = Field(default=None, max_length=20)
    is_active: bool | None = None

    @field_validator("fields")
    @classmethod
    def check_fields(cls, value: list[FieldDef] | None) -> list[FieldDef] | None:
        return _unique_names(value)

    @field_validator("allowed_origins")
    @classmethod
    def check_origins(cls, value: list[str] | None) -> list[str] | None:
        return _normalise_origins(value)


class WidgetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    public_id: str
    type: str
    title: str
    description: str
    button_text: str
    fields: list[FieldDef]
    display_options: DisplayOptions
    allowed_origins: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EmbedOut(BaseModel):
    widget_id: uuid.UUID
    public_id: str
    snippet: str