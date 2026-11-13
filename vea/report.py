"""Write agent results into an Excel scorecard for ANY number of vendors (formulas stay live).

Scorecard layout (built here, not taken from the template):
  col A  row marker: "C" = criterion row, "S" = sub-criterion row (used by the formulas)
  B-F    ID, Category, Criterion, Weight, Mandatory?
  G...   two columns per vendor: Score, Evidence
Each criterion row is followed by its sub-criterion rows. A criterion that has sub-criteria gets a FORMULA
(weighted average of its sub-scores); one without is a plain score. Totals, ranking and category averages
on 'Summary' are formulas too, so any override typed in Excel recalculates.
"""
from openpyxl import load_workbook
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

FIRST_ROW = 6          # first criterion row on 'Scorecard'
FIRST_VENDOR_COL = 7   # column G: first vendor's score column (evidence is the next column)
F = "Arial"
REVIEW_FILL = PatternFill("solid", fgColor="F8CBAD")
HDR_FILL = PatternFill("solid", fgColor="1F3864")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
SUB_FILL = PatternFill("solid", fgColor="F2F2F2")
TOTAL_FILL = PatternFill("solid", fgColor="D9E1F2")
thin = Side(style="thin", color="BFBFBF")
_thick = Side(style="thick", color="E36C09")
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
REVIEW_BORDER = Border(left=_thick, right=_thick, top=_thick, bottom=_thick)


def scorecard_rows(criteria):
    """Row layout shared by the writer and the dashboard reader.
    Returns [(kind, criterion, sub_or_None, row)] where kind is 'C' or 'S'."""
    rows, r = [], FIRST_ROW
    for c in criteria:
        rows.append(("C", c, None, r)); r += 1
        for s in c.subs:
            rows.append(("S", c, s, r)); r += 1
    return rows


def vendor_columns(ws):
    """[(vendor name, score col letter, evidence col letter)] read from row 4 of a generated Scorecard."""
    out, col = [], FIRST_VENDOR_COL
    while ws.cell(4, col).value not in (None, ""):
        out.append((ws.cell(4, col).value, L(col), L(col + 1)))
        col += 2
    return out


def _evidence_text(s):
    flag = "REVIEW: " if s.needs_review else ""
    if s.subscores:
        return f"{flag}Weighted average of {len(s.subscores)} sub-criteria (rows below). {s.review_reason if s.needs_review else ''}".strip()
    src = ", ".join(s.sources) if s.sources else ("p." + ",".join(str(p) for p in s.pages) if s.pages else "-")
    quote = f' "{s.evidence_quote}"' if s.evidence_quote else ""
    return f"{flag}[{src}] {s.justification}{quote}"


def _style_header(ws, row, ncols, start=1):
    for c in ws[row][start - 1:start - 1 + ncols]:
        c.font = Font(name=F, bold=True, color="FFFFFF"); c.fill = HDR_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _body_style(ws, first_row=2):
    for row in ws.iter_rows(min_row=first_row):
        for c in row:
            c.font = Font(name=F, size=10); c.alignment = Alignment(wrap_text=True, vertical="top")


