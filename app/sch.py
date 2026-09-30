from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=160)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Email khong hop le")
        return value


class LoginIn(BaseModel):
    email: str
    password: str


class ContactIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=9, max_length=20)
    enabled: bool = True


class SmsTestIn(BaseModel):
    phone: str = Field(min_length=9, max_length=20)
