import pytest

from c3.jsonutil import extract_json


@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n[{"a": 1}]\n```', [{"a": 1}]),
    ('Here is the result: {"a": "brace } in string"}. Done.', {"a": "brace } in string"}),
    ('Result follows:\n```json\n{"a": 1}\n```\nDone.', {"a": 1}),
    ('The [invalid] prefix precedes {"a": 1}', {"a": 1}),
    ('null', None),
])
def test_extract_json_variants(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "not JSON", "{broken}", "```json\n{\n```"])
def test_extract_json_invalid_raises(text):
    with pytest.raises(ValueError):
        extract_json(text)