def _build_scorecard(wb, criteria, vendors, scores_by_vendor):
    if "Scorecard" in wb.sheetnames:
        del wb["Scorecard"]
    sc = wb.create_sheet("Scorecard", index=wb.sheetnames.index("SubCriteria") + 1
                         if "SubCriteria" in wb.sheetnames else len(wb.sheetnames))
    sc.sheet_view.showGridLines = False
    sc["B2"] = "Technical Scorecard"
    sc["B2"].font = Font(name=F, size=14, bold=True, color="1F3864")
    sc["B3"] = ("Scores filled by the vendor evaluation agent. Score colour shows the value (red low, green high). "
                "Thick orange border + orange evidence = needs human review (see Agent_Log). "
                "Type in any yellow Score cell to override; criterion scores with sub-criteria are formulas "
                "(override the sub-criterion instead).")
    sc["B3"].font = Font(name=F, size=9)

    layout = scorecard_rows(criteria)
    last = layout[-1][3]
    crit_idx = {c.id: j for j, c in enumerate(criteria)}
    heads = {2: "ID", 3: "Category", 4: "Criterion", 5: "Weight", 6: "Mandatory?"}
    for col, h in heads.items():
        sc.cell(5, col, h)
    for i, v in enumerate(vendors):
        c0 = FIRST_VENDOR_COL + 2 * i
        sc.cell(4, c0, v)
        sc.merge_cells(start_row=4, start_column=c0, end_row=4, end_column=c0 + 1)
        sc.cell(4, c0).font = Font(name=F, bold=True, color="0000FF"); sc.cell(4, c0).fill = INPUT_FILL
        sc.cell(4, c0).alignment = Alignment(horizontal="center")
        sc.cell(5, c0, "Score (0-5)"); sc.cell(5, c0 + 1, "Evidence (file, page / slide / sheet)")
    _style_header(sc, 5, FIRST_VENDOR_COL + 2 * len(vendors) - 1, start=2)

    by_vendor = {v: {s.criterion_id: s for s in scores_by_vendor[v]} for v in vendors}
    for kind, c, sub, r in layout:
        j = crit_idx[c.id]
        if kind == "C":
            vals = {1: "C", 2: f"=Criteria!B{5 + j}", 3: f"=Criteria!C{5 + j}", 4: f"=Criteria!D{5 + j}",
                    5: f"=Criteria!F{5 + j}", 6: f"=Criteria!G{5 + j}"}
        else:
            vals = {1: "S", 2: f"=SubCriteria!B{sub.row}", 3: f"=Criteria!C{5 + j}",
                    4: f'="     - "&SubCriteria!E{sub.row}', 5: f"=SubCriteria!G{sub.row}"}
        for col, v in vals.items():
            cell = sc.cell(r, col, v); cell.font = Font(name=F, size=10, color="808080" if col == 1 else "000000",
                                                         bold=(kind == "C" and col in (2, 4)))
            cell.border = BOX
            if kind == "S":
                cell.fill = SUB_FILL
        sc.cell(r, 5).number_format = "0%"; sc.cell(r, 5).alignment = Alignment(horizontal="center")
        sc.cell(r, 6).alignment = Alignment(horizontal="center")
        sc.cell(r, 1).alignment = Alignment(horizontal="center")

        for i, v in enumerate(vendors):
            scol, ecol = FIRST_VENDOR_COL + 2 * i, FIRST_VENDOR_COL + 2 * i + 1
            parent = by_vendor[v].get(c.id)
            s = None
            if parent is not None:
                s = parent if kind == "C" else next((x for x in parent.subscores if x.criterion_id == sub.id), None)
            sc_cell, ev_cell = sc.cell(r, scol), sc.cell(r, ecol)
            is_formula = kind == "C" and c.subs and parent is not None and parent.subscores
            if is_formula:
                first, lastsub = r + 1, r + len(c.subs)
                cl = L(scol)
                sc_cell.value = (f'=IF(COUNT({cl}{first}:{cl}{lastsub})<{len(c.subs)},"",'
                                 f'ROUND(SUMPRODUCT($E{first}:$E{lastsub},{cl}{first}:{cl}{lastsub}),2))')
                sc_cell.font = Font(name=F, size=10, bold=True)
            elif s is not None:
                sc_cell.value = s.score
                sc_cell.font = Font(name=F, size=10, color="0000FF"); sc_cell.fill = INPUT_FILL
            else:
                sc_cell.font = Font(name=F, size=10, color="0000FF"); sc_cell.fill = INPUT_FILL
            sc_cell.number_format = "0.0"; sc_cell.alignment = Alignment(horizontal="center", vertical="top")
            sc_cell.border = BOX
            ev_cell.border = BOX
            ev_cell.font = Font(name=F, size=10, color="0000FF" if s is not None and not is_formula else "000000")
            ev_cell.alignment = Alignment(wrap_text=True, vertical="top")
            if s is not None:
                ev_cell.value = _evidence_text(s)
                if s.needs_review:
                    sc_cell.border = REVIEW_BORDER
                    ev_cell.fill = REVIEW_FILL

    # totals
    t = last + 2
    labels = ["Weighted score (out of 5)", "Weighted score (%)", "Criteria not yet scored",
              "Mandatory criteria failed", "Mandatory status"]
    for k, lab in enumerate(labels):
        c = sc.cell(t + k, 4, lab); c.font = Font(name=F, size=10, bold=True); c.fill = TOTAL_FILL
    rng = lambda col: f"{col}{FIRST_ROW}:{col}{last}"
    mark, wts, man = (f"$A${FIRST_ROW}:$A${last}", f"$E${FIRST_ROW}:$E${last}", f"$F${FIRST_ROW}:$F${last}")
    totals_rows = {}
    for i, v in enumerate(vendors):
        cl = L(FIRST_VENDOR_COL + 2 * i)
        f = {
            0: f'=SUMPRODUCT(({mark}="C")*{wts},{rng(cl)})',
            1: f"={cl}{t}/5",
            2: f'=SUMPRODUCT(({mark}="C")*({rng(cl)}=""))',
            3: f'=SUMPRODUCT(({mark}="C")*({man}="Yes")*({rng(cl)}<>"")*({rng(cl)}<Summary!$C$5))',
            4: f'=IF({cl}{t + 2}>0,"Incomplete",IF({cl}{t + 3}>0,"Fail","Pass"))',
        }
        for k, formula in f.items():
            c = sc.cell(t + k, FIRST_VENDOR_COL + 2 * i, formula)
            c.font = Font(name=F, size=10, bold=True); c.alignment = Alignment(horizontal="center")
        sc.cell(t, FIRST_VENDOR_COL + 2 * i).number_format = "0.00"
        sc.cell(t + 1, FIRST_VENDOR_COL + 2 * i).number_format = "0.0%"
        # conditional formats
        sc.conditional_formatting.add(f"{cl}{FIRST_ROW}:{cl}{last}",
            ColorScaleRule(start_type="num", start_value=0, start_color="F8696B", mid_type="num", mid_value=3,
                           mid_color="FFEB84", end_type="num", end_value=5, end_color="63BE7B"))
        st = f"{cl}{t + 4}"
        sc.conditional_formatting.add(st, CellIsRule(operator="equal", formula=['"Pass"'], font=Font(bold=True, color="006100")))
        sc.conditional_formatting.add(st, CellIsRule(operator="equal", formula=['"Fail"'], font=Font(bold=True, color="9C0006")))
        sc.conditional_formatting.add(st, CellIsRule(operator="equal", formula=['"Incomplete"'], font=Font(bold=True, color="7F6000")))
        totals_rows = {"score": t, "pct": t + 1, "status": t + 4}

    for col, w in {"A": 3, "B": 7, "C": 14, "D": 36, "E": 9, "F": 11}.items():
        sc.column_dimensions[col].width = w
    for i in range(len(vendors)):
        sc.column_dimensions[L(FIRST_VENDOR_COL + 2 * i)].width = 12
        sc.column_dimensions[L(FIRST_VENDOR_COL + 2 * i + 1)].width = 44
    sc.freeze_panes = sc.cell(FIRST_ROW, FIRST_VENDOR_COL)
    return layout, last, totals_rows


