from __future__ import annotations

import itertools
import math
import re
import statistics
from collections import Counter
from difflib import get_close_matches
from typing import Any, Sequence

import numpy as np

from component.core import (
    ALL_ROLE_WORDS,
    Cell,
    ColumnInterval,
    ConsensusResult,
    DIRECTION_MARKER_RE,
    EXTRACTION_CONFIDENCE_CONFLICT,
    EXTRACTION_CONFIDENCE_HIGH,
    EXTRACTION_CONFIDENCE_LOW,
    EXTRACTION_CONFIDENCE_MEDIUM,
    EXTRACTION_CONFIDENCE_OVERLAID,
    FOOTER_BOILERPLATE_RE,
    Line,
    MONTHS,
    NUMERIC_ROLES,
    NumericTokenProfile,
    OPENING_RE,
    PURE_TIME_RE,
    ROLE_KEYS,
    RowBand,
    STOP_RE,
    STRICT_ID_DECIMAL_RE,
    STRICT_INTL_DECIMAL_RE,
    SUMMARY_INTERCEPT_TOKENS,
    Span,
    State,
    TABLE_HEADER_KEYWORDS,
    Word,
    band_words,
)
from component.parsing import (
    has_alpha_contamination, is_int_amount, is_money, marker_direction, match_date, norm_money,
    normalize_time_ob, parse_amount, parse_time, validate_financial_grammar,
)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def median_height(words: Sequence[Word]) -> float:
    heights = [w.h for w in words if len(w.text) >= 2] or [w.h for w in words]
    return float(statistics.median(heights)) if heights else 10.0


def make_cell(words: list[Word]) -> Cell:
    text = " ".join(w.text for w in words)
    has_marker = len(words) >= 2 and bool(DIRECTION_MARKER_RE.match(words[-1].text.upper()))
    num_x1 = words[-2].x1 if has_marker else words[-1].x1
    contaminated = has_alpha_contamination(text)
    money = (not contaminated) and is_money(text)
    is_int = (not money) and (not contaminated) and is_int_amount(text)
    return Cell(
        text=text,
        x0=words[0].x0,
        x1=words[-1].x1,
        num_x1=num_x1,
        words=list(words),
        money=money,
        is_int=is_int,
        contaminated=contaminated,
    )


def group_lines(words: Sequence[Word], height: float) -> list[Line]:
    groups = band_words(words, 0.55 * height)
    lines: list[Line] = []
    for grp in groups:
        cells, cur = [], []
        for word in sorted(grp, key=lambda w: w.x0):
            if cur and (
                word.x0 - cur[-1].x1 > 0.8 * height
                or word.x0 < cur[-1].x1 - 0.2 * height
                or (is_money(cur[-1].text) and is_money(word.text))
                or (
                    is_money(word.text)
                    and word.x0 >= cur[-1].x1 - 0.5
                    and not re.fullmatch(r"(?:RP\.?|IDR|USD|SGD|EUR|[+\-(])", cur[-1].text.strip().upper())
                )
            ):
                cells.append(make_cell(cur))
                cur = []
            cur.append(word)
        if cur:
            cells.append(make_cell(cur))
        lines.append(Line(cells, sum(x.yc for x in grp) / len(grp)))

    # Stitch wrapped decimal fractions (e.g. "1,448,490,000." followed by "00" on the next line)
    for i in range(len(lines) - 1):
        l_curr = lines[i]
        l_next = lines[i + 1]
        if l_next.y - l_curr.y > 1.8 * height:
            continue
        for ci, c_prev in enumerate(l_curr.cells):
            if not re.fullmatch(r"[+\-]?\(?\d{1,4}(?:[.,]\d{3})+[.,]\d?", c_prev.text.strip()):
                continue
            match_idx = None
            for cj, c_next in enumerate(l_next.cells):
                if not re.fullmatch(r"\d{1,2}(?:\s*(?:DB|DR|CR|D|K|C|\+|-|\)))?", c_next.text.strip(), re.I):
                    continue
                if (
                    abs(c_next.x1 - c_prev.x1) <= 2.5 * height
                    or (c_prev.x0 - 1.0 * height <= c_next.x0 <= c_prev.x1 + 1.0 * height)
                ):
                    match_idx = cj
                    break
            if match_idx is not None:
                c_next = l_next.cells.pop(match_idx)
                last_w = c_prev.words[-1]
                merged_w = Word(
                    x0=last_w.x0,
                    y0=last_w.y0,
                    x1=max(last_w.x1, c_next.x1),
                    y1=last_w.y1,
                    text=last_w.text.rstrip() + "".join(w.text.strip() for w in c_next.words),
                )
                l_curr.cells[ci] = make_cell(c_prev.words[:-1] + [merged_w])

    return [ln for ln in lines if ln.cells]


def cluster(items: list, key_fn, eps: float) -> list[list]:
    clusters: list[list] = []
    for item in sorted(items, key=key_fn):
        if clusters and key_fn(item) - key_fn(clusters[-1][-1]) <= eps:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    return clusters


def keep_big(clusters: list[list]) -> list[list]:
    if not clusters:
        return []
    max_len = max(len(c) for c in clusters)
    min_len = 1 if max_len <= 2 else max(2, math.ceil(0.2 * max_len))
    return [c for c in clusters if len(c) >= min_len]


def edge(cell: Cell, align: str = "right") -> float:
    return cell.num_x1


def cell_role(text: str) -> str | None:
    tokens = re.findall(r"[a-z/:]+", text.lower())
    if not tokens or len(text) > 40:
        return None
    if any(t.startswith("mutasi") for t in tokens) and not ({"debit", "debet", "credit", "kredit"} & set(tokens)):
        return "amount"
    for role, keys in ROLE_KEYS:
        if any(t in keys for t in tokens):
            return role
    for token in tokens:
        if len(token) >= 5:
            close = get_close_matches(token, ALL_ROLE_WORDS, n=1, cutoff=0.82)
            if close:
                return next(r for r, ks in ROLE_KEYS if close[0] in ks)
    return None


def is_header_line(line: Line) -> bool:
    if any(c.money for c in line.cells):
        return False
    if any(match_date(c.text) is not None for c in line.cells):
        return False
    upper_text = line.text.upper()
    if re.search(
        r"\b(SALDO\s+AWAL|OPENING\s+BALANCE|SALDO\s+AKHIR|CLOSING\s+BALANCE|"
        r"BEGINNING\s+BALANCE|ENDING\s+BALANCE|TOTAL\s+MUTASI|JUMLAH\s+MUTASI|"
        r"TOTAL\s+AMOUNT|NO\.?\s+OF\s+(?:DEBIT|CREDIT)|PERIODE)\b",
        upper_text,
    ):
        return False
    roles = {r for c in line.cells if (r := cell_role(c.text))}
    if len(roles) >= 3 and bool(roles & NUMERIC_ROLES) and bool(roles & {"date", "desc"}):
        return True
    kw_hits: set[str] = set()
    non_kw_words = 0
    for c in line.cells:
        for w in c.words:
            clean_tok = re.sub(r"[^A-Z/]", "", w.text.upper())
            if not clean_tok:
                continue
            if clean_tok in TABLE_HEADER_KEYWORDS or cell_role(w.text) is not None:
                kw_hits.add(clean_tok)
            elif clean_tok not in {"NO", "REF", "CBG", "CAB", "CABANG", "IDR", "RP", "VAL", "KODE"}:
                non_kw_words += 1
    return (
        len(kw_hits) >= 2
        and len(roles) >= 2
        and (bool(roles & NUMERIC_ROLES) or bool(roles & {"date", "desc"}))
        and non_kw_words <= len(kw_hits)
    )


_TX_NARRATION_RE = re.compile(
    r"\b(?:TRSF|TRANSFER|E-?BANKING|BI-?FAST|PEMINDAHAN|KLIRING|RTGS|TARIKAN|SETORAN|PEMBAYARAN|SWITCHING|QRIS|AUTODEBET|BIAYA\s+ADM|BUNGA)\b",
    re.IGNORECASE,
)


def _expand_header_span(lines: list[Line], i: int, height: float) -> tuple[int, int]:
    start = i
    while start > 0 and lines[start].y - lines[start - 1].y <= 2.5 * height and not _TX_NARRATION_RE.search(lines[start - 1].text) and (
        is_header_line(lines[start - 1])
        or (any(cell_role(c.text) is not None for c in lines[start - 1].cells) and not any(c.money for c in lines[start - 1].cells))
    ):
        start -= 1
    end = i
    while end + 1 < len(lines) and lines[end + 1].y - lines[end].y <= 2.5 * height and not _TX_NARRATION_RE.search(lines[end + 1].text) and (
        is_header_line(lines[end + 1])
        or (any(cell_role(c.text) is not None for c in lines[end + 1].cells) and not any(c.money for c in lines[end + 1].cells))
    ):
        end += 1
    return start, end


