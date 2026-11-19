"""The FastAPI app. Start it from the project root:

  uv run uvicorn salesonboard.webapi.app:app --reload

The API runs on http://localhost:8000. The React dev server (frontend/)
forwards every /api request to it, so the browser sees one site and the
session cookie just works.
"""

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from salesonboard.config import get_settings
from salesonboard.webapi.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="SalesOnboard")
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_settings().session_secret.get_secret_value(),
        same_site="lax",  # the cookie is not sent from other sites' forms
    )
    app.include_router(router)
    return app


app = create_app()
