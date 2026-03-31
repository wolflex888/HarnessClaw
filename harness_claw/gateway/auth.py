from __future__ import annotations

import secrets


class AuthError(Exception):
    pass


class TokenStore:
    """In-memory token store. Tokens are revoked when the session ends."""

    def __init__(self) -> None:
        # token → (subject, scopes)
        self._tokens: dict[str, tuple[str, list[str]]] = {}

    def issue(self, subject: str, scopes: list[str]) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (subject, list(scopes))
        return token

    def validate(self, token: str) -> tuple[str, list[str]]:
        """Return (subject, scopes) or raise AuthError."""
        entry = self._tokens.get(token)
        if entry is None:
            raise AuthError("invalid or expired token")
        return entry

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)

    def revoke_by_subject(self, subject: str) -> None:
        """Revoke all tokens for a given subject (session_id)."""
        to_remove = [t for t, (s, _) in self._tokens.items() if s == subject]
        for t in to_remove:
            del self._tokens[t]
