"""Build the proposal-search index for an EXISTING scorecard, without re-scoring (no LLM calls, takes seconds).

  python build_index.py --proposals data/proposals --out output/scorecard.xlsx

Writes output/scorecard.chunks.json, which the dashboard's chat assistant uses to search the full proposals.
Use the same --proposals folder (and --per-file setting) that produced the scorecard, so vendor names match.
"""
import argparse

from vea.config import Settings
from vea.ingest import group_vendors, ingest_files
from vea.proposal_index import chunks_path_for, save_chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposals", default="data/proposals")
    ap.add_argument("--out", default="output/scorecard.xlsx", help="the scorecard workbook this index belongs to")
    ap.add_argument("--per-file", action="store_true")
    a = ap.parse_args()
    s = Settings()
    vendors = group_vendors(a.proposals, per_file=a.per_file)
    if not vendors:
        raise SystemExit(f"No proposals found in {a.proposals}")
    data = {}
    for vendor, paths in vendors.items():
        print(f"== {vendor}")
        chunks = ingest_files(vendor, paths, s.max_chunk_chars, s.overlap_chars)
        if chunks:
            data[vendor] = chunks
    path = chunks_path_for(a.out)
    save_chunks(path, data)
    print(f"\nSaved {sum(len(c) for c in data.values())} chunks for {len(data)} vendor(s): {path}")


if __name__ == "__main__":
    main()
