from collections import Counter
import itertools
import math
import re
import statistics
from datetime import date
from difflib import get_close_matches
from typing import Any, Sequence

import numpy as np

from .consensus import (
    build_dominant_font_profile,
    detect_overlaid_spans,
    resolve_numeric_consensus,
)
from .constants import (
    ALL_ROLE_WORDS,
    EXTRACTION_CONFIDENCE_CONFLICT,
    EXTRACTION_CONFIDENCE_HIGH,
    EXTRACTION_CONFIDENCE_MEDIUM,
    EXTRACTION_CONFIDENCE_OVERLAID,
    FOOTER_BOILERPLATE_RE,
    MONTHS,
    NUMERIC_ROLES,
    OPENING_RE,
    PURE_TIME_RE,
    ROLE_KEYS,
    STOP_RE,
    SUMMARY_INTERCEPT_TOKENS,
    TABLE_HEADER_KEYWORDS,
)
from .ner import get_transaction_ner
from .parser import (
    clean_text,
    description_direction,
    detect_account,
    explicit_direction,
    extract_clean_description,
    extract_counterparty_name,
    extract_page_fingerprint,
    has_alpha_contamination,
    hint_direction,
    is_int_amount,
    is_money,
    line_direction,
    match_date,
    norm_money,
    parse_amount,
    parse_time,
)
from .structures import (
    Cell,
    ColumnInterval,
    ExtractedRow,
    Layout,
    Line,
    RowBand,
    Span,
    State,
    Word,
)


def median_height(words: Sequence[Word]) -> float:
    heights = [w.h for w in words if len(w.text) >= 2] or [w.h for w in words]
    return float(statistics.median(heights)) if heights else 10.0


def make_cell(words: list[Word]) -> Cell:
    text = " ".join(w.text for w in words)
    has_marker = len(words) >= 2 and bool(re.fullmatch(r"(DB|DR|CR|D|K|C|\+|-)", words[-1].text.upper()))
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
    groups: list[dict] = []
    for word in sorted(words, key=lambda w: (w.yc, w.x0)):
        if groups and abs(word.yc - groups[-1]["y"]) <= 0.55 * height:
            group = groups[-1]
            group["w"].append(word)
            group["y"] = sum(x.yc for x in group["w"]) / len(group["w"])
        else:
            groups.append({"y": word.yc, "w": [word]})

    lines: list[Line] = []
    for group in groups:
        cells, cur = [], []
        for word in sorted(group["w"], key=lambda w: w.x0):
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
        lines.append(Line(cells, group["y"]))
    return lines


def cell_role(text: str) -> str | None:
    tokens = re.findall(r"[a-z/:]+", text.lower())
    if not tokens or len(text) > 40:
        return None
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
        r"\b(SALDO\s+AWAL|OPENING\s+BALANCE|"
        r"BEGINNING\s+BALANCE|TOTAL\s+MUTASI|JUMLAH\s+MUTASI|PERIODE)\b",
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
        j = i
        while (
            j + 1 < len(lines)
            and is_header_line(lines[j + 1])
            and lines[j + 1].y - lines[j].y <= 2.5 * height
        ):
            j += 1
        header_y0 = min(lines[k].y0 for k in range(i, j + 1))
        header_y1 = max(lines[k].y1 for k in range(i, j + 1))
        return (header_y0, header_y1)
    return None


def find_header(lines: list[Line], height: float) -> tuple[int, int, list[tuple[float, str]]] | None:
    for i, line in enumerate(lines):
        if not is_header_line(line):
            continue
        j = i
        while j + 1 < len(lines) and is_header_line(lines[j + 1]) and lines[j + 1].y - lines[j].y <= 2.5 * height:
            j += 1
        found: dict[str, list[float]] = {}
        for k in range(i, j + 1):
            for cell in lines[k].cells:
                role = cell_role(cell.text)
                if role in NUMERIC_ROLES:
                    positions = found.setdefault(role, [])
                    idx = next((ix for ix, cx in enumerate(positions) if abs(cx - cell.x1) <= 3 * height), None)
                    if idx is not None:
                        positions[idx] = (positions[idx] + cell.x1) / 2
                    else:
                        positions.append(cell.x1)
        return i, j, sorted((x, r) for r, xs in found.items() for x in xs)
    return None


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
        if len(clusters) >= 2 and assigned_cols[-1] != "balance":
            if "balance" in assigned_cols:
                bal_idx = assigned_cols.index("balance")
                assigned_cols[bal_idx], assigned_cols[-1] = assigned_cols[-1], "balance"
            elif assigned_cols[-1] is None and any(r in ("debit", "credit", "amount") for r in assigned_cols):
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


