import asyncio
import json
from pathlib import Path

TODOS_DIR = Path("data/todos")
_lock = asyncio.Lock()


def _todo_file(user_id: str) -> Path:
    return TODOS_DIR / f"{user_id}.json"


def _read_todos(user_id: str) -> list:
    path = _todo_file(user_id)
    if not path.exists():
        return []
    with open(path, "r") as f:
        return json.load(f)


def _write_todos(user_id: str, todos: list):
    TODOS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_todo_file(user_id), "w") as f:
        json.dump(todos, f, indent=2)


async def add_task(user_id: str, description: str) -> str:
    async with _lock:
        todos = _read_todos(user_id)
        new_id = max([t["id"] for t in todos], default=0) + 1
        todos.append({"id": new_id, "description": description, "status": "pending"})
        _write_todos(user_id, todos)
    return f"Added task #{new_id}: {description}"


async def list_tasks(user_id: str) -> str:
    async with _lock:
        todos = _read_todos(user_id)
    if not todos:
        return "You have no tasks."
    return "\n".join(f"#{t['id']} [{t['status']}] {t['description']}" for t in todos)


async def complete_task(user_id: str, task_id: int) -> str:
    async with _lock:
        todos = _read_todos(user_id)
        for t in todos:
            if t["id"] == task_id:
                t["status"] = "completed"
                _write_todos(user_id, todos)
                return f"Marked task #{task_id} as completed."
    return f"No task with id #{task_id} was found."


async def delete_task(user_id: str, task_id: int) -> str:
    async with _lock:
        todos = _read_todos(user_id)
        remaining = [t for t in todos if t["id"] != task_id]
        if len(remaining) == len(todos):
            return f"No task with id #{task_id} was found."
        _write_todos(user_id, remaining)
    return f"Deleted task #{task_id}."


ADD_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "add_task",
        "description": "Add a new task to the user's to-do list.",
        "parameters": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What the task is about."}
            },
            "required": ["description"]
        }
    }
}

LIST_TASKS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_tasks",
        "description": "List all of the user's current to-do tasks, including whether each is pending or completed.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    }
}

COMPLETE_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "complete_task",
        "description": "Mark a task as completed, given its task id number.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "The id number of the task to mark completed."}
            },
            "required": ["task_id"]
        }
    }
}

DELETE_TASK_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_task",
        "description": "Permanently delete a task from the to-do list, given its task id number.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "The id number of the task to delete."}
            },
            "required": ["task_id"]
        }
    }
}