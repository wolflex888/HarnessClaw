import pytest
from harness_claw.pricing import get_cost, PRICING


def test_pricing_dict_has_required_models():
    assert "claude-sonnet-4-6" in PRICING
    assert "claude-haiku-4-5-20251001" in PRICING
    assert "claude-opus-4-6" in PRICING


def test_get_cost_sonnet():
    # claude-sonnet-4-6: $3/M input, $15/M output
    cost = get_cost("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(18.00)


def test_get_cost_zero_tokens():
    cost = get_cost("claude-sonnet-4-6", input_tokens=0, output_tokens=0)
    assert cost == 0.0


def test_get_cost_unknown_model():
    cost = get_cost("unknown-model", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.0


def test_get_cost_partial():
    # 500k input tokens at $3/M = $1.50
    cost = get_cost("claude-sonnet-4-6", input_tokens=500_000, output_tokens=0)
    assert cost == pytest.approx(1.50)
