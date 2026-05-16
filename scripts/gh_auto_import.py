"""GitHub Actions auto-import: parse t.me/pocox5proin using strict structured format."""

import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_rom_version import ROM_NAME_ALIASES  # noqa: E402

CHANNEL = 'pocox5proin'
CHANNEL_URL = f'https://t.me/s/{CHANNEL}'
KNOWN_POSTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.known_posts.json')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('autoimport')


def _write_summary(text: str):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a') as f:
            f.write(text + '\n')


def fetch_channel_page() -> str:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    resp = session.get(CHANNEL_URL, timeout=30, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    resp.raise_for_status()
    return resp.text


def parse_date(raw: str) -> str:
    """Convert various date formats to YYYY-MM-DD."""
    raw = raw.strip().replace('\u2013', '-')
    for fmt in ['%Y-%m-%d', '%Y/%m/%d', '%d %B %Y', '%d %b %Y', '%B %d %Y',
                '%b %d %Y', '%d-%m-%Y', '%m-%d-%Y']:
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    m = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', raw)
    if m:
        day, mon, yr = m.group(1), m.group(2), m.group(3)
        for fmt in ['%d %B %Y', '%d %b %Y']:
            try:
                return datetime.strptime(f'{day} {mon} {yr}', fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
    return raw


def _get_line_data(html: str) -> list[dict]:
    """Split raw HTML by <br> and extract label + URLs per line."""
    lines_raw = re.split(r'<br\s*/?>', html, flags=re.IGNORECASE)
    data = []
    for raw in lines_raw:
        raw = raw.strip()
        if not raw:
            continue
        soup = BeautifulSoup(raw, 'lxml')
        text = soup.get_text(separator=' ', strip=True)
        label = ''
        b = soup.find('b')
        if b:
            label = b.get_text(strip=True)
        urls = []
        for a in soup.find_all('a'):
            href = a.get('href', '')
            if href and not href.startswith('tg://'):
                urls.append(href)
        data.append({'text': text, 'label': label, 'urls': urls})
    return data


def _validate_post(result: dict) -> str:
    """Validate extracted fields. Return error message or empty string."""
    if not result.get('romName'):
        return 'romName is empty'
    if not result.get('downloadLink') or result['downloadLink'] in ('', '#'):
        return 'downloadLink is missing or placeholder'
    dl = result['downloadLink']
    if not dl.startswith('http://') and not dl.startswith('https://'):
        return f'downloadLink is not a valid URL: {dl}'
    bd = result.get('buildDate', '')
    if bd and bd != datetime.now().strftime('%Y-%m-%d'):
        parsed = parse_date(bd)
        if parsed != bd:
            return f'buildDate could not be parsed: {bd}'
    return ''


def load_known_posts() -> dict:
    if os.path.exists(KNOWN_POSTS_PATH):
        with open(KNOWN_POSTS_PATH, 'r') as f:
            return json.load(f)
    return {}


def save_known_posts(posts: dict):
    with open(KNOWN_POSTS_PATH, 'w') as f:
        json.dump(posts, f, indent=2)
        f.write('\n')


def parse_structured(msg_text: str, html: str, el) -> dict | None:
    """Parse a structured Android ROM release post.

    Handles two formats:
      Old: ROM vX.Y based on Android N for Device (codename)
      New: ROM NAME Android N for Device (codename)  [version on separate line]

    Returns None if the post doesn't look like a ROM release.
    """
    lines = [l.strip() for l in msg_text.split('\n') if l.strip()]
    if not lines:
        return None

    first = lines[0]
    if not re.search(r'\bandroid\b', first, re.IGNORECASE):
        return None
    if not (re.search(r'\([\w_]+\)', first) or ' for ' in first.lower()):
        return None

    result = {
        'postId': '', 'romName': '', 'romVersion': '',
        'androidVersion': 'Android 16', 'deviceName': '', 'deviceCodename': '',
        'maintainerName': '', 'maintainerUrl': '',
        'downloadLink': '', 'recoveryLink': '', 'donateLink': '', 'ksuLink': '',
        'buildDate': '', 'buildType': '',
        'changelogSource': '', 'changelogDevice': '', 'changelogText': '',
        'screenshotsLink': '', 'supportLink': '',
        'tags': [], 'channelMentions': [],
        'hasPhoto': False, 'screenshots': [], 'banner': '',
        'desc': '', 'status': 'unofficial',
    }

    # ===== LINE 1: ROM NAME + VERSION + ANDROID + DEVICE =====
    # Try old format first: "... based on Android ..."
    old_parts = re.split(r'\s+based\s+on\s+android\s+', first, maxsplit=1, flags=re.IGNORECASE)
    if len(old_parts) >= 2:
        left_raw, right_raw = old_parts[0].strip(), old_parts[1].strip()
        rom_name = left_raw
        # Extract version from left side
        version_pats = [
            r'\s+(v*\d[\d.]*(?:\s+\w[\w\s]*?)?)\s*$',
            r'\s+(Alpha|Beta|RC\d+|Stable)\s*$',
        ]
        rom_version = ''
        for pat in version_pats:
            m = re.search(pat, left_raw, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip()
                if not re.match(r'^\d{4}\b', candidate):
                    rom_name = left_raw[:m.start()].strip()
                    rom_version = candidate
                    break
        if not rom_version and re.search(r'\s+v\s*$', rom_name, re.IGNORECASE):
            rom_name = re.sub(r'\s+v\s*$', '', rom_name, flags=re.IGNORECASE)
        result['romVersion'] = rom_version

        android_ver = 'Android 16'
        device_part = ''
        if ' for ' in right_raw:
            android_ver = 'Android ' + right_raw.split(' for ')[0].strip()
            device_part = right_raw.split(' for ', 1)[1].strip()
        else:
            m_av = re.match(r'(\d+)', right_raw)
            if m_av:
                android_ver = 'Android ' + m_av.group(1)
            device_part = right_raw
    else:
        # New format: {name} Android {ver} for {device} ({codename})
        first_lower = first.lower()
        android_idx = re.search(r'\bandroid\b', first_lower)
        if not android_idx:
            return None
        left_raw = first[:android_idx.start()].strip()
        right_raw = first[android_idx.end():].strip()
        rom_name = left_raw
        result['romVersion'] = ''

        # Check for "official"/"unofficial" status in name
        for s in ('official', 'unofficial'):
            ms = re.search(r'\b' + s + r'\b', rom_name, re.IGNORECASE)
            if ms:
                result['status'] = s
                rom_name = (rom_name[:ms.start()].strip() + ' ' + rom_name[ms.end():].strip()).strip()

        android_ver = 'Android 16'
        device_part = ''
        if ' for ' in right_raw:
            android_ver = 'Android ' + right_raw.split(' for ')[0].strip()
            device_part = right_raw.split(' for ', 1)[1].strip()
        else:
            m_av = re.match(r'(\d+[\w\s]*)', right_raw)
            if m_av:
                android_ver = 'Android ' + m_av.group(1).strip()
            device_part = right_raw

    device_name = device_part
    device_codename = ''
    paren = device_part.rfind('(')
    if paren >= 0 and device_part.endswith(')'):
        device_name = device_part[:paren].strip()
        device_codename = device_part[paren + 1:-1].strip().lower()

    result['romName'] = rom_name
    result['androidVersion'] = android_ver
    result['deviceName'] = device_name or device_part
    result['deviceCodename'] = device_codename

    # ===== EXTRACT VERSION, MAINTAINER, AND LABELED LINES =====
    line_data = _get_line_data(html)
    links_found = False

    for ld in line_data:
        t_lower = ld['text'].lower()
        t_full = ld['text']
        urls = ld['urls']

        # Version on a separate line (new format)
        if not result['romVersion'] and re.search(r'\b(?:rom\s+)?version\s*:', t_lower):
            m = re.search(r':\s*v*([\d.]+)', t_full)
            if m:
                result['romVersion'] = m.group(1).strip()

        # Maintainer: "By Name"
        if t_lower.startswith('by ') and not result['maintainerName']:
            result['maintainerName'] = t_full[3:].strip()
            if urls:
                result['maintainerUrl'] = urls[0]
            continue

        # Build date / type (plain text)
        if 'build date' in t_lower:
            m = re.search(r':\s*(.+?)$', t_full)
            result['buildDate'] = parse_date(m.group(1).strip()) if m else ''
            continue
        if 'build type' in t_lower:
            m = re.search(r':\s*(.+?)$', t_full)
            result['buildType'] = m.group(1).strip() if m else ''
            continue

        if not urls:
            continue

        # Only accept real URLs, reject "HERE" placeholders
        real_urls = [u for u in urls if not u.lower().strip() in ('here', 'link')]
        if not real_urls:
            continue

        # Strict label matching — match after bullet/whitespace prefix
        label_clean = re.sub(r'^[\s▫️\-•*]+', '', t_lower)
        label_first_word = label_clean.split(':')[0].split()[0].strip() if label_clean else ''

        if label_first_word == 'download':
            result['downloadLink'] = real_urls[0]
            links_found = True
        elif 'recovery' in t_lower:
            result['recoveryLink'] = real_urls[0]
            links_found = True
        elif 'donate' in t_lower:
            result['donateLink'] = real_urls[0]
            links_found = True
        elif 'ksu' in t_lower or ('kernel' in t_lower and 'manager' not in t_lower):
            result['ksuLink'] = real_urls[0]
            links_found = True
        elif 'source changelog' in t_lower:
            result['changelogSource'] = real_urls[0]
        elif 'device changelog' in t_lower:
            result['changelogDevice'] = real_urls[0]
        elif 'screenshot' in t_lower or '\u2b50' in t_full:
            result['screenshotsLink'] = real_urls[0]
            links_found = True
        elif 'support' in t_lower or '\U0001f4ac' in t_full:
            result['supportLink'] = real_urls[0]
            links_found = True

    # ===== CHANGELOG TEXT =====
    changelog_parts = []
    if result['changelogSource']:
        changelog_parts.append(f'Source Changelog: {result["changelogSource"]}')
    if result['changelogDevice']:
        changelog_parts.append(f'Device Changelog: {result["changelogDevice"]}')
    result['changelogText'] = '\n'.join(changelog_parts)

    # ===== HASHTAGS & MENTIONS (last line) =====
    last_line = lines[-1] if lines else ''
    for word in last_line.split():
        if word.startswith('#') and len(word) > 1:
            result['tags'].append(word[1:])
        elif word.startswith('@') and len(word) > 1:
            result['channelMentions'].append(word[1:])

    # ===== HAS PHOTO =====
    result['hasPhoto'] = bool(el.select_one('.tgme_widget_message_photo_wrap, .tgme_widget_message_photo'))

    # ===== SCREENSHOTS FROM HTML =====
    screenshots = []
    seen = set()
    for photo in el.select('.tgme_widget_message_photo_wrap'):
        style = photo.get('style', '')
        u = re.search(r"url\(['\"]?([^'\"]+)['\"]?\)", style)
        if u and u.group(1) not in seen:
            seen.add(u.group(1))
            screenshots.append(u.group(1))
    for img in el.select('.tgme_widget_message_text img'):
        src = img.get('src', '')
        if src.startswith('http') and src not in seen:
            seen.add(src)
            screenshots.append(src)
    result['screenshots'] = screenshots
    result['banner'] = screenshots[0] if screenshots else ''

    # ===== STATUS =====
    lower = msg_text.lower()
    if '#official' in lower and '#unofficial' not in lower:
        result['status'] = 'official'
    if '#unofficial' in lower or '#unoffical' in lower:
        result['status'] = 'unofficial'

    # ===== DESCRIPTION =====
    desc = ''
    for line in lines[2:]:
        line = line.replace('\ufe0f', '')
        clean = re.sub(r'^[•\-*>#\u25aa\u25ab\u25b6\u25c0]\s*', '', line).strip()
        if (clean and not clean.startswith('#')
                and not clean.startswith('\u25ab') and not clean.startswith('\u25aa')
                and not re.match(r'^(Download|Recovery|Build|By|@|\u2b50|\U0001f4ac)', clean, re.IGNORECASE)
                and not any(k in clean.lower() for k in ['changelog', 'screenshot', 'support'])
                and len(clean) > 15):
            desc = clean[:200]
            break
    if not desc:
        desc = f'{rom_name} for {device_codename or "Redwood"}'
    result['desc'] = desc

    if not links_found:
        return None

    # Validation
    err = _validate_post(result)
    if err:
        log.warning('Post validation failed for %s: %s', rom_name, err)
        return None

    return result


def parse_messages(html: str) -> list[dict]:
    soup = BeautifulSoup(html, 'lxml')
    results = []
    post_ids = set()
    known = load_known_posts()

    for el in soup.select('.tgme_widget_message_wrap, .tgme_widget_message'):
        post_el = el.select_one('.tgme_widget_message_text') or el
        inner_h = str(post_el)
        inner_h = re.sub(r'^<[^>]+>', '', inner_h)
        inner_h = re.sub(r'</[^>]+>$', '', inner_h)

        msg_text = inner_h
        msg_text = re.sub(r'<br\s*/?>', '\n', msg_text, flags=re.IGNORECASE)
        msg_text = re.sub(r'<[^>]+>', '', msg_text).strip()

        post_link = el.get('data-post', '')
        id_match = re.search(r'/(\d+)$', post_link)
        if not id_match:
            id_match = re.search(r't\.me/(?:\w+)/(\d+)', msg_text)
        post_id = id_match.group(1) if id_match else ''
        if not post_id or post_id in post_ids:
            continue
        post_ids.add(post_id)

        if not re.search(r'\bandroid\b', msg_text, re.IGNORECASE):
            continue

        # Detect edited posts
        content_hash = str(hash(msg_text))
        known_entry = known.get(post_id, {})
        if known_entry.get('hash') == content_hash:
            continue

        parsed = parse_structured(msg_text, inner_h, el)
        if parsed is None:
            continue

        parsed['postId'] = post_id
        parsed['link'] = f'https://t.me/{CHANNEL}/{post_id}'
        known[post_id] = {'hash': content_hash, 'postId': post_id}
        results.append(parsed)

    if known:
        save_known_posts(known)

    return results


def merge_into_roms(parsed: list[dict], roms_path: str) -> tuple[int, int, int, int]:
    if os.path.exists(roms_path):
        with open(roms_path, 'r', encoding='utf-8') as f:
            roms = json.load(f)
    else:
        roms = []

    added = 0
    updated = 0
    resynced = 0
    skipped = 0

    def _normalize(name: str) -> str:
        n = name.lower()
        n = re.sub(r'[#]\w+', '', n)
        n = re.sub(r'[^\w\s]', '', n)
        n = re.sub(r'\s+', '', n)
        return n

    def _match(parsed_name: str, existing_name: str) -> bool:
        p = _normalize(parsed_name)
        e = _normalize(existing_name)
        if e == p or e in p or p in e:
            return True
        if p in ROM_NAME_ALIASES and _normalize(ROM_NAME_ALIASES[p]) == e:
            return True
        return False

    for p in parsed:
        matched_name = p['romName']
        for existing in roms:
            if _match(p['romName'], existing['name']):
                matched_name = existing['name']
                break

        dev_name = p['maintainerName'] or 'Unknown'
        dev_username = dev_name
        if not dev_username.startswith('@') and dev_username != 'Unknown':
            url = p['maintainerUrl']
            at = re.search(r't\.me/(\w+)', url)
            if at:
                dev_username = '@' + at.group(1)

        build_date = p['buildDate'] or datetime.now().strftime('%Y-%m-%d')

        new_ver = {
            'ver': p['androidVersion'],
            'andVer': p['androidVersion'],
            'date': build_date,
            'rom': p['downloadLink'] or '#',
            'boot': '#',
            'vendor_boot': '#',
            'dtbo': '#',
            'romVer': p['romVersion'] or '',
            'vDev': dev_username if dev_username != 'Unknown' else '',
            'vDevInfo': f'Telegram: {dev_username}' if dev_username != 'Unknown' else '',
            'vChangelog': p['changelogText'] or '',
        }

        existing = next((r for r in roms if r['name'] == matched_name), None)

        if existing:
            # Check for duplicate version
            dup = None
            for v in existing.get('versions', []):
                if v.get('romVer') == new_ver['romVer'] and (new_ver['romVer'] or v.get('rom') == new_ver['rom']):
                    dup = v
                    break

            if dup:
                # Update existing entry metadata (re-sync)
                dup['date'] = build_date
                if p['changelogText']:
                    dup['vChangelog'] = p['changelogText']
                resynced += 1
            else:
                existing.setdefault('versions', []).append(new_ver)

            if dev_name != 'Unknown':
                existing['dev'] = dev_name
                existing['devInfo'] = f'Telegram: {dev_username}' if dev_username != 'Unknown' else ''
            if p['changelogText']:
                existing['changelog'] = p['changelogText']
            if p['banner']:
                existing['banner'] = p['banner']
            if p['desc'] and 'for Redwood' not in p['desc'] and 'for redwood' not in p['desc'].lower():
                existing['desc'] = p['desc']
            if p['buildType']:
                existing['buildType'] = p['buildType']
            if p['deviceName']:
                existing['device'] = p['deviceName']
            if p['deviceCodename']:
                existing['codename'] = p['deviceCodename']
            if p['recoveryLink']:
                existing['recoveryLink'] = p['recoveryLink']
            if p['donateLink']:
                existing['donateLink'] = p['donateLink']
            if p['ksuLink']:
                existing['ksuLink'] = p['ksuLink']
            if p['changelogSource']:
                existing['changelogSource'] = p['changelogSource']
            if p['changelogDevice']:
                existing['changelogDevice'] = p['changelogDevice']
            if p['screenshotsLink']:
                existing['screenshotsLink'] = p['screenshotsLink']
            if p['supportLink']:
                existing['supportGroup'] = p['supportLink']
            if p['tags']:
                existing['tags'] = p['tags']
            if p['channelMentions']:
                existing['channelMentions'] = p['channelMentions']
            if p['screenshots']:
                existing.setdefault('screenshots', [])
                for s in p['screenshots']:
                    if s not in existing['screenshots']:
                        existing['screenshots'].append(s)
            if not dup:
                updated += 1
        else:
            dev_info = f'Telegram: {dev_username}' if dev_username != 'Unknown' else ''
            entry = {
                'name': matched_name,
                'status': p['status'],
                'dev': dev_name,
                'devInfo': dev_info,
                'desc': p['desc'] or f'{matched_name} for {p["deviceCodename"] or "Redwood"}',
                'downloads': 0,
                'banner': p['banner'],
                'screenshots': p['screenshots'],
                'changelog': p['changelogText'] or '',
                'buildType': p['buildType'] or '',
                'device': p['deviceName'],
                'codename': p['deviceCodename'],
                'recoveryLink': p['recoveryLink'],
                'donateLink': p['donateLink'],
                'ksuLink': p['ksuLink'],
                'changelogSource': p['changelogSource'],
                'changelogDevice': p['changelogDevice'],
                'screenshotsLink': p['screenshotsLink'],
                'supportGroup': p['supportLink'],
                'tags': p['tags'],
                'channelMentions': p['channelMentions'],
                'xdaLink': '',
                'firmwareLink': '',
                'sourceCode': '',
                'knownIssues': '',
                'requirements': 'Unlocked bootloader, latest firmware',
                'isActive': True,
                'versions': [new_ver],
            }
            if dev_username != 'Unknown':
                entry['icon'] = dev_username[1:2].upper() if dev_username.startswith('@') else dev_username[:1].upper()
            else:
                entry['icon'] = matched_name[0].upper()
            roms.insert(0, entry)
            added += 1

    # Trim to max 3 versions per (ROM name × Android version)
    for rom in roms:
        groups = {}
        for v in rom.get('versions', []):
            av = v.get('andVer') or v.get('ver') or 'Unknown'
            groups.setdefault(av, []).append(v)
        new_versions = []
        for av, vers in groups.items():
            vers.sort(key=lambda x: x.get('date', ''), reverse=True)
            new_versions.extend(vers[:3])
        rom['versions'] = new_versions

    # Backup + atomic write with rollback
    backup_path = None
    if os.path.exists(roms_path):
        fd, backup_path = tempfile.mkstemp(suffix='.json', prefix='roms_backup_')
        os.close(fd)
        with open(roms_path, 'r') as src, open(backup_path, 'w') as dst:
            dst.write(src.read())

    try:
        with open(roms_path, 'w', encoding='utf-8') as f:
            json.dump(roms, f, indent=2, ensure_ascii=False)
            f.write('\n')
    except Exception:
        log.critical('Write to roms.json failed, rolling back')
        if backup_path and os.path.exists(backup_path):
            with open(backup_path, 'r') as src, open(roms_path, 'w') as dst:
                dst.write(src.read())
            log.info('Rollback complete')
        raise
    finally:
        if backup_path and os.path.exists(backup_path):
            os.remove(backup_path)

    return added, updated, resynced, skipped


def main():
    start = datetime.now()
    roms_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'roms.json')
    roms_path = os.path.normpath(roms_path)

    log.info('Fetching channel page...')
    try:
        html = fetch_channel_page()
    except Exception as e:
        log.critical('Failed to fetch channel after retries: %s', e)
        _write_summary(f'## ❌ Auto-Import Failed\n\nFailed to fetch channel: {e}')
        sys.exit(1)

    log.info('Parsing messages...')
    parsed = parse_messages(html)

    log.info('Found %d structured ROM posts', len(parsed))
    for p in parsed:
        log.info('  - %s | %s | %s | %s (%s) | dl=%s',
                 p['romName'], p['romVersion'], p['androidVersion'],
                 p['deviceName'], p['deviceCodename'], bool(p['downloadLink']))

    if not parsed:
        msg = 'No matching posts found. Exiting.'
        log.info(msg)
        _write_summary(f'## ✅ Auto-Import Complete\n\nNo new posts to process.')
        return

    log.info('Merging into roms.json...')
    added, updated, resynced, skipped = merge_into_roms(parsed, roms_path)

    elapsed = (datetime.now() - start).total_seconds()
    summary = (
        f'## ✅ Auto-Import Complete\n\n'
        f'- **Added:** {added} new ROMs\n'
        f'- **Updated:** {updated} existing versions\n'
        f'- **Resynced:** {resynced} existing entries\n'
        f'- **Skipped:** {skipped} duplicates\n'
        f'- **Duration:** {elapsed:.1f}s\n'
    )
    log.info(summary.replace('\n', ' | '))
    _write_summary(summary)


if __name__ == '__main__':
    main()