def detect_start(
    line: Line,
    page_w: float,
    date_col_x1: float | None = None,
) -> tuple[int, tuple, str, tuple[int, int, int] | None] | None:
    max_x0 = date_col_x1 if date_col_x1 is not None else 0.55 * page_w
    for idx, cell in enumerate(line.cells[:3]):
        if (idx > 0 and not all(re.fullmatch(r"\d{1,4}\.?", c.text) for c in line.cells[:idx])) or cell.x0 > max_x0:
            return None
        if (cell.text.upper().startswith("PEND") and len(cell.text) <= 6) or cell.text.rstrip(".").lower() in MONTHS:
            return idx, (None, None, None), "", None
        matched = match_date(cell.text)
        if matched:
            parts, end = matched
            time_tokens = re.findall(
                r"(?:[01]?\d|2[0-3])[:.][0-5]\d(?:[:.][0-5]\d)?(?:\s*(?:AM|PM|WIB|WITA|WIT))?",
                cell.text[:end],
                re.I,
            )
            row_time = next((parse_time(s) for s in time_tokens), None)
            return idx, parts, cell.text[end:].strip(), row_time
    return None


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

    for idx in region:
        cand_line = lines[idx]
        has_date = idx in starts

        if not has_date and OPENING_RE.match(cand_line.text) and row_state == "WAITING_FOR_ANCHOR":
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
            break

        if not has_date and (STOP_RE.match(cand_line.text) or FOOTER_BOILERPLATE_RE.search(cand_line.text)):
            flush_active_band(next_y0=cand_line.y0)
            break

        if has_date:
            di, parts, rest, row_time = starts[idx]
            date_cell = cand_line.cells[di]
            date_word = date_cell.words[0] if date_cell.words else None
            next_top_y = date_word.y0 if date_word is not None else cand_line.y0
            flush_active_band(next_y0=next_top_y)

            active_lines = [cand_line]
            active_parts = parts
            active_row_time = row_time
            active_rest = rest
            active_date_word = date_word
            active_top_y = next_top_y - eps
            cur_y = cand_line.y
            row_state = "SEALED_ROW" if _line_has_primary_flow(cand_line) else "OPEN_ROW"
            continue

        if row_state == "WAITING_FOR_ANCHOR":
            if allow_dateless_start and _line_has_primary_flow(cand_line) and not OPENING_RE.match(cand_line.text):
                active_lines = [cand_line]
                active_parts = None
                active_row_time = None
                active_rest = ""
                active_date_word = None
                active_top_y = cand_line.y0 - eps
                cur_y = cand_line.y
                row_state = "SEALED_ROW"
            continue

        if cand_line.y - cur_y > 2.6 * height + 2:
            flush_active_band(next_y0=cand_line.y0)
            if _line_has_primary_flow(cand_line) and not OPENING_RE.match(cand_line.text):
                active_lines = [cand_line]
                active_parts = None
                active_row_time = None
                active_rest = ""
                active_date_word = None
                active_top_y = cand_line.y0 - eps
                cur_y = cand_line.y
                row_state = "SEALED_ROW"
            continue

        if row_state == "OPEN_ROW":
            active_lines.append(cand_line)
            cur_y = cand_line.y
            if _line_has_primary_flow(cand_line):
                row_state = "SEALED_ROW"
        elif row_state == "SEALED_ROW":
            # Phase 3.1: A SEALED_ROW may only append subsequent lines if they contain
            # purely text tokens in Narration and ZERO numeric tokens in Debit/Credit/Balance.
            if not _line_has_any_numeric_gutter(cand_line):
                active_lines.append(cand_line)
                cur_y = cand_line.y
            else:
                flush_active_band(next_y0=cand_line.y0)
                # Phase 2.2: Blank Date cell + valid amount in Debit/Credit gutter ->
                # instantiate a new same-day transaction row and inherit active_date
                if _line_has_primary_flow(cand_line) and not OPENING_RE.match(cand_line.text):
                    active_lines = [cand_line]
                    active_parts = None
                    active_row_time = None
                    active_rest = ""
                    active_date_word = None
                    active_top_y = cand_line.y0 - eps
                    cur_y = cand_line.y
                    row_state = "SEALED_ROW"

    flush_active_band()
    return bands, quarantined_totals, is_sealed


