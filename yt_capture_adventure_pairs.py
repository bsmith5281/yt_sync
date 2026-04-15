import csv
import json
import os
import re
import sys
import tempfile
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from difflib import SequenceMatcher

try:
    import whisper
except Exception:
    whisper = None

from hearthstone_adventure_directory import ADVENTURE_DIRECTORY

INPUT_CSV = os.environ.get('INPUTCSV', 'adventure_metadata_validated_v6.csv')
OUTPUT_CSV = os.environ.get('OUTPUTCSV', 'output/adventure_pair_capture.csv')
PLAYLIST_ID = os.environ.get('ADVENTURE_PLAYLIST_ID', 'PL2wUlQkvGyYfSMMzX1Ak2WR6wNbfny45P')
WHISPER_MODEL = os.environ.get('WHISPER_MODEL_NAME', 'large-v2')
CLIP_WINDOWS = [
    ('intro', '0-18'),
    ('early', '18-45'),
    ('mid', '45-90'),
]
DEFAULT_TITLE_RE = re.compile(r'Hearthstone\s+Heroes\s+of\s+Warcraft\s+\d{4}\s*\d{2}', re.I)
NONALNUM_RE = re.compile(r'[^a-z0-9]+')
SPACE_RE = re.compile(r'\s+')


def norm(text):
    text = (text or '').lower().strip().replace('-', ' ')
    text = NONALNUM_RE.sub(' ', text)
    return SPACE_RE.sub(' ', text).strip()


def slugish(text):
    return norm(text).replace(' ', '')


def build_catalog():
    heroes = {}
    bosses = {}
    hero_to_bosses = defaultdict(set)
    boss_to_heroes = defaultdict(set)
    for series_key, series in ADVENTURE_DIRECTORY.items():
        series_name = series['series_name']
        for hero, boss_list in series['heroes'].items():
            heroes[hero] = {'hero': hero, 'series_name': series_name, 'series_key': series_key}
            for boss in boss_list:
                bosses[boss] = {'boss': boss, 'series_name': series_name, 'series_key': series_key}
                hero_to_bosses[hero].add(boss)
                boss_to_heroes[boss].add(hero)
    return heroes, bosses, hero_to_bosses, boss_to_heroes


HEROES, BOSSES, HERO_TO_BOSSES, BOSS_TO_HEROES = build_catalog()

HERO_ALIASES = {
    'guldan': 'Guldan', 'gul dan': 'Guldan', "gul'dan": 'Guldan', 'garrosh hellscream': 'Garrosh',
    'king rastakhan': 'King Rastakhan', 'rastakhan': 'King Rastakhan', 'kelthuzad': 'KelThuzad',
    "kel'thuzad": 'KelThuzad', 'n zoth': 'NZoth', "n'zoth": 'NZoth', 'scabbs': 'Chef Scabbs',
    'chef scabbs': 'Chef Scabbs', 'tamsin': 'Opera Diva Tamsin', 'mecha jaraxxus': 'Mecha Jaraxxus',
}
BOSS_ALIASES = {
    'blackseed the vile': 'Blackseed', 'brandlemar': 'Elder Brandlemar', 'graves': 'Graves the Cleric',
    'russell': 'Russell the Bard', 'hamm': 'Pathmaker Hamm', 'mogark': 'Overseer Mogark',
    'inara': 'Chronomancer Inara', 'flurgl': 'Fungalmancer Flurgl', 'af kay': 'A.F.Kay',
}

for hero in HEROES:
    HERO_ALIASES.setdefault(norm(hero), hero)
for boss in BOSSES:
    BOSS_ALIASES.setdefault(norm(boss), boss)

ALL_HERO_KEYS = list(HERO_ALIASES.keys())
ALL_BOSS_KEYS = list(BOSS_ALIASES.keys())


