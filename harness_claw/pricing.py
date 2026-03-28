from __future__ import annotations

# (input_price_per_million, output_price_per_million) in USD
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6":           (15.00, 75.00),
    "claude-sonnet-4-6":         (3.00,  15.00),
    "claude-haiku-4-5-20251001": (0.80,   4.00),
}


def get_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICING:
        return 0.0
    input_price, output_price = PRICING[model]
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