def process_page(
    words: list[Word],
    page_w: float,
    page_no: int,
    state: State,
    spans: list[Span] | None = None,
) -> tuple[list[ExtractedRow], dict, str]:
    diag: dict[str, Any] = {"page": page_no, "pdf_page_index": page_no, "rows": 0}
    if not words:
        return [], diag, ""
    page_spans = spans or []
    height = median_height(words)
    lines = group_lines(words, height)
    page_text = "\n".join(line.text for line in lines)
    band_totals = summary_band(lines)
    if band_totals:
        state.printed.update(band_totals)

    # Phase 1.1 & 1.2 & 1.3: Dynamic Horizon & Spatial Page Splitting
    header_band = find_table_header_band(words)
    header = find_header(lines, height)
    if header:
        state.header_numeric, state.layout = header[2], None
    diag["header"] = bool(header or header_band)
    diag["header_band"] = list(header_band) if header_band else None

    if header:
        first = header[1] + 1
        header_y0 = lines[header[0]].y0
        header_y1 = lines[header[1]].y1
        if header_band is not None:
            header_band = (min(header_band[0], header_y0), max(header_band[1], header_y1))
        else:
            header_band = (header_y0, header_y1)
    elif header_band:
        header_y0, header_y1 = header_band
        first = next((i for i, ln in enumerate(lines) if ln.y0 > header_y1), len(lines))
    else:
        first = 0
        header_y0 = None
        header_y1 = None
    diag["header_band"] = list(header_band) if header_band else None

    prelim_starts = {i: s for i in range(first, len(lines)) if (s := detect_start(lines[i], page_w))}
    first_start = min(prelim_starts) if prelim_starts else None

    if header_y0 is not None:
        # Phase 1.2: Metadata Region strictly above header_y0 - 2
        meta_lines = [ln for ln in lines if ln.y1 <= header_y0 + 2.0]
    else:
        # Fallback if unformatted/no titles exist: above first valid Date + Amount line y0
        h_end = first_start if first_start is not None else (0 if page_no > 1 and state.has_rows else len(lines))
        meta_lines = lines[:h_end]
    meta_text = "\n".join(line.text for line in meta_lines)

    page_detected_account = detect_account(meta_text)
    if page_no == 1 and not state.account and page_detected_account:
        state.account = page_detected_account

    fingerprint = extract_page_fingerprint(
        page_text=meta_text if (header_y0 is not None or first_start is not None) else page_text,
        header_text=meta_text,
        pdf_page_index=page_no,
        fallback_account=state.account,
        fallback_bank=state.bank,
    )
    if fingerprint.account_number != "UNKNOWN_ACCOUNT" and not state.account:
        state.account = fingerprint.account_number

    # Seed or update active_year / active_month from metadata_rect when present
    if fingerprint.statement_year is not None and state.active_year is None:
        state.active_year = fingerprint.statement_year
    if fingerprint.statement_month is not None and state.active_month is None:
        state.active_month = fingerprint.statement_month
    if fingerprint.statement_period_start is not None and state.active_date is None:
        state.active_year = fingerprint.statement_period_start.year
        state.active_month = fingerprint.statement_period_start.month

    diag["account"] = state.account or fingerprint.account_number
    diag["account_number"] = state.account or fingerprint.account_number
    diag["page_declared_account"] = page_detected_account
    diag["bank"] = state.bank
    diag["printed_page_number"] = fingerprint.printed_page_number
    diag["statement_month"] = fingerprint.statement_month
    diag["statement_year"] = fingerprint.statement_year
    diag["statement_period_start"] = (
        fingerprint.statement_period_start.isoformat() if fingerprint.statement_period_start else None
    )
    diag["statement_period_end"] = (
        fingerprint.statement_period_end.isoformat() if fingerprint.statement_period_end else None
    )

    # Phase 1.3 Case B: On headerless continuation pages, scan from y = 0 downward for the first valid transaction line
    if header is None and header_band is None and page_no > 1 and state.layout is not None:
        first_txn_idx = 0
        for idx, ln in enumerate(lines):
            if idx in prelim_starts or OPENING_RE.match(ln.text):
                first_txn_idx = idx
                break
            if any(c.money for c in ln.cells) and not FOOTER_BOILERPLATE_RE.search(ln.text):
                first_txn_idx = idx
                break
        first = first_txn_idx

    region = list(range(first, len(lines)))

    col_intervals = discover_column_intervals([lines[i] for i in region], page_w, height)
    date_interval = next((c for c in col_intervals if c.assigned_column == "date"), None)
    date_col_limit = (date_interval.x1 + 2.0 * height) if date_interval else (0.55 * page_w)

    starts = {
        i: s
        for i in region
        if (s := detect_start(lines[i], page_w, date_col_x1=date_col_limit))
    }
    if not starts and prelim_starts:
        starts = {i: s for i, s in prelim_starts.items() if i in region}
    first_start = min(starts) if starts else None

    date_x1 = max((lines[i].cells[starts[i][0]].x1 for i in starts), default=0.0)
    non_summary_region = [
        i for i in region
        if not STOP_RE.match(lines[i].text)
        and not (
            (header_band is None or lines[i].y0 > header_band[1])
            and intercept_summary_line(lines[i], has_date=(i in starts), header_band=header_band)[0]
        )
    ]
    money_cells = [
        c
        for i in (non_summary_region or region)
        for c in lines[i].cells
        if (c.money or (c.is_int and (len(re.sub(r"\D", "", c.text)) >= 4 or c.text.lstrip().startswith(("+", "-")))))
        and c.num_x1 > date_x1
    ]
    layout = state.layout
    learned = learn_numeric(money_cells, state.header_numeric, height, page_w) if money_cells else None
    if learned and (len(starts) >= 3 or layout is None):
        layout = Layout(*learned, [], column_intervals=col_intervals)
    elif layout is None:
        return [], diag, page_text
    align, numeric, tol = layout.align, layout.numeric, layout.tol

    for i in region:
        for cell in lines[i].cells:
            if not (cell.money or cell.is_int or cell.contaminated):
                cell.assigned_column = None
                continue
            if cell.num_x1 <= date_x1:
                cell.assigned_column = None
                continue
            cell_e = edge(cell, align)
            best = min(numeric, key=lambda n: abs(n["edge"] - cell_e), default=None)
            is_wide_or_signed = (
                cell.money
                or cell.contaminated
                or cell.text.lstrip().startswith(("+", "-"))
                or len(re.sub(r"\D", "", cell.text)) >= 4
            )
            allowed_tol = tol if is_wide_or_signed else 0.5 * height
            in_gutter = bool(
                best
                and (
                    abs(best["edge"] - cell_e) <= allowed_tol
                    or (
                        is_wide_or_signed
                        and best["assigned_column"] in ("debit", "credit", "amount", "balance")
                        and cell.x0 >= best.get("x0", best["edge"] - 8.5 * height) - 1.5 * height
                        and cell_e <= best.get("x1", best["edge"]) + allowed_tol
                    )
                )
            )
            matched_col = best if in_gutter else None
            cell.assigned_column = matched_col["assigned_column"] if matched_col else None
            if matched_col and not cell.contaminated:
                cell.money = True

    n_start = len(starts)
    text_cells = [
        c
        for i, (di, _, _, _) in starts.items()
        for j, c in enumerate(lines[i].cells)
        if j > di and not ((c.money or c.contaminated) and c.assigned_column)
    ]
    date_starts = [lines[i].cells[di].x0 for i, (di, _, _, _) in starts.items()]
    text_cols: list[dict] = layout.text_cols if (layout.text_cols and n_start < 3) else []
    if not text_cols and date_starts:
        thr = 1 if n_start <= 2 else max(2, math.ceil(0.15 * n_start))
        clusters = [c for c in cluster(text_cells, lambda c: c.x0, 0.8 * height) if len(c) >= thr]
        date_start = statistics.median(date_starts)
        cols = [
            {"start": statistics.median(c.x0 for c in cl), "chars": sum(len(c.text) for c in cl)}
            for cl in clusters
            if statistics.median(c.x0 for c in cl) > date_start + 0.8 * height
        ]
        if cols:
            desc = max(cols, key=lambda c: c["chars"])
            for col in cols:
                col["role"] = "desc" if col is desc else ("ref" if col["start"] < desc["start"] else "extra")
        text_cols = [{"start": date_start, "role": "date"}] + sorted(cols, key=lambda c: c["start"])
        layout.text_cols = text_cols
    state.layout = layout

    def text_role(cell: Cell) -> str:
        role = next((col["role"] for col in reversed(text_cols) if col["start"] - 1.0 * height <= cell.x0), None)
        if role in (None, "other") and not cell.money and not PURE_TIME_RE.match(cell.text):
            return "desc"
        return role or "other"

    table_y_min = lines[region[0]].y0 if region else 0.0
    table_y_max = lines[region[-1]].y1 if region else float("inf")
    dominant_font_profile = build_dominant_font_profile(page_spans, table_y_min, table_y_max)
    overlaid_pairs = detect_overlaid_spans(page_spans)

    rows: list[ExtractedRow] = []

    pre_desc: list[str] = []
    scan_limit = first_start if first_start is not None else len(lines)
    for i in region:
        if i >= scan_limit:
            break
        line = lines[i]
        text = line.text
        if OPENING_RE.match(text) and not rows:
            bal = [c for c in line.cells if c.money and c.assigned_column == "balance"] or [c for c in line.cells if c.money][-1:]
            if bal:
                amt = parse_amount(bal[-1].text)
                if amt:
                    desc_str = (clean_text(" ".join(c.text for c in line.cells if not c.money)) or "SALDO AWAL").upper()
                    rows.append(
                        ExtractedRow(
                            page=page_no,
                            pdf_page_index=page_no,
                            printed_page_number=fingerprint.printed_page_number,
                            statement_year=fingerprint.statement_year,
                            statement_month=fingerprint.statement_month,
                            row_top_y=line.y0,
                            raw_text=clean_text(text).upper(),
                            description=desc_str,
                            clean_description=extract_clean_description(desc_str),
                            counterparty_name="",
                            balance=amt.value,
                            printed_balance=amt.value,
                            has_printed_balance=True,
                            is_opening=True,
                            account=state.account,
                            bank=state.bank,
                        )
                    )
            continue
        if first_start is not None and lines[first_start].y - line.y <= 1.8 * height and not any(c.money for c in line.cells):
            pre_desc.append(text)

    allow_dateless_start = bool(page_no > 1 and state.has_rows and header is None and header_band is None)
    row_bands, quarantined_totals, page_is_sealed = build_date_row_bands(
        lines,
        starts,
        region,
        words,
        page_spans,
        height,
        allow_dateless_start=allow_dateless_start,
        header_band=header_band,
    )
    if quarantined_totals:
        for k, v in quarantined_totals.items():
            if k == "closing_balance":
                state.printed["closing"] = v
            elif k in ("debit", "credit", "opening"):
                state.printed[k] = v
    state.is_sealed = page_is_sealed
    col_by_role = {n["assigned_column"]: n for n in numeric}

    for band in row_bands:
        row_time = band.row_time
        if row_time is None and band.rest_text and PURE_TIME_RE.match(band.rest_text):
            row_time = parse_time(band.rest_text)

        cur = ExtractedRow(
            page=page_no,
            pdf_page_index=page_no,
            printed_page_number=fingerprint.printed_page_number,
            statement_year=fingerprint.statement_year,
            statement_month=fingerprint.statement_month,
            row_top_y=band.row_top_y,
            raw_text="",
            date_parts=band.date_parts,
            account=state.account,
            bank=state.bank,
            time=row_time,
            extraction_confidence=EXTRACTION_CONFIDENCE_HIGH,
        )
        if band.date_parts is None and band.date_word is not None:
            cur.validation.append("pending_transaction")

        desc_parts: list[str] = list(pre_desc) if band.index == 0 else []
        if band.rest_text and not PURE_TIME_RE.match(band.rest_text):
            desc_parts.append(band.rest_text)
        ref_parts: list[str] = []
        extra_parts: list[str] = []
        raw_parts: list[str] = list(pre_desc) if band.index == 0 else []

        for line_offset, line in enumerate(band.lines):
            if line_offset == 0 and band.date_word is not None:
                di = next(
                    (idx for idx, c in enumerate(line.cells) if band.date_word in c.words),
                    0,
                )
                raw_parts.append(line.cells[di].text)
                cells = line.cells[di + 1 :]
                if cells:
                    raw_parts.append(" ".join(c.text for c in cells))
            else:
                cells = line.cells
                raw_parts.append(line.text)

            line_dir = line_direction(cells)
            for cell in cells:
                if cur.time is None and PURE_TIME_RE.match(cell.text):
                    cur.time = parse_time(cell.text)
                    continue

                if (cell.money or cell.contaminated) and cell.assigned_column:
                    matched_col = min(
                        (n for n in numeric if n["assigned_column"] == cell.assigned_column),
                        key=lambda n: abs(n["edge"] - cell.num_x1),
                        default=col_by_role.get(cell.assigned_column),
                    )
                    consensus = resolve_numeric_consensus(
                        cell=cell,
                        matched_col=matched_col,
                        y_start=band.row_top_y,
                        y_end=band.y_end,
                        tol=tol,
                        spans=band.spans or page_spans,
                        dominant_profile=dominant_font_profile,
                        overlaid_pairs=overlaid_pairs,
                        height=height,
                    )
                    for flag in consensus.flags:
                        if flag not in cur.validation:
                            cur.validation.append(flag)
                    if consensus.extraction_confidence in (EXTRACTION_CONFIDENCE_OVERLAID, EXTRACTION_CONFIDENCE_CONFLICT):
                        cur.extraction_confidence = consensus.extraction_confidence
                    elif (
                        consensus.extraction_confidence == EXTRACTION_CONFIDENCE_MEDIUM
                        and cur.extraction_confidence == EXTRACTION_CONFIDENCE_HIGH
                    ):
                        cur.extraction_confidence = EXTRACTION_CONFIDENCE_MEDIUM

                    if consensus.profile is not None:
                        cur.numeric_profiles.append(consensus.profile)

                    if consensus.detected_amount is None:
                        continue

                    detected_amount = consensus.detected_amount
                    assigned_column = cell.assigned_column
                    direction = consensus.direction or line_dir

                    if assigned_column == "balance":
                        norm = norm_money(cell.text)
                        is_neg = (
                            norm.startswith(("-", "("))
                            or norm.endswith(("DB", "DR", "-"))
                            or consensus.direction == "debit"
                        )
                        printed_bal = -abs(detected_amount) if is_neg else detected_amount
                        if cur.printed_balance is not None:
                            if "multiple_balances" not in cur.validation:
                                cur.validation.append("multiple_balances")
                            if EXTRACTION_CONFIDENCE_OVERLAID in consensus.flags:
                                cur.printed_balance = printed_bal
                                cur.balance = printed_bal
                        else:
                            cur.printed_balance = printed_bal
                            cur.balance = printed_bal
                        cur.has_printed_balance = cur.printed_balance is not None
                    elif assigned_column in ("debit", "credit"):
                        if detected_amount != 0:
                            existing_val = getattr(cur, assigned_column)
                            if existing_val is not None and existing_val != 0.0:
                                cur.validation.append("multiple_amounts")
                            else:
                                setattr(cur, assigned_column, abs(detected_amount))
                        elif getattr(cur, assigned_column) is None:
                            setattr(cur, assigned_column, 0.0)
                    elif assigned_column == "amount" and detected_amount != 0:
                        if direction == "credit":
                            if cur.credit is not None:
                                cur.validation.append("multiple_amounts")
                            else:
                                cur.credit = abs(detected_amount)
                        elif direction == "debit":
                            if cur.debit is not None:
                                cur.validation.append("multiple_amounts")
                            else:
                                cur.debit = abs(detected_amount)
                        else:
                            if cur.pending_mutasi is not None:
                                cur.validation.append("multiple_amounts")
                            else:
                                cur.pending_mutasi = abs(detected_amount)
                elif cell.money and cell.assigned_column is None and cur.pending_mutasi is None and cur.debit is None and cur.credit is None:
                    amount = parse_amount(cell.text)
                    if amount and amount.value != 0:
                        direction = amount.direction or line_dir
                        if direction == "credit":
                            cur.credit = abs(amount.value)
                        elif direction == "debit":
                            cur.debit = abs(amount.value)
                        else:
                            cur.pending_mutasi = abs(amount.value)
                        cur.validation.append("amount_column_inferred")
                else:
                    role = text_role(cell)
                    if role in ("desc", "ref") or (role == "other" and not cell.money):
                        desc_parts.append(cell.text)
                    elif role == "extra":
                        extra_parts.append(cell.text)
                    elif role == "date" and not PURE_TIME_RE.match(cell.text):
                        ref_parts.append(cell.text)

        cur.description = clean_text(" ".join(desc_parts)).upper()
        cur.reference = clean_text(" ".join(ref_parts)).upper()
        cur.extra = clean_text(" ".join(extra_parts)).upper()
        cur.raw_text = clean_text(" ".join(raw_parts)).upper()
        cur.clean_description = extract_clean_description(cur.description)
        cur.counterparty_name = extract_counterparty_name(cur.description)
        cur.has_printed_balance = cur.printed_balance is not None

        if (
            not cur.is_opening
            and OPENING_RE.match(cur.description or "")
            and not (cur.debit or cur.credit or cur.pending_mutasi)
            and cur.printed_balance is not None
        ):
            cur.is_opening = True

        if cur.pending_mutasi is not None and not cur.debit and not cur.credit:
            narrative_dir = (
                cur.pending_dir
                or explicit_direction(cur.raw_text)
                or description_direction(cur.description)
                or hint_direction(cur.description)
            )
            if narrative_dir == "credit":
                cur.credit = cur.pending_mutasi
                cur.pending_mutasi = None
            elif narrative_dir == "debit":
                cur.debit = cur.pending_mutasi
                cur.pending_mutasi = None
            else:
                if "direction_unknown" not in cur.validation:
                    cur.validation.append("direction_unknown")

        rows.append(cur)

    kept = [
        r
        for r in rows
        if r.is_opening
        or r.printed_balance is not None
        or r.debit
        or r.credit
        or r.pending_mutasi
        or EXTRACTION_CONFIDENCE_CONFLICT in r.validation
        or EXTRACTION_CONFIDENCE_OVERLAID in r.validation
    ]

    if kept:
        ner_inputs = [
            "" if r.is_opening else " ".join(filter(None, (r.description, r.extra)))
            for r in kept
        ]
        ner_results = get_transaction_ner().extract_batch(ner_inputs)
        for r, ner_res, full_desc in zip(kept, ner_results, ner_inputs):
            if not r.is_opening:
                r.counterparty_name = ner_res["counterparty"] or extract_counterparty_name(full_desc)
                r.reference = ner_res["reference"] or r.reference

    page_years = [r.date_parts[2] for r in kept if r.date_parts and len(r.date_parts) >= 3 and r.date_parts[2]]
    effective_year = (
        fingerprint.statement_year
        or state.active_year
        or (Counter(page_years).most_common(1)[0][0] if page_years else None)
    )
    apply_dates(
        kept,
        effective_year,
        statement_month=fingerprint.statement_month or state.active_month,
        period_start=fingerprint.statement_period_start,
        state=state,
    )

    diag.update({
        "dropped_noise_rows": len(rows) - len(kept),
        "rows": len(kept),
        "columns": [n["assigned_column"] for n in numeric],
        "discovered_intervals": [
            {"role": c.assigned_column, "x0": c.x0, "x1": c.x1, "edge_x1": c.edge_x1}
            for c in col_intervals
        ],
        "dominant_font": dominant_font_profile,
        "overlaid_spans": len(overlaid_pairs),
        "reported_totals": quarantined_totals,
        "is_sealed": page_is_sealed,
    })
    if kept:
        state.has_rows = True
    return kept, diag, page_text