def _build_summary(wb, criteria, vendors, last, totals_rows):
    sm = wb["Summary"]
    for row in sm.iter_rows(min_row=8, max_row=max(sm.max_row, 8) + 5):
        for c in row:
            c.value = None; c.fill = PatternFill(); c.border = Border()
    sm.conditional_formatting = type(sm.conditional_formatting)()
    heads = ["Vendor", "Weighted score (/5)", "Score %", "Mandatory status", "Rank (passing vendors)", "Recommendation"]
    for i, h in enumerate(heads):
        sm.cell(8, 2 + i, h)
    _style_header(sm, 8, 6, start=2)
    n = len(vendors)
    r1, rN = 9, 8 + n
    for i in range(n):
        r = 9 + i
        cl = L(FIRST_VENDOR_COL + 2 * i)
        row = {2: f"=Scorecard!{cl}4", 3: f"=Scorecard!{cl}{totals_rows['score']}",
               4: f"=Scorecard!{cl}{totals_rows['pct']}", 5: f"=Scorecard!{cl}{totals_rows['status']}",
               6: f'=IF(E{r}="Pass",COUNTIFS($E${r1}:$E${rN},"Pass",$C${r1}:$C${rN},">"&C{r})+1,"-")',
               7: (f'=IF(E{r}="Incomplete","Complete scoring first",IF(E{r}="Fail","Rejected - mandatory criterion not met",'
                   f'IF(F{r}<=$C$6,"Shortlist","Not shortlisted")))')}
        for col, v in row.items():
            c = sm.cell(r, col, v); c.font = Font(name=F, size=10); c.border = BOX
            c.alignment = Alignment(horizontal="left" if col in (2, 7) else "center")
        sm.cell(r, 3).number_format = "0.00"; sm.cell(r, 4).number_format = "0.0%"
    sm.conditional_formatting.add(f"G{r1}:G{rN}", FormulaRule(formula=[f'G{r1}="Shortlist"'], font=Font(bold=True, color="006100")))
    sm.conditional_formatting.add(f"G{r1}:G{rN}", FormulaRule(formula=[f'LEFT(G{r1},8)="Rejected"'], font=Font(bold=True, color="9C0006")))

    t0 = rN + 2
    sm.cell(t0, 2, "Average score by category (weighted, out of 5)").font = Font(name=F, bold=True, color="1F3864")
    sm.cell(t0 + 1, 2, "Category")
    for i in range(n):
        sm.cell(t0 + 1, 3 + i, f"=B{9 + i}")
    _style_header(sm, t0 + 1, 1 + n, start=2)
    cats = list(dict.fromkeys(c.category for c in criteria))
    mark, cat_rng, wts = (f"Scorecard!$A${FIRST_ROW}:$A${last}", f"Scorecard!$C${FIRST_ROW}:$C${last}",
                          f"Scorecard!$E${FIRST_ROW}:$E${last}")
    for k, cat in enumerate(cats):
        r = t0 + 2 + k
        sm.cell(r, 2, cat).font = Font(name=F, size=10, bold=True, color="1F3864")
        for i in range(n):
            cl = L(FIRST_VENDOR_COL + 2 * i)
            f = (f'=IFERROR(SUMPRODUCT(({mark}="C")*({cat_rng}=$B{r})*{wts},Scorecard!{cl}${FIRST_ROW}:{cl}${last})'
                 f'/SUMIFS({wts},{cat_rng},$B{r},{mark},"C"),"")')
            c = sm.cell(r, 3 + i, f); c.font = Font(name=F, size=10); c.number_format = "0.0"
            c.alignment = Alignment(horizontal="center")
    if cats:
        sm.conditional_formatting.add(f"C{t0 + 2}:{L(2 + n)}{t0 + 1 + len(cats)}",
            ColorScaleRule(start_type="num", start_value=0, start_color="F8696B", mid_type="num", mid_value=3,
                           mid_color="FFEB84", end_type="num", end_value=5, end_color="63BE7B"))
    for col, w in {"B": 28, "C": 20, "D": 12}.items():
        sm.column_dimensions[col].width = max(w, sm.column_dimensions[col].width or 0)
    for i in range(n):
        cl = L(3 + i)
        sm.column_dimensions[cl].width = max(sm.column_dimensions[cl].width or 0, 20)
    sm.column_dimensions["G"].width = max(sm.column_dimensions["G"].width or 0, 38)


