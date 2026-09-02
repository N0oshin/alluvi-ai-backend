"""Summary-report PDF renderer (Profile → Export PDF Summary Report).

"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from fpdf import FPDF

# Core PDF fonts are Latin-1 only; a name or meal title outside that set
# (Arabic, emoji) must degrade to "?" rather than crash the export.
def _txt(value: str) -> str:
    return value.encode("latin-1", "replace").decode("latin-1")


@dataclass
class ReportMeal:
    time: str
    title: str
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int


@dataclass
class ReportDay:
    day: date
    meals: list[ReportMeal] = field(default_factory=list)


@dataclass
class ReportData:
    display_name: str
    range_start: date
    range_end: date
    generated_at: str  # pre-formatted, user-local
    starting_weight_kg: float | None
    current_weight_kg: float | None
    goal_weight_kg: float | None
    bmi_value: float | None
    bmi_category: str | None
    days: list[ReportDay] = field(default_factory=list)


_INK = (33, 33, 33)
_MUTED = (120, 120, 120)
_RULE = (220, 220, 220)
_HEADER_BG = (245, 245, 245)


def _kg(value: float | None) -> str:
    return f"{value:g} kg" if value is not None else "-"


class _ReportPdf(FPDF):
    def footer(self) -> None:  # page number, drawn by fpdf on every page
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(*_MUTED)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")


def build_summary_pdf(data: ReportData) -> bytes:
    pdf = _ReportPdf(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # --- header -----------------------------------------------------------
    pdf.set_text_color(*_INK)
    pdf.set_font("Helvetica", style="B", size=18)
    pdf.cell(0, 10, "Alluvi AI - Summary Report", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", size=10)
    pdf.set_text_color(*_MUTED)
    period = (
        f"{data.range_start.strftime('%b %d, %Y')} - "
        f"{data.range_end.strftime('%b %d, %Y')}"
    )
    pdf.cell(0, 6, _txt(data.display_name), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, period, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Generated on {data.generated_at}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    pdf.set_draw_color(*_RULE)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)

    # --- goals & progress -------------------------------------------------
    pdf.set_text_color(*_INK)
    pdf.set_font("Helvetica", style="B", size=13)
    pdf.cell(0, 8, "Goals & Progress", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    bmi_text = "-"
    if data.bmi_value is not None:
        bmi_text = f"{data.bmi_value:g}"
        if data.bmi_category:
            bmi_text += f" ({data.bmi_category})"

    stats = [
        ("Starting weight", _kg(data.starting_weight_kg)),
        ("Current weight", _kg(data.current_weight_kg)),
        ("Goal weight", _kg(data.goal_weight_kg)),
        ("BMI", bmi_text),
    ]
    label_w = 45.0
    for label, value in stats:
        pdf.set_font("Helvetica", size=10)
        pdf.set_text_color(*_MUTED)
        pdf.cell(label_w, 7, label)
        pdf.set_font("Helvetica", style="B", size=10)
        pdf.set_text_color(*_INK)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)

    # --- meal history (per-day table) -------------------------------------
    pdf.set_font("Helvetica", style="B", size=13)
    pdf.cell(0, 8, "Meal History - Last 7 Days", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    # Time | Meal | kcal | Protein | Carbs | Fat  (sums to 174mm,
    # the printable width of A4 with fpdf2's default margins)
    widths = (16.0, 98.0, 15.0, 15.0, 15.0, 15.0)
    headers = ("Time", "Meal", "kcal", "P (g)", "C (g)", "F (g)")

    def table_header() -> None:
        pdf.set_font("Helvetica", style="B", size=9)
        pdf.set_text_color(*_INK)
        pdf.set_fill_color(*_HEADER_BG)
        for w, h in zip(widths, headers):
            pdf.cell(w, 7, h, border="B", fill=True)
        pdf.ln()

    for report_day in data.days:
        # A day block (label + header + first row) must not straddle a page
        # break, so open a new page early when little room is left.
        if pdf.get_y() > pdf.h - 55:
            pdf.add_page()

        pdf.set_font("Helvetica", style="B", size=10)
        pdf.set_text_color(*_INK)
        pdf.cell(
            0, 8, report_day.day.strftime("%A, %b %d"), new_x="LMARGIN", new_y="NEXT"
        )

        if not report_day.meals:
            pdf.set_font("Helvetica", style="I", size=9)
            pdf.set_text_color(*_MUTED)
            pdf.cell(0, 6, "No meals logged", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
            continue

        table_header()
        pdf.set_font("Helvetica", size=9)
        for meal in report_day.meals:
            pdf.set_text_color(*_INK)
            title = meal.title if len(meal.title) <= 64 else meal.title[:61] + "..."
            row = (
                meal.time,
                _txt(title),
                str(meal.calories),
                str(meal.protein_g),
                str(meal.carbs_g),
                str(meal.fat_g),
            )
            for w, value in zip(widths, row):
                pdf.cell(w, 6.5, value, border="B")
            pdf.ln()

        # Day totals row
        pdf.set_font("Helvetica", style="B", size=9)
        totals = (
            "",
            "Total",
            str(sum(m.calories for m in report_day.meals)),
            str(sum(m.protein_g for m in report_day.meals)),
            str(sum(m.carbs_g for m in report_day.meals)),
            str(sum(m.fat_g for m in report_day.meals)),
        )
        for w, value in zip(widths, totals):
            pdf.cell(w, 6.5, value)
        pdf.ln()
        pdf.ln(3)

    return bytes(pdf.output())
