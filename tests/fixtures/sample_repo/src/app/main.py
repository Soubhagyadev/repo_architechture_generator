"""FastAPI application entry point for the sample service."""

from fastapi import FastAPI

from src.app.api import router as api_router
from src.app.config import settings


def create_app() -> FastAPI:
    """Construct the FastAPI application with all routers registered."""
    application = FastAPI(title="Sample Service")
    application.include_router(api_router)
    application.state.settings = settings
    return application


app = create_app()


def main() -> None:
    """Run a local development server."""
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