def find_table_header_band(words: Sequence[Word]) -> tuple[float, float] | None:
    if not words:
        return None
    height = median_height(words)
    y_tol = max(3.5, 0.55 * height)
    bands: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda w: (w.yc, w.x0)):
        if bands and abs(word.yc - bands[-1]["y"]) <= y_tol:
            b = bands[-1]
            b["w"].append(word)
            b["y"] = sum(x.yc for x in b["w"]) / len(b["w"])
        else:
            bands.append({"y": word.yc, "w": [word]})

    lines: list[Line] = []
    for b in bands:
        cells: list[Cell] = []
        cur: list[Word] = []
        for word in sorted(b["w"], key=lambda w: w.x0):
            if cur and (
                word.x0 - cur[-1].x1 > 0.8 * height
                or word.x0 < cur[-1].x1 - 0.2 * height
                or (is_money(cur[-1].text) and is_money(word.text))
            ):
                cells.append(make_cell(cur))
                cur = []
            cur.append(word)
        if cur:
            cells.append(make_cell(cur))
        lines.append(Line(cells, b["y"]))

    for i, line in enumerate(lines):
        if not is_header_line(line):
            continue
        start, end = _expand_header_span(lines, i, height)
        header_y0 = min(lines[k].y0 for k in range(start, end + 1))
        header_y1 = max(lines[k].y1 for k in range(start, end + 1))
        return (header_y0, header_y1)
    return None


_NON_NUMERIC_HEADER_TOKENS = {"CAB", "CBG", "CABANG", "BRANCH", "TELLER", "REF", "NO", "VALUTA", "KODE"}


def find_header(lines: list[Line], height: float) -> tuple[int, int, list[tuple[float, str]]] | None:
    for i, line in enumerate(lines):
        if not is_header_line(line):
            continue
        start, end = _expand_header_span(lines, i, height)
        found: dict[str, list[float]] = {}
        for k in range(start, end + 1):
            for cell in lines[k].cells:
                cell_tokens = {re.sub(r"[^A-Z]", "", tok) for tok in cell.text.upper().split()}
                if cell_tokens & _NON_NUMERIC_HEADER_TOKENS:
                    continue
                role = cell_role(cell.text)
                if role in NUMERIC_ROLES:
                    positions = found.setdefault(role, [])
                    idx = next((ix for ix, cx in enumerate(positions) if abs(cx - cell.x1) <= 3 * height), None)
                    if idx is not None:
                        positions[idx] = (positions[idx] + cell.x1) / 2
                    else:
                        positions.append(cell.x1)
        return start, end, sorted((x, r) for r, xs in found.items() for x in xs)
    return None


# ---------------------------------------------------------------------------
# Numeric Consensus
# ---------------------------------------------------------------------------


def build_dominant_font_profile(spans: Sequence[Span], y_min: float = 0.0, y_max: float = float("inf")) -> dict[str, Any]:
    region_spans = [s for s in spans if y_min <= s.yc <= y_max and s.font]
    if not region_spans:
        region_spans = [s for s in spans if s.font]
    if not region_spans:
        return {
            "font": "",
            "font_family": "",
            "subset_prefix": "",
            "size": 0.0,
            "color": 0,
            "flags": 0,
        }

    font_counts = Counter(s.font for s in region_spans)
    family_counts = Counter(s.font_family for s in region_spans)
    subset_counts = Counter(s.subset_prefix for s in region_spans if s.subset_prefix)
    size_counts = Counter(round(s.size, 1) for s in region_spans if s.size > 0)
    color_counts = Counter(s.color for s in region_spans)
    flags_counts = Counter(s.flags for s in region_spans)

    return {
        "font": font_counts.most_common(1)[0][0] if font_counts else "",
        "font_family": family_counts.most_common(1)[0][0] if family_counts else "",
        "subset_prefix": subset_counts.most_common(1)[0][0] if subset_counts else "",
        "size": size_counts.most_common(1)[0][0] if size_counts else 0.0,
        "color": color_counts.most_common(1)[0][0] if color_counts else 0,
        "flags": flags_counts.most_common(1)[0][0] if flags_counts else 0,
    }


def bbox_overlap_ratio(a: Span, b: Span) -> float:
    ix0 = max(a.x0, b.x0)
    iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1)
    iy1 = min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max((a.x1 - a.x0) * (a.y1 - a.y0), 1e-6)
    area_b = max((b.x1 - b.x0) * (b.y1 - b.y0), 1e-6)
    return inter / min(area_a, area_b)


def filter_numeric_column_spans(
    spans: Sequence[Span],
    numeric_cols: Sequence[dict[str, Any]],
    y_min: float = 0.0,
    y_max: float = float("inf"),
    height: float = 10.0,
) -> list[Span]:
    """Confine spans vertically to the transaction table and horizontally to Debit/Credit/Balance/Amount columns."""
    h = max(height, 1.0)
    target_cols = [
        col for col in numeric_cols
        if (col.get("assigned_column") or col.get("role")) in ("debit", "credit", "balance", "amount")
    ]
    if not target_cols:
        return [sp for sp in spans if (y_min - h) <= sp.yc <= (y_max + h)]

    x_ranges = [
        (
            col.get("x0", col["edge"] - 8.5 * h) - 2.0 * h,
            col.get("x1", col["edge"]) + 3.5 * h,
        )
        for col in target_cols
    ]
    return [
        sp for sp in spans
        if (y_min - h) <= sp.yc <= (y_max + h)
        and any(rx0 <= sp.xc <= rx1 for rx0, rx1 in x_ranges)
    ]


def detect_overlaid_spans(spans: Sequence[Span], min_overlap: float = 0.45) -> list[tuple[Span, Span]]:
    overlaid: list[tuple[Span, Span]] = []
    active = [s for s in spans if s.text.strip() and s.font != "OCR"]
    active.sort(key=lambda s: s.y0)
    n = len(active)
    for i in range(n):
        sa = active[i]
        for j in range(i + 1, n):
            sb = active[j]
            if sb.y0 >= sa.y1:
                break
            if abs(sa.yc - sb.yc) > max(sa.h, sb.h):
                continue
            # Ignore double-drawn identical text at the same spot (PDF faux-bold rendering, not tampering)
            if (
                sa.text.strip() == sb.text.strip()
                and abs(sa.x0 - sb.x0) <= 1.0
                and abs(sa.y0 - sb.y0) <= 1.0
            ):
                continue
            if bbox_overlap_ratio(sa, sb) >= min_overlap:
                overlaid.append((sa, sb))
    return overlaid


def find_cell_spans(
    cell: Cell,
    spans: Sequence[Span],
    height: float,
) -> tuple[list[Span], Span | None]:
    matched: list[Span] = []
    for sp in spans:
        tol_y = 0.35 * max(height, sp.h, 1.0)
        if sp.yc < cell.y0 - tol_y or sp.yc > cell.y1 + tol_y:
            continue
        ix0 = max(cell.x0, sp.x0)
        ix1 = min(cell.x1, sp.x1)
        if ix1 > ix0 - 0.5:
            matched.append(sp)

    matched.sort(key=lambda s: s.x0)
    adjacent_marker: Span | None = None
    ref_y = matched[-1].origin_y if matched else (cell.y0 + cell.y1) / 2
    ref_x1 = cell.num_x1
    for sp in spans:
        if sp in matched:
            continue
        baseline_diff = abs((sp.origin_y if sp.origin_y else sp.yc) - ref_y)
        if baseline_diff <= 0.4 * max(height, 1.0):
            dx = sp.x0 - ref_x1
            if -2.0 <= dx <= 3.0 * max(height, 1.0) and DIRECTION_MARKER_RE.match(sp.text.strip()):
                adjacent_marker = sp
                break

    return matched, adjacent_marker


def evaluate_signal_1_geometric(
    cell: Cell,
    matched_col: dict[str, Any] | None,
    row_top_y: float,
    y_end: float,
    tol: float,
) -> tuple[bool, bool, str | None]:
    in_y_band = (row_top_y - 1.0) <= cell.y0 < (y_end + 1.0)
    if not in_y_band or matched_col is None:
        return False, False, None

    edge_dist = abs(cell.num_x1 - matched_col["edge"])
    x1_locked = edge_dist <= tol
    col_x0 = matched_col.get("x0", matched_col["edge"] - 2.5 * tol)
    width_hesitation = x1_locked and (cell.x0 < col_x0 - 0.5 * tol)
    assigned_column = matched_col.get("assigned_column") or matched_col.get("role")
    return x1_locked, width_hesitation, assigned_column


