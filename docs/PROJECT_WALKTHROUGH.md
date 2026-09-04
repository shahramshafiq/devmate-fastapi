# DevMate: Complete Project Walkthrough

This document exists so you can explain every part of DevMate to your supervisor, from the ten-thousand-foot view down to individual lines of code, and so you have a written record of every real bug that was found and fixed while building it. Read it top to bottom once, then use it as a reference before any conversation about the project.

---

# PART 1: THE BIG PICTURE

## What DevMate actually is

**In plain words:** DevMate is a robot assistant that lives behind a web address. You send it a typed message, and it either answers you directly, or, if it needs outside help to answer properly, it quietly uses one of three tools (checking today's date, searching the web, or touching your to-do list), and then answers you. It also remembers things about you, both within one conversation and permanently across every conversation you ever have with it, and it has a second layer watching over it that refuses to let it be tricked into breaking its own rules.

**Technically:** DevMate is a FastAPI backend exposing two HTTP endpoints (`POST /chat` and `GET /chat/stream`) that wrap the OpenAI Chat Completions API. It adds function/tool calling, two tiers of memory (Valkey for short-term, per-user JSON files for long-term), and an input/output guardrail layer built on NVIDIA's NeMo Guardrails library.

## The five phases, in one line each

1. **Basic chat**: one message in, one complete reply out, with cost tracked per turn and per session.
2. **Streaming**: the same conversation, but the reply arrives piece by piece instead of all at once.
3. **Tools**: the model can ask DevMate to run real code on its behalf: get the date, search the web, or manage a to-do list.
4. **Memory**: DevMate remembers the last 10 turns of a conversation (short-term) and durable facts about you forever (long-term).
5. **Guardrails**: every message in and every reply out gets checked against a policy before it's allowed through.

## Why FastAPI, why this shape

FastAPI was chosen (matching Project 1, the Event Processing System) because it validates incoming data automatically via Pydantic, handles async I/O natively (important since every meaningful thing DevMate does, calling OpenAI, calling Tavily, talking to Valkey, is a network call, and blocking on any one of those while others could be proceeding would waste time), and generates interactive docs for free at `/docs`.

The project is split into layers on purpose, each with exactly one job:

```
routes/    → HTTP in, HTTP out. No business logic lives here, it orchestrates.
services/  → the actual thinking: talk to the LLM, manage memory, check guardrails.
tools/     → the three things the LLM is allowed to ask DevMate to do.
schemas/   → the shape of what's allowed in and what goes out.
utils/     → small, reusable, stateless helpers (cost math, error shape, logging).
```

If you're ever asked "why is the code split up like this instead of one big file," the answer is: so that changing how memory works, for example, never requires touching the HTTP layer, and so any one piece can be tested or reasoned about on its own.

---

# PART 2: THE COMPLETE REQUEST FLOW

## What happens once, at startup

Before any request can be served, `app/lifespan/lifespan.py` runs. This is the only place expensive, shared resources get created, and it happens exactly once, not per-request:

```
1. Logging is configured
2. One AsyncOpenAI client is created           → app.state.openai_client
3. One shared httpx.AsyncClient is created     → app.state.http_client   (used by the Tavily tool)
4. One Valkey connection is created and pinged → app.state.valkey_client
5. OPENAI_API_KEY is written into the real OS environment (NeMo Guardrails needs it there directly)
6. The guardrails config is loaded from app/guardrails/ and an LLMRails instance is built → app.state.guardrails
7. One SessionService (an in-memory dict) is created → app.state.session_service
```

**Why this matters:** every request that comes in afterward reuses these exact same objects. Creating a new OpenAI client or a new Valkey connection on every single request would be slow and wasteful. This is the same principle Project 1 used for its Valkey connection and HTTP session.

## The complete life of one `POST /chat` request

This is the single most important diagram in this document. Every phase you built is represented somewhere in this sequence:

```
Client sends JSON: {user_id, session_id, message, temperature, top_p, max_tokens}
        │
        ▼
FastAPI validates the body against ChatRequest (schemas/chat.py)
   - message/user_id/session_id non-empty, temperature 0-2, top_p 0-1, max_tokens 1-4096
   - if invalid → the global handler in main.py returns {"error": "VALIDATION_ERROR", ...}, 422
        │  (valid)
        ▼
routes/chat.py: chat() starts running
        │
        ▼
INPUT GUARDRAIL CHECK  (guardrails_service.check_input)
   - the raw message is sent to NeMo Guardrails' self-check-input flow
   - if blocked → return a canned refusal immediately, nothing below this line runs
        │  (allowed)
        ▼
MEMORY READ  (memory_service.get_recent_turns + get_long_term_facts)
   - last up-to-10 turns for this user_id+session_id, pulled from Valkey
   - durable facts about this user_id, pulled from data/memory/{user_id}.json
        │
        ▼
LLMService.generate_response() is called  (services/llm_service.py)
   - builds messages = [system+facts] + history + [current message, XML-wrapped]
   - calls OpenAI with all 6 tool schemas attached
        │
        ├── model answers directly ──────────────────────────────► skip to COST
        │
        └── model requests a tool (or several)
                 │
                 ▼
            DevMate runs the real Python function locally
            (get_current_datetime / tavily_search / add_task / list_tasks /
             complete_task / delete_task)
                 │
                 ▼
            the tool's result is appended to the conversation as a "tool" message
                 │
                 ▼
            OpenAI is called AGAIN with the tool result included, produces the final reply
                 │
                 ▼
            token counts from BOTH calls are added together
        │
        ▼
OUTPUT GUARDRAIL CHECK  (guardrails_service.check_output)
   - the model's reply is sent to NeMo Guardrails' self-check-output flow
   - if blocked → the reply is swapped for a canned refusal before continuing
        │
        ▼
MEMORY WRITE  (memory_service.append_turn, x2: user turn + assistant turn)
   - pushed onto the Valkey list, then the list is trimmed to the last 20 entries
        │
        ▼
BACKGROUND TASK SCHEDULED  (memory_service.extract_and_save_facts)
   - runs AFTER the response is already sent to the client
   - asks the model "did anything durable get revealed this turn?"
   - if yes, merges new facts into data/memory/{user_id}.json
        │
        ▼
COST  (utils/costs.py) → input_cost + output_cost = total_cost
        │
        ▼
SESSION UPDATE  (session_service.update_session) → running totals for this user+session
        │
        ▼
ChatResponse returned: {reply, turn: {...}, session: {...}}
```

**If anything throws an unexpected exception anywhere in that whole sequence**, one of two `except` blocks in `chat()` catches it: `openai.APIError` (OpenAI itself failed → clean 502) or the generic fallback (anything else → clean 500). Neither ever leaks a raw Python traceback to the caller.

## The complete life of one `GET /chat/stream` request

Deliberately simpler. No tools, no memory, no guardrails, yet, that gap is documented, not accidental.

```
Client sends a GET with query params (not a JSON body):
   ?message=...&user_id=...&session_id=...&temperature=...&top_p=...&max_tokens=...
        │
        ▼
FastAPI validates these query params against the SAME ChatRequest model
   (via Annotated[ChatRequest, Query()]: reused, not duplicated)
        │
        ▼
chat_stream() returns a StreamingResponse wrapping the generate() async generator
        │
        ▼
generate() iterates LLMService.generate_stream(), which calls OpenAI with stream=True
and stream_options={"include_usage": True}
        │
        ├── every real content chunk  → yield 'data: {"type":"content","content":"..."}\n\n'
        │
        └── the final usage-only chunk (empty choices list!) → capture token counts,
            do NOT try to read .content from it (that's the crash we fixed)
        │
        ▼
once the stream ends: cost calculated, session updated (same math as /chat),
final event sent: 'data: {"type":"done","turn":{...},"session":{...}}\n\n'
```

---

# PART 3: FILE BY FILE, LINE BY LINE

## `app/main.py`

**What it's for, in plain words:** this is the "front door" of the whole app. It doesn't do any real work itself, it just bolts the pieces together and sets one rule that applies to everything.

**Technically:** creates the `FastAPI` app with `lifespan=lifespan` (so startup/shutdown runs), registers the `chat` router, and registers one global exception handler.

```python
app = FastAPI(lifespan=lifespan)
app.include_router(chat_router)
```
This line alone is why `/chat` and `/chat/stream` exist at all, `chat_router` is defined in `routes/chat.py` and imported here.

```python
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    first_error = exc.errors()[0]
    return error_response(422, "VALIDATION_ERROR", first_error["msg"])
```
**Baby version:** whenever ANY request, to ANY endpoint, fails Pydantic's checks (empty message, temperature=99, whatever), instead of FastAPI's own default ugly error format, this function catches it and reshapes it into DevMate's own `{"error": ..., "message": ...}` style, matching Project 1.
**Example:** send `{"message": ""}` to `/chat` → this function runs, returns `422 {"error": "VALIDATION_ERROR", "message": "String should have at least 1 character"}`.

## `app/lifespan/lifespan.py`

Already walked through in Part 2. One thing worth calling out at the code level:

```python
os.environ["OPENAI_API_KEY"] = settings.openai_api_key
```
**Baby version:** NeMo Guardrails is its own separate piece of software living inside your app, and it doesn't know about your `settings` object at all, it only knows how to look up an API key the same way the raw OpenAI library normally does, by checking a real system environment variable. This line manually plants that variable so Guardrails can find it.

```python
guardrails_config = RailsConfig.from_path("app/guardrails")
app.state.guardrails = LLMRails(guardrails_config)
```
Loads `config.yml` + `prompts.yml` from that folder once, builds one reusable `LLMRails` object, stored on `app.state` exactly like the OpenAI client.

## `app/config/settings.py`

**Baby version:** a single typed list of every secret and setting the app needs, read from `.env`. If a required value (like `openai_api_key`) is missing, the app refuses to even start, on purpose, so you find out immediately instead of hours later.

**Technically:** a `pydantic-settings` `BaseSettings` subclass. Fields without a default (`openai_api_key`, `input_price`, `output_price`, `tavily_api_key`) are required. Fields with a default (`openai_model`, `tavily_timeout`, `valkey_host`, `valkey_port`) fall back to sane values if `.env` doesn't set them.

## `app/schemas/chat.py`

**`ChatRequest`** is the single rulebook used for BOTH endpoints (body for `/chat`, query params for `/chat/stream`), on purpose, so there's only one place validation rules live.

```python
message: str = Field(min_length=1)

@field_validator("message")
@classmethod
def message_must_not_be_blank(cls, v: str) -> str:
    if not v.strip():
        raise ValueError("message cannot be empty or whitespace only")
    return v
```
**Baby version:** `min_length=1` alone would still let someone send three spaces as a "message" (that's technically 3 characters, not 0). This extra check strips the spaces off and rejects it if nothing real is left.

`Turn`, `Session`, `ChatResponse` are just the shape of what goes back out, they mirror what `SessionService` tracks.

## `app/routes/chat.py`

This is the orchestrator, the file where every other piece gets called in order. Walking through `chat()` top to bottom:

```python
blocked = await check_input(http_request.app.state.guardrails, request.message)
if blocked:
    ...
    return ChatResponse(reply="I can't help with that request.", turn={...all zero...}, session=session)
```
**Baby version:** before anything else happens, including before spending a single cent on the real model, the message gets shown to the guardrail. If it says no, DevMate stops immediately and gives a canned, safe refusal. Notice `turn` reports all zeros, because no real API call happened, nothing was spent.

```python
history = get_recent_turns(valkey_client, request.user_id, request.session_id)
facts = get_long_term_facts(request.user_id)
```
Pulling both memory sources BEFORE calling the model, so they can be handed in as context.

```python
result = await llm_service.generate_response(..., history=history, facts=facts)
```
This one call is where Phases 1, 3, and 4 all actually converge, the LLM call itself, tool calling, and memory injection.

```python
output_blocked = await check_output(http_request.app.state.guardrails, result["reply"])
if output_blocked:
    result["reply"] = "I can't share that response. Let me know if I can help with something else."
```
**Baby version:** even after the model answers, that answer still has to pass a second checkpoint before the user ever sees it.

```python
append_turn(...); append_turn(...)
background_tasks.add_task(extract_and_save_facts, ...)
```
The conversation gets saved for next time, and fact-extraction is scheduled to run AFTER this function returns, the user isn't kept waiting on that second, invisible LLM call.

`chat_stream()` follows the same shape but skips the guardrail/memory/tool blocks entirely (documented gap), and has the extra complexity of separating `"content"` events from the one `"usage"` event, and only computing cost after the whole stream has finished.

## `app/services/llm_service.py`

The only file that ever talks to OpenAI directly.

```python
self.tool_functions = {
    "get_current_datetime": get_current_datetime,
    "tavily_search": lambda query: tavily_search(query, self.http_client),
    "add_task": lambda description: add_task(self.user_id, description),
    ...
}
```
**Baby version:** this is a phone book. When the model says "please run something called `tavily_search`", this dictionary is how DevMate turns that name (just text) into the real function to call. `tavily_search` and the to-do functions are wrapped in tiny `lambda`s because they need something extra the model doesn't provide, `tavily_search` needs the shared `http_client`, the to-do functions need to know WHICH user's list to touch (`self.user_id`), and the model never sends either of those, it only sends what's in the tool's schema (like `query` or `task_id`).

```python
if not assistant_message.tool_calls:
    return {"reply": ..., "input_tokens": ..., "output_tokens": ...}
```
The fast path: most questions don't need a tool at all.

```python
messages.append(assistant_message.model_dump(exclude_none=True))
for tool_call in assistant_message.tool_calls:
    ...
    result = await function_to_call(**arguments)
    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(result)})
second_response = await self.client.chat.completions.create(...)
```
**Baby version:** the model's "please run this tool" request has to be remembered and re-sent, otherwise when you hand back the tool's answer, the model has no idea what question that answer is even responding to. `tool_call_id` is like a claim ticket matching the result to the request.

```python
total_input_tokens = input_tokens + second_response.usage.prompt_tokens
total_output_tokens = output_tokens + second_response.usage.completion_tokens
```
**Why this line exists at all:** using a tool means TWO real, separately-billed API calls happened, not one. Forgetting to add both together would silently under-report real spending every single time a tool gets used.

`generate_stream()`'s tricky part:

```python
if chunk.choices:
    content = chunk.choices[0].delta.content
    if content:
        yield {"type": "content", "content": content}
if chunk.usage:
    yield {"type": "usage", ...}
```
**Baby version:** when you ask OpenAI to include usage info in a stream, it sends all the normal little pieces of text as usual, then ONE extra, final, special piece that has NO text at all, just the token counts. That special piece has an EMPTY list where the text normally lives. Trying to read `choices[0]` off an empty list crashes the program. This `if chunk.choices:` check is the entire fix, only try to read text if there's actually some there.

## `app/services/session_service.py`

**Baby version:** a notebook, kept only in the computer's short-term memory (RAM), that adds up how many tokens and how much money every conversation has used so far. `key = f"{user_id}:{session_id}"` means each unique person-plus-conversation gets its own running total.
**Important limitation, worth knowing for a supervisor Q&A:** this resets to zero every time the server restarts, because it's just a Python dictionary, not saved anywhere permanent.

## `app/services/memory_service.py`

**Short-term (Valkey):**
```python
valkey_client.rpush(key, json.dumps({"role": role, "content": content}))
valkey_client.ltrim(key, -(MAX_TURNS * 2), -1)
```
**Baby version:** `rpush` adds a new message to the end of a list stored in Valkey (like adding a new page to a notebook). `ltrim` then throws away every page except the most recent 20 (10 user messages + 10 assistant replies), keeping the notebook from growing forever. This is the "sliding window" approach, chosen instead of summarization specifically because it costs zero extra API calls.

**Long-term (JSON file):**
```python
async def extract_and_save_facts(client, model, user_id, user_message, assistant_reply):
    ...
    response = await client.chat.completions.create(..., response_format={"type": "json_object"})
    data = json.loads(response.choices[0].message.content)
    new_facts = data.get("facts", [])
    ...
    for fact in new_facts:
        if fact not in existing_facts:
            existing_facts.append(fact)
```
**Baby version:** after every reply, DevMate quietly asks the model a completely separate question, in the background, not shown to the user: "did we just learn something permanent about this person?" If yes, it's written into that person's own file, and duplicates are skipped so the same fact doesn't pile up twice.
`response_format={"type": "json_object"}` forces the model's answer to actually BE valid JSON, so `json.loads` doesn't crash on a stray sentence of English.

## `app/services/guardrails_service.py`

```python
result = await rails.generate_async(
    messages=[{"role": "user", "content": user_message}],
    options={"rails": {"input": True, "output": False, "dialog": False}}
)
```
**Baby version:** this asks NeMo Guardrails to ONLY run the input check on this one message, nothing else, not the full conversation engine.

```python
for activated in result.log.activated_rails:
    if "bot refuse to respond" in activated.decisions:
        blocked = True
```
**Baby version:** Guardrails keeps a detailed log of everything it decided while checking the message. This walks through that log looking for the specific decision `"bot refuse to respond"`, that phrase, coming from the library itself, is the real proof the message was actually blocked, not just an inference from reading the reply text.

`check_output` does the same thing, but has to fake a `"placeholder"` user message first, since the API expects a full back-and-forth conversation shape even when you only actually care about checking the assistant's side.

## `app/prompts/system_prompt.py`

`SYSTEM_PROMPT` defines who DevMate is, what it won't do, and explicitly instructs it to keep following its own rules even if the user or (per the injection test) a tool result tries to tell it otherwise. `build_user_prompt` wraps every real user message in `<user_input>...</user_input>` tags, so the model can always tell "this part is what the user actually typed" apart from "this part is my own instructions", which matters a lot once untrusted content (like a Tavily search result) is also in the conversation.

## `app/tools/datetime_tool.py`

```python
async def get_current_datetime(days_offset: int = 0) -> str:
    target = datetime.now() + timedelta(days=days_offset)
    return target.strftime("%A, %B %d, %Y %I:%M %p")
```
Pure, local, instant Python, no network call. `days_offset=0` is today, `3` is three days from now, `-3` is three days ago. It's `async` purely for consistency with the other tools (so the dispatch code in `llm_service.py` can always `await` every tool function the same way), not because it needs to be.

## `app/tools/tavily_tool.py`

```python
for attempt in range(1, 4):
    try:
        response = await http_client.post(TAVILY_SEARCH_URL, headers=headers, json=body, timeout=settings.tavily_timeout)
        response.raise_for_status()
        ...
        return "\n\n".join(formatted)
    except httpx.TimeoutException:
        ...
    except httpx.HTTPStatusError as e:
        ...
    except httpx.RequestError as e:
        ...
return "Web search is currently unavailable. ..."
```
**Baby version:** try up to 3 times. If it works, format the results into readable text and hand them back immediately (`return` inside the loop stops it). If all 3 attempts fail for any reason, timeout, bad response, network trouble, give the model a plain, honest sentence saying search isn't available right now, instead of crashing the whole conversation.
This mirrors, almost line for line, the exact retry pattern from Project 1's webhook code.

## `app/tools/todo_tool.py`

```python
_lock = asyncio.Lock()

async def add_task(user_id, description):
    async with _lock:
        todos = _read_todos(user_id)
        new_id = max([t["id"] for t in todos], default=0) + 1
        todos.append(...)
        _write_todos(user_id, todos)
    return f"Added task #{new_id}: {description}"
```
**Baby version:** `_lock` is like a single bathroom key for the whole building, only one request at a time is allowed to read-then-write a user's to-do file. Without it, two requests arriving at nearly the same moment could both read "2 tasks exist," both decide "the new task is #3," and one of them would silently overwrite the other's work. This was actually tested: 20 simultaneous `add_task` calls produced exactly IDs 1 through 20, no duplicates, no loss.

## `app/utils/costs.py`

```python
def calculate_input_cost(input_tokens, input_price):
    return (input_tokens / 1_000_000) * input_price
```
OpenAI prices per 1 million tokens. If `input_price = 0.40` (dollars per 1M tokens) and a turn used 2,203 input tokens: `(2203 / 1_000_000) * 0.40 = $0.00088`. Tiny numbers, which is exactly why real usage needs to be tracked precisely rather than estimated.

## `app/utils/errors.py`

```python
def error_response(status_code, error_code, message):
    return JSONResponse(status_code=status_code, content={"error": error_code, "message": message})
```
One function, used everywhere an error needs to go back to the client, so the shape is guaranteed identical everywhere, never a typo'd key name.

## `app/utils/logging.py`

```python
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
```
One line, called once at startup, that turns on timestamped, leveled logging for the entire app. Every `logging.info(...)` / `logging.error(...)` you see anywhere else in the codebase depends on this having run first.

## `app/guardrails/config.yml` and `prompts.yml`

`config.yml` tells NeMo Guardrails which model to use for its own checks, and which two flows to run: `self check input` and `self check output`, both are BUILT INTO the library, DevMate doesn't have to define their logic, only their policy text. `prompts.yml` is that policy text, literally the question NeMo Guardrails asks itself: "does this message/reply violate these rules, yes or no?" The `{{ user_input }}` and `{{ bot_response }}` are template placeholders the library fills in automatically.

## `tests/manual_test_requests.py`

A runnable script (not a mocked pytest suite) that fires real requests at all 5 phases, organized as one function per phase, runnable individually or all together. Documented in its own header that it costs real money since it hits the real OpenAI and Tavily APIs.

---

# PART 4: EVERY REAL ISSUE FACED, AND HOW IT GOT FIXED

This is the honest, chronological story. Good to have ready if asked "what problems did you run into."

1. **SSE streaming silently dropped list items.** `yield f"data: {chunk}\n\n"` sent raw model text straight into the SSE protocol. SSE is line-based, and a newline INSIDE the model's own reply (like between list items) got misread as "message finished," so anything after it just vanished, no error, no crash, just missing text. **Fix:** wrap every chunk in `json.dumps({...})` before sending, since JSON escapes real newlines into the harmless two characters `\n`, guaranteeing one event = one line.

2. **Zero input validation.** `temperature=99`, empty messages, and whitespace-only messages all sailed straight through to a real, billed OpenAI call. **Fix:** `Field(ge=..., le=...)` constraints plus a custom `field_validator` for the blank-message case.

3. **GET vs POST confusion on `/chat/stream`.** An earlier attempt at a POST version seemed to hang. Root cause diagnosed as a testing-tool problem (Swagger doesn't render SSE live, and browser `EventSource` can only ever send GET), not the endpoint. The spec explicitly asked for GET anyway, so that's what stayed.

4. **One giant `except Exception` hid every failure behind an identical, unhelpful 500, and leaked raw exception text.** **Fix:** catch `openai.APIError` specifically first (→ clean 502, since that means OpenAI itself failed) before the generic fallback (→ clean 500), and never put `str(e)` in the response body, only in the log.

5. **No logging at all**, and one stray `print()` in the streaming error path. **Fix:** real `logging` calls at every meaningful step, mirroring Project 1's discipline.

6. **Streaming tracked no cost, and would have crashed the moment cost tracking was added.** Adding `stream_options={"include_usage": True}` makes OpenAI send one final chunk with an EMPTY `choices` list. The original code did `chunk.choices[0]` unconditionally, that's an `IndexError` waiting to happen. **Fix:** `if chunk.choices:` before ever indexing into it.

7. **`ChatStreamParams` was unnecessary duplication.** A second Pydantic class, identical field-for-field to `ChatRequest`, was created "just in case the two endpoints' needs diverge someday." Caught by direct question ("can you not just reuse ChatRequest?"), verified reuse actually works with a real test, then simplified to one shared class. Lesson: don't build for a need that doesn't exist yet.

8. **Misreading a working stream as broken.** Postman correctly showed each SSE event as a separate timestamped log line rather than one flowing paragraph, that's the tool's design, not a bug. Confirmed by literally reading the timestamps and reconstructing the sentence by hand.

9. **Tool dispatch needed to mix "plain function" and "function needing extra shared state."** `tavily_search` needs the shared `http_client`, the to-do functions need `user_id`, neither of which the model ever provides. Solved with small `lambda`s closing over `self.http_client` / `self.user_id`, tested in isolation before being trusted.

10. **NeMo Guardrails would not import at all on Python 3.14.** `langchain` (a transitive dependency) crashed at import time with a cryptic `TypeError: 'function' object is not subscriptable` inside Pydantic's type-hint resolution. Root cause confirmed directly from `nemoguardrails`'s own PyPI metadata: `requires_python: <3.14,>=3.10`, it explicitly does not support 3.14 yet, which is an extremely new Python release. **Fix:** a dedicated virtual environment on Python 3.12 (already installed on this machine from a different project), which also finally gave DevMate proper dependency isolation it never had before.

11. **`RailsConfig.from_path("app/guardrails")` failed with "Invalid config path."** Root cause: the folder had never actually been created, only the service code referencing it existed. Fixed by creating `config.yml` and `prompts.yml` in that folder.

12. **Claude ended up listed as a git co-author twice**, once via a `Co-Authored-By:` trailer on the `DevMate-AI-Assistant` repo (fixed by amending the one existing commit and force-pushing), and prevented entirely the second time when the project moved to the `devmate-fastapi` repo, by simply never adding the trailer. Verified both times against GitHub's live `contributors` API, not just by eyeballing the repo page (which runs on a slower, separate cache).

---

# PART 5: FAQs A SUPERVISOR MIGHT ASK

**Q: Why FastAPI over Flask?**
Native async support (matters when every real operation here is I/O: calling OpenAI, Tavily, Valkey), automatic request validation via Pydantic, and free interactive docs.

**Q: Why Valkey instead of just a Python dictionary for conversation history?**
A dictionary lives inside one process's memory, it vanishes on restart and can't be shared if the app ever runs as more than one process/worker. Valkey is a separate, persistent, shareable store, the same reasoning already applied to caching and locks in Project 1.

**Q: Why does `SessionService` still use a plain dictionary, then?**
Scoped intentionally to what Phase 1 asked for. It's a known, documented limitation (resets on restart), and would be the natural next thing to move into Valkey if this became a real production system.

**Q: Why sliding window instead of summarization for short-term memory?**
Summarization needs an extra LLM call every time the limit is hit, more cost, more latency, another thing that can fail. Sliding window is one Valkey command and is explicitly allowed by the spec as an equally valid choice.

**Q: How does tool calling actually work, mechanically?**
The model is handed a list of tool descriptions (name, plain-English description, expected parameters) alongside the normal prompt. It can respond with a request to call one instead of a normal answer. DevMate runs the real Python function locally, no code from OpenAI ever executes on your machine, sends the result back as a new message, and asks the model again for a final answer using that result.

**Q: Why two separate API calls when a tool is used, and how is that reflected in the cost?**
Because the model can't know a tool's result before it exists, it truly does take two separate, real round trips. Both calls' token usage are added together (`total_input_tokens = input_tokens + second_response.usage.prompt_tokens`), so cost reporting is never silently short-counted.

**Q: Why NeMo Guardrails and not just a second prompt to the same model?**
The self-check pattern used here IS effectively "a second prompt to the model", NeMo Guardrails' `self_check_input`/`self_check_output` flows are a structured, reusable, config-driven way of doing exactly that, rather than hand-rolling it. It's also the library specifically required by the project brief.

**Q: What happens if OpenAI is down?**
`openai.APIError` (and everything it covers, timeouts, connection errors, rate limits) is caught specifically in `routes/chat.py` and returned as a clean `502 LLM_REQUEST_FAILED`, never a raw stack trace.

**Q: What happens if Tavily is down?**
Up to 3 retries with real timeout/connection/status-code handling. If all fail, the tool returns a plain sentence saying search is unavailable, the model still answers using what it already knows, rather than the whole request failing.

**Q: How do you prevent prompt injection through a tool result?**
Two layers: the system prompt explicitly instructs the model to treat tool/user content as data, not instructions, and the output guardrail checks the final reply before it's ever sent to the user. Tested directly by planting a fake malicious instruction inside a Tavily result, the injection did not succeed.

**Q: How was any of this actually tested, given you can't unit-test a real LLM easily?**
Two ways. Structural correctness (message formats, token summing, retry logic, concurrency safety of the to-do file lock, the empty-choices streaming crash) was verified offline against faked OpenAI/Tavily responses, no real API calls, no cost, fully deterministic. Actual behavior (does the model pick the right tool, does the guardrail actually block a jailbreak) was verified with real, live requests through Postman and the `tests/manual_test_requests.py` script.

**Q: What's still missing or deliberately deferred?**
`/chat/stream` doesn't yet support tools, memory, or guardrails. `SessionService` is in-memory only. The tool-ordering test (confirming selection stays reliable when the tools list order changes) hasn't been run yet. Both projects' Postman collections haven't been exported into the repos yet.

**Q: What would you do differently with more time?**
Move `SessionService` into Valkey for persistence across restarts, bring streaming up to parity with the non-streaming endpoint, and write custom Colang flows for guardrails instead of relying only on the built-in self-check flows, for finer-grained control than a single yes/no per message.
