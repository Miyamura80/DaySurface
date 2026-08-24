"""Contracts for the public waitlist signup endpoint.

Kept dependency-light on purpose: ``email`` is a plain ``str`` validated by the
service (a simple format check) rather than ``EmailStr``, so the template does
not pull in ``email-validator`` just for one field. The ``company`` field is a
honeypot - real users never see or fill it; a non-empty value is treated as a
bot and silently dropped by the route.
"""

from pydantic import BaseModel, Field


class WaitlistJoinInput(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    company: str = Field(default="", max_length=320)
    source: str | None = Field(default=None, max_length=64)


class WaitlistJoinResult(BaseModel):
    # Always true on a 2xx: the endpoint reports success whether the email was
    # newly stored or already present (idempotent), and even when a bot trips
    # the honeypot, so nothing about the outcome leaks to an attacker.
    success: bool = True
