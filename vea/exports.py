"""Download formats for the evaluation results.

Every function takes the two dicts the dashboard already has:
    res = load_results(...)   (raw workbook data)
    out = compute(res, ...)   (ranking after any what-if settings)
and returns bytes, so Streamlit's download_button and a future FastAPI endpoint can both use them.

    export(res, out, "pdf")  ->  (bytes, filename, mime_type)
"""
import io
import json
from datetime import datetime

import pandas as pd

FORMATS = {   # key: (label, extension, mime type)
    "pdf":  ("PDF report", "pdf", "application/pdf"),
    "docx": ("Word report", "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "csv":  ("CSV (ranking)", "csv", "text/csv"),
    "json": ("JSON (all data)", "json", "application/json"),
    "md":   ("Markdown report", "md", "text/markdown"),
}


# ---------------------------------------------------------------- shared helpers
def _clean(v):
    """None / NaN / blank -> '-'; everything else -> str."""
    return "-" if v is None or v != v or str(v).strip() == "" else str(v)


def _num(v, nd=2):
    try:
        return "-" if v is None or v != v else f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return _clean(v)


def _settings(out):
    return (f"Mandatory minimum score: {out['min_score']:g}   |   Shortlist size: {out['shortlist_n']}")


def _ranking_rows(out):
    s = out["summary"]
    head = ["Rank", "Vendor", "Weighted score (/5)", "Score %", "Status", "Recommendation", "Failed mandatory"]
    rows = []
    for _, r in s.iterrows():
        rank = "-" if r["Rank"] is None or r["Rank"] != r["Rank"] else str(int(r["Rank"]))
        rows.append([rank, _clean(r["Vendor"]), _num(r["Weighted score"]), _num(r["Score %"], 1),
                     _clean(r["Status"]), _clean(r["Recommendation"]), _clean(r["Failed mandatory"])])
    return head, rows


def _matrix(df, index_name):
    """DataFrame (index x vendors) -> header + string rows."""
    head = [index_name] + [str(c) for c in df.columns]
    rows = [[str(i)] + [_num(x, 1) for x in df.loc[i]] for i in df.index]
    return head, rows


def _vendor_detail(res, out, vendor):
    """Per-vendor facts: summary text, scores (criteria + sub-criteria) and flagged items."""
    scores = res["scores"].get(vendor, {})
    subs = res["sub_scores"].get(vendor, {})
    rows = []
    for c in out["criteria"]:
        rows.append([c.id, c.name, _num(scores.get(c.id), 1)])
        for s in c.subs:
            rows.append([s.id, "   " + s.name, _num(subs.get(s.id), 0)])
    sm = res["summaries"]
    summ = {}
    if not sm.empty and vendor in set(sm["Vendor"]):
        r = sm[sm["Vendor"] == vendor].iloc[0]
        summ = {k: _clean(r[k]) for k in ("Overview", "Strengths", "Gaps", "Risks")}
    fl = out["flagged"]
    flagged = []
    if not fl.empty:
        for _, r in fl[fl["Vendor"] == vendor].iterrows():
            flagged.append([_clean(r["ID"]), _clean(r["Criterion"]), _num(r["Score"], 0),
                            _clean(r["Review reason"])])
    files = []
    if not res["files"].empty:
        files = res["files"][res["files"]["Vendor"] == vendor]["File"].tolist()
    return {"summary": summ, "scores": rows, "flagged": flagged, "files": files}


def _title():
    return "Vendor Technical Evaluation Report"


def _stamp():
    return datetime.now().strftime("%d %b %Y, %H:%M")


# ---------------------------------------------------------------- who was selected / rejected, and why
# (background, text) colour per status: Pass = green, Fail = red, Incomplete = grey.
_STATUS = {"Pass": ("#C6EFCE", "#006100"), "Fail": ("#FFC7CE", "#9C0006"), "Incomplete": ("#EDEDED", "#404040")}


def _g(v):
    """Score for text: 4.0 -> '4', 3.25 -> '3.25'."""
    try:
        return f"{round(float(v), 2):g}"
    except (TypeError, ValueError):
        return "n/a"


def _first_line(res, vendor, col, limit=170):
    sm = res["summaries"]
    if sm.empty or vendor not in set(sm["Vendor"]) or col not in sm.columns:
        return ""
    txt = str(sm[sm["Vendor"] == vendor].iloc[0][col] or "").strip()
    line = next((x.strip(" -*\u2022\t") for x in txt.split("\n") if x.strip(" -*\u2022\t")), "")
    return line if len(line) <= limit else line[:limit].rsplit(" ", 1)[0] + "..."


def _decision(res, out):
    """One entry per vendor: group (selected / qualified / rejected / incomplete), a one-line headline and the
    reasons. Everything comes from the computed ranking and scores, so it always matches the tables."""
    crit, min_s, n_short = out["criteria"], out["min_score"], out["shortlist_n"]
    mand = [c for c in crit if c.mandatory]
    summ = out["summary"]
    n_pass = int((summ["Status"] == "Pass").sum())
    entries = []
    for _, r in summ.iterrows():
        v, status = r["Vendor"], r["Status"]
        sc = res["scores"].get(v, {})
        have = [(c, sc[c.id]) for c in crit if sc.get(c.id) is not None]
        e = {"vendor": v, "status": status, "score": r["Weighted score"], "pct": r["Score %"],
             "rank": None if r["Rank"] is None or r["Rank"] != r["Rank"] else int(r["Rank"]), "reasons": []}
        if status == "Pass":
            e["group"] = "selected" if r["Recommendation"] == "Shortlist" else "qualified"
            if e["group"] == "selected":
                e["headline"] = f"SELECTED - rank {e['rank']} of {n_pass}, shortlisted"
                e["reasons"].append(f"Weighted score {_num(e['score'])}/5 ({_num(e['pct'], 1)}%), "
                                    f"rank {e['rank']} of {n_pass} vendors that passed.")
            else:
                e["headline"] = f"PASSED - rank {e['rank']} of {n_pass}, not shortlisted"
                e["reasons"].append(f"Weighted score {_num(e['score'])}/5 ({_num(e['pct'], 1)}%). Only the top "
                                    f"{n_short} are shortlisted.")
            if mand:
                e["reasons"].append(f"Met all {len(mand)} mandatory criteria (minimum {_g(min_s)}/5).")
            top = sorted(have, key=lambda x: (-x[1], -x[0].weight))[:3]
            if top:
                e["reasons"].append("Strongest: " + "; ".join(f"{c.id} {c.name} ({_g(x)})" for c, x in top) + ".")
            weak = [(c, x) for c, x in sorted(have, key=lambda x: (x[1], -x[0].weight)) if x < min_s][:2]
            if weak:
                e["reasons"].append("Weaker areas: " + "; ".join(f"{c.id} {c.name} ({_g(x)})" for c, x in weak) + ".")
            note = _first_line(res, v, "Strengths")
            if note:
                e["reasons"].append("Evaluator summary: " + note)
        elif status == "Fail":
            e["group"] = "rejected"
            bad = [(c, sc.get(c.id)) for c in mand if sc.get(c.id) is not None and sc[c.id] < min_s]
            e["headline"] = f"REJECTED - failed {len(bad)} mandatory criterion" + ("" if len(bad) == 1 else "s")
            for c, x in bad:
                e["reasons"].append(f"{c.id} {c.name}: scored {_g(x)}/5, below the mandatory minimum of {_g(min_s)}/5.")
            e["reasons"].append(f"Weighted score was {_num(e['score'])}/5, but failing a mandatory criterion rejects "
                                "the vendor whatever the total.")
            note = _first_line(res, v, "Gaps")
            if note:
                e["reasons"].append("Evaluator summary of gaps: " + note)
        else:
            e["group"] = "incomplete"
            e["headline"] = "INCOMPLETE - not ranked"
            e["reasons"].append("Some criteria have no score yet, so the vendor cannot be ranked.")
        entries.append(e)
    return entries


_GROUPS = [("selected", "Selected (shortlisted)"), ("qualified", "Passed all mandatory criteria, not shortlisted"),
           ("rejected", "Rejected"), ("incomplete", "Not ranked (incomplete scoring)")]


# ---------------------------------------------------------------- CSV / JSON / Markdown
def to_csv(res, out):
    head, rows = _ranking_rows(out)
    return pd.DataFrame(rows, columns=head).to_csv(index=False).encode("utf-8")


def to_json(res, out):
    head, rows = _ranking_rows(out)
    data = {
        "generated": _stamp(),
        "settings": {"min_score": out["min_score"], "shortlist_n": out["shortlist_n"]},
        "ranking": [dict(zip(head, r)) for r in rows],
        "decision": [{k: (None if (isinstance(x, float) and x != x) else x) for k, x in e.items()}
                     for e in _decision(res, out)],
        "criteria_scores": json.loads(out["criteria_scores"].to_json()),
        "category_scores": json.loads(out["categories"].to_json()),
        "vendors": {v: _vendor_detail(res, out, v) for v in res["scores"]},
    }
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def _md_table(head, rows):
    esc = lambda x: str(x).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(map(esc, head)) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(map(esc, r)) + " |" for r in rows]
    return "\n".join(lines)


