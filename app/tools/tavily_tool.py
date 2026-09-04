import httpx
import logging

from app.config.settings import settings

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


async def tavily_search(query: str, http_client: httpx.AsyncClient) -> str:

    headers = {
        "Authorization": f"Bearer {settings.tavily_api_key}"
    }

    body = {
        "query": query,
        "max_results": 5
    }

    for attempt in range(1, 4):

        try:
            logging.info(f"Calling Tavily search (attempt {attempt}/3): {query}")

            response = await http_client.post(
                TAVILY_SEARCH_URL,
                headers=headers,
                json=body,
                timeout=settings.tavily_timeout
            )

            response.raise_for_status()

            data = response.json()
            results = data.get("results", [])

            if not results:
                return "No search results were found for this query."

            formatted = []
            for i, result in enumerate(results, start=1):
                formatted.append(
                    f"{i}. {result.get('title', 'Untitled')}\n"
                    f"   URL: {result.get('url', '')}\n"
                    f"   {result.get('content', '')}"
                )

            logging.info(f"Tavily search succeeded: {len(results)} results")

            return "\n\n".join(formatted)

        except httpx.TimeoutException:
            logging.error(f"Tavily search timeout (attempt {attempt}/3)")

        except httpx.HTTPStatusError as e:
            logging.error(f"Tavily search failed (attempt {attempt}/3): {e}")

        except httpx.RequestError as e:
            logging.error(f"Tavily connection error (attempt {attempt}/3): {e}")

    return (
        "Web search is currently unavailable. Answer based on what you "
        "already know, and let the user know a live search could not be "
        "completed."
    )


TAVILY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "tavily_search",
        "description": (
            "Search the web for current or factual information you might "
            "not know, such as recent events, prices, or facts outside "
            "your training data. Use this when the user asks about "
            "something that requires up-to-date or specific real-world "
            "information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up on the web."
                }
            },
            "required": ["query"]
        }
    }
}