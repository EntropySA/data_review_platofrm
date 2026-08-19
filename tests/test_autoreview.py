import json

import pytest

from autoreview import (
    Record,
    extract_calls,
    extract_tools,
    failure_rows,
    load_records,
    review_records,
    row_hash,
)


def record(instruction="حول", parts=("",), output="", source_id="1"):
    return Record(source_id, instruction, list(parts), output)


def fired(records, data_type):
    findings, unchecked = review_records(records, data_type)
    return [item.check for item in findings], unchecked


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def test_accepts_a_bare_string_input_like_the_importer_does():
    records, unchecked = load_records({"data": [{"id": 7, "instruction": "i", "input": "one", "output": "o"}]})
    assert records[0].input == ["one"]
    assert unchecked == []


def test_reports_a_malformed_record_instead_of_dropping_it():
    records, unchecked = load_records({"data": [{"id": 7, "instruction": "i", "input": [3], "output": "o"}]})
    assert records == []
    assert unchecked[0].source_id == "7"
    assert "input" in unchecked[0].reason


def test_a_root_without_a_data_array_is_refused():
    with pytest.raises(ValueError):
        load_records({"rows": []})


# --------------------------------------------------------------------------
# Duplicates
# --------------------------------------------------------------------------

def test_the_first_copy_survives_and_later_ones_fail():
    records = [record(parts=["a"], source_id="1"), record(parts=["a"], source_id="2"),
               record(parts=["a"], source_id="3")]
    findings, _ = review_records(records, "arabic_grammar")
    duplicates = [item.source_id for item in findings if item.check == "duplicate"]
    assert duplicates == ["2", "3"]


def test_a_different_instruction_is_not_a_duplicate():
    records = [record(instruction="a", parts=["x"]), record(instruction="b", parts=["x"])]
    findings, _ = review_records(records, "arabic_grammar")
    assert [item.check for item in findings if item.check == "duplicate"] == []


# --------------------------------------------------------------------------
# arabizi
# --------------------------------------------------------------------------

def test_a_latin_letter_in_the_output_fails():
    checks, _ = fired([record(parts=["ana reht lal mall"], output="أنا رحت لل mall")], "arabizi")
    assert "english" in checks


def test_digits_and_emoji_are_not_english():
    checks, _ = fired([record(parts=["wselt el saa 3"], output="وصلت الساعة 3 🔥")], "arabizi")
    assert "english" not in checks


def test_an_arabic_question_mark_matches_an_ascii_one():
    checks, _ = fired([record(parts=["kefak ya sadeeqi?"], output="كيفك يا صديقي؟")], "arabizi")
    assert checks == []


def test_a_dropped_comma_fails_the_punctuation_rule():
    checks, _ = fired([record(parts=["wein reht, w meta?"], output="وين رحت؟")], "arabizi")
    assert checks == ["punctuation"]


def test_repeating_a_mark_is_not_a_difference():
    checks, _ = fired([record(parts=["ah! ah!"], output="آه!")], "arabizi")
    assert checks == []


# --------------------------------------------------------------------------
# saudi_dialect
# --------------------------------------------------------------------------

def test_an_output_word_absent_from_the_input_fails():
    checks, _ = fired([record(parts=["وصلت المدرسة متأخر"], output="وصلت للمدرسة")], "saudi_dialect")
    assert checks == ["word_mismatch"]


def test_a_subset_of_the_input_words_passes():
    checks, _ = fired([record(parts=["وصلت المدرسة متأخر"], output="وصلت المدرسة")], "saudi_dialect")
    assert checks == []


def test_punctuation_in_the_output_is_its_own_failure():
    checks, _ = fired([record(parts=["وصلت المدرسة متأخر"], output="وصلت المدرسة،")], "saudi_dialect")
    assert checks == ["output_punctuation"]


def test_added_tashkeel_breaks_the_exact_match():
    checks, _ = fired([record(parts=["وصلت المدرسة"], output="وَصَلت المدرسة")], "saudi_dialect")
    assert checks == ["word_mismatch"]


def test_a_respelled_hamza_breaks_the_exact_match():
    checks, _ = fired([record(parts=["وصلت متأخر"], output="وصلت متاخر")], "saudi_dialect")
    assert checks == ["word_mismatch"]


# --------------------------------------------------------------------------
# arabic_grammar
# --------------------------------------------------------------------------

@pytest.mark.parametrize("output", ["أ", "ب", " ج ", "\nد\n"])
def test_a_bare_option_passes(output):
    checks, _ = fired([record(output=output)], "arabic_grammar")
    assert checks == []


@pytest.mark.parametrize("output", ["أ.", "ا", "الجواب هو ج", "أ أو ب", "", "a"])
def test_anything_else_fails(output):
    checks, _ = fired([record(output=output)], "arabic_grammar")
    assert checks == ["not_an_option"]


