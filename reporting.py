"""Excel reporting adapter for completed reviews."""

from io import BytesIO
from typing import Mapping, Sequence


EXPORT_COLUMNS = ("instruction", "question", "output", "pass/fail", "notes")


def create_excel_export(rows: Sequence[Mapping[str, str]]) -> bytes:
    # Imported here so core workflows and tests remain available before optional
    # UI dependencies are installed.
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Reviewed Data"
    worksheet.append(EXPORT_COLUMNS)
    for row in rows:
        worksheet.append([row[column] for column in EXPORT_COLUMNS])

    header_fill = PatternFill("solid", fgColor="164E63")
    for cell in worksheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(vertical="center")

    widths = (34, 48, 48, 14, 48)
    for index, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = width
    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions
    worksheet.row_dimensions[1].height = 24

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
