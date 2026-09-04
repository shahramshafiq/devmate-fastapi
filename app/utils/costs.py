
#assuming price per 1 million tokens
def calculate_input_cost(input_tokens: int, input_price: float) -> float:
    return (input_tokens / 1_000_000) * input_price

def calculate_output_cost(output_tokens: int, output_price: float) -> float:
    return (output_tokens / 1_000_000) * output_price


def calculate_total_cost(input_cost: float, output_cost: float) -> float:
    return input_cost + output_cost
