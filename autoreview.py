"""Deterministic checks applied to an upload document ahead of human review.

Every rule here is mechanical: it either fires or it does not, with no judgment
involved. A record that trips a rule is failed with a fixed Arabic note, so the
same defect always reads the same way in the review surface.

Nothing in this module performs I/O. It takes parsed records and returns
findings, which keeps every rule directly testable.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import namedtuple
from typing import Any, Callable, Iterable, Sequence

# The note recorded against a failing record, keyed by the rule that fired.
NOTES = {
    "duplicate": "سؤال مكرر",
    "english": "لفظ انجليزي في المخرج",
    "punctuation": "علامات الترقيم في المخرج تختلف عن المدخل",
    "unknown_tool": "الأداة المستخدمة في المخرج ليست من الأدوات المتاحة",
    "unknown_argument": "المدخل المستخدم في الأداة غير متاح للاستخدام",
    "missing_required": "أحد المدخلات المطلوبة غير مستخدم في الأداة",
    "output_punctuation": "علامات ترقيم غير مسموحة في المخرج",
    "word_mismatch": "كلمات المخرج لا تتطابق مع المدخل",
    "not_an_option": "يجب أن يكون المخرج أحد الأربع خيارات المتاحة",
}

DATA_TYPES = ("arabizi", "tool_calling", "saudi_dialect", "arabic_grammar")

Record = namedtuple("Record", "source_id instruction input output")
# `position` is the record's index in the file, which identifies it even when
# two records share an id. `source_id` alone does not.
Finding = namedtuple("Finding", "position source_id check note")
Unchecked = namedtuple("Unchecked", "source_id reason")


def finding(position: int, record: Record, check: str) -> Finding:
    return Finding(position, record.source_id, check, NOTES[check])


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_records(document: Any) -> tuple[list[Record], list[Unchecked]]:
    """Normalise an upload document into records, exactly as the platform does.

    A record whose shape the platform itself would reject cannot be checked, so
    it is reported rather than silently dropped or guessed at.
    """
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise ValueError("The JSON root must contain a data array.")
    records: list[Record] = []
    unchecked: list[Unchecked] = []
    for index, value in enumerate(document["data"], start=1):
        label = f"row {index}"
        if not isinstance(value, dict):
            unchecked.append(Unchecked(label, "record is not an object"))
            continue
        label = f"{value.get('id', label)}"
        # A question may arrive as one string rather than a list of parts; the
        # importer accepts both (models.py:23, import.ts:17) and so must this.
        parts = value.get("input")
        parts = [parts] if isinstance(parts, str) else parts
        if not isinstance(value.get("instruction"), str) or not isinstance(value.get("output"), str):
            unchecked.append(Unchecked(label, "instruction or output is not a string"))
        elif not isinstance(parts, list) or not all(isinstance(item, str) for item in parts):
            unchecked.append(Unchecked(label, "input is not a string or an array of strings"))
        else:
            records.append(Record(str(value.get("id", "")), value["instruction"], list(parts), value["output"]))
    return records, unchecked


def row_hash(instruction: str, input_parts: Sequence[str], output: str) -> str:
    """Content fingerprint used to match an Excel row back to a stored question.

    The separators are control characters that cannot occur in the JSON text,
    so no combination of fields can collide with another.
    """
    payload = "\x1f".join([instruction, "\x1e".join(input_parts), output])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

# Arabic punctuation and its ASCII equivalent. Arabizi input is written in Latin
# script and its output in Arabic, so a question mark arrives as ? on one side
# and ؟ on the other; without folding, the punctuation rule would fire on almost
# every record and say nothing.
PUNCT_FOLD = {
    "؟": "?", "،": ",", "؛": ";", "٪": "%", "٫": ".", "٬": ",", "۔": ".",
    "«": '"', "»": '"', "“": '"', "”": '"', "‘": "'", "’": "'",
    "–": "-", "—": "-", "…": ".",
}


def is_punctuation(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P")


def is_latin_letter(ch: str) -> bool:
    """A letter from a Latin alphabet. Digits, emoji and symbols are not."""
    return ch.isalpha() and "LATIN" in unicodedata.name(ch, "")


def punctuation_set(text: str) -> set[str]:
    """Which marks a text uses, script-folded. Counts and positions are ignored."""
    return {PUNCT_FOLD.get(ch, ch) for ch in text if is_punctuation(ch)}


def trim_punctuation(token: str) -> str:
    start, end = 0, len(token)
    while start < end and is_punctuation(token[start]):
        start += 1
    while end > start and is_punctuation(token[end - 1]):
        end -= 1
    return token[start:end]


def words(text: str) -> list[str]:
    return [word for word in (trim_punctuation(token) for token in text.split()) if word]


def input_text(record: Record) -> str:
    """The parts joined as the platform joins them for display (store.py:270)."""
    return "\n".join(record.input)


# --------------------------------------------------------------------------
# Tolerant JSON reading
#
# Tool schemas are written by hand and reach us with JavaScript-style comments
# and trailing commas, neither of which any JSON parser accepts. Both are
# removed with a string-aware scan so that a brace or comma inside a quoted
# Arabic value is never mistaken for syntax.
# --------------------------------------------------------------------------

def _scan(text: str, i: int) -> tuple[int, bool]:
    """Advance past one character of a string literal, reporting if it ended."""
    if text[i] == "\\":
        return i + 2, False
    return i + 1, text[i] == '"'


def strip_comments(text: str) -> str:
    out: list[str] = []
    i, n, in_string = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_string:
            step, closed = _scan(text, i)
            out.append(text[i:step])
            in_string = not closed
            i = step
            continue
        if ch == '"':
            in_string = True
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            i = text.find("\n", i)
            if i == -1:
                break
            continue
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def drop_trailing_commas(text: str) -> str:
    out: list[str] = []
    i, n, in_string = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_string:
            step, closed = _scan(text, i)
            out.append(text[i:step])
            in_string = not closed
            i = step
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            ahead = i + 1
            while ahead < n and text[ahead].isspace():
                ahead += 1
            if ahead < n and text[ahead] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def relax(text: str) -> str:
    return drop_trailing_commas(strip_comments(text))


def balanced_end(text: str, start: int) -> int:
    """Index just past the bracket closing the one at `start`, or -1."""
    closer = {"[": "]", "{": "}"}[text[start]]
    opener, depth, in_string = text[start], 0, False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            i, closed = _scan(text, i)
            in_string = not closed
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _structures(text: str, opener: str) -> Iterable[Any]:
    """Every parsable JSON structure starting at an `opener` in the text."""
    i = 0
    while True:
        i = text.find(opener, i)
        if i == -1:
            return
        end = balanced_end(text, i)
        if end != -1:
            try:
                yield json.loads(text[i:end])
            except ValueError:
                pass
        i += 1


def _definition(item: Any) -> dict | None:
    """One entry's function definition, wrapped or bare.

    Mirrors the reader the review interface already uses (toolSchema.ts:24) so
    the checker recognises exactly what a reviewer sees rendered.
    """
    if not isinstance(item, dict):
        return None
    wrapped = item.get("function")
    if isinstance(wrapped, dict) and isinstance(wrapped.get("name"), str):
        return wrapped
    if isinstance(item.get("name"), str) and isinstance(item.get("parameters"), dict):
        return item
    return None


def _tool(definition: dict) -> dict:
    parameters = definition.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    properties = parameters.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = parameters.get("required")
    required = [name for name in required if isinstance(name, str)] if isinstance(required, list) else []
    return {"name": definition["name"], "properties": set(properties), "required": required}


def extract_tools(instruction: str) -> list[dict] | None:
    """The tools offered by an instruction, or None if it declares none.

    Every entry of the array must be a function definition, so an ordinary list
    of objects appearing in the prose is not mistaken for a tool schema.
    """
    for structure in _structures(relax(instruction), "["):
        if not isinstance(structure, list) or not structure:
            continue
        definitions = [_definition(item) for item in structure]
        if all(definition is not None for definition in definitions):
            return [_tool(definition) for definition in definitions]
    return None


TOOL_CALL = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)


def extract_calls(output: str) -> list[dict] | None:
    """Every tool call made by an output, or None if any of them is unreadable.

    A block we cannot parse means we do not know what was called, so the record
    is reported as unchecked rather than failed on a parser's limitation.
    """
    blocks = TOOL_CALL.findall(output)
    if not blocks:
        return None
    calls = []
    for block in blocks:
        payload = next(_structures(relax(block), "{"), None)
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            return None
        arguments = payload.get("arguments")
        calls.append({"name": payload["name"], "arguments": set(arguments) if isinstance(arguments, dict) else set()})
    return calls


# --------------------------------------------------------------------------
# Per-type rules
#
# Each returns the rule keys that fired, and a reason when the record could not
# be checked at all. A reason and findings are mutually exclusive.
# --------------------------------------------------------------------------

def check_arabizi(record: Record) -> tuple[list[str], str | None]:
    checks = []
    if any(is_latin_letter(ch) for ch in record.output):
        checks.append("english")
    if punctuation_set(input_text(record)) != punctuation_set(record.output):
        checks.append("punctuation")
    return checks, None


def check_tool_calling(record: Record) -> tuple[list[str], str | None]:
    tools = extract_tools(record.instruction)
    if tools is None:
        return [], "no tool schema found in the instruction"
    calls = extract_calls(record.output)
    if calls is None:
        return [], "no readable <tool_call> in the output"
    by_name = {tool["name"]: tool for tool in tools}
    checks: list[str] = []
    for call in calls:
        tool = by_name.get(call["name"])
        if tool is None:
            # The argument rules only apply to a call naming a real tool.
            checks.append("unknown_tool")
            continue
        if call["arguments"] - tool["properties"]:
            checks.append("unknown_argument")
        if set(tool["required"]) - call["arguments"]:
            checks.append("missing_required")
    return list(dict.fromkeys(checks)), None


def check_saudi_dialect(record: Record) -> tuple[list[str], str | None]:
    checks = []
    if any(is_punctuation(ch) for ch in record.output):
        checks.append("output_punctuation")
    available = set(words(input_text(record)))
    if any(word not in available for word in words(record.output)):
        checks.append("word_mismatch")
    return checks, None


GRAMMAR_OPTIONS = ("أ", "ب", "ج", "د")


def check_arabic_grammar(record: Record) -> tuple[list[str], str | None]:
    return ([] if record.output.strip() in GRAMMAR_OPTIONS else ["not_an_option"]), None


CHECKS: dict[str, Callable[[Record], tuple[list[str], str | None]]] = {
    "arabizi": check_arabizi,
    "tool_calling": check_tool_calling,
    "saudi_dialect": check_saudi_dialect,
    "arabic_grammar": check_arabic_grammar,
}


def duplicate_ids(records: Sequence[Record]) -> set[int]:
    """Positions of every record repeating an earlier instruction and input.

    The first occurrence is kept, so one usable copy of the data survives.
    """
    seen: set[tuple[str, tuple[str, ...]]] = set()
    repeats = set()
    for position, record in enumerate(records):
        key = (record.instruction, tuple(record.input))
        if key in seen:
            repeats.add(position)
        seen.add(key)
    return repeats


def review_records(records: Sequence[Record], data_type: str) -> tuple[list[Finding], list[Unchecked]]:
    """Every rule that fired, and every record no rule could be applied to."""
    if data_type not in CHECKS:
        raise ValueError(f"Unknown data type {data_type!r}. Expected one of {', '.join(DATA_TYPES)}.")
    check = CHECKS[data_type]
    repeats = duplicate_ids(records)
    findings: list[Finding] = []
    unchecked: list[Unchecked] = []
    for position, record in enumerate(records):
        if position in repeats:
            findings.append(finding(position, record, "duplicate"))
        fired, reason = check(record)
        if reason:
            unchecked.append(Unchecked(record.source_id, reason))
        findings.extend(finding(position, record, key) for key in fired)
    return findings, unchecked


def failure_rows(records: Sequence[Record], findings: Sequence[Finding]) -> list[dict]:
    """One row per failed record, notes and rules stacked in their own cells."""
    grouped: dict[int, list[Finding]] = {}
    for item in findings:
        grouped.setdefault(item.position, []).append(item)
    rows = []
    for position, record in enumerate(records):
        group = grouped.get(position)
        if not group:
            continue
        rows.append({
            "source_id": record.source_id,
            "instruction": record.instruction,
            "input": input_text(record),
            "output": record.output,
            "notes": "\n".join(item.note for item in group),
            "check": "\n".join(item.check for item in group),
            "row_hash": row_hash(record.instruction, record.input, record.output),
        })
    return rows
