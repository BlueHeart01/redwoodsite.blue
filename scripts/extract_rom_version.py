import re
from typing import Optional


ROM_NAME_ALIASES = {
    'clover': 'CloverOS', 'thecloverproject': 'CloverOS', 'cloverproject': 'CloverOS',
    'lunarisaosp': 'LunarisAOSP', 'lunaris-aosp': 'LunarisAOSP',
    'axionaosp': 'AxionOS', 'axionos': 'AxionOS', 'axion': 'AxionOS',
    'infinityx': 'Infinity X', 'projectinfinityx': 'Infinity X',
    'pixelos': 'Pixel OS', 'pixel os': 'Pixel OS',
    'derpfest': 'DerpFest OS', 'derpfestaosp': 'DerpFest OS',
    'evolutionx': 'Evolution X', 'evox': 'Evolution X',
    'risingos': 'RisingOS', 'rising os': 'RisingOS',
    'ascpos': 'ASCP OS', 'ascp': 'ASCP OS',
    'scpos': 'SCP OS', 'scp': 'SCP OS',
    'fluidos': 'Fluid OS', 'fluid': 'Fluid OS',
    'crDroid': 'crDroid',
    'lineageos': 'LineageOS', 'lineage': 'LineageOS',
    'voltageos': 'VoltageOS', 'voltage': 'VoltageOS',
    'afterlifeos': 'AfterlifeOS', 'afterlife': 'AfterlifeOS',
    'projectmatrixx': 'Matrixx',
}


def extract_rom_version(text: str, known_rom_names: Optional[list[str]] = None) -> tuple[str, str]:
    """Extract Android version and ROM version from a Telegram post.

    Returns (android_version, rom_version).
    - android_version: "Android 16", "Android 17", etc., or "Android 16" as fallback.
    - rom_version: "3.9", "v2.5", "16.2 Bloom", etc., or empty string if not found.
    """
    if not text:
        return ('Android 16', '')

    clean = text.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '')
    clean = re.sub(r'[🍀🌙⭐✨🌸🌟💫⚡🔥💥💎🔮🌈🎯🎉✅❌🔹🔸▪️▫️▶️⏩🔽🔼⬇️⬆️🔗📎📌💡💪👨‍💻📱📦📥📋📝📄\U0001f539\U0001f538\U0001f4e5\U0001f4ce\U0001f449]', '', clean)
    clean = clean.strip()

    lines = clean.split('\n')

    # Extract Android version from anywhere in text
    android_ver = 'Android 16'
    android_match = re.search(r'Android\s*(\d+)', clean, re.IGNORECASE)
    if android_match:
        android_ver = f'Android {android_match.group(1)}'

    # Try to detect ROM name from the first line
    first_line = lines[0].strip() if lines else ''
    knowns = known_rom_names or list(ROM_NAME_ALIASES.values())
    knowns = sorted(set(knowns), key=len, reverse=True)  # longer names first

    detected_rom_name = None
    for name in knowns:
        if name.lower() in first_line.lower():
            detected_rom_name = name
            break
    # Also check alias keys
    if not detected_rom_name:
        for key, name in ROM_NAME_ALIASES.items():
            if key in first_line.lower().replace(' ', '').replace('-', ''):
                detected_rom_name = name
                break

    rom_version = ''

    # Strategy 1: Check first line for "ROMName vX.Y" or "ROMName X.Y" pattern
    if detected_rom_name:
        after_name = first_line[first_line.lower().find(detected_rom_name.lower()) + len(detected_rom_name):]
        # Match vX.Y, vX.Y.Z, X.Y, X.Y.Z, optionally followed by a word (Bloom, Alpha, etc.)
        m = re.match(r'\s+[vV]?(\d[\d.]*(?:\s+\w[\w\s]*?)?)(?:\s*[|/\-–—]\s*|$)', after_name)
        if m:
            candidate = m.group(1).strip()
            if re.match(r'^[\dv]', candidate):  # starts with digit or v
                rom_version = candidate

    # Strategy 2: Scan first 3 lines for version patterns
    if not rom_version:
        for line in lines[:3]:
            line = line.strip()
            if not line:
                continue
            # "ROM Name vX.Y" or "ROM Name X.Y" pattern (even without known name list)
            m = re.search(r'(?:^|\s)([vV]?\d+\.\d+(?:\.\d+)?(?:\s+\w[\w\s]*?)?)(?:\s*[|/\-–—]\s*|$)', line)
            if m:
                candidate = m.group(1).strip()
                if re.match(r'^[vV]?\d', candidate) and len(candidate) < 30:
                    candidate_clean = re.sub(r'\s+', ' ', candidate).strip()
                    # Skip if it looks like a date (2026, 2025, etc.)
                    if not re.match(r'^\d{4}\b', candidate_clean):
                        rom_version = candidate_clean
                        break

    # Strategy 3: Explicit "version:" or "Version:" keyword
    if not rom_version:
        m = re.search(r'(?:version|build|rom\s*version)\s*:?\s*([vV]?\d[\d.]*(?:\s+\w[\w\s]*?)?)', clean, re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()
            if re.match(r'^[vV]?\d', candidate) and len(candidate) < 30:
                rom_version = re.sub(r'\s+', ' ', candidate).strip()

    # Strategy 4: Extract from filename in download link if present
    if not rom_version:
        url_match = re.search(r'https?://\S+', clean)
        if url_match:
            url = url_match.group(0)
            filename = url.split('/')[-1]
            if filename:
                m = re.search(r'(?:-v?|\b)(\d+\.\d+(?:\.\d+)?(?:-\w+)?)', filename, re.IGNORECASE)
                if m:
                    rom_version = m.group(1)

    # Cleanup: if the version starts with "v" or "V", standardize to lowercase "v" prefix form
    if rom_version:
        rom_version = re.sub(r'\s+', ' ', rom_version).strip().rstrip(',;./')
        # If user wants "vX.Y" format, ensure it starts with v
        if re.match(r'^\d', rom_version) and not rom_version.startswith('v'):
            # Keep as-is (e.g. "16.2 Bloom") — don't force v prefix
            pass

    return (android_ver, rom_version)