def _build_costs(wb, costs):
    """Costs sheet: information only, never read by the scoring."""
    ws = wb.create_sheet("Costs")
    cur = costs.get("currency") or ""
    ws.append(["Vendor", "Found", "Price as written", "Written currency", "Quoted as", "Contract years",
               "Total over contract", f"Price in {cur} (as quoted)", f"Total in {cur}", "Exchange rates dated",
               "Price quote", "Contract length quote", "Source", "Status", "Note"])
    basis = {"total": "Total", "per_year": "Per year", "per_month": "Per month", "other": "Other basis"}
    for r in costs.get("vendors", []):
        ws.append([r.get("vendor"), "Yes" if r.get("found") else "No", r.get("amount"), r.get("written_currency"),
                   basis.get(r.get("basis"), ""), r.get("term_years"), r.get("total"), r.get("converted"),
                   r.get("converted_total"), costs.get("rates_date"), r.get("quote"), r.get("term_quote"),
                   r.get("source"), r.get("status"), r.get("note")])
    if costs.get("error"):
        ws.append([f"The costs could not be read: {costs['error']}"])
    _style_header(ws, 1, 15); _body_style(ws)
    for row in ws.iter_rows(min_row=2):
        for i in (2, 6, 7, 8):                             # number columns: C, G, H, I
            row[i].number_format = "#,##0.00"
    for col, w in zip("ABCDEFGHIJKLMNO", [20, 8, 16, 11, 11, 10, 18, 20, 18, 12, 40, 28, 14, 14, 50]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    ws["A" + str(ws.max_row + 2)] = "Information only: costs do not affect any score."


def write_workbook(template_path, out_path, criteria, scores_by_vendor, summaries, targets=None, files=None, costs=None):
    wb = load_workbook(template_path)  # keeps Criteria / SubCriteria / Guide sheets and their validation
    vendors = list(scores_by_vendor)
    layout, last, totals_rows = _build_scorecard(wb, criteria, vendors, scores_by_vendor)
    _build_summary(wb, criteria, vendors, last, totals_rows)

    # ---- audit trail
    for name in ("Agent_Log", "Vendor_Summaries", "Targets", "Vendor_Files"):
        if name in wb.sheetnames:
            del wb[name]
    log = wb.create_sheet("Agent_Log")
    log.append(["Vendor", "ID", "Criterion", "Level", "Score", "Confidence", "Needs review", "Review reason",
                "Sources", "Justification", "Evidence quote"])
    for v in vendors:
        for s in scores_by_vendor[v]:
            for x, level in [(s, "Criterion")] + [(sub, "Sub-criterion") for sub in s.subscores]:
                log.append([v, x.criterion_id, x.criterion, level, x.score, x.confidence,
                            "Yes" if x.needs_review else "No", x.review_reason, "; ".join(x.sources),
                            x.justification, x.evidence_quote])
    _style_header(log, 1, 11); _body_style(log)
    for col, w in zip("ABCDEFGHIJK", [20, 7, 28, 13, 7, 11, 12, 34, 30, 55, 55]):
        log.column_dimensions[col].width = w
    log.freeze_panes = "A2"

    sm = wb.create_sheet("Vendor_Summaries")
    sm.append(["Vendor", "Overview", "Strengths", "Gaps", "Risks"])
    for v in vendors:
        s = summaries[v]
        sm.append([v, s["overview"], "\n".join(f"- {x}" for x in s["strengths"]),
                   "\n".join(f"- {x}" for x in s["gaps"]), "\n".join(f"- {x}" for x in s["risks"])])
    _style_header(sm, 1, 5); _body_style(sm)
    for col, w in zip("ABCDE", [20, 45, 40, 40, 40]):
        sm.column_dimensions[col].width = w

    tg = wb.create_sheet("Targets")
    tg.append(["ID", "Criterion", "Method", "Target", "Unit", "Source", "RFP quote"])
    crit_by_id = {c.id: c for c in criteria}
    for cid, t in (targets or {}).items():
        tg.append([cid, crit_by_id[cid].name, crit_by_id[cid].method, t["value"], t["unit"], t["source"], t["quote"]])
    _style_header(tg, 1, 7); _body_style(tg)
    for col, w in zip("ABCDEFG", [6, 30, 18, 10, 10, 26, 60]):
        tg.column_dimensions[col].width = w

    vf = wb.create_sheet("Vendor_Files")
    vf.append(["Vendor", "File", "Format"])
    for v in vendors:
        for name in (files or {}).get(v, []):
            vf.append([v, name, name.rsplit(".", 1)[-1].upper() if "." in name else ""])
    _style_header(vf, 1, 3); _body_style(vf)
    for col, w in zip("ABC", [24, 44, 10]):
        vf.column_dimensions[col].width = w
    if "Costs" in wb.sheetnames:
        del wb["Costs"]
    if costs and (costs.get("vendors") or costs.get("error")):
        _build_costs(wb, costs)
    wb.save(out_path)
