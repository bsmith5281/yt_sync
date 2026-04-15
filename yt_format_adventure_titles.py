import csv
import os
from pathlib import Path

INPUT_CSV = os.environ.get('INPUTCSV', 'output/adventure_pair_capture.csv')
OUTPUT_CSV = os.environ.get('OUTPUTCSV', 'output/adventure_titles_formatted.csv')
SERIES_FALLBACK = os.environ.get('SERIES_NAME', 'Book of Heroes')
TITLE_TEMPLATE = '{hero} vs {boss} – {series} | Hearthstone | Adventure Mode'
DESCRIPTION_TEMPLATE = 'Hearthstone {series} Adventure Mode gameplay featuring {hero} vs {boss}.'


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
    current_hero = (row.get('currenthero') or '').strip()
    current_boss = (row.get('currentboss') or '').strip()
    captured_hero = (row.get('capturedhero') or '').strip()
    captured_boss = (row.get('capturedboss') or '').strip()
    capture_status = (row.get('capturestatus') or '').strip()
    capture_conf = int((row.get('captureconfidence') or '0').strip() or '0')

    if current_hero and current_boss:
        return current_hero, current_boss, 'trusted_current'
    if captured_hero and captured_boss and capture_status in {'confirmed', 'probable'} and capture_conf >= 70:
        return captured_hero, captured_boss, 'captured_pair'
    return '', '', 'unresolved'


def main():
    rows = load_rows(INPUT_CSV)
    for row in rows:
        row.setdefault('formattedtitle', '')
        row.setdefault('formatteddescription', '')
        row.setdefault('formatstatus', '')
        row.setdefault('formatsource', '')
        hero, boss, source = pick_pair(row)
        series = (row.get('capturedseries') or row.get('currentseries') or SERIES_FALLBACK).strip() or SERIES_FALLBACK
        if hero and boss:
            row['formattedtitle'] = TITLE_TEMPLATE.format(hero=hero, boss=boss, series=series)
            row['formatteddescription'] = DESCRIPTION_TEMPLATE.format(hero=hero, boss=boss, series=series)
            row['formatstatus'] = 'ready'
            row['formatsource'] = source
        else:
            row['formatstatus'] = 'blocked'
            row['formatsource'] = source
    save_rows(OUTPUT_CSV, rows)
    print(OUTPUT_CSV)


if __name__ == '__main__':
    main()
