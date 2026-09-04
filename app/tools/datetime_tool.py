from datetime import datetime, timedelta


async def get_current_datetime(days_offset: int = 0) -> str:
    target = datetime.now() + timedelta(days=days_offset)
    return target.strftime("%A, %B %d, %Y %I:%M %p")


DATETIME_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_current_datetime",
        "description": (
            "Get the current date and time, or a date/time a number of days "
            "from now. Use this whenever the user asks about today's date, "
            "the current time, or a relative date like 'tomorrow' or "
            "'3 days from now'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days_offset": {
                    "type": "integer",
                    "description": (
                        "Number of days from today. Use 0 for today, "
                        "positive numbers for future dates, negative "
                        "numbers for past dates."
                    )
                }
            },
            "required": []
        }
    }
}
