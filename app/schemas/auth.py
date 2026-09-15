from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def _check_bcrypt_limit(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes")
    return value


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    tenant_name: str = Field(min_length=1, max_length=120)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, value: str) -> str:
        return _check_bcrypt_limit(value)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"