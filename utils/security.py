"""
Security utilities for DocFlow.

Provides optional HTTP Basic auth for sensitive endpoints.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from config.settings import settings
import secrets


security = HTTPBasic()


def require_basic_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not settings.auth_enabled:
        return True
    correct_username = secrets.compare_digest(credentials.username, settings.default_username)
    correct_password = secrets.compare_digest(credentials.password, settings.default_password)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True

