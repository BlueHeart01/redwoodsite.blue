"""GitHub Actions auto-import: fetch t.me/s/pocox5proin, parse ROM posts, merge into roms.json"""

import json
import os
import re
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_rom_version import extract_rom_version, ROM_NAME_ALIASES

CHANNEL = 'pocox5proin'
CHANNEL_URL = f'https://t.me/s/{CHANNEL}'
KNOWN_HOSTS = [
    'pixeldrain.com', 'sourceforge.net', 'gofile.io', 'mega.nz',
    'drive.google.com', 'mediafire.com', 'androidfilehost.com',
    'devuploads.com', 'sf.net',
]
KNOWN_ROMS = sorted(set(ROM_NAME_ALIASES.values()), key=len, reverse=True)


def extract_links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, 'lxml')
    links = []
    for a in soup.find_all('a'):
        href = a.get('href', '')
        if href and not href.startswith('tg://') and 't.me/' not in href:
            links.append(href)
    return links


def extract_download_from_telegram(html: str, all_links: list[str]) -> str:
    soup = BeautifulSoup(html, 'lxml')
    text = soup.get_text(separator=' ', strip=True)

    # CASE A: "DOWNLOAD:" then keyword lines
    m = re.search(r'download\s*:\s*([\s\S]*?)(?=\n[#A-Z]|$)', text, re.IGNORECASE)
    if m:
        block = m.group(1)
        for kw in ['ROM', 'GAPPS', 'Gapps', 'HERE']:
            kwm = re.search(rf'{re.escape(kw)}\s*:\s*(https?://\S+)', block, re.IGNORECASE)
            if kwm:
                return kwm.group(1)

    # CASE B: inline "Download ROM:", "Download Gapps:", "Download HERE:"
    m = re.search(r'download\s+(rom|gapps|here)\s*:\s*(https?://\S+)', text, re.IGNORECASE)
    if m:
        return m.group(2)

    # CASE C: <a> whose text is exactly "DOWNLOAD" or "Download"
    for a in soup.find_all('a'):
        txt = (a.get_text(strip=True) or '')
        href = a.get('href', '')
        if txt.upper() == 'DOWNLOAD' or re.match(r'^download\b', txt, re.IGNORECASE):
            if href and not href.startswith('tg://'):
                return href

    # CASE D: <a> whose text contains "download" and href matches known host
    for a in soup.find_all('a'):
        txt = (a.get_text(strip=True) or '')
        href = a.get('href', '')
        if re.search(r'download', txt, re.IGNORECASE):
            for host in KNOWN_HOSTS:
                if host in href:
                    return href

    return find_download_link(all_links)


def find_download_link(links: list[str]) -> str:
    if not links:
        return ''
    direct = next((h for h in links if re.search(r'\.(zip|img|tar\.gz|tar\.xz|tgz)$', h, re.IGNORECASE) or '/download' in h), None)
    if direct:
        return direct
    by_host = next((h for h in links if any(dh in h for dh in KNOWN_HOSTS)), None)
    if by_host:
        return by_host
    gh_rel = next((h for h in links if re.search(r'github\.com/[^/]+/[^/]+/(?:releases|raw)', h, re.IGNORECASE)), None)
    if gh_rel:
        return gh_rel
    other = next((h for h in links if not re.match(r'github\.com/[^/]+/?$', h) and 't.me/' not in h), None)
    return other or (links[0] if links else '')


def detect_build_type(text: str) -> str:
    lower = text.lower()
    has_gapps = bool(re.search(r'\bgapps\b', lower))
    has_vanilla = 'vanilla' in lower
    if has_gapps and not has_vanilla:
        return 'GApps'
    if has_vanilla and not has_gapps:
        return 'Vanilla'
    return ''


def fetch_channel_page() -> str:
    resp = requests.get(CHANNEL_URL, timeout=30, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    })
    resp.raise_for_status()
    return resp.text


