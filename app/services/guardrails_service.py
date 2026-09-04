import logging

# Adversarial test (2026-09-04): 5 jailbreaks (instruction override, role-play,
# reveal-system-prompt, identity change, tool misuse) + 1 tool-result injection
# (poisoned Tavily result). All 6 attempts were blocked, no successful attacks.


async def check_input(rails, user_message: str) -> bool:
    result = await rails.generate_async(
        messages=[{"role": "user", "content": user_message}],
        options={"rails": {"input": True, "output": False, "dialog": False}}
    )

    blocked = False
    triggered_rail = None

    if result.log:
        for activated in result.log.activated_rails:
            if "bot refuse to respond" in activated.decisions:
                blocked = True
                triggered_rail = activated.name

    if blocked:
        logging.info(f"Input guardrail BLOCKED a message (rail: {triggered_rail})")
    else:
        logging.info("Input guardrail allowed the message through")

    return blocked


async def check_output(rails, bot_message: str) -> bool:
    result = await rails.generate_async(
        messages=[
            {"role": "user", "content": "placeholder"},
            {"role": "assistant", "content": bot_message}
        ],
        options={"rails": {"input": False, "output": True, "dialog": False}}
    )

    blocked = False
    triggered_rail = None

    if result.log:
        for activated in result.log.activated_rails:
            if "bot refuse to respond" in activated.decisions:
                blocked = True
                triggered_rail = activated.name

    if blocked:
        logging.info(f"Output guardrail BLOCKED a response (rail: {triggered_rail})")
    else:
        logging.info("Output guardrail allowed the response through")

    return blocked