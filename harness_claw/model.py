from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PolicyDecision(BaseModel):
    allowed: bool
    reason: Optional[str] = None
