"""API key scope definitions and validation."""

from fastapi import Depends, HTTPException
from loguru import logger as log

from api_server.auth.unified_auth import AuthenticatedUser, get_authenticated_user

# Scope constants
SERVICES_READ = "services:read"
SERVICES_EXECUTE = "services:execute"
BILLING_READ = "billing:read"
BILLING_WRITE = "billing:write"
ADMIN_READ = "admin:read"
ADMIN_WRITE = "admin:write"
PAYMENTS_READ = "payments:read"
PAYMENTS_WRITE = "payments:write"

ALL_SCOPES = frozenset(
    {
        SERVICES_READ,
        SERVICES_EXECUTE,
        BILLING_READ,
        BILLING_WRITE,
        ADMIN_READ,
        ADMIN_WRITE,
        PAYMENTS_READ,
        PAYMENTS_WRITE,
    }
)

# Scope templates
SCOPE_TEMPLATES: dict[str, list[str]] = {
    "read_only": [SERVICES_READ, BILLING_READ, PAYMENTS_READ],
    "standard": [
        SERVICES_READ,
        SERVICES_EXECUTE,
        BILLING_READ,
        BILLING_WRITE,
        PAYMENTS_READ,
        PAYMENTS_WRITE,
    ],
    "admin": ["*"],
}


def validate_scopes(scopes: list[str], *, allow_templates: bool = True) -> list[str]:
    """Expand templates and validate scope strings.

    Returns the resolved list of scopes.
    Raises ``ValueError`` for unknown scopes.
    Set *allow_templates* to ``False`` to reject template names
    (callers should use the ``scope_template`` field instead).
    """
    resolved: list[str] = []
    for s in scopes:
        if s in SCOPE_TEMPLATES:
            if not allow_templates:
                raise ValueError(
                    f"{s!r} is a scope template; use the 'scope_template' field instead"
                )
            resolved.extend(SCOPE_TEMPLATES[s])
        elif (
            s == "*"
            or s in ALL_SCOPES
            or (
                s.endswith(":*")
                and s.count(":") == 1
                and s.split(":")[0]
                in {scope.split(":")[0] for scope in ALL_SCOPES if ":" in scope}
            )
        ):
            resolved.append(s)
        else:
            raise ValueError(f"Unknown scope: {s!r}")
    return sorted(set(resolved))


def check_scopes(required: list[str], granted: list[str] | None) -> bool:
    """Check whether *granted* scopes satisfy all *required* scopes.

    ``None`` means legacy key with no scope restrictions (allow all).
    Supports wildcards: ``*`` (everything) and ``resource:*`` (all in resource).

    Note: wildcard matching is one-directional. A *granted* wildcard
    (e.g. ``services:*``) satisfies any concrete required scope in
    that resource. However, a *required* wildcard (e.g. ``services:*``)
    is only satisfied by an exact ``services:*`` or ``*`` in the
    granted list -- not by having all concrete ``services:`` scopes
    individually granted. This is intentional: ``require_scopes``
    always uses concrete scope names, never wildcards.
    """
    if granted is None:
        return True  # Legacy key - no restrictions
    if "*" in granted:
        return True
    for req in required:
        parts = req.split(":")
        resource_wildcard = f"{parts[0]}:*" if len(parts) > 1 else None
        if req not in granted and (
            resource_wildcard is None or resource_wildcard not in granted
        ):
            return False
    return True


def require_scopes(*scopes: str):
    """FastAPI dependency factory that enforces scope requirements."""

    def _check(
        user: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> AuthenticatedUser:
        if not check_scopes(list(scopes), user.scopes):
            log.debug(
                "Scope check failed for user {}: required={}, granted={}",
                user.user_id,
                list(scopes),
                user.scopes,
            )
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions for this operation.",
            )
        return user

    return _check