def parse_messages(html: str) -> list[dict]:
    soup = BeautifulSoup(html, 'lxml')
    results = []
    post_ids = set()

    msg_wraps = soup.select('.tgme_widget_message_wrap, .tgme_widget_message')

    for el in msg_wraps:
        post_el = el.select_one('.tgme_widget_message_text') or el
        inner_h = str(post_el)
        # Reconstruct inner HTML without the wrapper tags
        inner_h = re.sub(r'^<[^>]+>', '', inner_h)
        inner_h = re.sub(r'</[^>]+>$', '', inner_h)

        txt = post_el.get_text(strip=True)
        lower = txt.lower()

        has_keyword = any(k in lower for k in ['#rom', '#redwood', 'android', 'rom', 'build', 'gapps', 'kernel'])
        if not has_keyword or 'redwood' not in lower:
            continue
        if any(k in lower for k in ['zkui', 'turboos']):
            continue
        if 'miui' in lower and 'custom' not in lower and 'aosp' not in lower and 'port' not in lower:
            continue

        # Extract post ID
        post_link = el.get('data-post', '')
        id_match = re.search(r'/(\d+)$', post_link)
        if not id_match:
            id_match = re.search(r't\.me/(?:\w+)/(\d+)', txt)
        post_id = id_match.group(1) if id_match else ''
        if not post_id or post_id in post_ids:
            continue
        post_ids.add(post_id)

        # Convert inner HTML to plain text with newlines
        msg_text = inner_h
        msg_text = re.sub(r'<br\s*/?>', '\n', msg_text, flags=re.IGNORECASE)
        msg_text = re.sub(r'<[^>]+>', '', msg_text).strip()

        # Clean emojis for matching
        clean_text = re.sub(r'[\U0001F300-\U0001FAFF\U00002700-\U000027BF\u25AA\u25B6\u25C0\u2B50\u2022\u2013\u2043]', '', msg_text)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        clean_lower = clean_text.lower().replace('-', '').replace(' ', '')

        # ROM name detection
        rom_name = 'New ROM'
        for key, val in ROM_NAME_ALIASES.items():
            if key in clean_lower:
                rom_name = val
                break
        if rom_name == 'New ROM':
            for n in KNOWN_ROMS:
                if n.lower().replace('-', '').replace(' ', '') in clean_lower:
                    rom_name = n
                    break
        if rom_name == 'New ROM':
            bold = re.search(r'\*\*(.+?)\*\*', msg_text)
            if bold:
                rom_name = bold.group(1).strip()
            else:
                rom_name = msg_text.split('\n')[0].strip()
                rom_name = re.sub(r'^[\s#\u2022\-*]+', '', rom_name).strip()

        # Developer detection
        dev = 'Unknown'
        by_link = re.search(r'[Bb]y[^<]*(?:<[^>]*>)*\s*<a[^>]*href="https://t\.me/(\w+)[^>]*>', inner_h)
        if by_link:
            dev = '@' + by_link.group(1)
        else:
            by_match = re.search(r'[Bb]y\s*(?:</?[^>]+>)*\s*@?(\w[\w.]*)', inner_h)
            if by_match:
                dev = by_match.group(1)
                if not dev.startswith('@'):
                    dev = '@' + dev
        if dev in ('Unknown', '@', ''):
            fwd = el.select_one('.tgme_widget_message_forwarded_from_name')
            if fwd:
                fwd_text = fwd.get_text(strip=True)
                at = re.search(r'@(\w+)', fwd_text)
                if at:
                    dev = '@' + at.group(1)
                elif fwd_text and len(fwd_text) < 30:
                    dev = fwd_text
        if dev in ('Unknown', '@'):
            at_match = re.search(r'@(\w[\w.]*)', msg_text)
            if at_match:
                dev = '@' + at_match.group(1)

        # Status detection
        status = 'unofficial'
        if '#official' in lower and '#unofficial' not in lower:
            status = 'official'
        if '#unofficial' in lower or '#unoffical' in lower:
            status = 'unofficial'

        # Version extraction
        and_ver, rom_ver = extract_rom_version(msg_text, list(KNOWN_ROMS))
        if not and_ver:
            and_ver = 'Android 16'

        # Download link extraction
        all_links = extract_links_from_html(inner_h)
        dl = extract_download_from_telegram(inner_h, all_links)

        # Changelog extraction
        changelog = ''
        cl_match = re.search(r'Changelog[:\s]*([\s\S]*?)(?=Notes|Credits|Join|Donate|⭐|💬|$)', msg_text, re.IGNORECASE)
        if cl_match:
            changelog = cl_match.group(1).strip()
        else:
            cl_lines = [l for l in msg_text.split('\n') if re.match(r'^[\u25aa\u2022\-*]\s*.*\S', l)]
            if len(cl_lines) > 1:
                changelog = '\n'.join(l.strip() for l in cl_lines)
        changelog = re.sub(r'[\U0001F300-\U0001FAFF\u2700-\u27BF]\s*', '', changelog).strip()

        # Description extraction
        desc = ''
        clean_lines = [
            re.sub(r'^[•\-*>#]\s*', '', l).strip()
            for l in msg_text.split('\n')
            if l.strip()
        ]
        for line in clean_lines:
            if (line and not line.startswith('#')
                    and not re.match(r'^(Download|Recovery|Build|By|@)', line, re.IGNORECASE)
                    and len(line) > 15):
                desc = line[:200]
                break
        if not desc:
            desc = f'{rom_name} for Redwood'

        # Screenshots extraction
        screenshots = []
        seen = set()
        for photo in el.select('.tgme_widget_message_photo_wrap'):
            style = photo.get('style', '')
            u = re.search(r'url\([\'"]?([^\'"]+)[\'"]?\)', style)
            if u and u.group(1) not in seen:
                seen.add(u.group(1))
                screenshots.append(u.group(1))
        for img in post_el.select('img'):
            src = img.get('src', '')
            if src.startswith('http') and src not in seen:
                seen.add(src)
                screenshots.append(src)

        banner = screenshots[0] if screenshots else ''

        # Build type detection
        build_type = detect_build_type(msg_text)

        results.append({
            'postId': post_id,
            'romName': rom_name,
            'dev': dev,
            'downloadUrl': dl,
            'status': status,
            'andVer': and_ver,
            'romVer': rom_ver,
            'changelog': changelog,
            'desc': desc,
            'banner': banner,
            'screenshots': screenshots,
            'link': f'https://t.me/{CHANNEL}/{post_id}',
            'buildType': build_type,
        })

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

    for p in parsed:
        # Normalize ROM name
        matched_name = p['romName']
        for existing in roms:
            if existing['name'].lower().replace('-', '').replace(' ', '') == p['romName'].lower().replace('-', '').replace(' ', ''):
                matched_name = existing['name']
                break

        new_ver = {
            'ver': p['andVer'],
            'andVer': p['andVer'],
            'date': datetime.now().strftime('%Y-%m-%d'),
            'rom': p['downloadUrl'] or p['link'],
            'boot': '#',
            'vendor_boot': '#',
            'dtbo': '#',
            'romVer': p['romVer'] or '',
            'vDev': p['dev'] if p['dev'] != 'Unknown' else '',
            'vDevInfo': ('Telegram: ' + p['dev']) if p['dev'] != 'Unknown' else '',
            'vChangelog': p['changelog'] or '',
        }

        existing = next((r for r in roms if r['name'] == matched_name), None)

        if existing:
            has_ver = any(v.get('rom') == new_ver['rom'] or v.get('romVer') == new_ver['romVer'] for v in existing.get('versions', []))
            if has_ver:
                skipped += 1
                continue
            existing.setdefault('versions', []).append(new_ver)
            if p['dev'] and p['dev'] != 'Unknown':
                existing['dev'] = p['dev']
                existing['devInfo'] = f'Telegram: {p["dev"]}'
            if p['changelog']:
                existing['changelog'] = p['changelog']
            if p['banner']:
                existing['banner'] = p['banner']
            if p['desc'] and 'for Redwood' not in p['desc']:
                existing['desc'] = p['desc']
            if p['buildType']:
                existing['buildType'] = p['buildType']
            if p['screenshots']:
                existing.setdefault('screenshots', [])
                for s in p['screenshots']:
                    if s not in existing['screenshots']:
                        existing['screenshots'].append(s)
            updated += 1
        else:
            entry = {
                'name': matched_name,
                'status': p['status'],
                'dev': p['dev'],
                'devInfo': f'Telegram: {p["dev"]}' if p['dev'] != 'Unknown' else '',
                'desc': p['desc'] or f'{matched_name} for Redwood',
                'downloads': 0,
                'banner': p['banner'],
                'screenshots': p['screenshots'],
                'changelog': p['changelog'] or '',
                'buildType': p['buildType'] or '',
                'tags': [],
                'supportGroup': '',
                'xdaLink': '',
                'firmwareLink': '',
                'recoveryLink': '',
                'sourceCode': '',
                'donateLink': '',
                'knownIssues': '',
                'requirements': 'Unlocked bootloader, latest firmware',
                'isActive': True,
                'versions': [new_ver],
            }
            if p['dev'] != 'Unknown':
                entry['icon'] = p['dev'][1:2].upper() if p['dev'].startswith('@') else p['dev'][:1].upper()
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

    print(f'[{CHANNEL}] Found {len(parsed)} ROM posts')
    for p in parsed:
        print(f'  - {p["romName"]} ({p["andVer"]}) by {p["dev"]} | ver={p["romVer"]} | status={p["status"]} | dl={bool(p["downloadUrl"])}')

    if not parsed:
        print('No new ROMs found. Exiting.')
        return

    print(f'[{CHANNEL}] Merging into roms.json...')
    added, updated, skipped = merge_into_roms(parsed, roms_path)

    print(f'Done: {added} added, {updated} updated, {skipped} skipped')


if __name__ == '__main__':
    main()
