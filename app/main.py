from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from app.lifespan.lifespan import lifespan
from app.routes.chat import router as chat_router
from app.utils.errors import error_response


app = FastAPI(lifespan=lifespan)

app.include_router(chat_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    first_error = exc.errors()[0]
    return error_response(
        status_code=422,
        error_code="VALIDATION_ERROR",
        message=first_error["msg"]
    )
