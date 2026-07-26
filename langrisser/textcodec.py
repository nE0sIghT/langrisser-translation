#!/usr/bin/env python3
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

TAG_RE = re.compile(r"<\$(?P<h>[0-9A-Fa-f]{4})>")


def load_token_map_json(path: Path) -> Dict[int, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: Dict[int, str] = {}
    for k, v in raw.items():
        try:
            t = int(k, 16)
        except Exception:
            continue
        if isinstance(v, str) and v:
            out[t] = v
    return out


def load_tbl(path: Path) -> Dict[int, str]:
    mp: Dict[int, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.rstrip("\n")
        if not s.strip() or s.lstrip().startswith("#") or "=" not in s:
            continue
        a, b = s.split("=", 1)
        a = a.strip()
        b = b
        try:
            tok = int(a, 16)
        except Exception:
            continue
        mp[tok] = b
    return mp


def save_tbl(path: Path, mp: Dict[int, str]) -> None:
    lines = [
        "# Langrisser V token table",
        "# Format: HHHH=text",
    ]
    for tok in sorted(mp):
        lines.append(f"{tok:04X}={mp[tok]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def decode_words(words: List[int], tok2txt: Dict[int, str]) -> str:
    out: List[str] = []
    for w in words:
        if w in tok2txt:
            out.append(tok2txt[w])
        else:
            out.append(f"<${w:04X}>")
    return "".join(out)


def _build_reverse(tok2txt: Dict[int, str]) -> Tuple[Dict[str, int], List[str]]:
    txt2tok: Dict[str, int] = {}
    for tok in sorted(tok2txt):
        txt = tok2txt[tok]
        if txt and txt not in txt2tok:
            txt2tok[txt] = tok
    keys = sorted(txt2tok.keys(), key=len, reverse=True)
    return txt2tok, keys


def encode_text(text: str, tok2txt: Dict[int, str]) -> List[int]:
    txt2tok, keys = _build_reverse(tok2txt)
    out: List[int] = []
    i = 0
    n = len(text)
    while i < n:
        m = TAG_RE.match(text, i)
        if m:
            out.append(int(m.group("h"), 16))
            i = m.end()
            continue
        matched = False
        for k in keys:
            if text.startswith(k, i):
                out.append(txt2tok[k])
                i += len(k)
                matched = True
                break
        if matched:
            continue
        ch = text[i]
        raise ValueError(f"cannot encode char at pos {i}: {ch!r}; add to .tbl or use <$XXXX>")
    return out