def apply_dates(
    rows: Sequence[ExtractedRow],
    year: int | None,
    statement_month: int | None = None,
    period_start: date | None = None,
    state: State | None = None,
) -> None:
    """Phase 2: Two-Stage Contextual Date Resolver.

    Stage 1 (Strict Day-First & Indonesian Month Lexicon):
      Group 1 -> Day, Group 2 -> Month, Group 3 -> Year (optional). Never flips to US MM/DD.
    Stage 2 (Stateful Date Carry-Forward & Monotonic Year Rollover):
      Maintains active_date, active_month, active_year across rows and pages.
      Applies monotonic year rollover (active_month == 12 and month == 1 -> active_year += 1).
      Inherits active_date on blank-date same-day transaction rows.
    """
    if not rows:
        return

    active_year = (state.active_year if state and state.active_year else None) or year
    active_month = (state.active_month if state and state.active_month else None) or statement_month
    active_date = state.active_date if state else None

    for row in rows:
        if row.date_parts and len(row.date_parts) == 4:
            day, month, row_year, _ = row.date_parts
            row.date_parts = (day, month, row_year)

    for idx in range(1, len(rows) - 1):
        prev_p, cur_p, next_p = rows[idx - 1].date_parts, rows[idx].date_parts, rows[idx + 1].date_parts
        if prev_p and cur_p and next_p:
            m_prev, y_prev = prev_p[1], prev_p[2] or rows[idx].statement_year or active_year
            m_next, y_next = next_p[1], next_p[2] or rows[idx].statement_year or active_year
            if (m_prev, y_prev) == (m_next, y_next) and (cur_p[1], cur_p[2] or rows[idx].statement_year or active_year) != (m_prev, y_prev):
                rows[idx].date_parts = (cur_p[0], m_prev, y_prev)

    for row in rows:
        if row.date_parts is None:
            if not row.is_opening and active_date is not None:
                row.date = active_date
                if isinstance(active_date, date):
                    row.date_parts = (active_date.day, active_date.month, active_date.year)
                    row.statement_year = row.statement_year or active_date.year
                    row.statement_month = row.statement_month or active_date.month
            continue

        day, month, row_year = row.date_parts[:3]
        if row_year is not None:
            ry = row_year
            active_year = ry
            active_month = month
        elif active_year is not None:
            if active_month == 12 and month == 1:
                active_year += 1
            elif row.statement_month == 12 and month == 1 and active_month is None:
                active_year += 1
            ry = active_year
            active_month = month
        elif row.statement_year is not None:
            ry = row.statement_year + (1 if (row.statement_month == 12 and month == 1) else 0)
            active_year = ry
            active_month = month
        else:
            ry = None
            active_month = month

        if ry is None:
            row.date = f"{day:02d}/{month:02d}"
            row.statement_month = row.statement_month or month
            active_date = row.date
        else:
            try:
                resolved_dt = date(ry, month, day)
                row.date = resolved_dt
                row.statement_year = row.statement_year or resolved_dt.year
                row.statement_month = row.statement_month or resolved_dt.month
                active_date = resolved_dt
            except ValueError:
                if "date_invalid" not in row.validation:
                    row.validation.append("date_invalid")
                row.date = f"{day:02d}/{month:02d}/{ry}"
                row.statement_year = row.statement_year or ry
                row.statement_month = row.statement_month or month
                active_date = row.date

    # Look-Ahead Borrowing for SALDO AWAL / Dateless Opening Header Rows
    for idx, row in enumerate(rows):
        if row.date is not None:
            continue
        if row.is_opening or idx == 0:
            if period_start is not None:
                row.date = period_start
                row.date_parts = (period_start.day, period_start.month, period_start.year)
                row.statement_year = row.statement_year or period_start.year
                row.statement_month = row.statement_month or period_start.month
                if active_date is None:
                    active_date = period_start
                continue

            next_dated = next(
                (r for r in rows[idx + 1 :] if r.date is not None or (r.date_parts and r.date_parts[1])),
                None,
            )
            if next_dated is not None:
                if isinstance(next_dated.date, date):
                    borrowed = date(next_dated.date.year, next_dated.date.month, 1)
                    row.date = borrowed
                    row.date_parts = (1, borrowed.month, borrowed.year)
                    row.statement_year = row.statement_year or borrowed.year
                    row.statement_month = row.statement_month or borrowed.month
                elif next_dated.date_parts and next_dated.date_parts[1]:
                    m_next = next_dated.date_parts[1]
                    y_next = next_dated.date_parts[2] or row.statement_year or active_year
                    if y_next is not None:
                        borrowed = date(y_next, m_next, 1)
                        row.date = borrowed
                        row.date_parts = (1, m_next, y_next)
                        row.statement_year = row.statement_year or y_next
                        row.statement_month = row.statement_month or m_next
                    else:
                        row.date = f"01/{m_next:02d}"
                        row.date_parts = (1, m_next, None)
                        row.statement_month = row.statement_month or m_next
            elif (row.statement_year or active_year) and (row.statement_month or active_month):
                sy = row.statement_year or active_year
                sm = row.statement_month or active_month
                if sy and sm:
                    borrowed = date(sy, sm, 1)
                    row.date = borrowed
                    row.date_parts = (1, sm, sy)

    # Look-Behind Fallback for any remaining dateless rows
    for idx in range(1, len(rows)):
        row = rows[idx]
        if row.date is None and rows[idx - 1].date is not None:
            prev_row = rows[idx - 1]
            row.date = prev_row.date
            row.date_parts = prev_row.date_parts
            row.statement_year = row.statement_year or prev_row.statement_year
            row.statement_month = row.statement_month or prev_row.statement_month

    if state is not None:
        if active_year is not None:
            state.active_year = active_year
        if active_month is not None:
            state.active_month = active_month
        if active_date is not None:
            state.active_date = active_date


