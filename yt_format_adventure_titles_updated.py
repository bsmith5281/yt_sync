import csv
import os
from pathlib import Path

INPUT_CSV = os.environ.get(
    'INPUTCSV',
    'output/adventure_pair_verified_v14.csv',  # default to verified v14 output
)
OUTPUT_CSV = os.environ.get(
    'OUTPUTCSV',
    'output/adventure_titles_formatted_v14.csv',
)
SERIES_FALLBACK = os.environ.get('SERIES_NAME', 'Book of Heroes')

TITLE_TEMPLATE = '{hero} vs {boss} – {series} | Hearthstone | Adventure Mode'
DESCRIPTION_TEMPLATE = (
    'Hearthstone {series} Adventure Mode gameplay featuring {hero} vs {boss}.'
)


def load_rows(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def save_rows(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def pick_pair(row):
    # Current metadata
    current_hero = (row.get('currenthero') or '').strip()
    current_boss = (row.get('currentboss') or '').strip()

    # Capture-based pair from the capture script
    captured_hero = (row.get('capturedhero') or '').strip()
    captured_boss = (row.get('capturedboss') or '').strip()
    capture_status = (row.get('capturestatus') or '').strip()
    try:
        capture_conf = int((row.get('captureconfidence') or '0').strip() or '0')
    except ValueError:
        capture_conf = 0

    # NEW: verification columns from OCR / manual review
    verified_hero = (row.get('verifiedhero') or '').strip()
    verified_boss = (row.get('verifiedboss') or '').strip()
    verify_status = (row.get('verifystatus') or '').strip()
    try:
        verify_conf = int((row.get('verifyconfidence') or '0').strip() or '0')
    except ValueError:
        verify_conf = 0

    # 0) If verification says "verified" with high confidence, prefer that,
    #    and mark it safe-for-format.
    if (
        verified_hero
        and verified_boss
        and verify_status == 'verified'
        and verify_conf >= 90
    ):
        return verified_hero, verified_boss, 'verified_pair', True

    # 1) If metadata already has a pair, trust it (but not auto-safe)
    if current_hero and current_boss:
        return current_hero, current_boss, 'trusted_current', False

    # 2) If capture pair is legal and confident, use it (not auto-safe)
    if (
        captured_hero
        and captured_boss
        and capture_status in {'confirmed', 'probable'}
        and capture_conf >= 70
    ):
        return captured_hero, captured_boss, 'captured_pair', False

    # 3) Nothing usable
    return '', '', 'unresolved', False


def main():
    rows = load_rows(INPUT_CSV)

    for row in rows:
        row.setdefault('formattedtitle', '')
        row.setdefault('formatteddescription', '')
        row.setdefault('formatstatus', '')
        row.setdefault('formatsource', '')

        hero, boss, source, is_safe = pick_pair(row)
        series = (
            row.get('capturedseries')
            or row.get('currentseries')
            or SERIES_FALLBACK
        ).strip() or SERIES_FALLBACK

        if hero and boss:
            row['formattedtitle'] = TITLE_TEMPLATE.format(
                hero=hero,
                boss=boss,
                series=series,
            )
            row['formatteddescription'] = DESCRIPTION_TEMPLATE.format(
                hero=hero,
                boss=boss,
                series=series,
            )
            row['formatstatus'] = 'safeforformat' if is_safe else 'ready'
            row['formatsource'] = source
        else:
            row['formatstatus'] = 'blocked'
            row['formatsource'] = source

    save_rows(OUTPUT_CSV, rows)
    print(OUTPUT_CSV)


if __name__ == '__main__':
    main()