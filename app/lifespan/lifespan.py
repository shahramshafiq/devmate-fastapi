import logging
from contextlib import asynccontextmanager

import httpx
import valkey
from fastapi import FastAPI
from openai import AsyncOpenAI

from app.config.settings import settings
from app.services.session_service import SessionService
from app.utils.logging import setup_logging

import os

from nemoguardrails import LLMRails, RailsConfig

@asynccontextmanager
async def lifespan(app: FastAPI):

    setup_logging()
    logging.info("DevMate starting up")

    app.state.openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key
    )

    app.state.http_client = httpx.AsyncClient()

    # create the valkey connection for short-term chat memory
    app.state.valkey_client = valkey.Valkey(
        host=settings.valkey_host,
        port=settings.valkey_port,
        decode_responses=True
    )
    try:
        app.state.valkey_client.ping()
        logging.info("Valkey connection is available")
    except Exception as e:
        logging.warning(f"Valkey is unavailable: {e}")

    # nemo guardrails makes its own internal openai calls, which read this
    # environment variable directly, so it has to actually be set here
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key

    guardrails_config = RailsConfig.from_path("app/guardrails")
    app.state.guardrails = LLMRails(guardrails_config)
    logging.info("Guardrails loaded")

    app.state.session_service = SessionService()

    yield

    logging.info("DevMate shutting down")
    await app.state.openai_client.close()
    await app.state.http_client.aclose()
    app.state.valkey_client.close()