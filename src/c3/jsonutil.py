"""Extract JSON from CLI or model response text."""

import json
from typing import Any


def extract_json(text: str) -> Any:
    """Accept plain JSON, fenced JSON, or an object/array surrounded by prose."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character not in "[{":
            continue
        try:
            result, _ = decoder.raw_decode(stripped, index)
            return result
        except json.JSONDecodeError:
            continue
    raise ValueError("No valid JSON found in response")
