from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    detail: dict | None = None


class FastTrackError(Exception):
    """Base class for all typed, expected application errors."""

    error_code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class ConfigurationError(FastTrackError):
    error_code = "configuration_error"
    status_code = 500


class ValidationFailedError(FastTrackError):
    error_code = "validation_failed"
    status_code = 422


class NotFoundError(FastTrackError):
    error_code = "not_found"
    status_code = 404


class ConflictError(FastTrackError):
    error_code = "conflict"
    status_code = 409


class DatabaseUnavailableError(FastTrackError):
    error_code = "database_unavailable"
    status_code = 503


class VectorSearchError(FastTrackError):
    error_code = "vector_search_failed"
    status_code = 503


class ToolExecutionError(FastTrackError):
    error_code = "tool_execution_failed"
    status_code = 502


class ToolOutputInvalidError(FastTrackError):
    error_code = "tool_output_invalid"
    status_code = 502


class LLMUnavailableError(FastTrackError):
    error_code = "llm_unavailable"
    status_code = 503


class LLMOutputInvalidError(FastTrackError):
    error_code = "llm_output_invalid"
    status_code = 502


class DiagnosisInvalidError(FastTrackError):
    error_code = "diagnosis_invalid"
    status_code = 502


class DependencyUnavailableError(FastTrackError):
    error_code = "dependency_unavailable"
    status_code = 503


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(FastTrackError)
    async def _handle_fasttrack_error(request: Request, exc: FastTrackError) -> JSONResponse:
        body = ErrorResponse(error_code=exc.error_code, message=exc.message, detail=exc.detail)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = ErrorResponse(
            error_code="validation_failed",
            message="Request body failed validation",
            detail={"errors": jsonable_encoder(exc.errors())},
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(SQLAlchemyError)
    async def _handle_sqlalchemy_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        # Anything that reaches here escaped the tool executor's own DB-error
        # handling (e.g. a failure during ingestion or approval, outside a
        # tool call) -- still surfaced as a structured error, never a bare 500.
        body = ErrorResponse(
            error_code="database_unavailable",
            message="A database operation failed",
            detail={"exception": exc.__class__.__name__},
        )
        return JSONResponse(status_code=503, content=body.model_dump())
