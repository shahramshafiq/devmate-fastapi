import json
import logging
from pathlib import Path

MEMORY_DIR = Path("data/memory")
MAX_TURNS = 10  # keep last 10 user+assistant turn pairs = 20 stored messages


def _chat_history_key(user_id: str, session_id: str) -> str:
    return f"chat_history:{user_id}:{session_id}"


def get_recent_turns(valkey_client, user_id: str, session_id: str) -> list:
    key = _chat_history_key(user_id, session_id)
    raw_turns = valkey_client.lrange(key, 0, -1)
    return [json.loads(t) for t in raw_turns]


def append_turn(valkey_client, user_id: str, session_id: str, role: str, content: str):
    key = _chat_history_key(user_id, session_id)
    valkey_client.rpush(key, json.dumps({"role": role, "content": content}))
    # sliding window: chosen over summarization because it needs zero extra LLM
    # calls (no added cost or latency) and is explicitly allowed by the spec
    valkey_client.ltrim(key, -(MAX_TURNS * 2), -1)


def _memory_file(user_id: str) -> Path:
    return MEMORY_DIR / f"{user_id}.json"


def get_long_term_facts(user_id: str) -> list:
    path = _memory_file(user_id)
    if not path.exists():
        return []
    with open(path, "r") as f:
        return json.load(f)


def _save_long_term_facts(user_id: str, facts: list):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(_memory_file(user_id), "w") as f:
        json.dump(facts, f, indent=2)


async def extract_and_save_facts(client, model: str, user_id: str, user_message: str, assistant_reply: str):

    extraction_prompt = f"""Given this conversation turn, extract any new durable facts about
the user that would be useful to remember in future conversations (e.g.
preferences, habits, recurring details). Only include facts that are
genuinely durable, not one-off details specific to this single message.

User: {user_message}
Assistant: {assistant_reply}

Respond ONLY with a JSON object in this exact shape:
{{"facts": ["fact one", "fact two"]}}
If there are no new durable facts, respond with {{"facts": []}}."""

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": extraction_prompt}],
            response_format={"type": "json_object"}
        )

        data = json.loads(response.choices[0].message.content)
        new_facts = data.get("facts", [])

        if not new_facts:
            return

        existing_facts = get_long_term_facts(user_id)
        for fact in new_facts:
            if fact not in existing_facts:
                existing_facts.append(fact)

        _save_long_term_facts(user_id, existing_facts)
        logging.info(f"Saved {len(new_facts)} new fact(s) for user_id={user_id}")

    except Exception as e:
        logging.error(f"Fact extraction failed for user_id={user_id}: {e}")