def evaluate_signal_2_structural(
    cell: Cell,
    spans: Sequence[Span],
    dominant_profile: dict[str, Any],
    overlaid_pairs: Sequence[tuple[Span, Span]],
    height: float,
) -> dict[str, Any]:
    matched_spans, adj_marker = find_cell_spans(cell, spans, height)
    overlaid_set = {id(sa) for sa, _ in overlaid_pairs} | {id(sb) for _, sb in overlaid_pairs}
    is_overlaid = any(id(sp) in overlaid_set for sp in matched_spans)

    if not matched_spans:
        return {
            "ok": True,
            "contiguous": True,
            "baseline_match": True,
            "is_overlaid": False,
            "direction": None,
            "font": dominant_profile.get("font", ""),
            "font_family": dominant_profile.get("font_family", ""),
            "subset_prefix": dominant_profile.get("subset_prefix", ""),
            "size": dominant_profile.get("size", 0.0),
            "color": dominant_profile.get("color", 0),
            "flags": dominant_profile.get("flags", 0),
        }

    num_tokens = [w for w in cell.words if not DIRECTION_MARKER_RE.match(w.text.strip())]
    num_x0 = num_tokens[0].x0 if num_tokens else cell.x0
    num_x1 = cell.num_x1
    num_spans = [
        sp for sp in matched_spans
        if max(num_x0, sp.x0) < min(num_x1, sp.x1) + 0.5
        and not DIRECTION_MARKER_RE.match(sp.text.strip())
    ] or matched_spans[:1]

    exact_spans = [sp for sp in num_spans if cell.text.strip() in sp.text]
    primary = exact_spans[-1] if exact_spans else num_spans[-1]
    contiguous = len(exact_spans or num_spans) == 1 or all(
        sp.font == primary.font
        and abs(sp.size - primary.size) <= 0.1
        and (
            abs(sp.origin_y - primary.origin_y) <= 0.5
            or max(sp.y0, primary.y0) >= min(sp.y1, primary.y1) - 0.5
        )
        for sp in (exact_spans or num_spans)
    )

    dom_family = dominant_profile.get("font_family", "")
    dom_size = dominant_profile.get("size", 0.0)
    family_ok = (not dom_family) or (primary.font_family == dom_family)
    size_ok = (dom_size <= 0.0) or (abs(primary.size - dom_size) <= 1.5)
    baseline_match = family_ok and size_ok

    span_dir: str | None = None
    for sp in matched_spans:
        m = re.search(r"(?:^|\s|\d)(DB|DR|CR|D|K|C|[+\-])\s*$", sp.text.upper())
        if m:
            span_dir = marker_direction(m.group(1))
            if span_dir:
                break
    if span_dir is None and adj_marker is not None:
        span_dir = marker_direction(adj_marker.text)

    return {
        "ok": contiguous and not is_overlaid,
        "contiguous": contiguous,
        "baseline_match": baseline_match,
        "is_overlaid": is_overlaid,
        "direction": span_dir,
        "font": primary.font,
        "font_family": primary.font_family,
        "subset_prefix": primary.subset_prefix,
        "size": primary.size,
        "color": primary.color,
        "flags": primary.flags,
    }


def evaluate_signal_3_lexical(cell_text: str) -> tuple[bool, float | None, str | None, str | None]:
    ok, amount, conflict_flag = validate_financial_grammar(cell_text)
    if not ok or amount is None:
        return False, None, None, conflict_flag
    return True, amount.value, amount.direction, None


def resolve_numeric_consensus(
    cell: Cell,
    matched_col: dict[str, Any] | None,
    y_start: float,
    y_end: float,
    tol: float,
    spans: Sequence[Span],
    dominant_profile: dict[str, Any],
    overlaid_pairs: Sequence[tuple[Span, Span]],
    height: float,
) -> ConsensusResult:
    sig1_ok, width_hesitation, assigned_column = evaluate_signal_1_geometric(cell, matched_col, y_start, y_end, tol)
    sig2 = evaluate_signal_2_structural(cell, spans, dominant_profile, overlaid_pairs, height)
    sig3_ok, detected_amount, lex_dir, conflict_flag = evaluate_signal_3_lexical(cell.text)

    flags: list[str] = []
    if sig2["is_overlaid"]:
        flags.append(EXTRACTION_CONFIDENCE_OVERLAID)
    if conflict_flag == EXTRACTION_CONFIDENCE_CONFLICT or cell.contaminated:
        if EXTRACTION_CONFIDENCE_CONFLICT not in flags:
            flags.append(EXTRACTION_CONFIDENCE_CONFLICT)

    direction = lex_dir or sig2["direction"]
    sig2_ok = bool(sig2["ok"])

    if EXTRACTION_CONFIDENCE_OVERLAID in flags:
        confidence = EXTRACTION_CONFIDENCE_OVERLAID
    elif EXTRACTION_CONFIDENCE_CONFLICT in flags:
        confidence = EXTRACTION_CONFIDENCE_CONFLICT
    elif sig1_ok and sig2_ok and sig3_ok:
        if width_hesitation and not (sig2["contiguous"] and sig2["baseline_match"]):
            confidence = EXTRACTION_CONFIDENCE_MEDIUM
        else:
            confidence = EXTRACTION_CONFIDENCE_HIGH
    elif (sig2["contiguous"] and sig2["baseline_match"] and sig1_ok) and sig3_ok:
        confidence = EXTRACTION_CONFIDENCE_HIGH
    elif sig3_ok and (sig1_ok or sig2_ok):
        confidence = EXTRACTION_CONFIDENCE_MEDIUM
    else:
        confidence = EXTRACTION_CONFIDENCE_LOW

    profile = (
        NumericTokenProfile(
            assigned_column=assigned_column or "unknown",
            text=cell.text,
            detected_amount=detected_amount if detected_amount is not None else 0.0,
            x0=cell.x0,
            y0=cell.y0,
            x1=cell.x1,
            y1=cell.y1,
            font=sig2["font"],
            font_family=sig2["font_family"],
            subset_prefix=sig2["subset_prefix"],
            size=sig2["size"],
            color=sig2["color"],
            flags=sig2["flags"],
            contiguous=sig2["contiguous"],
            signal1_ok=sig1_ok,
            signal2_ok=sig2_ok,
            signal3_ok=sig3_ok,
            confidence=confidence,
        )
        if detected_amount is not None
        else None
    )

    return ConsensusResult(
        detected_amount=detected_amount,
        assigned_column=assigned_column,
        direction=direction,
        extraction_confidence=confidence,
        signal1_geometric=sig1_ok,
        signal2_structural=sig2_ok,
        signal3_lexical=sig3_ok,
        width_hesitation=width_hesitation,
        contiguous_span=sig2["contiguous"],
        baseline_match=sig2["baseline_match"],
        flags=flags,
        profile=profile,
    )


# ---------------------------------------------------------------------------
# Row Banding
# ---------------------------------------------------------------------------


def _extract_cell_time(
    cell: Cell,
    date_x1: float,
    height: float,
    time_col_ranges: Sequence[tuple[float, float]],
) -> tuple[int, int, int] | None:
    """Safely extract standalone optional time from a non-numeric cell."""
    if cell.money or cell.assigned_column:
        return None
    raw = cell.text.strip()
    if not raw or len(raw) > 22:
        return None
    in_date_or_time_col = cell.x0 <= date_x1 + 2.0 * height or any(
        x0 <= cell.xc <= x1 for x0, x1 in time_col_ranges
    )
    has_colon = ":" in raw
    has_hms_dots = bool(re.search(r"\d{1,2}\.\d{2}\.\d{2}", raw))
    has_clock_suffix = bool(re.search(r"\b(?:WIB|WITA|WIT|AM|PM)\s*$", raw, re.IGNORECASE))
    if has_colon or has_hms_dots or has_clock_suffix or (in_date_or_time_col and "." in raw and "," not in raw):
        return parse_time(raw)
    return None


def _extract_leading_time(text: str) -> tuple[tuple[int, int, int] | None, str]:
    if not text:
        return None, text
    m = re.match(
        r"^\s*((?:[01]?\d|2[0-3])[:.][0-5]\d(?:[:.][0-5]\d)?(?:\s*(?:AM|PM|WIB|WITA|WIT))?)\b\s*(.*)$",
        text,
        re.IGNORECASE,
    )
    if m:
        t = parse_time(m.group(1))
        if t is not None:
            return t, m.group(2).strip()
    return None, text


