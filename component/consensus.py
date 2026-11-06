from collections import Counter
import re
from typing import Any, Sequence

from .constants import (
    DIRECTION_MARKER_RE,
    EXTRACTION_CONFIDENCE_CONFLICT,
    EXTRACTION_CONFIDENCE_HIGH,
    EXTRACTION_CONFIDENCE_LOW,
    EXTRACTION_CONFIDENCE_MEDIUM,
    EXTRACTION_CONFIDENCE_OVERLAID,
)
from .parser import marker_direction, validate_financial_grammar
from .structures import Cell, ConsensusResult, NumericTokenProfile, Span


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
        }

    font_counts = Counter(s.font for s in region_spans)
    family_counts = Counter(s.font_family for s in region_spans)
    subset_counts = Counter(s.subset_prefix for s in region_spans if s.subset_prefix)
    size_counts = Counter(round(s.size, 1) for s in region_spans if s.size > 0)
    color_counts = Counter(s.color for s in region_spans)

    return {
        "font": font_counts.most_common(1)[0][0] if font_counts else "",
        "font_family": family_counts.most_common(1)[0][0] if family_counts else "",
        "subset_prefix": subset_counts.most_common(1)[0][0] if subset_counts else "",
        "size": size_counts.most_common(1)[0][0] if size_counts else 0.0,
        "color": color_counts.most_common(1)[0][0] if color_counts else 0,
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


def detect_overlaid_spans(spans: Sequence[Span], min_overlap: float = 0.45) -> list[tuple[Span, Span]]:
    overlaid: list[tuple[Span, Span]] = []
    n = len(spans)
    for i in range(n):
        sa = spans[i]
        if not sa.text.strip():
            continue
        for j in range(i + 1, n):
            sb = spans[j]
            if not sb.text.strip():
                continue
            if abs(sa.yc - sb.yc) > max(sa.h, sb.h):
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
        if abs(sp.yc - ((cell.y0 + cell.y1) / 2)) > 0.85 * max(height, sp.h):
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
        sp.font == primary.font and abs(sp.size - primary.size) <= 0.1 and abs(sp.origin_y - primary.origin_y) <= 0.5
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
