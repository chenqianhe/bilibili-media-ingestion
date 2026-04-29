import re


BVID_RE = re.compile(r"(?i)(BV[0-9A-Za-z]{10})")


def extract_bvid(input_text: str) -> str | None:
    match = BVID_RE.search(input_text.strip())
    if not match:
        return None
    bvid = match.group(1)
    return f"BV{bvid[2:]}"