def to_md(res, out):
    h, r = _ranking_rows(out)
    parts = [f"# {_title()}", f"_Generated {_stamp()}. {_settings(out)}_", "", "## Decision summary"]
    dec = _decision(res, out)
    for key, title in _GROUPS:
        grp = [e for e in dec if e["group"] == key]
        if grp:
            parts += [f"### {title}"]
            for e in grp:
                parts += [f"- **{e['vendor']}** - {e['headline'].replace('SELECTED', '**SELECTED**').replace('REJECTED', '**REJECTED**')}"] + [f"  - {x}" for x in e["reasons"]]
            parts.append("")
    parts += ["## Ranking", _md_table(h, r), "",
             "## Average score by category", _md_table(*_matrix(out["categories"], "Category")), "",
             "## Scores by criterion", _md_table(*_matrix(out["criteria_scores"], "Criterion")), ""]
    for v in res["scores"]:
        d = _vendor_detail(res, out, v)
        parts += [f"## {v}"]
        if d["files"]:
            parts.append("_Files read: " + ", ".join(d["files"]) + "_")
        for k, t in d["summary"].items():
            parts += [f"**{k}**", t, ""]
        parts += [_md_table(["ID", "Criterion", "Score"], d["scores"]), ""]
        if d["flagged"]:
            parts += ["**Flagged for human review**",
                      _md_table(["ID", "Criterion", "Score", "Reason"], d["flagged"]), ""]
    return "\n".join(parts).encode("utf-8")


