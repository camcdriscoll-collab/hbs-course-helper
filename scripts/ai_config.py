"""
ai_config.py — One place to pick the Claude model and read its response.

Both canvas_refresh.py and cheat_sheet.py generate Notes, and both used to
carry their own copy of the model name and the per-token prices. Change the
model here and the printed cost estimate stays honest.

Prices are USD per million tokens, from https://claude.com/pricing.
"""

# Sonnet is the default: it reads case PDFs well and keeps a term's worth of
# notes in the tens of dollars. Swap to "claude-opus-5" (5.00 / 25.00) for
# harder analytical courses, or "claude-haiku-4-5" (1.00 / 5.00) to cut cost.
MODEL = "claude-sonnet-4-6"

PRICES_PER_MTOK = {
    "claude-opus-5":     (5.00, 25.00),
    "claude-sonnet-5":   (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5":  (1.00,  5.00),
}


def estimate_cost(usage, model: str = MODEL) -> float:
    """USD for one message, or 0.0 if the model isn't in the price table."""
    price_in, price_out = PRICES_PER_MTOK.get(model, (0.0, 0.0))
    return (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000


def response_text(message) -> str:
    """
    Concatenate the text blocks of a response.

    message.content[0] is not reliably the text block — a model with thinking
    enabled puts a thinking block first, and indexing [0] then raises
    AttributeError instead of returning the notes.
    """
    return "".join(b.text for b in message.content if b.type == "text")