def detect_start(
    line: Line,
    page_w: float,
    date_col_x1: float | None = None,
) -> tuple[int, tuple, str, tuple[int, int, int] | None] | None:
    max_x0 = min(date_col_x1, 0.45 * page_w) if date_col_x1 is not None else 0.45 * page_w
    for idx, cell in enumerate(line.cells[:3]):
        if cell.x0 > max_x0:
            break
        if idx > 0 and not all(re.fullmatch(r"\d{1,4}\.?", c.text) for c in line.cells[:idx]):
            continue
        if (cell.text.upper().startswith("PEND") and len(cell.text) <= 6) or cell.text.rstrip(".").lower() in MONTHS:
            return idx, (None, None, None), "", None
        matched = match_date(cell.text)
        if matched:
            parts, end = matched
            time_tokens = re.findall(
                r"(?:[01O]?\d|2[0-3])[:.][0-5O]\d(?:[:.][0-5O]\d)?(?:\s*(?:AM|PM|WIB|WITA|WIT))?",
                cell.text[:end],
                re.I,
            )
            row_time = next((t for s in time_tokens if (t := parse_time(s)) is not None), None)
            rest = cell.text[end:].strip()
            if row_time is None and rest:
                lead_t, clean_rest = _extract_leading_time(rest)
                if lead_t is not None:
                    row_time, rest = lead_t, clean_rest
            return idx, parts, rest, row_time
    return None


_DOT_TIME_TOKEN_RE = re.compile(r"^([01]?\d|2[0-3])\.([0-5]\d)\.([0-5]\d)$")
_DATE_SEP_RE = re.compile(r"^\d{1,4}([/\-.])\d{1,2}\1\d{1,4}")


def filter_time_only_starts(
    lines: Sequence[Line],
    starts: dict[int, tuple[int, tuple, str, tuple[int, int, int] | None]],
) -> dict[int, tuple[int, tuple, str, tuple[int, int, int] | None]]:
    """Drop row starts whose date cell is really an HH.MM.SS timestamp.

    Guard: only active when the page's dominant full-date separator is '/' or '-'
    (>= 3 such starts and they outnumber dot-dates). Dot-date templates are untouched.
    """
    if len(starts) < 3:
        return starts
    sep_counts: Counter[str] = Counter()
    time_like: set[int] = set()
    for i, (idx, _parts, _rest, _t) in starts.items():
        text = lines[i].cells[idx].text.strip()
        if _DOT_TIME_TOKEN_RE.match(text):
            time_like.add(i)
            continue
        m = _DATE_SEP_RE.match(text)
        if m:
            sep_counts[m.group(1)] += 1
    if not time_like:
        return starts
    non_dot = sep_counts["/"] + sep_counts["-"]
    if non_dot < 3 or non_dot <= sep_counts["."]:
        return starts
    return {i: s for i, s in starts.items() if i not in time_like}


def _line_has_primary_flow(line: Line) -> bool:
    """True when a line contains a numeric token in the Debit, Credit, or Amount gutter."""
    return any(
        (c.money or c.contaminated) and c.assigned_column in ("debit", "credit", "amount")
        for c in line.cells
    )


def _line_has_any_numeric_gutter(line: Line) -> bool:
    """True when a line contains any numeric token in the Debit, Credit, Amount, or Balance gutter."""
    return any(
        (c.money or c.contaminated) and c.assigned_column in ("debit", "credit", "amount", "balance")
        for c in line.cells
    )


_WRAPPED_NUMERIC_FRAG_RE = re.compile(r"^(?:\d?[.,]\d{2}|\d)$")


def _is_wrapped_numeric_fragment_line(line: Line) -> bool:
    """True when a line has no balance cell and its numeric gutter tokens are only wrapped continuation fragments."""
    if any(c.assigned_column == "balance" for c in line.cells):
        return False
    gutter_cells = [
        c for c in line.cells
        if (c.money or c.is_int or c.contaminated)
        and c.assigned_column in ("debit", "credit", "amount", "balance")
    ]
    if not gutter_cells:
        return False
    return all(
        bool(_WRAPPED_NUMERIC_FRAG_RE.fullmatch(re.sub(r"\s+", "", c.text.strip())))
        for c in gutter_cells
    )


_FOOTER_NOTE_LABEL_RE = re.compile(r"^\W*(?:CATATAN|DISCLAIMER|PERHATIAN|NOTE\s*:)", re.IGNORECASE)


def intercept_summary_line(
    line: Line,
    has_date: bool,
    header_band: tuple[float, float] | None = None,
) -> tuple[bool, dict[str, float]]:
    if has_date:
        return False, {}
    if header_band is not None and line.y0 <= header_band[1]:
        return False, {}
    if re.search(r":\s*RP\b", line.text, re.IGNORECASE) or ": RP" in line.text.upper():
        return False, {}
    if OPENING_RE.match(line.text):
        return False, {}

    clean_str = re.sub(r"\s+", "", line.text).upper()
    clean_alpha = re.sub(r"^[^A-Z0-9]+", "", clean_str)
    label_text = " ".join(c.text for c in line.cells if not (c.money or c.is_int))
    clean_label = re.sub(r"[^A-Z0-9]", "", label_text.upper())

    regex_triggered = (
        any(clean_alpha.startswith(tok) for tok in SUMMARY_INTERCEPT_TOKENS)
        or any(clean_label.startswith(tok) for tok in SUMMARY_INTERCEPT_TOKENS)
        or clean_label in SUMMARY_INTERCEPT_TOKENS
        or any(
            tok in clean_label
            for tok in (
                "MUTASIDEBET",
                "MUTASIDEBIT",
                "MUTASIKREDIT",
                "MUTASICREDIT",
                "TOTALMUTASI",
                "JUMLAHMUTASI",
                "SUBTOTAL",
                "TOTALDEBIT",
                "TOTALDEBET",
                "TOTALKREDIT",
                "TOTALCREDIT",
            )
        )
    )

    money_items: list[tuple[Cell, float, str | None]] = []
    for cell in line.cells:
        if cell.money:
            amt = parse_amount(cell.text)
            if amt is not None:
                money_items.append((cell, amt.value, cell.assigned_column))

    has_balance_cell = any(col == "balance" for _, _, col in money_items)
    has_pos_debit = any(col == "debit" and val > 0 for _, val, col in money_items)
    has_pos_credit = any(col == "credit" and val > 0 for _, val, col in money_items)
    topology_triggered = (not has_balance_cell) and has_pos_debit and has_pos_credit

    # Footer-glued totals: two printed totals followed by disclaimer/boilerplate text
    # (e.g. "802,342,253 842,206,620 CATATAN SALDO DAN DETAIL ..."). Undated lines only.
    if (
        not regex_triggered
        and len(money_items) == 2
        and not has_balance_cell
        and (_FOOTER_NOTE_LABEL_RE.match(label_text) or FOOTER_BOILERPLATE_RE.match(label_text))
    ):
        return True, {"debit": money_items[0][1], "credit": money_items[1][1]}

    if len(money_items) == 4:
        parsed_four = _parse_four_totals(line.cells, check_balance=True)
        if parsed_four is not None:
            return True, parsed_four

    if not (regex_triggered or topology_triggered):
        return False, {}

    totals: dict[str, float] = {}
    if not money_items:
        return False, totals

    is_closing_only = any(
        k in clean_label or clean_alpha.startswith(k)
        for k in ("SALDOAKHIR", "CLOSINGBALANCE", "ENDINGBALANCE", "SALDOREKENING")
    ) and not any(k in clean_label for k in ("DEBET", "DEBIT", "KREDIT", "CREDIT", "TOTAL", "JUMLAH"))
    is_debit_only = any(
        k in clean_label or clean_alpha.startswith(k)
        for k in ("MUTASIDEBET", "MUTASIDEBIT", "TOTALDEBET", "TOTALDEBIT", "JUMLAHDEBET", "JUMLAHDEBIT")
    ) and not any(k in clean_label for k in ("KREDIT", "CREDIT", "SALDO"))
    is_credit_only = any(
        k in clean_label or clean_alpha.startswith(k)
        for k in ("MUTASIKREDIT", "MUTASICREDIT", "TOTALKREDIT", "TOTALCREDIT", "JUMLAHKREDIT", "JUMLAHCREDIT")
    ) and not any(k in clean_label for k in ("DEBET", "DEBIT", "SALDO"))

    if is_closing_only and len(money_items) == 1:
        totals["closing_balance"] = money_items[-1][1]
        totals["closing"] = money_items[-1][1]
    elif is_debit_only and len(money_items) == 1:
        totals["debit"] = money_items[-1][1]
    elif is_credit_only and len(money_items) == 1:
        totals["credit"] = money_items[-1][1]
    else:
        val_d = next((val for _, val, col in money_items if col == "debit"), None)
        val_c = next((val for _, val, col in money_items if col == "credit"), None)
        val_b = next((val for _, val, col in money_items if col == "balance"), None)
        if len(money_items) == 2 and (val_d is None or val_c is None):
            val_d = money_items[0][1]
            val_c = money_items[1][1]
            val_b = None
        elif len(money_items) >= 3 and (val_d is None or val_c is None):
            val_d = money_items[-3][1]
            val_c = money_items[-2][1]
            val_b = money_items[-1][1]
        if val_d is not None:
            totals["debit"] = val_d
        if val_c is not None:
            totals["credit"] = val_c
        if val_b is not None:
            totals["closing_balance"] = val_b
            totals["closing"] = val_b

    return True, totals


