"""Shared request validators for TakeoffAI API routes."""

import re

from fastapi import HTTPException


def validate_client_id(client_id: str) -> None:
    """Reject client_id values that could enable path traversal."""
    if not re.match(r'^[a-zA-Z0-9_\-]+$', client_id):
        raise HTTPException(status_code=400, detail="Invalid client_id format")
