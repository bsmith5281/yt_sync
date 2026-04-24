import csv
import os
from pathlib import Path

# Input: the verified v14 CSV (your current canonical verification output)
INPUT_CSV = os.environ.get(
    "INPUTCSV",
    "output/adventure_titles_formatted_v14.csv",
)

# Output: a NEW file, does NOT overwrite your existing formatted CSV
OUTPUT_CSV = os.environ.get(
    "OUTPUTCSV",
    "output/adventure_titles_safe_v14.csv",
)

SERIES_FALLBACK = os.environ.get("SERIES_NAME", "Book of Heroes")

def load_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def save_rows(path, rows, fieldnames):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def compute_safety(row):
    """
    Decide if this row is safe for auto-apply, WITHOUT changing any
    of your existing title/description logic.
    Returns: (is_safe: bool, safety_source: str, safety_reason: str, hero: str, boss: str, series: str)
    """

    # Existing verification (old pipeline)
    old_ver_status = (row.get("verification_status") or "").strip()
    old_ver_reason = (row.get("verification_reason") or "").strip()

    # OCR verification
    verified_hero = (row.get("verifiedhero") or "").strip()
    verified_boss = (row.get("verifiedboss") or "").strip()
    verifystatus = (row.get("verifystatus") or "").strip()
    try:
        verify_conf = int((row.get("verifyconfidence") or "0").strip() or "0")
    except ValueError:
        verify_conf = 0

    # Current metadata
    current_hero = (row.get("currenthero") or row.get("current_hero") or "").strip()
    current_boss = (row.get("currentboss") or row.get("current_boss") or "").strip()

    # Series
    series = (
        (row.get("capturedseries") or "").strip()
        or (row.get("currentseries") or "").strip()
        or SERIES_FALLBACK
    )

    # 1) High-confidence OCR verified pair -> safe
    if (
        verified_hero
        and verified_boss
        and verifystatus == "verified"
        and verify_conf >= 90
    ):
        return (
            True,
            "ocr_verified",
            f"verifystatus=verified;verifyconfidence={verify_conf}",
            verified_hero,
            verified_boss,
            series,
        )

    # 2) Old meta validator says this is a trusted match -> safe
    if (
        current_hero
        and current_boss
        and old_ver_status == "verified_match"
        and "current_title_matches_trusted_matchup" in old_ver_reason
        and "meta_pair_directory_validated" in old_ver_reason
    ):
        return (
            True,
            "trusted_meta",
            old_ver_reason,
            current_hero,
            current_boss,
            series,
        )

    # 3) Not safe / unresolved
    return (
        False,
        "unresolved",
        "no_high_confidence_verification",
        current_hero,
        current_boss,
        series,
    )

def main():
    rows = load_rows(INPUT_CSV)
    safe_rows = []

    for row in rows:
        is_safe, safety_source, safety_reason, hero, boss, series = compute_safety(row)

        # Build a NEW row for the output CSV; keep useful context
        out = {
            "playlist_position": row.get("playlist_position", ""),
            "video_id": row.get("video_id", ""),
            "published_at": row.get("published_at", ""),
            "privacy_status": row.get("privacy_status", ""),
            "title_family": row.get("title_family", ""),
            "is_target_default": row.get("is_target_default", ""),
            "normalized_timestamp": row.get("normalized_timestamp", ""),
            "old_title": row.get("old_title", ""),
            "old_description": row.get("old_description", ""),
            "current_hero": (row.get("currenthero") or row.get("current_hero") or "").strip(),
            "current_boss": (row.get("currentboss") or row.get("current_boss") or "").strip(),
            "current_series": (row.get("currentseries") or row.get("current_series") or "").strip(),
            "verification_status": row.get("verification_status", ""),
            "verification_reason": row.get("verification_reason", ""),
            "verifiedhero": row.get("verifiedhero", ""),
            "verifiedboss": row.get("verifiedboss", ""),
            "verifyconfidence": row.get("verifyconfidence", ""),
            "verifystatus": row.get("verifystatus", ""),
            "verifysource": row.get("verifysource", ""),
            "safe_for_auto_apply": "TRUE" if is_safe else "FALSE",
            "safety_source": safety_source,
            "safety_reason": safety_reason,
        }

        # Also include a suggested title/description using the chosen pair,
        # without touching your existing formatted CSV.
        if hero and boss:
            out["suggested_title"] = f"{hero} vs {boss} – {series} | Hearthstone | Adventure Mode"
            out["suggested_description"] = (
                f"Hearthstone {series} Adventure Mode gameplay featuring {hero} vs {boss}."
            )
        else:
            out["suggested_title"] = ""
            out["suggested_description"] = ""

        safe_rows.append(out)

    # Define columns in a stable order
    fieldnames = [
        "playlist_position",
        "video_id",
        "published_at",
        "privacy_status",
        "title_family",
        "is_target_default",
        "normalized_timestamp",
        "old_title",
        "old_description",
        "current_hero",
        "current_boss",
        "current_series",
        "verification_status",
        "verification_reason",
        "verifiedhero",
        "verifiedboss",
        "verifyconfidence",
        "verifystatus",
        "verifysource",
        "safe_for_auto_apply",
        "safety_source",
        "safety_reason",
        "suggested_title",
]
    save_rows(OUTPUT_CSV, safe_rows, fieldnames)
    print(f"Wrote safety flags to {OUTPUT_CSV}")

if __name__ == "__main__":
    main()