def check_summary_table_header(text: str) -> bool:
    t = text.upper()
    return bool(
        re.search(r"SALDO\s+AWAL|OPENING\s+BALANCE|STARTING\s+BALANCE|BEGINNING\s+BALANCE", t)
        and re.search(r"DEBE?T|DEBIT", t)
        and re.search(r"KREDIT|CREDIT", t)
        and re.search(r"SALDO\s+AKHIR|CLOSING|ENDING\s+BALANCE", t)
    )


def _parse_four_totals(
    cells: Sequence[Cell],
    check_balance: bool = False,
) -> dict[str, float] | None:
    """Parse 4-number summary table row (opening, debit, credit, closing) with optional arithmetic consistency check."""
    nums = [c for c in cells if (c.money or is_money(c.text))] if check_balance else [c for c in cells if c.money]
    if len(nums) != 4:
        return None
    vals = [parse_amount(c.text) for c in nums]
    if not all(vals):
        return None
    signed_vals: list[float] = []
    for idx, (c, amt) in enumerate(zip(nums, vals)):
        assert amt is not None
        norm = norm_money(c.text)
        is_neg = norm.startswith(("-", "(")) or amt.direction == "debit"
        if idx in (0, 3) and is_neg:
            signed_vals.append(-abs(amt.value))
        else:
            signed_vals.append(abs(amt.value))
    v0, v1, v2, v3 = signed_vals
    if check_balance:
        if not (abs(v0 - v1 + v2 - v3) < 0.05 or abs(v0 + v1 - v2 - v3) < 0.05):
            return None
    return {"opening": v0, "debit": v1, "credit": v2, "closing": v3}


def summary_band(lines: list[Line], state: State | None = None) -> dict[str, float]:
    for i, line in enumerate(lines):
        text = line.text.upper()
        if check_summary_table_header(text):
            found_nums = False
            for nxt in lines[i + 1 : i + 4]:
                parsed = _parse_four_totals(nxt.cells, check_balance=False)
                if parsed is not None:
                    found_nums = True
                    if state is not None:
                        state.has_pending_summary_table = False
                    return parsed
            if not found_nums and state is not None:
                state.has_pending_summary_table = True
    return {}


_TOTALS_NOISE_LINE_RE = re.compile(r"\b(?:NPWP|FAKTUR|PAJAK|DISCLAIMER)\b")


def printed_totals(text: str) -> dict[str, float]:
    out, upper = {}, text.upper()
    skip_label_re = re.compile(r"\b(?:COUNT|NO\.?\s*OF|JUMLAH\s+TRANSAKSI|FREK(?:UENSI)?|TIME\s+DEPOSIT)\b")
    for key, pat in (
        ("credit", r"\b(?:TOTAL|MUTASI|JUMLAH|SUBTOTAL)\s+(?:AMOUNT\s+|TRANSAKSI\s+)?(?:KREDIT|CREDITED|CREDIT|INCOMING|DEPOSIT(?:S)?|CR|KR)\b[^\d\n]{0,25}([\d.,]+)"),
        ("debit", r"\b(?:TOTAL|MUTASI|JUMLAH|SUBTOTAL)\s+(?:AMOUNT\s+|TRANSAKSI\s+)?(?:DEBE?T|DEBITED|DEBIT|OUTGOING|WITHDRAWAL(?:S)?|DB)\b[^\d\n]{0,25}([\d.,]+)"),
        ("opening", r"(?:SALDO\s+AWAL|OPENING\s+BALANCE|STARTING\s+BALANCE|BEGINNING\s+BALANCE)\b[^\d\n]{0,25}([\d.,]+)"),
    ):
        for match in re.finditer(pat, upper):
            ctx_start = max(0, match.start() - 16)
            ctx = upper[ctx_start : match.start(1)]
            if skip_label_re.search(ctx):
                continue
            # Skip matches embedded in tax-invoice / disclaimer boilerplate lines.
            ln_start = upper.rfind("\n", 0, match.start()) + 1
            ln_end = upper.find("\n", match.end())
            line_ctx = upper[ln_start : ln_end if ln_end != -1 else len(upper)]
            if _TOTALS_NOISE_LINE_RE.search(line_ctx):
                continue
            raw_num = match.group(1)
            amt = parse_amount(raw_num)
            if amt:
                out[key] = amt.value
                break
    return out


_BALANCE_CHECKPOINT_LABEL_RE = re.compile(
    r"^(?:(?:AVAILABLE\s+BALANCE|ENDING\s+BALANCE|CLOSING\s+BALANCE|LEDGER\s+BALANCE|"
    r"BALANCE|SALDO\s+AKHIR|SALDO\s+TERSEDIA)\s*)+$",
    re.IGNORECASE,
)


_UNFORMATTED_ECHO_AMT_RE = re.compile(r"^\d{4,}(?:[.,]\d{2})?$")
_FORMATTED_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3})+\.\d{2}|\d{1,3}(?:\.\d{3})+,\d{2}")


def _is_echoed_reference_line(cand_line: Line, active_lines: Sequence[Line]) -> bool:
    """True when a dateless continuation line only holds an unformatted echoed amount
    matching the active row's formatted flow amount (e.g. KlikBCA / BCA PRI narration wrap).
    """
    if any(c.assigned_column == "balance" for c in cand_line.cells):
        return False
    flow_cells = [
        c for c in cand_line.cells
        if (c.money or c.contaminated) and c.assigned_column in ("debit", "credit", "amount")
    ]
    if len(flow_cells) != 1:
        return False
    cand_tok = flow_cells[0].text.strip()
    if not _UNFORMATTED_ECHO_AMT_RE.fullmatch(cand_tok):
        return False
    cand_amt = parse_amount(cand_tok)
    if cand_amt is None or cand_amt.value <= 0:
        return False
    for ln in active_lines:
        for c in ln.cells:
            if c.assigned_column in ("debit", "credit", "amount") and _FORMATTED_THOUSANDS_RE.search(c.text):
                act_amt = parse_amount(c.text)
                if act_amt is not None and abs(act_amt.value - cand_amt.value) <= 0.01:
                    return True
    return False


