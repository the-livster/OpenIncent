"""Generate test .xlsx fixtures. Run with `uv run python _generate.py`."""

from pathlib import Path

from openpyxl import Workbook

IN_DIR = str(Path(__file__).parent)


def _clean_transactions() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"

    ws.append(["id", "payee_id", "deal_id", "period", "amount", "product", "close_date"])
    ws.append(["T001", "P001", "D001", "2026-04", "1500.00", "Enterprise", "2026-04-15"])
    ws.append(["T002", "P001", "D002", "2026-04", "2500.00", "Standard", "2026-04-20"])
    ws.append(["T003", "P002", "D003", "2026-05", "1000.00", "", "2026-05-10"])

    wb.save(f"{IN_DIR}/clean_transactions.xlsx")


def _messy_transactions() -> None:
    """Header on row 3, totals row, merged cells, $-prefixed amounts, MM/DD dates."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Deals"

    # Row 1: merged title cell
    ws.merge_cells("A1:G1")
    ws.cell(1, 1, "Q1 2026 Sales Report")

    # Row 2: blank
    # Row 3: actual headers
    headers = [
        "Transaction ID", "Rep Name", "Opportunity ID",
        "Period", "TCV", "Product", "Close Date",
    ]
    for i, h in enumerate(headers, 1):
        ws.cell(3, i, h)

    rows = [
        ["T001", "Alice", "D001", "2026-04", "$1,500.00", "Enterprise", "04/15/2026"],
        ["T002", "Alice", "D002", "2026-04", "$2,500.00", "Standard", "04/20/2026"],
        ["T003", "Bob", "D003", "2026-05", "$1,000.00", "", "05/10/2026"],
    ]
    for r, row_data in enumerate(rows, 4):
        for c, val in enumerate(row_data, 1):
            ws.cell(r, c, val)

    # Totals row
    ws.merge_cells("A7:F7")
    ws.cell(7, 1, "Total")
    ws.cell(7, 7, "$5,000.00")

    wb.save(f"{IN_DIR}/messy_transactions.xlsx")


def _multi_sheet() -> None:
    wb = Workbook()
    wb.remove(wb.active)

    # Sheet 1: data sheet (picked first)
    ws1 = wb.create_sheet("Transaction Data")
    ws1.append(["id", "payee_id", "deal_id", "period", "amount", "product", "close_date"])
    ws1.append(["T001", "P001", "D001", "2026-04", "1500.00", "Enterprise", "2026-04-15"])

    # Sheet 2: non-data summary sheet
    ws2 = wb.create_sheet("Summary")
    ws2.cell(1, 1, "Report Information")

    # Sheet 3: another data sheet
    ws3 = wb.create_sheet("Payee Data")
    ws3.append(["id", "name", "quota", "plan_id", "effective_from"])
    ws3.append(["P001", "Alice", "10000.00", "flat_rate_plan", "2026-01-01"])

    wb.save(f"{IN_DIR}/multi_sheet.xlsx")


def _clean_payees() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Payees"

    ws.append(["id", "name", "quota", "plan_id", "effective_from"])
    ws.append(["P001", "Alice", "10000.00", "flat_rate_plan", "2026-01-01"])
    ws.append(["P002", "Bob", "8000.00", "flat_rate_plan", "2026-01-01"])

    wb.save(f"{IN_DIR}/clean_payees.xlsx")


if __name__ == "__main__":
    _clean_transactions()
    _messy_transactions()
    _multi_sheet()
    _clean_payees()
    print("Done generating xlsx fixtures.")