def looks_like_target(row):
    playlist_id = (row.get('playlistid') or row.get('playlist_id') or '').strip()
    if playlist_id and playlist_id != PLAYLIST_ID:
        return False
    title = (row.get('title') or row.get('oldtitle') or '').strip()
    return bool(DEFAULT_TITLE_RE.search(title) or (row.get('istargetdefault') or '').strip().upper() == 'TRUE')


def load_rows(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def save_rows(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def score_phrase(phrase, candidates):
    best_name, best_score = None, 0.0
    for cand in candidates:
        ratio = SequenceMatcher(None, phrase, cand).ratio()
        if phrase in cand or cand in phrase:
            ratio = max(ratio, 0.985)
        if ratio > best_score:
            best_name, best_score = cand, ratio
    return best_name, best_score


def extract_candidates(text, alias_map, label):
    cleaned = norm(text)
    out = Counter()
    evidence = defaultdict(list)
    if not cleaned:
        return out, evidence
    tokens = cleaned.split()
    for size in range(1, min(6, len(tokens)) + 1):
        for i in range(0, len(tokens) - size + 1):
            phrase = ' '.join(tokens[i:i+size])
            if phrase in alias_map:
                canonical = alias_map[phrase]
                out[canonical] += 5
                evidence[canonical].append(f'{label}:exact:{phrase}')
                continue
            best_key, score = score_phrase(phrase, list(alias_map.keys()))
            if score >= 0.94:
                canonical = alias_map[best_key]
                weight = 4 if score >= 0.975 else 2
                out[canonical] += weight
                evidence[canonical].append(f'{label}:fuzzy:{phrase}->{best_key}:{score:.3f}')
    return out, evidence


def parse_existing_pair(title):
    text = title or ''
    if ' vs ' not in text.lower():
        return None, None
    m = re.search(r'(?P<hero>.+?)\s+vs\.?\s+(?P<boss>.+?)(?:\s+[\-|–|—]\s+|\||$)', text, re.I)
    if not m:
        return None, None
    hero = HERO_ALIASES.get(norm(m.group('hero')))
    boss = BOSS_ALIASES.get(norm(m.group('boss')))
    return hero, boss


def yt_url(video_id):
    return f'https://youtu.be/{video_id}'


def download_section(video_id, outdir, label, section):
    outtmpl = os.path.join(outdir, f'{video_id}_{label}.%(ext)s')
    cmd = [
        sys.executable, '-m', 'yt_dlp', '-f', 'bestaudio/best', '--force-overwrites',
        '--download-sections', f'*{section}', '-o', outtmpl, yt_url(video_id)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for name in os.listdir(outdir):
        if name.startswith(f'{video_id}_{label}.'):
            return os.path.join(outdir, name)
    raise FileNotFoundError(f'No clip downloaded for {video_id} {label}')


def transcribe(model, audio_path):
    result = model.transcribe(audio_path, language='en')
    return (result.get('text') or '').strip()


def choose_pair(hero_votes, boss_votes, row, series_hint=None):
    current_hero = (row.get('currenthero') or '').strip()
    current_boss = (row.get('currentboss') or '').strip()
    if current_hero in HERO_TO_BOSSES and current_boss in HERO_TO_BOSSES[current_hero]:
        return current_hero, current_boss, 'trusted_metadata', 100

    title_pair = parse_existing_pair(row.get('oldtitle') or row.get('title') or '')
    if title_pair[0] and title_pair[1] and title_pair[1] in HERO_TO_BOSSES.get(title_pair[0], set()):
        return title_pair[0], title_pair[1], 'existing_title_pair', 95

    hero_ranked = hero_votes.most_common()
    boss_ranked = boss_votes.most_common()
    best = None
    best_score = -1
    for hero, hs in hero_ranked[:8]:
        for boss, bs in boss_ranked[:12]:
            if boss not in HERO_TO_BOSSES.get(hero, set()):
                continue
            score = hs * 10 + bs * 8
            if current_hero == hero:
                score += 20
            if current_boss == boss:
                score += 20
            if series_hint and HEROES.get(hero, {}).get('series_name') == series_hint:
                score += 6
            if best is None or score > best_score:
                best = (hero, boss)
                best_score = score
    if best:
        source = 'stt_pair_resolved'
        conf = min(99, max(60, best_score))
        return best[0], best[1], source, conf

    if current_hero in HERO_TO_BOSSES and len(HERO_TO_BOSSES[current_hero]) == 1 and boss_ranked:
        boss = next(iter(HERO_TO_BOSSES[current_hero]))
        return current_hero, boss, 'hero_locked_single_boss', 70
    return '', '', 'unresolved', 0


def main():
    rows = load_rows(INPUT_CSV)
    targets = [r for r in rows if looks_like_target(r)]
    if whisper is None:
        raise RuntimeError('whisper is required for capture script')
    model = whisper.load_model(WHISPER_MODEL)
    with tempfile.TemporaryDirectory() as tmpdir:
        for row in targets:
            row.setdefault('capturedhero', '')
            row.setdefault('capturedboss', '')
            row.setdefault('capturedseries', '')
            row.setdefault('captureconfidence', '')
            row.setdefault('capturesource', '')
            row.setdefault('capturestatus', '')
            row.setdefault('captureevidence', '')
            row.setdefault('capturetranscript', '')
            row.setdefault('capturereviewnotes', '')
            current_hero = (row.get('currenthero') or '').strip()
            series_hint = (row.get('currentseries') or '').strip() or None
            hero_votes = Counter()
            boss_votes = Counter()
            evidence_log = []
            transcripts = []
            video_id = (row.get('videoid') or row.get('id') or '').strip()
            if not video_id:
                row['capturestatus'] = 'missing_video_id'
                continue
            for label, section in CLIP_WINDOWS:
                try:
                    clip = download_section(video_id, tmpdir, label, section)
                    text = transcribe(model, clip)
                    if text:
                        transcripts.append(f'[{label}] {text}')
                    hv, he = extract_candidates(text, HERO_ALIASES, label)
                    bv, be = extract_candidates(text, BOSS_ALIASES, label)
                    hero_votes.update(hv)
                    boss_votes.update(bv)
                    for k, vals in he.items():
                        evidence_log.extend([f'hero:{k}:{v}' for v in vals])
                    for k, vals in be.items():
                        evidence_log.extend([f'boss:{k}:{v}' for v in vals])
                except Exception as e:
                    evidence_log.append(f'{label}:error:{e}')
            if current_hero in HERO_TO_BOSSES:
                hero_votes[current_hero] += 12
                evidence_log.append(f'hero_boost:currenthero:{current_hero}')
                allowed = HERO_TO_BOSSES[current_hero]
                boss_votes = Counter({k: v for k, v in boss_votes.items() if k in allowed})
            hero, boss, source, conf = choose_pair(hero_votes, boss_votes, row, series_hint)
            row['capturedhero'] = hero
            row['capturedboss'] = boss
            row['capturedseries'] = HEROES.get(hero, {}).get('series_name', series_hint or 'Book of Heroes' if hero else '')
            row['captureconfidence'] = str(conf)
            row['capturesource'] = source
            row['capturetranscript'] = ' || '.join(transcripts)
            row['captureevidence'] = ' | '.join(evidence_log[:80])
            if hero and boss and conf >= 80:
                row['capturestatus'] = 'confirmed'
                row['capturereviewnotes'] = 'safe_for_format'
            elif hero and boss:
                row['capturestatus'] = 'probable'
                row['capturereviewnotes'] = 'format_allowed_if_no_better_metadata'
            else:
                row['capturestatus'] = 'needs_review'
                row['capturereviewnotes'] = 'no_safe_pair'
    save_rows(OUTPUT_CSV, rows)
    print(OUTPUT_CSV)


if __name__ == '__main__':
    main()
