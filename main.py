"""Vendor technical evaluation agent.

Usage:
  python main.py --proposals data/proposals --out output/scorecard.xlsx           # real LLM
  python main.py --proposals data/proposals --out output/scorecard.xlsx --mock    # offline test

Proposals folder: any number of vendors, any mix of PDF / Word / PowerPoint / Excel / TXT.
  data/proposals/Orion/            <- a sub-folder = ONE vendor, however many files / formats it holds
  data/proposals/Orion__tech.pdf   <- loose files sharing the prefix before "__" = the same vendor
  data/proposals/Alpha_Systems.docx<- any other loose file = its own vendor
  --per-file                       <- every file is its own vendor (e.g. to compare how formats are read)
"""
import argparse

from vea.pipeline import EvaluationError, run_evaluation


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposals", default="data/proposals")
    ap.add_argument("--template", default="template/scorecard_template.xlsx")
    ap.add_argument("--out", default="output/scorecard.xlsx")
    ap.add_argument("--samples", type=int, default=1, help="LLM runs per criterion (median; flags disagreement)")
    ap.add_argument("--rfp", default=None, help="RFP file (.pdf/.docx/.txt): targets for numeric criteria are read from it")
    ap.add_argument("--per-file", action="store_true", help="treat every file as its own vendor")
    ap.add_argument("--yes", action="store_true", help="accept extracted targets without asking")
    ap.add_argument("--mock", action="store_true", help="offline mode, no API key or model downloads")
    a = ap.parse_args()
    try:
        result = run_evaluation(a.proposals, a.out, rfp=a.rfp, template=a.template, samples=a.samples,
                                per_file=a.per_file, mock=a.mock, confirm=not a.yes,
                                progress=lambda message, done, total: print(message))
    except EvaluationError as e:
        raise SystemExit(str(e))

    print("\n== Ranking")
    rows = result["rows"]
    for r in sorted(rows, key=lambda r: (r["rank"] is None, r["rank"] or 0)):
        print(f"  {r['vendor']:<22} {r['weighted_score']:.2f}/5  {r['status']:<5} {r['recommendation']}")
    print(f"\nSaved: {a.out}  (open in Excel; orange scores need review)")


if __name__ == "__main__":
    main()
