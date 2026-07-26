#!/usr/bin/env python3
"""Generate record-by-record JP/reference/target script review pages.

The JP dump is authoritative and drives the page list. Each record shows the
existing reference translation, the target translation, speaker plates,
control signatures, and page/line boundaries. Durable review decisions come
from the target language pack's review_status.csv.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

from langrisser.project import (
    COMMON_SCENARIO_MAP,
    add_language_args,
    language_from_args,
    load_language,
)
from langrisser.rewrap import semantic_plate_slots, speaker_pool_sizes
from langrisser.scen import FORCE_PAGE_BREAK, TAG_RE
from langrisser.validate_translation import control_signature

JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
LINE_BREAK = "<$FFFC>"
PAGE_BREAK = "<$FFFD>"
STATUS_FIELDS = ("chunk", "record", "target_done", "reference_checked", "note")

CSS = """
:root{--bg:#171a1d;--panel:#20252a;--line:#394149;--text:#e8e3d8;
--muted:#999b98;--jp:#f1d08a;--ref:#9cc5dd;--target:#a9d9a1;
--bad:#ff8f7f;--good:#79c98c;--warn:#e4bf68}
*{box-sizing:border-box}
body{font-family:"DejaVu Sans",sans-serif;background:var(--bg);color:var(--text);
margin:0;padding:22px}
a{color:var(--ref)}
h1{font:700 24px Georgia,serif;margin:0 0 8px}
.sub,.meta{color:var(--muted);font-size:13px}
.toolbar{position:sticky;top:0;z-index:2;background:rgba(23,26,29,.96);
border-bottom:1px solid var(--line);padding:10px 0;margin-bottom:14px}
.toolbar label{margin-right:18px}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
.badge{display:inline-block;border:1px solid var(--line);border-radius:2px;
padding:3px 7px;font-size:12px;background:var(--panel)}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{border-collapse:collapse;width:100%;table-layout:fixed}
td,th{border:1px solid var(--line);padding:9px;vertical-align:top}
th{background:#262c31;text-align:left}
td.idx{width:58px;text-align:right;color:var(--muted)}
td.text{font-size:15px;line-height:1.45;overflow-wrap:anywhere}
td.jp{color:var(--jp)}td.ref{color:var(--ref)}td.target{color:var(--target)}
tr.issue td.idx{border-left:4px solid var(--bad)}
tr.pending td.idx{border-left:4px solid var(--warn)}
.tag{color:#8a9095;font:11px monospace}
.linebreak{display:inline-block;color:#77808a;font:10px monospace;margin-left:4px}
.pagebreak{display:block;border-top:1px dashed #6d747a;color:#9ea5ab;
font:10px monospace;margin:7px 0 3px;padding-top:3px}
.speaker{color:var(--muted);font-size:12px;margin-bottom:5px}
.controls{color:#7f878e;font:10px monospace;margin-top:7px}
.flags{margin-top:7px}.flags .badge{margin:2px 3px 0 0}
.note{margin-top:6px;color:var(--warn);font-size:12px}
.missing{color:var(--bad);font-style:italic}
.chunks{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));
gap:9px;padding:0;list-style:none}
.chunks li{border:1px solid var(--line);background:var(--panel);padding:10px}
.chunks a{font-weight:bold}.chunks .meta{margin-top:4px}
body.hide-complete tr.complete{display:none}
body.issues-only tr:not(.issue){display:none}
"""

JS = """
function toggleClass(name, enabled) {
  document.body.classList.toggle(name, enabled);
}
"""


@dataclass(frozen=True)
class ReviewStatus:
    target_done: bool = False
    reference_checked: bool = False
    note: str = ""


@dataclass(frozen=True)
class ChunkSummary:
    chunk: int
    records: int
    target_done: int
    reference_checked: int
    issues: int
    context: str


def parse_bool(value: str, location: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("", "0", "no", "false"):
        return False
    if normalized in ("1", "yes", "true"):
        return True
    raise SystemExit(f"{location}: expected boolean 0/1, got {value!r}")


def load_statuses(path: Path) -> dict[tuple[int, int], ReviewStatus]:
    if not path.exists():
        return {}
    out: dict[tuple[int, int], ReviewStatus] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != STATUS_FIELDS:
            raise SystemExit(
                f"{path}: expected CSV header {','.join(STATUS_FIELDS)}"
            )
        for line_no, row in enumerate(reader, 2):
            location = f"{path}:{line_no}"
            try:
                key = (int(row["chunk"]), int(row["record"]))
            except ValueError as exc:
                raise SystemExit(f"{location}: invalid chunk/record") from exc
            if key in out:
                raise SystemExit(f"{location}: duplicate status for {key[0]}:{key[1]}")
            out[key] = ReviewStatus(
                target_done=parse_bool(row["target_done"], location),
                reference_checked=parse_bool(row["reference_checked"], location),
                note=row["note"].strip(),
            )
    return out


def read_records(path: Path) -> dict[int, str]:
    out: dict[int, str] = {}
    if not path.exists():
        return out
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "\t" not in raw or raw.startswith("#"):
            continue
        idx, text = raw.split("\t", 1)
        try:
            record = int(idx)
        except ValueError as exc:
            raise SystemExit(f"{path}:{line_no}: invalid record index {idx!r}") from exc
        if record in out:
            raise SystemExit(f"{path}:{line_no}: duplicate record {record}")
        out[record] = text
    return out


def clean_text(text: str) -> str:
    return TAG_RE.sub("", text.replace(FORCE_PAGE_BREAK, "")).strip()


def has_japanese(text: str) -> bool:
    return bool(JP_RE.search(clean_text(text).replace("・", "")))


def pretty(text: str) -> str:
    if not text:
        return "<span class='missing'>missing</span>"
    out: list[str] = []
    pos = 0
    normalized = text.replace(FORCE_PAGE_BREAK, PAGE_BREAK)
    for match in TAG_RE.finditer(normalized):
        if match.start() > pos:
            out.append(html.escape(normalized[pos : match.start()]))
        tag = match.group(0).upper()
        escaped = html.escape(tag)
        if tag == LINE_BREAK:
            out.append(
                f"<span class='tag'>{escaped}</span>"
                "<span class='linebreak'>LINE</span><br>"
            )
        elif tag == PAGE_BREAK:
            out.append(
                f"<span class='pagebreak'>{escaped} PAGE</span>"
            )
        else:
            out.append(f"<span class='tag'>{escaped}</span>")
        pos = match.end()
    if pos < len(normalized):
        out.append(html.escape(normalized[pos:]))
    return "".join(out)


def signature_html(text: str) -> str:
    if not text:
        return ""
    signature = control_signature(text)
    value = " ".join(f"&lt;${tag}&gt;" for tag in signature)
    return f"<div class='controls'>control: {value or '(none)'}</div>"


def plate_label(
    slot: int | None,
    records: dict[int, str],
    pool_size: int,
) -> str:
    if slot is None:
        return "(no plate)"
    if slot < 0:
        return "(location/crowd)"
    if slot >= pool_size:
        return f"(invalid slot {slot})"
    name = clean_text(records.get(slot + 1, ""))
    return name or f"(slot {slot})"


def cell(
    css_class: str,
    text: str,
    speaker: str,
    flags: list[tuple[str, str]],
    note: str = "",
) -> str:
    badges = "".join(
        f"<span class='badge {kind}'>{html.escape(label)}</span>"
        for kind, label in flags
    )
    note_html = (
        f"<div class='note'>note: {html.escape(note)}</div>" if note else ""
    )
    speaker_html = (
        f"<div class='speaker'>plate: {html.escape(speaker)}</div>"
        if speaker else ""
    )
    return (
        f"<td class='text {css_class}'>"
        f"{speaker_html}{pretty(text)}{signature_html(text)}"
        f"<div class='flags'>{badges}</div>{note_html}</td>"
    )


def scenario_contexts(path: Path) -> dict[int, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, list[str]] = {}

    def add(chunk: int, label: str) -> None:
        out.setdefault(chunk, []).append(label)

    for chunk in data["quiz"]["chunks"]:
        add(chunk, "startup quiz")
    for chunk in data["tutorial_battle"]["chunks"]:
        add(chunk, "tutorial battle")
    count = int(data["scenario_rule"]["scenarios"])
    for scenario in range(1, count + 1):
        add(44 + scenario, f"scenario {scenario} / scene A")
        add(scenario, f"scenario {scenario} / battle")
        add(86 + scenario, f"scenario {scenario} / scene B")
    for name, value in data["optional_maps"].items():
        if not isinstance(value, dict):
            continue
        add(int(value["intro"]), f"optional {name} / intro")
        add(int(value["battle"]), f"optional {name} / battle")
    for chunk in data["optional_maps"].get("epilogues_unassigned", []):
        add(int(chunk), "unassigned epilogue")
    add(int(data["recaps"]["world_situation"]), "world recap")
    add(int(data["recaps"]["character_bios"]), "character bios")
    return out


def render_chunk(
    chunk: int,
    jp: dict[int, str],
    reference: dict[int, str],
    target: dict[int, str],
    statuses: dict[tuple[int, int], ReviewStatus],
    slots: dict[int, int | None],
    pool_size: int,
    reference_label: str,
    target_label: str,
    include_reference: bool,
    context: str,
) -> tuple[str, ChunkSummary]:
    rows: list[str] = []
    target_done = reference_checked = issues = 0
    record_ids = sorted(set(jp) | set(reference) | set(target))
    for record in record_ids:
        jp_text = jp.get(record, "")
        ref_text = reference.get(record, "")
        target_text = target.get(record, "")
        status = statuses.get((chunk, record), ReviewStatus())
        row_issues: list[str] = []

        if record not in jp:
            row_issues.append("record absent from JP")
        if include_reference and record not in reference:
            row_issues.append(f"missing {reference_label}")
        if record not in target:
            row_issues.append(f"missing {target_label}")

        jp_sig = control_signature(jp_text) if jp_text else []
        if ref_text and control_signature(ref_text) != jp_sig:
            row_issues.append(f"{reference_label} control mismatch")
        if target_text and control_signature(target_text) != jp_sig:
            row_issues.append(f"{target_label} control mismatch")
        if ref_text and has_japanese(ref_text):
            row_issues.append(f"Japanese remains in {reference_label}")
        if target_text and has_japanese(target_text):
            row_issues.append(f"Japanese remains in {target_label}")
        if status.target_done and not target_text:
            row_issues.append("target marked done but missing")
        if status.reference_checked and not ref_text:
            row_issues.append("reference marked checked but missing")

        if status.target_done:
            target_done += 1
        if status.reference_checked:
            reference_checked += 1
        if row_issues:
            issues += 1

        row_class = "issue" if row_issues else (
            "complete" if status.target_done and (
                status.reference_checked or not include_reference
            ) else "pending"
        )
        status_badges = [
            (
                "good" if status.target_done else "warn",
                f"{target_label}: {'done' if status.target_done else 'pending'}",
            )
        ]
        if include_reference:
            status_badges.append(
                (
                    "good" if status.reference_checked else "warn",
                    f"{reference_label} vs JP: "
                    f"{'checked' if status.reference_checked else 'pending'}",
                )
            )
        status_badges.extend(("bad", problem) for problem in row_issues)

        slot = slots.get(record)
        jp_speaker = plate_label(slot, jp, pool_size) if record in slots else ""
        ref_speaker = (
            plate_label(slot, reference, pool_size) if record in slots else ""
        )
        target_speaker = (
            plate_label(slot, target, pool_size) if record in slots else ""
        )

        cells = [
            f"<td class='idx'><a id='r{record}' href='#r{record}'>{record}</a></td>",
            cell("jp", jp_text, jp_speaker, []),
        ]
        if include_reference:
            cells.append(cell("ref", ref_text, ref_speaker, []))
        cells.append(
            cell(
                "target",
                target_text,
                target_speaker,
                status_badges,
                status.note,
            )
        )
        rows.append(f"<tr class='{row_class}'>{''.join(cells)}</tr>")

    columns = (
        f"<th>#</th><th>JP source</th><th>{html.escape(reference_label)}</th>"
        f"<th>{html.escape(target_label)}</th>"
        if include_reference else
        f"<th>#</th><th>JP source</th><th>{html.escape(target_label)}</th>"
    )
    summary = ChunkSummary(
        chunk=chunk,
        records=len(record_ids),
        target_done=target_done,
        reference_checked=reference_checked,
        issues=issues,
        context=context,
    )
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>chunk {chunk:03d} review</title><style>{CSS}</style>"
        f"<script>{JS}</script></head><body>"
        f"<h1>SCEN chunk {chunk:03d}</h1>"
        f"<div class='sub'>{html.escape(context or 'unassigned chunk')}</div>"
        "<div class='summary'>"
        f"<span class='badge'>records: {summary.records}</span>"
        f"<span class='badge'>target done: {summary.target_done}</span>"
        + (
            f"<span class='badge'>reference checked: "
            f"{summary.reference_checked}</span>"
            if include_reference else ""
        )
        + f"<span class='badge {'bad' if issues else 'good'}'>issues: {issues}</span>"
        "</div><div class='toolbar'>"
        "<label><input type='checkbox' "
        "onchange=\"toggleClass('hide-complete',this.checked)\"> hide complete</label>"
        "<label><input type='checkbox' "
        "onchange=\"toggleClass('issues-only',this.checked)\"> issues only</label>"
        "<a href='index.html'>index</a></div>"
        f"<table><thead><tr>{columns}</tr></thead><tbody>{''.join(rows)}</tbody>"
        "</table></body></html>"
    )
    return page, summary


def render_index(
    summaries: list[ChunkSummary],
    reference_label: str,
    target_label: str,
    include_reference: bool,
) -> str:
    total_records = sum(item.records for item in summaries)
    total_target = sum(item.target_done for item in summaries)
    total_reference = sum(item.reference_checked for item in summaries)
    total_issues = sum(item.issues for item in summaries)
    cards = []
    for item in summaries:
        context = item.context or "unassigned chunk"
        cards.append(
            "<li>"
            f"<a href='chunk_{item.chunk:03d}.html'>chunk {item.chunk:03d}</a>"
            f"<div>{html.escape(context)}</div>"
            f"<div class='meta'>{item.target_done}/{item.records} target done"
            + (
                f" | {item.reference_checked}/{item.records} reference checked"
                if include_reference else ""
            )
            + f" | <span class='{'bad' if item.issues else 'good'}'>"
            f"{item.issues} issues</span></div></li>"
        )
    relation = (
        f"JP / {html.escape(reference_label)} / {html.escape(target_label)}"
        if include_reference else f"JP / {html.escape(target_label)}"
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>script review</title><style>{CSS}</style></head><body>"
        f"<h1>{relation} script review</h1>"
        "<div class='summary'>"
        f"<span class='badge'>records: {total_records}</span>"
        f"<span class='badge'>target done: {total_target}</span>"
        + (
            f"<span class='badge'>reference checked: {total_reference}</span>"
            if include_reference else ""
        )
        + f"<span class='badge {'bad' if total_issues else 'good'}'>"
        f"issues: {total_issues}</span></div>"
        f"<ul class='chunks'>{''.join(cards)}</ul></body></html>"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    add_language_args(ap)
    ap.add_argument("chunks", nargs="*", type=int)
    ap.add_argument(
        "--scenario",
        help="Review one scenario: 1..36, quiz, or opt:<name>.",
    )
    ap.add_argument("--reference-lang", default="en")
    ap.add_argument("--jp-dump", default="work/scriptdump")
    ap.add_argument("--translation-root", default=None,
                    help="Override the target language pack's text root.")
    ap.add_argument("--reference-root", default=None,
                    help="Override the reference language pack's text root.")
    ap.add_argument("--status", default=None)
    ap.add_argument("--scen", default="work/extracted/SCEN.DAT")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--stem", default="SCEN")
    args = ap.parse_args()

    target_lang = language_from_args(args)
    reference_lang = load_language(args.reference_lang, args.lang_root)
    include_reference = reference_lang.code != target_lang.code
    target_root = (
        Path(args.translation_root)
        if args.translation_root else target_lang.dump_root
    )
    reference_root = (
        Path(args.reference_root)
        if args.reference_root else reference_lang.dump_root
    )
    status_path = Path(args.status) if args.status else target_lang.review_status
    statuses = load_statuses(status_path)

    source_dir = Path(args.jp_dump) / args.stem
    source_files = sorted(source_dir.glob("chunk_*.txt"))
    if not source_files:
        raise SystemExit(
            f"no JP source chunks in {source_dir}; run scendump.py first"
        )
    if args.scenario and args.chunks:
        raise SystemExit("use either positional chunks or --scenario, not both")
    scenario_map = json.loads(COMMON_SCENARIO_MAP.read_text(encoding="utf-8"))
    selected_order: list[int] = []
    if args.scenario:
        selector = args.scenario
        if selector == "quiz":
            selected_order = [
                *map(int, scenario_map["quiz"]["chunks"]),
                *map(int, scenario_map["tutorial_battle"]["chunks"]),
            ]
        elif selector.startswith("opt:"):
            name = selector[4:]
            optional = scenario_map["optional_maps"].get(name)
            if not isinstance(optional, dict):
                raise SystemExit(f"unknown optional scenario {name!r}")
            selected_order = [int(optional["intro"]), int(optional["battle"])]
        else:
            try:
                scenario = int(selector)
            except ValueError as exc:
                raise SystemExit(
                    "--scenario must be 1..36, quiz, or opt:<name>"
                ) from exc
            scenario_count = int(scenario_map["scenario_rule"]["scenarios"])
            if not 1 <= scenario <= scenario_count:
                raise SystemExit(f"scenario must be 1..{scenario_count}")
            selected_order = [44 + scenario, scenario, 86 + scenario]
    elif args.chunks:
        selected_order = args.chunks

    source_by_chunk = {
        int(path.stem.split("_")[1]): path for path in source_files
    }
    if selected_order:
        missing = set(selected_order) - set(source_by_chunk)
        if missing:
            raise SystemExit(
                "JP source chunks not found: "
                + ", ".join(str(value) for value in sorted(missing))
            )
        source_files = [source_by_chunk[chunk] for chunk in selected_order]
    else:
        empty_chunks = set(map(int, scenario_map.get("empty_chunks", [])))
        source_files = [
            path for path in source_files
            if int(path.stem.split("_")[1]) not in empty_chunks
        ]

    scen_path = Path(args.scen)
    if not scen_path.exists():
        raise SystemExit(
            f"{scen_path} not found; extract SCEN.DAT before generating review"
        )
    slots_by_chunk = semantic_plate_slots(scen_path)
    pool_sizes = speaker_pool_sizes(scen_path)
    contexts = scenario_contexts(COMMON_SCENARIO_MAP)

    source_keys: set[tuple[int, int]] = set()
    out_dir = Path(args.out_dir) if args.out_dir else target_lang.review_root
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale_page in out_dir.glob("chunk_*.html"):
        stale_page.unlink()
    summaries: list[ChunkSummary] = []
    for source_path in source_files:
        chunk = int(source_path.stem.split("_")[1])
        jp = read_records(source_path)
        reference = read_records(
            reference_root / args.stem / source_path.name
        )
        target = read_records(target_root / args.stem / source_path.name)
        source_keys.update((chunk, record) for record in jp)
        context = "; ".join(contexts.get(chunk, []))
        page, summary = render_chunk(
            chunk=chunk,
            jp=jp,
            reference=reference,
            target=target,
            statuses=statuses,
            slots=slots_by_chunk.get(chunk, {}),
            pool_size=pool_sizes.get(chunk, 0),
            reference_label=reference_lang.label,
            target_label=target_lang.label,
            include_reference=include_reference,
            context=context,
        )
        out_path = out_dir / f"chunk_{chunk:03d}.html"
        out_path.write_text(page, encoding="utf-8")
        summaries.append(summary)
        print(
            f"wrote {out_path}: records={summary.records} "
            f"target_done={summary.target_done} "
            f"reference_checked={summary.reference_checked} "
            f"issues={summary.issues}"
        )

    stale = sorted(set(statuses) - source_keys) if not selected_order else []
    if stale:
        preview = ", ".join(f"{chunk}:{record}" for chunk, record in stale[:10])
        suffix = " ..." if len(stale) > 10 else ""
        raise SystemExit(f"{status_path}: stale review rows: {preview}{suffix}")

    index = render_index(
        summaries,
        reference_label=reference_lang.label,
        target_label=target_lang.label,
        include_reference=include_reference,
    )
    index_path = out_dir / "index.html"
    index_path.write_text(index, encoding="utf-8")
    print(f"wrote {index_path}")


if __name__ == "__main__":
    main()