def _extract_balance_checkpoint(line: Line, skip_cell_idx: int | None = None) -> float | None:
    """Return first parsed amount when line has numeric cell(s), no narrative, and only balance-restatement labels."""
    numeric_vals: list[float] = []
    has_label = False
    for idx, cell in enumerate(line.cells):
        if skip_cell_idx is not None and idx == skip_cell_idx:
            continue
        if cell.money:
            amt = parse_amount(cell.text)
            if amt is not None:
                norm = norm_money(cell.text)
                is_neg = norm.startswith(("-", "(")) or amt.direction == "debit"
                numeric_vals.append(-abs(amt.value) if is_neg else amt.value)
                continue
        # Unwrap brackets/parentheses/colons instead of deleting bracketed text
        cleaned = re.sub(r"[\[\]():]", " ", cell.text)
        cleaned = re.sub(
            r"\b(?:IDR|USD|SGD|EUR|AUD|JPY|GBP|CNY|RP\.?)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            continue
        if _BALANCE_CHECKPOINT_LABEL_RE.fullmatch(cleaned):
            has_label = True
        else:
            return None
    if not numeric_vals or not has_label:
        return None
    return numeric_vals[0]


def build_date_row_bands(
    lines: Sequence[Line],
    starts: dict[int, tuple[int, tuple, str, tuple[int, int, int] | None]],
    region: Sequence[int],
    words: Sequence[Word],
    spans: Sequence[Span],
    height: float,
    allow_dateless_start: bool = False,
    header_band: tuple[float, float] | None = None,
) -> tuple[list[RowBand], dict[str, float], bool]:
    """Build transaction row bands using the Complete Row Seal state machine and Summary Quarantine."""
    if not region:
        return [], {}, False

    eps = 0.35 * height
    bands: list[RowBand] = []
    quarantined_totals: dict[str, float] = {}
    is_sealed = False

    row_state = "WAITING_FOR_ANCHOR"
    active_lines: list[Line] = []
    pending_pre_lines: list[Line] = []
    active_parts: tuple | None = None
    active_row_time: tuple[int, int, int] | None = None
    active_rest: str = ""
    active_date_word: Word | None = None
    active_top_y: float = 0.0
    cur_y: float = 0.0

    def flush_active_band(next_y0: float | None = None) -> None:
        nonlocal row_state, active_lines, active_parts, active_row_time, active_rest, active_date_word, active_top_y
        if not active_lines:
            row_state = "WAITING_FOR_ANCHOR"
            return
        y_end = active_lines[-1].y1 + eps
        if next_y0 is not None:
            y_end = min(y_end, next_y0 - eps)
        if y_end <= active_top_y:
            y_end = active_lines[-1].y1 + eps
        band_words = [w for w in words if active_top_y <= w.y0 < y_end]
        band_spans = [sp for sp in spans if active_top_y <= sp.y0 < y_end]
        bands.append(
            RowBand(
                index=len(bands),
                row_top_y=active_top_y,
                y_end=y_end,
                date_parts=active_parts if (active_parts and active_parts[0]) else None,
                row_time=active_row_time,
                rest_text=active_rest,
                date_word=active_date_word,
                words=band_words,
                spans=band_spans,
                lines=list(active_lines),
            )
        )
        active_lines = []
        active_parts = None
        active_row_time = None
        active_rest = ""
        active_date_word = None
        row_state = "WAITING_FOR_ANCHOR"

    def open_dateless_band(line: Line) -> None:
        nonlocal active_lines, active_parts, active_row_time, active_rest, active_date_word, active_top_y, cur_y, row_state
        active_lines = [line]
        active_parts = None
        active_row_time = None
        active_rest = ""
        active_date_word = None
        active_top_y = line.y0 - eps
        cur_y = line.y
        row_state = "SEALED_ROW"

    def _is_pre_date_twin_line(curr_pos: int, cand: Line) -> bool:
        """True ONLY when `cand` is a dateless line with primary flow immediately preceding
        a dated line (`curr_pos + 1`) that has NO primary flow of its own in its entire band."""
        if curr_pos + 1 >= len(region):
            return False
        nxt_idx = region[curr_pos + 1]
        if nxt_idx not in starts:
            return False
        nxt_line = lines[nxt_idx]
        # Must be tightly coupled vertically to the upcoming date line (<= 1.35 * height)
        if (nxt_line.y - cand.y) > 1.35 * height or (nxt_line.y - cand.y) <= 0:
            return False
        # If there is an active row above, `cand` must be closer to `nxt_line` than to `cur_y` (or active row is already sealed)
        if row_state == "OPEN_ROW":
            return False
        # The upcoming dated line MUST NOT have any primary flow of its own
        if _line_has_primary_flow(nxt_line):
            return False
        # Also verify continuation lines of `nxt_idx` (before the next date or vertical gap) have no primary flow
        probe_y = nxt_line.y
        for look in range(curr_pos + 2, min(curr_pos + 5, len(region))):
            l_idx = region[look]
            if l_idx in starts:
                break
            l_obj = lines[l_idx]
            if (l_obj.y - probe_y) > 1.45 * height:
                break
            if _line_has_primary_flow(l_obj) and not _is_wrapped_numeric_fragment_line(l_obj):
                return False
            probe_y = l_obj.y
        return True

    for reg_idx, idx in enumerate(region):
        cand_line = lines[idx]
        has_date = idx in starts

        if not has_date and OPENING_RE.match(cand_line.text) and row_state == "WAITING_FOR_ANCHOR":
            continue

        if (
            not has_date
            and not is_sealed
            and (header_band is None or cand_line.y0 > header_band[1])
        ):
            chk_bal = _extract_balance_checkpoint(cand_line)
            if chk_bal is not None:
                if row_state == "OPEN_ROW":
                    has_narrative_or_flow = bool(active_rest.strip()) or len(active_lines) > 1 or any(
                        _line_has_primary_flow(ln) for ln in active_lines
                    )
                    if has_narrative_or_flow:
                        flush_active_band(next_y0=cand_line.y0)
                        if bands:
                            bands[-1].eod_balance = chk_bal
                    else:
                        active_lines = []
                        active_parts = None
                        active_row_time = None
                        active_rest = ""
                        active_date_word = None
                        row_state = "WAITING_FOR_ANCHOR"
                    continue
                if row_state == "SEALED_ROW":
                    flush_active_band(next_y0=cand_line.y0)
                    if bands:
                        bands[-1].eod_balance = chk_bal
                    continue
                if row_state == "WAITING_FOR_ANCHOR":
                    continue

        # Skip standalone SALDO AKHIR / CLOSING BALANCE lines and the number that follows after
        if not has_date and re.match(
            r"^\W*(?:SALDO\s+AKHIR|CLOSING\s+BALANCE|ENDING\s+BALANCE|SALDO\s+REKENING)\b",
            cand_line.text,
            re.IGNORECASE,
        ):
            continue

        # Phase 3.2 & 3.3: Summary Line Interceptor (Regex + Empty-Cell Dual-Amount Topology)
        is_sum_line, q_totals = (
            intercept_summary_line(cand_line, has_date=has_date, header_band=header_band)
            if (header_band is None or cand_line.y0 > header_band[1])
            else (False, {})
        )
        if is_sum_line:
            flush_active_band(next_y0=cand_line.y0)
            quarantined_totals.update(q_totals)
            is_sealed = True
            continue
        if is_sealed:
            if has_date or STOP_RE.match(cand_line.text) or FOOTER_BOILERPLATE_RE.search(cand_line.text):
                break
            continue

        if not has_date and (check_summary_table_header(cand_line.text) or STOP_RE.match(cand_line.text) or FOOTER_BOILERPLATE_RE.search(cand_line.text)):
            flush_active_band(next_y0=cand_line.y0)
            break

        if has_date:
            di, parts, rest, row_time = starts[idx]
            if not rest:
                chk_bal = _extract_balance_checkpoint(cand_line, skip_cell_idx=di)
                if chk_bal is not None:
                    flush_active_band(next_y0=cand_line.y0)
                    continue
            date_cell = cand_line.cells[di]
            date_word = date_cell.words[0] if date_cell.words else None
            next_top_y = date_word.y0 if date_word is not None else cand_line.y0
            if pending_pre_lines:
                next_top_y = min(next_top_y, pending_pre_lines[0].y0)
            flush_active_band(next_y0=next_top_y)

            active_lines = pending_pre_lines + [cand_line]
            pending_pre_lines = []
            active_parts = parts
            active_row_time = row_time
            active_rest = rest
            active_date_word = date_word
            active_top_y = next_top_y - eps
            cur_y = cand_line.y
            row_state = "SEALED_ROW" if any(_line_has_primary_flow(ln) for ln in active_lines) else "OPEN_ROW"
            continue

        is_frag_line = (not has_date) and _is_wrapped_numeric_fragment_line(cand_line)
        if is_frag_line and row_state in ("OPEN_ROW", "SEALED_ROW"):
            active_lines.append(cand_line)
            cur_y = cand_line.y
            continue

        # Check if a pure-text line after a vertical gap is the top of the next dated row's
        # vertically-centered description (e.g. SeaBank, Mandiri Kopra)
        if not is_frag_line and not _line_has_any_numeric_gutter(cand_line):
            if pending_pre_lines:
                pending_pre_lines.append(cand_line)
                continue
            if row_state in ("SEALED_ROW", "OPEN_ROW") and (cand_line.y - cur_y) > 1.4 * height:
                next_s = next((k for k in (idx + 1, idx + 2, idx + 3) if k in starts and k in region), None)
                if next_s is not None:
                    steps = next_s - idx
                    avg_step_to_next = (lines[next_s].y - cand_line.y) / steps
                    tight_to_next = all(
                        not _line_has_any_numeric_gutter(lines[m])
                        and (lines[m + 1].y - lines[m].y) <= 1.45 * height
                        for m in range(idx, next_s)
                    )
                    if tight_to_next and (cand_line.y - cur_y) > 1.25 * avg_step_to_next:
                        flush_active_band(next_y0=cand_line.y0)
                        pending_pre_lines = [cand_line]
                        continue

        if (
            not has_date
            and _line_has_primary_flow(cand_line)
            and not is_frag_line
            and row_state in ("WAITING_FOR_ANCHOR", "SEALED_ROW")
            and _is_pre_date_twin_line(reg_idx, cand_line)
        ):
            flush_active_band(next_y0=cand_line.y0)
            pending_pre_lines.append(cand_line)
            continue

        if row_state == "WAITING_FOR_ANCHOR":
            if (
                allow_dateless_start
                and _line_has_primary_flow(cand_line)
                and not is_frag_line
                and not OPENING_RE.match(cand_line.text)
            ):
                open_dateless_band(cand_line)
            continue

        if cand_line.y - cur_y > 2.6 * height + 2:
            flush_active_band(next_y0=cand_line.y0)
            if (
                _line_has_primary_flow(cand_line)
                and not is_frag_line
                and not OPENING_RE.match(cand_line.text)
            ):
                open_dateless_band(cand_line)
            continue

        if row_state == "OPEN_ROW":
            active_lines.append(cand_line)
            cur_y = cand_line.y
            if _line_has_primary_flow(cand_line) and not is_frag_line:
                row_state = "SEALED_ROW"
        elif row_state == "SEALED_ROW":
            # Phase 3.1: A SEALED_ROW may only append subsequent lines if they contain
            # purely text tokens in Narration and ZERO numeric tokens in Debit/Credit/Balance,
            # or if the line only holds an orphaned wrapped numeric continuation fragment.
            if (not _line_has_any_numeric_gutter(cand_line)) or is_frag_line:
                active_lines.append(cand_line)
                cur_y = cand_line.y
            elif _is_echoed_reference_line(cand_line, active_lines):
                for c in cand_line.cells:
                    if c.assigned_column in ("debit", "credit", "amount"):
                        c.assigned_column = None
                        c.money = False
                active_lines.append(cand_line)
                cur_y = cand_line.y
            elif not _line_has_primary_flow(cand_line) and any(c.assigned_column == "balance" for c in cand_line.cells):
                active_lines.append(cand_line)
                flush_active_band(next_y0=cand_line.y1 + eps)
            else:
                flush_active_band(next_y0=cand_line.y0)
                # Phase 2.2: Blank Date cell + valid amount in Debit/Credit gutter ->
                # instantiate a new same-day transaction row and inherit active_date
                if _line_has_primary_flow(cand_line) and not OPENING_RE.match(cand_line.text):
                    open_dateless_band(cand_line)

    flush_active_band()
    return bands, quarantined_totals, is_sealed


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

_COMPLETE_INTL_MONEY_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d{1,3},\d{6})\.\d{2}$")
_COMPLETE_ID_MONEY_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d{2}$")


