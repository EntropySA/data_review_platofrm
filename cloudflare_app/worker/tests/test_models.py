import pytest
from pydantic import ValidationError

from cloudflare_app.worker.src.models import QuestionRecord


def test_input_given_as_a_single_string_becomes_one_part():
    record = QuestionRecord(id=1, instruction="أجب", input="سؤال واحد", output="جواب")
    assert record.input == ["سؤال واحد"]


def test_input_given_as_a_list_is_unchanged():
    record = QuestionRecord(id=1, instruction="أجب", input=["أ", "ب"], output="جواب")
    assert record.input == ["أ", "ب"]


def test_input_of_another_type_is_still_rejected():
    with pytest.raises(ValidationError):
        QuestionRecord(id=1, instruction="أجب", input=5, output="جواب")
