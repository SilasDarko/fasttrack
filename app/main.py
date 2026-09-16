from fastapi import FastAPI

import app.tools  # noqa: F401  (registers the 5 diagnostic tools)
from app.errors import register_exception_handlers
from app.metrics import MetricsMiddleware, metrics_response
from app.routers import alerts, health, ingestion, investigations


def create_app() -> FastAPI:
    app = FastAPI(title="FastTrack", description="AI incident analysis service")

    app.add_middleware(MetricsMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(ingestion.router)
    app.include_router(alerts.router)
    app.include_router(investigations.router)

    @app.get("/metrics")
    async def metrics():
        return metrics_response()

    return app


app = create_app()
