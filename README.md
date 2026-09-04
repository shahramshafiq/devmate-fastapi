# DevMate

DevMate is a personal assistant built with FastAPI and the OpenAI API. It manages a to-do list, answers general questions, decides on its own when to reach for a tool (checking the date, searching the web, or touching your to-do list), remembers things about you across sessions, and pushes back when someone tries to talk it out of its own rules.

This project builds directly on an earlier one, the Event Processing System (FastAPI), and reuses its discipline on purpose: consistent error responses, structured logging, lifespan-managed shared resources, and SSE for streaming.

## Features

- **Chat**: `POST /chat`, a normal request/response conversation with the assistant
- **Streaming**: `GET /chat/stream`, the same conversation delivered token by token over Server-Sent Events
- **Tool calling**: the model decides for itself when it needs help, and can reach for:
  - a date/time tool, for "today," "tomorrow," or "3 days from now"
  - a Tavily web search tool, for anything current or outside its own knowledge
  - a to-do list tool (add, list, complete, delete), stored per user
- **Memory**
  - short-term: the last 10 turns of a conversation, kept in Valkey
  - long-term: durable facts about a user (preferences, habits), extracted automatically after each turn and kept in a JSON file per user
- **Guardrails**: NeMo Guardrails checks messages on the way in and replies on the way out, and has held up against 5 jailbreak attempts and a tool-result prompt injection test
- **Cost tracking**: every reply reports its own token usage and dollar cost, plus a running total for the session

## Tech stack

- [FastAPI](https://fastapi.tiangolo.com/): web framework
- [OpenAI Python SDK](https://github.com/openai/openai-python): the model, via Chat Completions
- [Valkey](https://valkey.io/): short-term conversation memory
- [httpx](https://www.python-httpx.org/): the shared async client used for Tavily calls
- [NeMo Guardrails](https://github.com/NVIDIA-NeMo/Guardrails): input/output guardrails
- [Pydantic](https://docs.pydantic.dev/) / `pydantic-settings`: request validation and configuration

## Project structure

```
app/
├── config/          # environment-driven settings (pydantic-settings)
├── guardrails/       # NeMo Guardrails config.yml + prompts.yml
├── lifespan/          # startup/shutdown: openai client, http client, valkey, guardrails
├── main.py            # app assembly + the global validation-error handler
├── prompts/           # the system prompt and the <user_input> wrapper
├── routes/            # HTTP layer: /chat and /chat/stream
├── schemas/           # request/response models (Pydantic)
├── services/           # business logic: llm, memory, session totals, guardrails
├── tools/              # the three LLM-callable tools
└── utils/               # error shape, logging setup, cost math

data/
├── memory/            # long-term facts, one JSON file per user
└── todos/               # to-do lists, one JSON file per user

tests/
└── manual_test_requests.py   # runnable smoke test covering all 5 phases
```

## Getting started

### Prerequisites

- **Python 3.12.** Not 3.14: NeMo Guardrails' own dependencies don't support 3.14 yet (its `requires_python` explicitly excludes it), and importing it will crash on a newer interpreter.
- A running Valkey (or Redis) instance
- An [OpenAI API key](https://platform.openai.com/)
- A [Tavily API key](https://tavily.com/) (free tier is fine)

### Setup

1. Create and activate a virtual environment on Python 3.12:

   ```bash
   py -3.12 -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Copy the example environment file and fill in your own keys:

   ```bash
   cp .env.example .env
   ```

4. Start Valkey locally:

   ```bash
   docker run -p 6379:6379 valkey/valkey
   ```

5. Run the server:

   ```bash
   python -m uvicorn app.main:app --reload
   ```

   The API is now at `http://127.0.0.1:8000`, with interactive docs at `http://127.0.0.1:8000/docs`.

## Configuration

All configuration comes from environment variables (see `.env.example`):

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `OPENAI_MODEL` | Model used for chat and tool calls (default `gpt-4.1-mini`) |
| `INPUT_PRICE` / `OUTPUT_PRICE` | Price per 1M tokens, used to compute cost per turn |
| `TAVILY_API_KEY` | Your Tavily API key, for the web search tool |
| `TAVILY_TIMEOUT` | Timeout in seconds for Tavily requests (default `10.0`) |
| `VALKEY_HOST` / `VALKEY_PORT` | Where to reach Valkey (default `localhost:6379`) |

## API overview

| Method | Path | Description |
|---|---|---|
| `POST` | `/chat` | Send a message, get a complete reply back |
| `GET` | `/chat/stream` | Same conversation, streamed as SSE (query params, not a JSON body) |

Both take `user_id`, `session_id`, `message`, and optionally `temperature`, `top_p`, `max_tokens`.

## How it fits together

A request to `/chat` reads recent conversation history from Valkey and any known long-term facts about the user from `data/memory/`, folds both into the prompt, and sends it to the model along with the three tool definitions. If the model asks for a tool, DevMate runs it locally and sends the result back for a final answer, if not, the first response is the answer. Either way, the turn's token usage and cost get calculated, the session's running total gets updated, the turn gets saved back into Valkey for next time, and a background task asks the model whether anything durable was just revealed about the user, saving it to their facts file if so. Before any of that happens, the message passes an input guardrail check, and the generated reply passes an output guardrail check before it's returned.

`/chat/stream` currently covers chat, streaming, and cost tracking, but not tools, memory, or guardrails yet, that's deliberately staged for later, not an oversight.

## Testing

- **Postman**: every endpoint was tested interactively during development. An exported collection file isn't in the repo yet.
- **`tests/manual_test_requests.py`**: a runnable script covering all 5 build phases (basic chat, streaming, tools, memory, guardrails). It makes real calls to OpenAI (and Tavily, for phase 3), so it costs real money to run, it's not a mocked test suite. Run everything with `python tests/manual_test_requests.py`, or just one phase with `python tests/manual_test_requests.py --phase 3`.
- The tool-result injection test needs one temporary line added to `app/tools/tavily_tool.py`, explained in the script's own output.

## Known limitations

- `/chat/stream` doesn't yet support tools, memory, or guardrails.
- Session totals (`SessionService`) live in memory only and reset when the server restarts.
- Long-term memory and to-do storage are per-user JSON files, not a database, by design for this project's scope.
- The tool-ordering test (confirming tool selection stays reliable when the tools list is reordered) hasn't been run yet.
