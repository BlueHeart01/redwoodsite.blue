"""GitHub Actions auto-import: parse t.me/pocox5proin using strict structured format."""

import json
import os
import re
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

CHANNEL = 'pocox5proin'
CHANNEL_URL = f'https://t.me/s/{CHANNEL}'


def fetch_channel_page() -> str:
    resp = requests.get(CHANNEL_URL, timeout=30, headers={
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
    # Try ISO-style with months like "05 March 2026"
    m = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})', raw)
    if m:
        day, mon, yr = m.group(1), m.group(2), m.group(3)
        try:
            dt = datetime.strptime(f'{day} {mon} {yr}', '%d %B %Y')
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            try:
                dt = datetime.strptime(f'{day} {mon} {yr}', '%d %b %Y')
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                pass
    return raw  # return as-is if can't parse


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


def parse_structured(msg_text: str, html: str, el) -> dict | None:
    """Parse a single Telegram post using the strict structured format.

    Returns None if the post does not match the format.
    """
    lines = [l.strip() for l in msg_text.split('\n') if l.strip()]
    if not lines:
        return None

    first = lines[0]
    if not re.search(r'based\s+on\s+android', first, re.IGNORECASE):
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
    parts = re.split(r'based\s+on\s+android', first, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) < 2:
        return None
    left_raw, right_raw = parts
    left_str = left_raw.strip()
    right_str = right_raw.strip()

    rom_name = left_str
    rom_version = ''
    version_pats = [
        r'\s+(v*\d[\d.]*(?:\s+\w[\w\s]*?)?)\s*$',
        r'\s+(Alpha|Beta|RC\d+|Stable)\s*$',
    ]
    for pat in version_pats:
        m = re.search(pat, left_str, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if not re.match(r'^\d{4}\b', candidate):
                rom_name = left_str[:m.start()].strip()
                rom_version = candidate
                break

    android_ver = 'Android 16'
    device_part = ''
    if ' for ' in right_str:
        android_ver = 'Android ' + right_str.split(' for ')[0].strip()
        device_part = right_str.split(' for ', 1)[1].strip()
    else:
        m_av = re.match(r'(\d+)', right_str)
        if m_av:
            android_ver = 'Android ' + m_av.group(1)
        device_part = right_str

    device_name = device_part
    device_codename = ''
    paren = device_part.rfind('(')
    if paren >= 0 and device_part.endswith(')'):
        device_name = device_part[:paren].strip()
        device_codename = device_part[paren + 1:-1].strip().lower()

    # Clean up trailing " v" if no version captured (e.g. "Clover v based on...")
    if not rom_version and re.search(r'\s+v\s*$', rom_name, re.IGNORECASE):
        rom_name = re.sub(r'\s+v\s*$', '', rom_name, flags=re.IGNORECASE)

    result['romName'] = rom_name
    result['romVersion'] = rom_version
    result['androidVersion'] = android_ver
    result['deviceName'] = device_name or device_part
    result['deviceCodename'] = device_codename

    # Require at least one labeled link to qualify as structured post
    links_found = False

    # ===== LINE 2: MAINTAINER =====
    if len(lines) > 1 and lines[1].startswith('By '):
        m = re.match(r'By\s+(.+?)\s+\((.+?)\)', lines[1])
        if m:
            result['maintainerName'] = m.group(1).strip()
            result['maintainerUrl'] = m.group(2).strip()

    # ===== EXTRACT LABELED URLs VIA LINE-LEVEL PARSING =====
    line_data = _get_line_data(html)
    for ld in line_data:
        t = ld['text'].lower()
        urls = ld['urls']
        if not urls:
            continue

        if 'download' in t:
            result['downloadLink'] = urls[0]
            links_found = True
        elif 'recovery' in t:
            result['recoveryLink'] = urls[0]
            links_found = True
        elif 'donate' in t:
            result['donateLink'] = urls[0]
            links_found = True
        elif 'ksu' in t or ('kernel' in t and 'manager' not in t):
            result['ksuLink'] = urls[0]
            links_found = True
        elif 'build date' in t:
            m = re.search(r':\s*(.+?)$', ld['text'])
            result['buildDate'] = parse_date(m.group(1).strip()) if m else ''
        elif 'build type' in t:
            m = re.search(r':\s*(.+?)$', ld['text'])
            result['buildType'] = m.group(1).strip() if m else ''
        elif 'source changelog' in t:
            result['changelogSource'] = urls[0]
        elif 'device changelog' in t:
            result['changelogDevice'] = urls[0]
        elif 'screenshot' in t or '\u2b50' in ld['text']:
            result['screenshotsLink'] = urls[0]
            links_found = True
        elif 'support' in t or '\U0001f4ac' in ld['text']:
            result['supportLink'] = urls[0]
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
    return result


def parse_messages(html: str) -> list[dict]:
    soup = BeautifulSoup(html, 'lxml')
    results = []
    post_ids = set()

    for el in soup.select('.tgme_widget_message_wrap, .tgme_widget_message'):
        post_el = el.select_one('.tgme_widget_message_text') or el
        inner_h = str(post_el)
        inner_h = re.sub(r'^<[^>]+>', '', inner_h)
        inner_h = re.sub(r'</[^>]+>$', '', inner_h)

        msg_text = inner_h
        msg_text = re.sub(r'<br\s*/?>', '\n', msg_text, flags=re.IGNORECASE)
        msg_text = re.sub(r'<[^>]+>', '', msg_text).strip()

        # Extract post ID
        post_link = el.get('data-post', '')
        id_match = re.search(r'/(\d+)$', post_link)
        if not id_match:
            id_match = re.search(r't\.me/(?:\w+)/(\d+)', msg_text)
        post_id = id_match.group(1) if id_match else ''
        if not post_id or post_id in post_ids:
            continue
        post_ids.add(post_id)

        # Only process structured format
        if not re.search(r'based\s+on\s+android', msg_text, re.IGNORECASE):
            continue

        parsed = parse_structured(msg_text, inner_h, el)
        if parsed is None:
            continue

        parsed['postId'] = post_id
        parsed['link'] = f'https://t.me/{CHANNEL}/{post_id}'
        results.append(parsed)

    return results


def merge_into_roms(parsed: list[dict], roms_path: str) -> tuple[int, int, int]:
    if os.path.exists(roms_path):
        with open(roms_path, 'r', encoding='utf-8') as f:
            roms = json.load(f)
    else:
        roms = []

    added = 0
    updated = 0
    skipped = 0

    def _normalize(name: str) -> str:
        n = name.lower()
        n = re.sub(r'[#]\w+', '', n)
        n = re.sub(r'[^\w\s]', '', n)
        n = re.sub(r'\s+', '', n)
        return n

    for p in parsed:
        matched_name = p['romName']
        p_norm = _normalize(p['romName'])
        for existing in roms:
            e_norm = _normalize(existing['name'])
            if e_norm == p_norm or e_norm in p_norm or p_norm in e_norm:
                matched_name = existing['name']
                break

        dev_name = p['maintainerName'] or 'Unknown'
        dev_username = dev_name
        if not dev_username.startswith('@') and dev_username != 'Unknown':
            url = p['maintainerUrl']
            at = re.search(r't\.me/(\w+)', url)
            if at:
                dev_username = '@' + at.group(1)

        # Parse build date or use today
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
            has_ver = any(v.get('rom') == new_ver['rom'] or v.get('romVer') == new_ver['romVer'] for v in existing.get('versions', []))
            if has_ver:
                skipped += 1
                continue
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

    with open(roms_path, 'w', encoding='utf-8') as f:
        json.dump(roms, f, indent=2, ensure_ascii=False)
        f.write('\n')

    return added, updated, skipped


def main():
    roms_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'roms.json')
    roms_path = os.path.normpath(roms_path)

    print(f'[{CHANNEL}] Fetching channel page...')
    try:
        html = fetch_channel_page()
    except Exception as e:
        print(f'ERROR: Failed to fetch channel: {e}')
        sys.exit(1)

    print(f'[{CHANNEL}] Parsing messages...')
    parsed = parse_messages(html)

    print(f'[{CHANNEL}] Found {len(parsed)} structured ROM posts')
    for p in parsed:
        print(f'  - {p["romName"]} v{p["romVersion"]} | {p["androidVersion"]} | '
              f'{p["deviceName"]} ({p["deviceCodename"]}) | by {p["maintainerName"]} | '
              f'dl={bool(p["downloadLink"])}')

    if not parsed:
        print('No matching posts found. Exiting.')
        return

    print(f'[{CHANNEL}] Merging into roms.json...')
    added, updated, skipped = merge_into_roms(parsed, roms_path)

    print(f'Done: {added} added, {updated} updated, {skipped} skipped')


if __name__ == '__main__':
    main()
