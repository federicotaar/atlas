"""Errori API di ATLAS: forma unica e codici stabili.

Ogni risposta di errore ha la stessa struttura
`{"error": {"code": ..., "message": ...}}`, cosi il frontend puo' fare
switch sul codice invece di leggere messaggi. `AtlasError` e' l'eccezione
che i router e i servizi sollevano; qui viene convertita in JSON con lo
status scelto da chi la lancia. Anche gli errori di validazione Pydantic
(422) passano per la stessa forma, con i primi dettagli utili.
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AtlasError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AtlasError)
    async def atlas_error_handler(_: Request, exc: AtlasError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content=_body("validation_error", str(exc.errors()[:3])))

    @app.exception_handler(Exception)
    async def generic_error_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content=_body("internal_error", "Errore interno."))
