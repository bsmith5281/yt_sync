import csv
import os
from pathlib import Path

INPUT_CSV = "output/adventure_titles_safe_from_formatted_v14.csv"
OUTPUT_CSV = "output/adventure_titles_to_apply_v14.csv"

def load_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def save_rows(path, rows, fieldnames):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def main():
    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"Cannot find {INPUT_CSV} in {os.getcwd()}")

    rows = load_rows(INPUT_CSV)
    out_rows = []

    for row in rows:
        safe_flag = (row.get("safe_for_auto_apply") or "").strip().upper()
        if safe_flag != "TRUE":
            continue  # skip unsafe rows

        video_id = (row.get("video_id") or "").strip()
        # Use the formatted title/description you already generated
        new_title = (row.get("formattedtitle") or "").strip()
        new_desc = (row.get("formatteddescription") or "").strip()

        if not video_id or not new_title:
            continue  # nothing to apply

        out_rows.append({
            "video_id": video_id,
            "new_title": new_title,
            "new_description": new_desc,
        })

    if not out_rows:
        print("No safe rows found to apply.")
        return

    fieldnames = ["video_id", "new_title", "new_description"]
    save_rows(OUTPUT_CSV, out_rows, fieldnames)
    print(f"Wrote {len(out_rows)} rows to {OUTPUT_CSV} for bulk apply.")

if __name__ == "__main__":
    main()