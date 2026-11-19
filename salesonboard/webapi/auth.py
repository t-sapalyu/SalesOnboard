"""Login with a username and password, then a signed session cookie.

After login, the tenant id is stored in the session. Starlette's
SessionMiddleware signs the cookie with SESSION_SECRET, so a customer cannot
change it to another tenant's id. Every route that needs a logged-in customer
uses current_tenant() to get their id.
"""

from fastapi import HTTPException, Request

from salesonboard.db import connect
from salesonboard.db.control import get_login
from salesonboard.passwords import verify_password


def check_login(username: str, password: str) -> dict | None:
    """The tenant ({"tenant_id", "name"}) if the password is right, else None."""
    with connect() as conn:
        login = get_login(conn, username)
    if login is None:
        return None
    if not verify_password(password, login["password_hash"]):
        return None
    return {"tenant_id": login["tenant_id"], "name": login["name"]}


def current_tenant(request: Request) -> str:
    """The logged-in tenant's id. Answers 401 when nobody is logged in.

    Used as a FastAPI dependency: `tenant_id: str = Depends(current_tenant)`.
    """
    tenant_id = request.session.get("tenant_id")
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="Please log in.")
    return tenant_id
