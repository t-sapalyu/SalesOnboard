"""The JSON API that the React pages call. Every URL starts with /api.

Routes are plain `def` functions (not `async def`): our database code is
normal blocking code, and FastAPI runs plain functions in a thread pool,
so one slow request does not block the others.
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel

from salesonboard.config import get_settings
from salesonboard.db import connect
from salesonboard.db.control import get_tenant
from salesonboard.pipeline.source_file import read_source_file
from salesonboard.webapi.auth import check_login, current_tenant
from salesonboard.workflow.batch_steps import create_batch, new_batch_id

PREVIEW_ROWS = 5

router = APIRouter(prefix="/api")


class LoginRequest(BaseModel):
    username: str
    password: str


class Tenant(BaseModel):
    tenant_id: str
    name: str


class UploadResult(BaseModel):
    batch_id: str
    file_name: str
    row_count: int
    columns: list[str]
    preview: list[dict[str, str]]  # the first rows, as {column: value}


# --- Login ------------------------------------------------------------------


@router.post("/login")
def login(body: LoginRequest, request: Request) -> Tenant:
    tenant = check_login(body.username, body.password)
    if tenant is None:
        # Same message for a wrong username and a wrong password, so nobody
        # can find out which usernames exist.
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    request.session.clear()
    request.session["tenant_id"] = tenant["tenant_id"]
    return Tenant(**tenant)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(tenant_id: str = Depends(current_tenant)) -> Tenant:
    """Who is logged in. The page calls this first, to know whether to show login."""
    with connect() as conn:
        tenant = get_tenant(conn, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Please log in.")
    return Tenant(tenant_id=tenant["tenant_id"], name=tenant["name"])


# --- Upload -----------------------------------------------------------------


@router.post("/upload")
def upload(file: UploadFile, tenant_id: str = Depends(current_tenant)) -> UploadResult:
    """Save the file as <tenant_id>/<batch_id>.csv, create the batch, and
    return the first rows so the customer sees it was read correctly."""
    file_name = file.filename or "upload.csv"
    if not file_name.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    batch_id = new_batch_id()
    path = get_settings().upload_dir / tenant_id / f"{batch_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(file.file.read())

    try:
        source = read_source_file(path)
        create_batch(tenant_id, path, batch_id=batch_id, file_name=file_name)
    except ValueError as error:  # pandas could not read it (empty, broken, not text)
        path.unlink()
        raise HTTPException(
            status_code=400, detail=f"Could not read this file as CSV: {error}"
        ) from error
    except psycopg.errors.UniqueViolation as error:  # the one-active-batch index
        path.unlink()
        raise HTTPException(
            status_code=409,
            detail="You already have an upload in progress. Finish or stop it first.",
        ) from error

    return UploadResult(
        batch_id=batch_id,
        file_name=file_name,
        row_count=len(source.data),
        columns=list(source.data.columns),
        preview=source.data.head(PREVIEW_ROWS).to_dict(orient="records"),
    )