# --------------------------------------------------------------------------
# tool_calling
# --------------------------------------------------------------------------

TOOLS = json.dumps([{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "يعيد حالة الطقس",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}, "unit": {"type": "string"}},
            "required": ["city"],
        },
    },
}], ensure_ascii=False)


def call(name, arguments):
    return f'<tool_call>{{"name": "{name}", "arguments": {json.dumps(arguments)}}}</tool_call>'


def test_a_correct_call_passes():
    checks, unchecked = fired([record(instruction=TOOLS, output=call("get_weather", {"city": "الرياض"}))], "tool_calling")
    assert checks == [] and unchecked == []


def test_a_tool_that_was_never_offered_fails():
    checks, _ = fired([record(instruction=TOOLS, output=call("send_sms", {"to": "1"}))], "tool_calling")
    assert checks == ["unknown_tool"]


def test_an_argument_the_tool_does_not_declare_fails():
    checks, _ = fired([record(instruction=TOOLS, output=call("get_weather", {"city": "الرياض", "when": "غدا"}))], "tool_calling")
    assert checks == ["unknown_argument"]


def test_a_missing_required_argument_fails():
    checks, _ = fired([record(instruction=TOOLS, output=call("get_weather", {"unit": "c"}))], "tool_calling")
    assert checks == ["missing_required"]


def test_argument_rules_are_skipped_when_the_name_is_already_wrong():
    checks, _ = fired([record(instruction=TOOLS, output=call("nope", {"bogus": 1}))], "tool_calling")
    assert checks == ["unknown_tool"]


def test_every_block_is_checked_and_notes_are_not_repeated():
    output = call("get_weather", {"city": "الرياض"}) + call("send_sms", {}) + call("send_email", {})
    checks, _ = fired([record(instruction=TOOLS, output=output)], "tool_calling")
    assert checks == ["unknown_tool"]


def test_an_instruction_with_javascript_comments_still_parses():
    # The shape schemas are actually written in. JSON has no comments, so this
    # would be unreadable without the tolerant reader.
    assert extract_tools("""[
      {
        "type": "function",
        "function": {
          "name": "get_weather",   // what it does
          "parameters": {
            "properties": {"city": {"type": "string"}},
            "required": [
              "city",              // the properties from above
            ]
          }
        }
      }
    ]""") == [{"name": "get_weather", "properties": {"city"}, "required": ["city"]}]


def test_an_ordinary_array_in_the_prose_is_not_a_tool_schema():
    assert extract_tools("اختر من القائمة [1, 2, 3] ثم أجب") is None


def test_a_record_with_no_tool_schema_is_unchecked_not_failed():
    checks, unchecked = fired([record(instruction="لا توجد أدوات", output=call("f", {}))], "tool_calling")
    assert checks == [] and "instruction" in unchecked[0].reason


def test_an_output_with_no_tool_call_is_unchecked_not_failed():
    checks, unchecked = fired([record(instruction=TOOLS, output="لا أستطيع المساعدة")], "tool_calling")
    assert checks == [] and "output" in unchecked[0].reason


def test_an_unreadable_block_leaves_the_record_unchecked():
    checks, unchecked = fired([record(instruction=TOOLS, output="<tool_call>{broken</tool_call>")], "tool_calling")
    assert checks == [] and unchecked


def test_a_call_written_with_a_trailing_comma_is_read():
    assert extract_calls('<tool_call>{"name": "f", "arguments": {"a": 1,},}</tool_call>') == [
        {"name": "f", "arguments": {"a"}}]


# --------------------------------------------------------------------------
# Rows and hashing
# --------------------------------------------------------------------------

def test_every_note_for_a_record_lands_in_one_row():
    records = [record(parts=["a"], source_id="1", output="ok"),
               record(parts=["a"], source_id="2", output="mall!")]
    findings, _ = review_records(records, "arabizi")
    rows = failure_rows(records, findings)
    assert len(rows) == 2
    assert rows[1]["notes"].splitlines() == ["سؤال مكرر", "لفظ انجليزي في المخرج",
                                             "علامات الترقيم في المخرج تختلف عن المدخل"]
    assert rows[1]["check"].splitlines() == ["duplicate", "english", "punctuation"]


def test_records_sharing_an_id_keep_their_own_rows():
    records = [record(parts=["x"], source_id="9", output="a"), record(parts=["y"], source_id="9", output="ب")]
    findings, _ = review_records(records, "arabic_grammar")
    rows = failure_rows(records, findings)
    assert [row["output"] for row in rows] == ["a"]


def test_the_hash_depends_on_every_field_and_on_the_split():
    base = row_hash("i", ["a", "b"], "o")
    assert base == row_hash("i", ["a", "b"], "o")
    assert base != row_hash("i", ["ab"], "o")
    assert base != row_hash("i", ["a", "b"], "O")
    assert base != row_hash("I", ["a", "b"], "o")
