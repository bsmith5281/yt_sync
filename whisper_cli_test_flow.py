import csv

INPUT = "adventure_metadata_validated.csv"
OUTPUT = "whisper_cli_test_flow.csv"

rows = []
with open(INPUT, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for r in reader:
        rows.append(r)

def is_target(r):
    family = (r.get("title_family") or r.get("titlefamily") or "").lower()
    return "default_timestamp" in family or "contains_timestamp" in family

test_rows = [r for r in rows if is_target(r)][:10]

if not test_rows:
    raise SystemExit("Filter returned 0 rows; tweak is_target().")

out_rows = []
for r in test_rows:
    out_rows.append({
        "video_id": r.get("video_id") or r.get("videoid") or "",
        "Video": r.get("old_title") or r.get("oldtitle") or r.get("title") or "",
        "Actual Hero": r.get("hero_name") or r.get("heroname") or "",
        "Actual Boss": r.get("boss_name") or r.get("bossname") or "",
        "Transcript snippet": "",
        "Matched hero": "",
        "Matched boss": "",
        "Correct?": "",
    })

with open(OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(f, fieldnames=out_rows[0].keys())
    writer.writeheader()
    writer.writerows(out_rows)

print(f"Wrote {OUTPUT} with {len(out_rows)} rows.")