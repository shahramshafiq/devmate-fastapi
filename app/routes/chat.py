import logging
from typing import Annotated

import openai
from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import StreamingResponse

from app.config.settings import settings
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.llm_service import LLMService
from app.utils.costs import (
    calculate_input_cost,
    calculate_output_cost,
    calculate_total_cost
)
from app.utils.errors import error_response
import json
from app.services.memory_service import (
    get_recent_turns, append_turn, get_long_term_facts, extract_and_save_facts
)

from app.services.guardrails_service import check_input, check_output

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request, background_tasks: BackgroundTasks):

    logging.info(f"Chat request received: user_id={request.user_id}, session_id={request.session_id}")

    try:
        client = http_request.app.state.openai_client
        http_client = http_request.app.state.http_client
        valkey_client = http_request.app.state.valkey_client
        session_service = http_request.app.state.session_service

        blocked = await check_input(http_request.app.state.guardrails, request.message)
        if blocked:
            logging.info(f"Blocked by input guardrail: user_id={request.user_id}")
            session = session_service.get_session(request.user_id, request.session_id)
            return ChatResponse(
                reply="I can't help with that request.",
                turn={"turn_input_tokens": 0, "turn_output_tokens": 0, "turn_cost": 0.0},
                session=session
            )
        
        llm_service = LLMService(client, http_client, request.user_id)

        history = get_recent_turns(valkey_client, request.user_id, request.session_id)
        facts = get_long_term_facts(request.user_id)

        result = await llm_service.generate_response(
            message=request.message,
            temperature=request.temperature,
            top_p=request.top_p,
            max_tokens=request.max_tokens,
            history=history,
            facts=facts
        )

        output_blocked = await check_output(http_request.app.state.guardrails, result["reply"])
        if output_blocked:
            logging.info(f"Blocked by output guardrail: user_id={request.user_id}")
            result["reply"] = "I can't share that response. Let me know if I can help with something else."

        append_turn(valkey_client, request.user_id, request.session_id, "user", request.message)
        append_turn(valkey_client, request.user_id, request.session_id, "assistant", result["reply"])

        background_tasks.add_task(
            extract_and_save_facts,
            client,
            settings.openai_model,
            request.user_id,
            request.message,
            result["reply"]
        )

        input_cost = calculate_input_cost(result["input_tokens"], settings.input_price)
        output_cost = calculate_output_cost(result["output_tokens"], settings.output_price)
        total_cost = calculate_total_cost(input_cost, output_cost)

        session = session_service.update_session(
            user_id=request.user_id,
            session_id=request.session_id,
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            cost=total_cost
        )

        logging.info(
            f"Chat response generated: user_id={request.user_id}, "
            f"session_id={request.session_id}, "
            f"input_tokens={result['input_tokens']}, "
            f"output_tokens={result['output_tokens']}"
        )

        return ChatResponse(
            reply=result["reply"],
            turn={
                "turn_input_tokens": result["input_tokens"],
                "turn_output_tokens": result["output_tokens"],
                "turn_cost": total_cost
            },
            session=session
        )

    except openai.APIError as e:
        logging.error(f"OpenAI API call failed: {e}")
        return error_response(502, "LLM_REQUEST_FAILED", "Failed to get a response from the language model.")

    except Exception as e:
        logging.exception("Unexpected error in /chat")
        return error_response(500, "INTERNAL_SERVER_ERROR", "Something went wrong while processing your request.")

@router.get("/chat/stream")
async def chat_stream(
    params: Annotated[ChatRequest, Query()],
    http_request: Request
):

    logging.info(f"Chat stream request received: user_id={params.user_id}, session_id={params.session_id}")

    client = http_request.app.state.openai_client
    http_client = http_request.app.state.http_client
    session_service = http_request.app.state.session_service
    llm_service = LLMService(client, http_client, params.user_id)

    async def generate():
        try:
            input_tokens = 0
            output_tokens = 0

            async for event in llm_service.generate_stream(
                message=params.message,
                temperature=params.temperature,
                top_p=params.top_p,
                max_tokens=params.max_tokens
            ):
                if event["type"] == "content":
                    payload = json.dumps({"type": "content", "content": event["content"]})
                    yield f"data: {payload}\n\n"

                elif event["type"] == "usage":
                    input_tokens = event["input_tokens"]
                    output_tokens = event["output_tokens"]

            input_cost = calculate_input_cost(input_tokens, settings.input_price)
            output_cost = calculate_output_cost(output_tokens, settings.output_price)
            total_cost = calculate_total_cost(input_cost, output_cost)

            session = session_service.update_session(
                user_id=params.user_id,
                session_id=params.session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=total_cost
            )

            logging.info(
                f"Chat stream completed: user_id={params.user_id}, "
                f"session_id={params.session_id}, "
                f"input_tokens={input_tokens}, output_tokens={output_tokens}"
            )

            done_payload = json.dumps({
                "type": "done",
                "turn": {
                    "turn_input_tokens": input_tokens,
                    "turn_output_tokens": output_tokens,
                    "turn_cost": total_cost
                },
                "session": session
            })
            yield f"data: {done_payload}\n\n"

        except openai.APIError as e:
            logging.error(f"OpenAI API call failed during streaming: {e}")
            error_payload = json.dumps({"type": "error", "message": "Failed to get a response from the language model."})
            yield f"data: {error_payload}\n\n"

        except Exception as e:
            logging.exception("Unexpected error in /chat/stream")
            error_payload = json.dumps({"type": "error", "message": "Something went wrong while streaming the response."})
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")