# ---------------------------------------------------------------- PDF
import re as _re_tint


def _tint_html(s):
    """PDF: colour only the status words (green = selected/shortlist, red = rejected), bold."""
    s = _re_tint.sub(r"\b(SELECTED|Shortlist)\b", r'<font color="#006100"><b>\1</b></font>', s, count=1)
    s = _re_tint.sub(r"\b(REJECTED|Rejected)\b", r'<font color="#9C0006"><b>\1</b></font>', s, count=1)
    return s


def to_pdf(res, out):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)
    from xml.sax.saxutils import escape

    def safe(t):   # the built-in PDF fonts only cover Latin-1 / cp1252
        t = str(t).replace("\u2265", ">=").replace("\u2264", "<=").replace("\u2192", "->").replace("\u2713", "yes")
        return escape(t.encode("cp1252", "replace").decode("cp1252"))

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    cell = ParagraphStyle("c", parent=body, fontSize=8, leading=10)
    cellh = ParagraphStyle("ch", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=20, textColor=colors.HexColor("#1F3864"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=colors.HexColor("#1F3864"), spaceBefore=10)
    small = ParagraphStyle("s", parent=body, fontSize=8, textColor=colors.grey)

    buf = io.BytesIO()
    page = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=_title())
    width = page[0] - 30 * mm

    def table(head, rows, col_w=None, status_col=None):
        def one(i, c):
            if status_col == i and str(c) in _STATUS:
                return Paragraph(f'<font color="{_STATUS[str(c)][1]}"><b>{safe(c)}</b></font>', cell)
            return Paragraph(_tint_html(safe(c)), cell)
        data = [[Paragraph(safe(h), cellh) for h in head]] + [[one(i, c) for i, c in enumerate(r)] for r in rows]
        if col_w:
            tot = sum(col_w)
            col_w = [width * w / tot for w in col_w]
        t = Table(data, colWidths=col_w, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BFBFBF")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FA")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        if status_col is not None:                       # Pass rows green, Fail rows red
            for i, r in enumerate(rows, start=1):
                if str(r[status_col]) in _STATUS:
                    t.setStyle(TableStyle([("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FFFFFF"))]))
        return t

    def banner(e):
        bg, fg = _STATUS.get(e["status"], _STATUS["Incomplete"])
        st = ParagraphStyle("bn", parent=body, fontSize=11, leading=14, textColor=colors.black, fontName="Helvetica-Bold")
        t = Table([[Paragraph(_tint_html(safe(e["headline"])), st)]], colWidths=[width])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.white),
                               ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#999999")),
                               ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
        return t

    def summary_box(title, group_entries):
        bg, fg = _STATUS.get(group_entries[0]["status"], _STATUS["Incomplete"])
        head_st = ParagraphStyle("bh", parent=body, fontName="Helvetica-Bold", textColor=colors.black, fontSize=10)
        rows_ = [[Paragraph(safe(title), head_st)]]
        for e in group_entries:
            txt = f"<b>{safe(e['vendor'])}</b> - {_tint_html(safe(e['headline']))}" + "".join(
                f"<br/>&bull; {safe(x)}" for x in e["reasons"])
            rows_.append([Paragraph(txt, ParagraphStyle("bt", parent=body, fontSize=9, leading=12))])
        t = Table(rows_, colWidths=[width])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                               ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                               ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#999999")),
                               ("LINEBELOW", (0, 1), (-1, -2), 0.4, colors.HexColor("#CCCCCC")),
                               ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        return t

    dec = _decision(res, out)
    dec_by_vendor = {e["vendor"]: e for e in dec}

    def pagenum(canvas, d):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(page[0] - 15 * mm, 8 * mm, f"Page {d.page}")
        canvas.drawString(15 * mm, 8 * mm, _title())

    s = [Paragraph(_title(), h1), Paragraph(safe(f"Generated {_stamp()}.  {_settings(out)}"), small), Spacer(1, 6)]
    s.append(Paragraph("Decision summary", h2))
    for key, title in _GROUPS:
        grp = [e for e in dec if e["group"] == key]
        if grp:
            s += [summary_box(title, grp), Spacer(1, 6)]
    h, r = _ranking_rows(out)
    s += [Paragraph("Ranking", h2), table(h, r, [5, 16, 10, 8, 8, 28, 14], status_col=4), Spacer(1, 4),
          Paragraph("Weighted score = sum of (weight x score), out of 5. A vendor below the minimum score on any "
                    "mandatory criterion is rejected.", small)]
    s += [Paragraph("Average score by category", h2), table(*_matrix(out["categories"], "Category"))]
    s += [Paragraph("Scores by criterion", h2), table(*_matrix(out["criteria_scores"], "Criterion"))]

    for v in res["scores"]:
        d = _vendor_detail(res, out, v)
        s += [PageBreak(), Paragraph(safe(v), h1), banner(dec_by_vendor[v]), Spacer(1, 4)]
        if d["files"]:
            s.append(Paragraph(safe("Files read: " + ", ".join(d["files"])), small))
        for k, t in d["summary"].items():
            s.append(Paragraph(f"<b>{k}</b>", body))
            for line in str(t).split("\n"):
                s.append(Paragraph(safe(line), body))
            s.append(Spacer(1, 3))
        s += [Paragraph("Scores", h2), table(["ID", "Criterion / sub-criterion", "Score"], d["scores"], [6, 40, 8])]
        if d["flagged"]:
            s += [Paragraph("Flagged for human review", h2),
                  table(["ID", "Criterion", "Score", "Reason"], d["flagged"], [6, 25, 6, 50])]
    doc.build(s, onFirstPage=pagenum, onLaterPages=pagenum)
    return buf.getvalue()


