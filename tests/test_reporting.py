from io import BytesIO

import pytest

openpyxl = pytest.importorskip("openpyxl")

from reporting import EXPORT_COLUMNS, create_excel_export


def test_excel_workbook_has_exact_columns_and_wrapped_question_text():
    workbook_bytes = create_excel_export(
        [
            {
                "instruction": "Be accurate.",
                "question": "First line\nSecond line",
                "output": "Answer",
                "pass/fail": "Fail",
                "notes": "Unsupported.",
            }
        ]
    )

    workbook = openpyxl.load_workbook(BytesIO(workbook_bytes))
    worksheet = workbook["Reviewed Data"]
    assert tuple(cell.value for cell in worksheet[1]) == EXPORT_COLUMNS
    assert worksheet.max_column == 5
    assert worksheet.max_row == 2
    assert worksheet["B2"].value == "First line\nSecond line"