def printed_totals(text: str) -> dict[str, float]:
    out, upper = {}, text.upper()
    for key, pat in (
        ("credit", r"(?:TOTAL\s+)?(?:TRANSAKSI\s+)?(?:KREDIT|CREDIT|INCOMING|DEPOSIT(?:S)?)[^\d\n]{0,20}([\d.,]+)"),
        ("debit", r"(?:TOTAL\s+)?(?:TRANSAKSI\s+)?(?:DEBE?T|DEBIT|OUTGOING|WITHDRAWAL(?:S)?)[^\d\n]{0,20}([\d.,]+)"),
        ("opening", r"(?:SALDO\s+AWAL|OPENING\s+BALANCE|STARTING\s+BALANCE|BEGINNING\s+BALANCE)[^\d\n]{0,20}([\d.,]+)"),
    ):
        match = re.search(pat, upper)
        if match:
            amt = parse_amount(match.group(1))
            if amt:
                out[key] = amt.value
    return out


def summary_band(lines: list[Line]) -> dict[str, float]:
    for i, line in enumerate(lines):
        text = line.text.upper()
        if (
            re.search(r"SALDO\s+AWAL|OPENING\s+BALANCE", text)
            and re.search(r"DEBE?T|DEBIT", text)
            and re.search(r"KREDIT|CREDIT", text)
            and re.search(r"SALDO\s+AKHIR|CLOSING", text)
        ):
            for nxt in lines[i + 1 : i + 4]:
                nums = [c for c in nxt.cells if c.money]
                if len(nums) == 4:
                    vals = [parse_amount(c.text) for c in nums]
                    if all(vals):
                        return dict(zip(("opening", "debit", "credit", "closing"), (v.value for v in vals if v)))
    return {}