# ---------------------------------------------------------------- Word
def to_docx(res, out):
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Pt

    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = sec.page_height, sec.page_width
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)

    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    def shade(cell, hex_):
        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("w:shd")):
            tcPr.remove(old)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), hex_.lstrip("#"))
        tcPr.append(shd)

    def rgb(hex_):
        h_ = hex_.lstrip("#")
        return RGBColor(int(h_[0:2], 16), int(h_[2:4], 16), int(h_[4:6], 16))

    def banner(e):
        bg, fg = _STATUS.get(e["status"], _STATUS["Incomplete"])
        t = doc.add_table(rows=1, cols=1)
        t.style = "Table Grid"
        c = t.rows[0].cells[0]
        shade(c, "#FFFFFF")
        run = c.paragraphs[0].add_run(e["headline"])
        run.bold, run.font.size, run.font.color.rgb = True, Pt(12), RGBColor(0, 0, 0)
        doc.add_paragraph()

    def summary_box(title, group_entries):
        bg, fg = _STATUS.get(group_entries[0]["status"], _STATUS["Incomplete"])
        t = doc.add_table(rows=1, cols=1)
        t.style = "Table Grid"
        c = t.rows[0].cells[0]
        shade(c, "#EEEEEE")
        run = c.paragraphs[0].add_run(title)
        run.bold, run.font.color.rgb = True, RGBColor(0, 0, 0)
        for e in group_entries:
            c = t.add_row().cells[0]
            shade(c, "#FFFFFF")
            p = c.paragraphs[0]
            p.add_run(e["vendor"]).bold = True
            import re as _re
            _hl = " - " + e["headline"]
            _m = _re.search(r"\b(SELECTED|REJECTED)\b", _hl)
            if _m:
                p.add_run(_hl[:_m.start()]).bold = True
                _r = p.add_run(_m.group(1))
                _r.bold = True
                _r.font.color.rgb = RGBColor(0, 97, 0) if _m.group(1) == "SELECTED" else RGBColor(156, 0, 6)
                p.add_run(_hl[_m.end():]).bold = True
            else:
                p.add_run(_hl).bold = True
            for x in e["reasons"]:
                q = c.add_paragraph("\u2022 " + x)
                q.paragraph_format.space_after = Pt(0)
        doc.add_paragraph()

    def table(head, rows, status_col=None):
        t = doc.add_table(rows=1, cols=len(head))
        t.style = "Light Grid Accent 1"
        for i, x in enumerate(head):
            t.rows[0].cells[i].text = str(x)
            for run in t.rows[0].cells[i].paragraphs[0].runs:
                run.bold = True
        for row in rows:
            cells = t.add_row().cells
            for i, x in enumerate(row):
                cells[i].text = str(x)
                if status_col is not None and str(x).split(" ")[0] in ("Shortlist", "Rejected"):
                    _first, _, _rest = str(x).partition(" ")
                    _par = cells[i].paragraphs[0]
                    for _old in list(_par.runs):
                        _old._r.getparent().remove(_old._r)
                    _a = _par.add_run(_first)
                    _a.bold = True
                    _a.font.color.rgb = RGBColor(0, 97, 0) if _first == "Shortlist" else RGBColor(156, 0, 6)
                    if _rest:
                        _par.add_run(" " + _rest)
            if status_col is not None and str(row[status_col]) in _STATUS:      # Pass rows green, Fail rows red
                bg, fg = _STATUS[str(row[status_col])]
                for run in cells[status_col].paragraphs[0].runs:
                    run.bold, run.font.color.rgb = True, rgb(fg)
        for row in t.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(9)
        doc.add_paragraph()

    doc.add_heading(_title(), 0)
    doc.add_paragraph(f"Generated {_stamp()}.  {_settings(out)}")
    dec = _decision(res, out)
    dec_by_vendor = {e["vendor"]: e for e in dec}
    doc.add_heading("Decision summary", 1)
    for key, title in _GROUPS:
        grp = [e for e in dec if e["group"] == key]
        if grp:
            summary_box(title, grp)
    doc.add_heading("Ranking", 1)
    table(*_ranking_rows(out), status_col=4)
    doc.add_paragraph("Weighted score = sum of (weight x score), out of 5. A vendor below the minimum score on any "
                      "mandatory criterion is rejected.")
    doc.add_heading("Average score by category", 1)
    table(*_matrix(out["categories"], "Category"))
    doc.add_heading("Scores by criterion", 1)
    table(*_matrix(out["criteria_scores"], "Criterion"))
    for v in res["scores"]:
        d = _vendor_detail(res, out, v)
        doc.add_page_break()
        doc.add_heading(str(v), 1)
        banner(dec_by_vendor[v])
        if d["files"]:
            doc.add_paragraph().add_run("Files read: " + ", ".join(d["files"])).italic = True
        for k, t in d["summary"].items():
            p = doc.add_paragraph()
            p.add_run(k + "\n").bold = True
            p.add_run(t)
        doc.add_heading("Scores", 2)
        table(["ID", "Criterion / sub-criterion", "Score"], d["scores"])
        if d["flagged"]:
            doc.add_heading("Flagged for human review", 2)
            table(["ID", "Criterion", "Score", "Reason"], d["flagged"])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- single entry point
_BUILDERS = {"pdf": to_pdf, "docx": to_docx, "csv": to_csv, "json": to_json, "md": to_md}


def export(res, out, fmt):
    """Returns (bytes, file name, mime type). fmt is one of FORMATS."""
    if fmt not in _BUILDERS:
        raise ValueError(f"Unknown format {fmt!r}. Choose from: {', '.join(FORMATS)}")
    _, ext, mime = FORMATS[fmt]
    return _BUILDERS[fmt](res, out), f"vendor_evaluation.{ext}", mime
