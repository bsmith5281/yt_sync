import csv
import os
from pathlib import Path

# Input: your existing formatted CSV (the one you shared)
INPUT_CSV = "output/adventure_titles_formatted_v14.csv"

# Output: NEW file with safety flags; does not overwrite anything
OUTPUT_CSV = "output/adventure_titles_safe_from_formatted_v14.csv"


def load_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_rows(path, rows, fieldnames):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def is_trusted_meta(row):
    """
    Safe if the old metadata validator already said this row is a verified match
    AND the reason includes the meta_pair_directory_validated signal.
    """
    status = (row.get("verification_status") or "").strip()
    reason = (row.get("verification_reason") or "").strip()
    if status != "verified_match":
        return False
    return (
        "current_title_matches_trusted_matchup" in reason
        and "meta_pair_directory_validated" in reason
    )


def main():
    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"Cannot find {INPUT_CSV} in {os.getcwd()}")

    rows = load_rows(INPUT_CSV)
    out_rows = []

    for row in rows:
        safe = is_trusted_meta(row)

        new_row = dict(row)
        new_row["safe_for_auto_apply"] = "TRUE" if safe else "FALSE"
        new_row["safety_source"] = "trusted_meta" if safe else "unresolved"
        new_row["safety_reason"] = (
            new_row.get("verification_reason", "")
            if safe
            else "not_verified_match_or_not_meta_validated"
        )

        out_rows.append(new_row)

    # Original columns plus the 3 new safety columns
    fieldnames = list(rows[0].keys()) + [
        "safe_for_auto_apply",
        "safety_source",
        "safety_reason",
    ]

    save_rows(OUTPUT_CSV, out_rows, fieldnames)
    print(f"Wrote {OUTPUT_CSV} with safe_for_auto_apply flags.")


if __name__ == "__main__":
    main()