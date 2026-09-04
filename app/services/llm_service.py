import json

import httpx
from openai import AsyncOpenAI
from app.prompts.system_prompt import SYSTEM_PROMPT, build_user_prompt
from app.config.settings import settings
from app.tools.datetime_tool import get_current_datetime, DATETIME_TOOL_SCHEMA
from app.tools.tavily_tool import tavily_search, TAVILY_TOOL_SCHEMA
from app.tools.todo_tool import (
    add_task, list_tasks, complete_task, delete_task,
    ADD_TASK_SCHEMA, LIST_TASKS_SCHEMA, COMPLETE_TASK_SCHEMA, DELETE_TASK_SCHEMA
)


AVAILABLE_TOOLS = [
    DATETIME_TOOL_SCHEMA,
    TAVILY_TOOL_SCHEMA,
    ADD_TASK_SCHEMA,
    LIST_TASKS_SCHEMA,
    COMPLETE_TASK_SCHEMA,
    DELETE_TASK_SCHEMA
]


class LLMService:

    def __init__(self, client: AsyncOpenAI, http_client: httpx.AsyncClient, user_id: str):
        self.client = client
        self.http_client = http_client
        self.user_id = user_id

        self.tool_functions = {
            "get_current_datetime": get_current_datetime,
            "tavily_search": lambda query: tavily_search(query, self.http_client),
            "add_task": lambda description: add_task(self.user_id, description),
            "list_tasks": lambda: list_tasks(self.user_id),
            "complete_task": lambda task_id: complete_task(self.user_id, task_id),
            "delete_task": lambda task_id: delete_task(self.user_id, task_id)
        }

    async def generate_response(
        self,
        message: str,
        temperature: float,
        top_p: float,
        max_tokens: int,
        history: list,
        facts: list
    ):

        system_content = SYSTEM_PROMPT
        if facts:
            system_content += "\n\nKnown facts about this user:\n" + "\n".join(f"- {fact}" for fact in facts)

        user_prompt = build_user_prompt(message)

        messages = [{"role": "system", "content": system_content}] + history + [
            {"role": "user", "content": user_prompt}
        ]

        # Tested temp 0.1 vs 1.0 and top_p 0.1 vs 1.0 on the same prompt:
        # low temp and low top_p both gave near-identical, "safe" phrasing
        # high temp/top_p varied the wording more (e.g. "modern tool" vs "modern web framework")
        # facts stayed accurate in all 4 runs, only word choice changed
        response = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            tools=AVAILABLE_TOOLS
        )

        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens

        assistant_message = response.choices[0].message

        if not assistant_message.tool_calls:
            return {
                "reply": assistant_message.content,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            }

        messages.append(assistant_message.model_dump(exclude_none=True))

        for tool_call in assistant_message.tool_calls:
            function_name = tool_call.function.name
            arguments = json.loads(tool_call.function.arguments)

            function_to_call = self.tool_functions[function_name]
            result = await function_to_call(**arguments)

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result)
            })

        second_response = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens
        )

        reply = second_response.choices[0].message.content

        total_input_tokens = input_tokens + second_response.usage.prompt_tokens
        total_output_tokens = output_tokens + second_response.usage.completion_tokens

        return {
            "reply": reply,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens
        }

    async def generate_stream(
        self,
        message: str,
        temperature: float,
        top_p: float,
        max_tokens: int
    ):

        user_prompt = build_user_prompt(message)

        stream = await self.client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True}
        )

        async for chunk in stream:

            if chunk.choices:
                content = chunk.choices[0].delta.content
                if content:
                    yield {"type": "content", "content": content}

            if chunk.usage:
                yield {
                    "type": "usage",
                    "input_tokens": chunk.usage.prompt_tokens,
                    "output_tokens": chunk.usage.completion_tokens
                }