def build_x_density_histogram(words: Sequence[Word], page_w: float) -> np.ndarray:
    width = max(int(math.ceil(page_w)) + 2, 10)
    hist = np.zeros(width, dtype=np.int32)
    for w in words:
        if (w.x1 - w.x0) > 0.45 * max(page_w, 1.0):
            continue
        ix0 = max(0, min(width - 1, int(math.floor(w.x0))))
        ix1 = max(ix0 + 1, min(width, int(math.ceil(w.x1))))
        hist[ix0:ix1] += 1
    return hist


def segment_x_intervals(hist: np.ndarray, min_gutter: int = 2) -> list[tuple[float, float]]:
    n = len(hist)
    intervals: list[tuple[float, float]] = []
    in_seg = False
    start = 0
    zero_run = 0

    for x in range(n):
        if hist[x] > 0:
            if not in_seg:
                start = x
                in_seg = True
            zero_run = 0
        else:
            if in_seg:
                zero_run += 1
                if zero_run >= min_gutter:
                    end = x - zero_run + 1
                    if end > start:
                        intervals.append((float(start), float(end)))
                    in_seg = False
                    zero_run = 0
    if in_seg and n > start:
        intervals.append((float(start), float(n - zero_run)))
    return intervals


def discover_column_intervals(
    region_lines: Sequence[Line],
    page_w: float,
    height: float,
) -> list[ColumnInterval]:
    region_words = [w for line in region_lines for cell in line.cells for w in cell.words]
    if not region_words:
        return []

    hist = build_x_density_histogram(region_words, page_w)
    raw_intervals = segment_x_intervals(hist, min_gutter=max(2, int(0.3 * height)))
    if not raw_intervals:
        return []

    all_cells = [cell for line in region_lines for cell in line.cells]
    discovered: list[ColumnInterval] = []

    for ix0, ix1 in raw_intervals:
        col_cells = [c for c in all_cells if c.xc >= ix0 - 1.0 and c.xc <= ix1 + 1.0]
        if not col_cells:
            continue
        non_time_cells = [c for c in col_cells if not PURE_TIME_RE.match(c.text)]
        denom = max(len(non_time_cells), 1)
        date_matches = sum(1 for c in non_time_cells if match_date(c.text) is not None)
        num_matches = sum(1 for c in col_cells if c.money or c.is_int)
        date_density = date_matches / denom
        numeric_density = num_matches / max(len(col_cells), 1)

        if date_density >= 0.9 or (ix0 < 0.35 * page_w and date_matches >= 2 and date_density >= 0.5):
            assigned_column = "date"
        elif numeric_density >= 0.45 and num_matches >= 1:
            assigned_column = "numeric"
        else:
            assigned_column = "text"

        num_x1s = [c.num_x1 for c in col_cells if c.money or c.is_int]
        edge_x1 = float(statistics.median(num_x1s)) if num_x1s else ix1
        discovered.append(
            ColumnInterval(
                assigned_column=assigned_column,
                x0=ix0,
                x1=ix1,
                edge_x1=edge_x1,
                date_density=round(date_density, 3),
                numeric_density=round(numeric_density, 3),
                token_count=len(col_cells),
            )
        )

    if not any(c.assigned_column == "date" for c in discovered):
        left_candidates = [c for c in discovered if c.x0 < 0.45 * page_w and c.date_density > 0]
        if left_candidates:
            best_date = max(left_candidates, key=lambda c: c.date_density)
            best_date.assigned_column = "date"

    text_candidates = [c for c in discovered if c.assigned_column == "text"]
    if text_candidates:
        widest = max(text_candidates, key=lambda c: c.width)
        widest.assigned_column = "desc"
        for c in text_candidates:
            if c is not widest:
                c.assigned_column = "ref" if c.x0 < widest.x0 else "extra"

    return discovered


def best_match(centers: list[float], header: list[float]) -> list[int]:
    c_len, h_len = len(centers), len(header)
    best_cost, best = math.inf, None
    for sub in itertools.combinations(range(max(c_len, h_len)), min(c_len, h_len)):
        if c_len <= h_len:
            diffs = [centers[i] - header[sub[i]] for i in range(c_len)]
        else:
            diffs = [centers[sub[i]] - header[i] for i in range(h_len)]
        med = statistics.median(diffs)
        cost = sum(abs(x - med) for x in diffs) + 0.25 * abs(med)
        if cost < best_cost:
            best_cost, best = cost, list(sub)
    if c_len <= h_len:
        return best or []
    out = [-1] * c_len
    for hi, ci in enumerate(best or []):
        out[ci] = hi
    return out


def learn_numeric(
    money_cells: list[Cell],
    header_numeric: list[tuple[float, str]],
    height: float,
    page_w: float = 0.0,
) -> tuple[str, list[dict], float] | None:
    if not money_cells:
        return None

    align = "right"
    raw_clusters = cluster(money_cells, lambda c: c.num_x1, 0.75 * height)
    big_clusters = keep_big(raw_clusters)
    if header_numeric:
        hx_list = [x for x, _ in header_numeric]
        kept_clusters = [
            cl for cl in raw_clusters
            if cl in big_clusters
            or any(abs(statistics.mean(c.num_x1 for c in cl) - hx) <= 3.5 * height for hx in hx_list)
        ]
    else:
        kept_clusters = big_clusters
    if not kept_clusters:
        kept_clusters = raw_clusters

    if header_numeric:
        min_x = min(x for x, _ in header_numeric) - 4 * height
    else:
        min_x = 0.38 * page_w if page_w > 0 else -math.inf

    clusters = sorted(
        [cl for cl in kept_clusters if max(c.num_x1 for c in cl) > min_x],
        key=lambda cl: statistics.mean(c.num_x1 for c in cl),
    )
    if not clusters:
        return None

    centers = [statistics.mean(c.num_x1 for c in cl) for cl in clusters]
    if header_numeric:
        assigned_cols: list[str | None] = [
            header_numeric[i][1] if i >= 0 else None
            for i in best_match(centers, [x for x, _ in header_numeric])
        ]
        header_roles = {r for _, r in header_numeric}
        if len(clusters) >= 2 and assigned_cols[-1] != "balance":
            if "balance" in assigned_cols:
                bal_idx = assigned_cols.index("balance")
                assigned_cols[bal_idx], assigned_cols[-1] = assigned_cols[-1], "balance"
            elif (
                assigned_cols[-1] is None
                and "balance" in header_roles
                and any(r in ("debit", "credit", "amount") for r in assigned_cols)
            ):
                assigned_cols[-1] = "balance"
    elif len(clusters) == 1:
        assigned_cols = ["amount"]
    elif len(clusters) == 2:
        marker_cnt = sum(1 for c in clusters[1] if re.search(r" (DB|DR|CR|D|K|C)(?: |$)", c.text.upper()))
        assigned_cols = [None, "amount"] if marker_cnt >= 0.3 * len(clusters[1]) else ["amount", "balance"]
    else:
        assigned_cols = [None] * (len(clusters) - 3) + ["debit", "credit", "balance"]

    numeric = [
        {
            "role": col_name,
            "assigned_column": col_name,
            "edge": statistics.mean(c.num_x1 for c in cl),
            "x0": min(min(c.x0 for c in cl), statistics.mean(c.num_x1 for c in cl) - 8.5 * height),
            "x1": max(c.num_x1 for c in cl),
            "n": len(cl),
        }
        for cl, col_name in zip(clusters, assigned_cols)
        if col_name is not None
    ]
    if header_numeric:
        existing_roles = {n["assigned_column"] for n in numeric}
        for hx, h_role in header_numeric:
            if h_role not in existing_roles:
                numeric.append(
                    {
                        "role": h_role,
                        "assigned_column": h_role,
                        "edge": hx,
                        "x0": hx - 8.5 * height,
                        "x1": hx,
                        "n": 1,
                    }
                )
        numeric.sort(key=lambda n: n["edge"])
    if not numeric:
        return None
    edges = sorted(n["edge"] for n in numeric)
    sep = min((b - a for a, b in zip(edges, edges[1:])), default=6 * height)
    tol = max(2.2 * height, min(sep / 2, 5.0 * height))
    return align, numeric, tol


def get_text_role(cell: Cell, text_cols: Sequence[dict[str, Any]], height: float, default_role: str = "other") -> str:
    for tcol in text_cols:
        if tcol["x0"] - 2.0 * height <= cell.xc <= tcol["x1"] + 2.0 * height:
            return tcol["role"]
    return default_role


def _can_stitch_wrapped_numeric(t1: str, t2: str) -> str | None:
    """Return stitched amount text when t1 and t2 are wrapped halves of one split number, else None."""
    s1 = re.sub(r"\s+", "", (t1 or "").strip())
    s2 = re.sub(r"\s+", "", (t2 or "").strip().upper())
    if not s1 or not s2:
        return None
    m1 = re.fullmatch(r"([+\-]?\(?)([\d.,]+)", s1)
    m2 = re.fullmatch(r"([\d.,]+)(\)?(?:DB|DR|CR|D|K|C|[+\-])?)", s2)
    if not m1 or not m2:
        return None
    prefix1, n1 = m1.groups()
    n2, suffix2 = m2.groups()
    if not any(ch.isdigit() for ch in n1) or not any(ch.isdigit() for ch in n2):
        return None
    # Never merge onto an already-complete formatted amount
    if _COMPLETE_INTL_MONEY_RE.fullmatch(n1) or _COMPLETE_ID_MONEY_RE.fullmatch(n1):
        return None
    if re.fullmatch(r"\d+[.,]\d{2}", n1) and re.fullmatch(r"\d+[.,]\d{2}", n2):
        return None

    is_split = False
    # Case A: Comma-grouped integer split before final digit + .dd (e.g. "30,395,356,55" + "9.00")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+,\d{2}", n1) and re.fullmatch(r"\d\.\d{2}", n2):
        is_split = True
    # Case B: Dot-grouped integer split before final digit + ,dd
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+\.\d{2}", n1) and re.fullmatch(r"\d,\d{2}", n2):
        is_split = True
    # Case C: Split inside .00 / ,00 cents (e.g. "949,562,220.0" + "0")
    elif re.fullmatch(r"[\d.,]+[.,]\d", n1) and re.fullmatch(r"\d", n2):
        is_split = True
    # Case D: Split right before .00 / ,00 cents (e.g. "89,036,213,821" + ".00")
    elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", n1) and re.fullmatch(r"[.,]\d{2}", n2):
        is_split = True
    elif n1.endswith((".", ",")) and n2[0].isdigit():
        is_split = True
    elif n2.startswith((".", ",")) and n1[-1].isdigit():
        is_split = True
    else:
        m_grp_c = re.search(r",(\d{1,2})$", n1)
        m_head_c = re.match(r"^(\d{1,2})(?:\.\d{2})?$", n2)
        if m_grp_c and m_head_c and "," in n1 and "." not in n1:
            if len(m_grp_c.group(1)) + len(m_head_c.group(1)) == 3:
                is_split = True
        m_grp_d = re.search(r"\.(\d{1,2})$", n1)
        m_head_d = re.match(r"^(\d{1,2})(?:,\d{2})?$", n2)
        if not is_split and m_grp_d and m_head_d and "." in n1 and "," not in n1:
            if len(m_grp_d.group(1)) + len(m_head_d.group(1)) == 3:
                is_split = True

    if not is_split:
        return None
    joined = f"{prefix1}{n1}{n2}" + (f" {suffix2}" if suffix2 and suffix2 not in (")", "+", "-") else suffix2)
    ok_grammar, _, _ = validate_financial_grammar(joined)
    if not (ok_grammar or parse_amount(joined) is not None):
        return None
    return joined


def _stitch_wrapped_numeric_cells(
    lines: Sequence[Line],
    region: Sequence[int],
    starts: dict[int, Any],
    numeric: Sequence[dict[str, Any]],
    date_x1: float,
    tol: float,
    height: float,
    header_band: tuple[float, float] | None = None,
) -> None:
    """Merge multi-line wrapped numeric fragments inside the same column gutter before column assignment."""
    if len(region) < 2:
        return

    def _in_numeric_col(c1: Cell, c2: Cell) -> bool:
        if c1.num_x1 <= date_x1 or c2.num_x1 <= date_x1:
            return False
        if numeric:
            for col in numeric:
                cx0 = col.get("x0", col["edge"] - 8.5 * height) - 2.0 * height
                cx1 = col.get("x1", col["edge"]) + max(tol, 2.0 * height)
                if cx0 <= c1.x0 and c1.num_x1 <= cx1 and cx0 <= c2.x0 and c2.num_x1 <= cx1:
                    return True
            return False
        right_tol = max(tol, height * 1.2)
        return (
            abs(c2.x1 - c1.x1) <= right_tol
            or abs(c1.num_x1 - c2.num_x1) <= max(2.5 * height, right_tol)
            or abs(c1.x0 - c2.x0) <= 2.5 * height
        )

    reg_pos = 0
    while reg_pos < len(region) - 1:
        idx1 = region[reg_pos]
        l1 = lines[idx1]
        if STOP_RE.match(l1.text) or FOOTER_BOILERPLATE_RE.search(l1.text):
            break
        if (header_band is None or l1.y0 > header_band[1]) and intercept_summary_line(
            l1, has_date=(idx1 in starts), header_band=header_band
        )[0]:
            reg_pos += 1
            continue

        # Look ahead immediately following lines for wrapped continuations of any cell on l1
        look_pos = reg_pos + 1
        while look_pos < len(region):
            idx2 = region[look_pos]
            if idx2 in starts:
                break
            l2 = lines[idx2]
            if OPENING_RE.match(l2.text) or STOP_RE.match(l2.text) or FOOTER_BOILERPLATE_RE.search(l2.text):
                break
            if l2.y - l1.y > 2.2 * height + 2 and abs(l2.y0 - l1.y0) > 2.2 * height + 2:
                break

            to_remove: list[Cell] = []
            stitched_any = False
            for c2 in l2.cells:
                for c1 in l1.cells:
                    if not _in_numeric_col(c1, c2):
                        continue
                    if abs(c2.y0 - c1.y1) > height * 1.2 + 2 and abs(c2.y0 - c1.y0) > height * 2.2 + 2:
                        continue
                    joined = _can_stitch_wrapped_numeric(c1.text, c2.text)
                    if joined is not None:
                        c1.text = joined
                        c1.words.extend(c2.words)
                        c1.x0 = min(c1.x0, c2.x0)
                        c1.x1 = max(c1.x1, c2.x1)
                        c1.num_x1 = max(c1.num_x1, c2.num_x1)
                        c1.money = is_money(joined)
                        c1.is_int = is_int_amount(joined)
                        c1.contaminated = has_alpha_contamination(joined)
                        to_remove.append(c2)
                        stitched_any = True
                        break
            for c2 in to_remove:
                l2.cells.remove(c2)
            if not stitched_any:
                break
            look_pos += 1
        reg_pos += 1
