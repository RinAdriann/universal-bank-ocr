import math
import statistics
import itertools
import re
from difflib import get_close_matches
from typing import Sequence, Any
from datetime import date

from .structures import Word, Cell, Line, Layout, ExtractedRow, State
from .constants import ROLE_KEYS, NUMERIC_ROLES, _ALL_ROLE_WORDS, MONTHS, OPENING_RE, STOP_RE, _PURE_TIME_RE
from .parser import _clean_text, _is_money, _is_int_amount, parse_amount, _match_date, _norm_money, _description_direction, _explicit_direction, _line_direction

def _median_height(words: Sequence[Word]) -> float:
    heights = [w.h for w in words if len(w.text) >= 2] or [w.h for w in words]
    return float(statistics.median(heights)) if heights else 10.0

def _make_cell(words: list[Word]) -> Cell:
    text = " ".join(w.text for w in words)
    num_x1 = words[-1].x1
    if len(words) >= 2 and re.fullmatch(r"(DB|DR|CR|D|K|C)", words[-1].text.upper()):
        num_x1 = words[-2].x1
    cell = Cell(text, words[0].x0, words[-1].x1, num_x1, list(words))
    cell.money = _is_money(text)
    cell.is_int = (not cell.money) and _is_int_amount(text)
    return cell

def _group_lines(words: Sequence[Word], u: float) -> list[Line]:
    groups: list[dict] = []
    for w in sorted(words, key=lambda w: (w.yc, w.x0)):
        if groups and abs(w.yc - groups[-1]["y"]) <= 0.55 * u:
            g = groups[-1]
            g["w"].append(w)
            g["y"] = sum(x.yc for x in g["w"]) / len(g["w"])
        else:
            groups.append({"y": w.yc, "w": [w]})
    lines = []
    gap = 0.8 * u
    for g in groups:
        cells, cur = [], []
        for w in sorted(g["w"], key=lambda w: w.x0):
            if cur and w.x0 - cur[-1].x1 > gap:
                cells.append(_make_cell(cur))
                cur = []
            cur.append(w)
        if cur:
            cells.append(_make_cell(cur))
        lines.append(Line(cells, g["y"]))
    return lines

def _cell_role(text: str) -> str | None:
    tokens = re.findall(r"[a-z/:]+", text.lower())
    if not tokens or len(text) > 40:
        return None
    for role, keys in ROLE_KEYS:
        for tok in tokens:
            if tok in keys:
                return role
    for tok in tokens:
        if len(tok) >= 5:
            close = get_close_matches(tok, _ALL_ROLE_WORDS, n=1, cutoff=0.82)
            if close:
                for role, keys in ROLE_KEYS:
                    if close[0] in keys:
                        return role
    return None

def _is_header_line(line: Line) -> bool:
    roles = {r for c in line.cells if (r := _cell_role(c.text))}
    return len(roles) >= 3 and bool(roles & NUMERIC_ROLES) and bool(roles & {"date", "desc"})

def _find_header(lines: list[Line], u: float) -> tuple[int, int, list[tuple[float, str]]] | None:
    for i, line in enumerate(lines):
        if not _is_header_line(line):
            continue
        j = i
        while j + 1 < len(lines) and _is_header_line(lines[j + 1]) and lines[j + 1].y - lines[j].y <= 2.5 * u:
            j += 1
        found: dict[str, list[float]] = {}
        for k in range(i, j + 1):
            for c in lines[k].cells:
                role = _cell_role(c.text)
                if role in NUMERIC_ROLES:
                    bucket = found.setdefault(role, [])
                    for idx, centre in enumerate(bucket):
                        if abs(centre - c.xc) <= 3 * u:
                            bucket[idx] = (centre + c.xc) / 2
                            break
                    else:
                        bucket.append(c.xc)
        header_numeric = sorted((x, r) for r, xs in found.items() for x in xs)
        return i, j, header_numeric
    return None

def _cluster(items: list, key, eps: float) -> list[list]:
    ordered = sorted(items, key=key)
    clusters: list[list] = []
    for it in ordered:
        if clusters and key(it) - key(clusters[-1][-1]) <= eps:
            clusters[-1].append(it)
        else:
            clusters.append([it])
    return clusters

def _keep_big(clusters: list[list]) -> list[list]:
    if not clusters:
        return []
    mx = max(len(c) for c in clusters)
    thr = 1 if mx <= 2 else max(2, math.ceil(0.2 * mx))
    return [c for c in clusters if len(c) >= thr]

def _edge(cell: Cell, align: str) -> float:
    return cell.num_x1 if align == "right" else cell.x0 if align == "left" else cell.xc

def _best_match(centres: list[float], header: list[float]) -> list[int]:
    c, h = len(centres), len(header)
    best_cost, best = math.inf, None
    if c <= h:
        for sub in itertools.combinations(range(h), c):
            d = [centres[i] - header[sub[i]] for i in range(c)]
            med = statistics.median(d)
            cost = sum(abs(x - med) for x in d)
            if cost < best_cost:
                best_cost, best = cost, list(sub)
        return best or []
    for sub in itertools.combinations(range(c), h):
        d = [centres[sub[i]] - header[i] for i in range(h)]
        med = statistics.median(d)
        cost = sum(abs(x - med) for x in d)
        if cost < best_cost:
            best_cost, best = cost, sub
    out = [-1] * c
    for hi, ci in enumerate(best or []):
        out[ci] = hi
    return out

def _learn_numeric(money_cells: list[Cell], header_numeric: list[tuple[float, str]], u: float, page_w: float = 0.0) -> tuple[str, list[dict], float] | None:
    if not money_cells:
        return None
    eps = 0.7 * u
    best_align, best_cov, best_clusters = "right", -1.0, []
    for align in ("right", "left", "center"):
        kept = _keep_big(_cluster(money_cells, lambda c, a=align: _edge(c, a), eps))
        cov = sum(len(c) for c in kept) / len(money_cells)
        if cov > best_cov + 0.03:
            best_align, best_cov, best_clusters = align, cov, kept
            
    # CRITICAL FIX: Eliminate fake money clusters embedded in the Description column
    valid_clusters = []
    if header_numeric:
        min_hx = min(x for x, r in header_numeric)
        for cl in best_clusters:
            # The right edge of the cluster must at least approach the leftmost numeric header
            if max(c.x1 for c in cl) > min_hx - 3 * u:
                valid_clusters.append(cl)
    elif page_w > 0:
        for cl in best_clusters:
            # If no header, standard money columns are usually in the right 60% of the page
            if max(c.x1 for c in cl) > 0.4 * page_w:
                valid_clusters.append(cl)
    else:
        valid_clusters = best_clusters
        
    clusters = sorted(valid_clusters, key=lambda cl: statistics.mean(_edge(c, best_align) for c in cl))
    if not clusters:
        return None
        
    centres = [statistics.mean(_edge(c, best_align) for c in cl) for cl in clusters]
    roles: list[str | None]
    
    if header_numeric:
        match = _best_match(centres, [x for x, _ in header_numeric])
        roles = [header_numeric[i][1] if i >= 0 else None for i in match]
        balance_headers = [x for x, role in header_numeric if role == "balance"]
        if balance_headers and len(clusters) >= 2:
            balance_header = max(balance_headers)
            balance_index = min(range(len(centres)), key=lambda index: abs(centres[index] - balance_header))
            rightmost = len(clusters) - 1
            if balance_index != rightmost:
                roles[balance_index], roles[rightmost] = roles[rightmost], roles[balance_index]
            if roles[rightmost] != "balance":
                roles[rightmost] = "balance"
                for index in range(rightmost):
                    if roles[index] == "balance":
                        roles[index] = "amount"
                        break
    else:
        n = len(clusters)
        if n == 1:
            roles = ["amount"]
        elif n == 2:
            # Safe lock: if the cluster has explicit DB/CR markers, it's Mutasi (amount)
            c2_markers = sum(1 for c in clusters[1] if re.search(r"(DB|DR|CR|D|K|C)", c.text.upper()))
            if c2_markers >= 0.3 * len(clusters[1]):
                roles = [None, "amount"]
            else:
                roles = ["amount", "balance"]
        else:
            roles = [None] * (n - 3) + ["debit", "credit", "balance"]
            
    numeric = []
    for cl, role in zip(clusters, roles):
        if role is not None:
            numeric.append({"role": role, "edge": statistics.mean(_edge(c, best_align) for c in cl), "n": len(cl)})
            
    if not numeric:
        return None
        
    edges = sorted(n["edge"] for n in numeric)
    sep = min((b - a for a, b in zip(edges, edges[1:])), default=6 * u)
    return best_align, numeric, max(1.5 * u, min(sep / 2, 4 * u))

def _detect_start(line: Line, page_w: float) -> tuple[int, tuple, str] | None:
    for idx, cell in enumerate(line.cells[:3]):
        if idx > 0 and not all(re.fullmatch(r"\d{1,4}\.?", c.text) for c in line.cells[:idx]):
            return None
        if cell.x0 > 0.55 * page_w:
            return None
        if cell.text.upper().startswith("PEND") and len(cell.text) <= 6:
            return idx, (None, None, None), ""
        if cell.text.rstrip(".").lower() in MONTHS:
            return idx, (None, None, None), ""
        m = _match_date(cell.text)
        if m:
            parts, end = m
            return idx, parts, cell.text[end:].strip()
    return None

def process_page(words: list[Word], page_w: float, page_no: int, state: State) -> tuple[list[ExtractedRow], dict, str]:
    diag: dict[str, Any] = {"page": page_no, "rows": 0}
    if not words:
        return [], diag, ""
    u = _median_height(words)
    lines = _group_lines(words, u)
    page_text = "\n".join(l.text for l in lines)
    band = summary_band(lines)
    if band:
        state.printed.update(band)

    header = _find_header(lines, u)
    first = 0
    if header:
        first = header[1] + 1
        state.header_numeric = header[2]
        state.layout = None
    diag["header"] = bool(header)

    starts: dict[int, tuple[int, tuple, str]] = {}
    for i in range(first, len(lines)):
        s = _detect_start(lines[i], page_w)
        if s:
            starts[i] = s
    first_start = min(starts) if starts else None

    end = len(lines)
    if first_start is not None:
        for i in range(first_start + 1, len(lines)):
            if i not in starts and STOP_RE.match(lines[i].text):
                end = i
                break
    region = list(range(first, end))

    date_x1 = max((lines[i].cells[starts[i][0]].x1 for i in starts), default=0.0)
    money_cells = [c for i in region for c in lines[i].cells if c.money and c.x0 > date_x1]
    layout = state.layout
    learned = _learn_numeric(money_cells, state.header_numeric, u, page_w) if money_cells else None
    if learned and (len(starts) >= 3 or layout is None):
        align, numeric, tol = learned
        layout = Layout(align, numeric, tol, [])
    elif layout is None:
        return [], diag, page_text
    align, numeric, tol = layout.align, layout.numeric, layout.tol

    def numeric_for(cell: Cell) -> dict | None:
        if not (cell.money or cell.is_int):
            return None
        e = _edge(cell, align)
        best = min(numeric, key=lambda n: abs(n["edge"] - e), default=None)
        if best is None:
            return None
        limit = tol if cell.money else 0.5 * u
        return best if abs(best["edge"] - e) <= limit else None

    for i in region:
        for c in lines[i].cells:
            n = numeric_for(c)
            c.role = n["role"] if n else None
            if n and not c.money:
                c.money = True

    n_start = len(starts)
    text_cells: list[Cell] = []
    date_starts: list[float] = []
    for i, (di, _p, _r) in starts.items():
        date_starts.append(lines[i].cells[di].x0)
        text_cells += [c for j, c in enumerate(lines[i].cells) if j > di and not (c.money and c.role)]
    text_cols: list[dict] = []
    if layout.text_cols and n_start < 3:
        text_cols = layout.text_cols
    elif date_starts:
        thr = 1 if n_start <= 2 else max(2, math.ceil(0.15 * n_start))
        clusters = [c for c in _cluster(text_cells, lambda c: c.x0, 0.8 * u) if len(c) >= thr]
        date_start = statistics.median(date_starts)
        cols = [{"start": statistics.median(c.x0 for c in cl), "chars": sum(len(c.text) for c in cl)} for cl in clusters]
        cols = [c for c in cols if c["start"] > date_start + 0.8 * u]
        if cols:
            desc = max(cols, key=lambda c: c["chars"])
            for c in cols:
                c["role"] = "desc" if c is desc else ("ref" if c["start"] < desc["start"] else "extra")
        text_cols = [{"start": date_start, "role": "date"}] + sorted(cols, key=lambda c: c["start"])
        layout.text_cols = text_cols
    state.layout = layout

    def text_role(cell: Cell) -> str:
        role = None
        for col in text_cols:
            if col["start"] - 1.0 * u <= cell.x0:
                role = col["role"]

        if role is None or role =="other":
            if not cell.money and not _PURE_TIME_RE.match(cell.text):
                return "desc"
            
        return role or "other"

    rows: list[ExtractedRow] = []
    cur: ExtractedRow | None = None
    cur_y = 0.0
    closed = True
    desc_parts: list[str] = []
    ref_parts: list[str] = []
    extra_parts: list[str] = []
    raw_parts: list[str] = []

    def commit() -> None:
        nonlocal cur
        if cur is None:
            return
        cur.description = _clean_text(" ".join(desc_parts)).upper()
        cur.reference = _clean_text(" ".join(ref_parts)).upper()
        cur.extra = _clean_text(" ".join(extra_parts)).upper()
        cur.raw_text = _clean_text(" ".join(raw_parts)).upper()
        explicit_direction = _explicit_direction(cur.raw_text)
        narrative_direction = _description_direction(cur.description)
        if explicit_direction:
            narrative_direction = explicit_direction
        if cur.pending_mutasi is not None and narrative_direction:
            if cur.pending_dir and cur.pending_dir != narrative_direction:
                cur.validation.append("direction_corrected")
            cur.pending_dir = narrative_direction
        rows.append(cur)
        cur = None

    def put_numeric(row: ExtractedRow, cell: Cell, direction: str | None) -> None:
        amt = parse_amount(cell.text)
        if amt is None:
            return
        role = cell.role
        if role == "balance":
            if row.balance is not None:
                row.validation.append("multiple_balances")
                return
            norm = _norm_money(cell.text)
            overdrawn = norm.startswith(("-", "(")) or norm.endswith(("DB", "DR", "-"))
            row.balance = -abs(amt.value) if overdrawn else amt.value
        elif role in ("debit", "credit"):
            if amt.value == 0:
                return
            if getattr(row, role) is not None:
                row.validation.append("multiple_amounts")
                return
            setattr(row, role, abs(amt.value))
        elif role == "amount":
            if amt.value == 0:
                return
            if row.pending_mutasi is not None:
                row.validation.append("multiple_amounts")
                return
            row.pending_mutasi = abs(amt.value)
            row.pending_dir = amt.direction or direction

    for i in region:
        line = lines[i]
        if i in starts:
            commit()
            di, parts, rest = starts[i]
            cur = ExtractedRow(page=page_no, raw_text="", date_parts=parts if parts[0] else None)
            if parts[0] is None:
                cur.validation.append("pending_transaction")
            desc_parts, ref_parts, extra_parts, raw_parts = [], [], [], []
            closed = False
            if rest and not _PURE_TIME_RE.match(rest):
                desc_parts.append(rest)
            cells = [c for j, c in enumerate(line.cells) if j > di]
            raw_parts.append(line.cells[di].text)
        else:
            text = line.text
            is_opening = OPENING_RE.match(text) is not None
            if is_opening and not state.has_rows and not rows and first_start is not None and i < first_start:
                bal = [c for c in line.cells if c.money and c.role == "balance"] or [c for c in line.cells if c.money][-1:]
                if bal:
                    amt = parse_amount(bal[-1].text)
                    if amt:
                        rows.append(ExtractedRow(
                            page=page_no,
                            raw_text=_clean_text(text).upper(),
                            description=(_clean_text(" ".join(c.text for c in line.cells if not c.money)) or "SALDO AWAL").upper(),
                            balance=amt.value,
                            is_opening=True,
                        ))
                continue
            if cur is None or closed:
                continue
            if STOP_RE.match(text) or line.y - cur_y > 2.6 * u + 2:
                closed = True
                continue
            cells = line.cells
        line_direction = _line_direction(cells)
        cur_y = line.y
        raw_parts.append(line.text if i not in starts else " ".join(c.text for c in cells))
        for c in cells:
            if c.money and c.role:
                put_numeric(cur, c, line_direction)
                continue
            if c.money and c.role is None:
                amount = parse_amount(c.text)
                if amount and cur.pending_mutasi is None:
                    cur.pending_mutasi = abs(amount.value)
                    cur.pending_dir = amount.direction or line_direction
                    cur.validation.append("amount_column_inferred")
                    continue
            role = text_role(c)
            if role in ("desc", "ref"):
                desc_parts.append(c.text)
            elif role == "extra":
                extra_parts.append(c.text)
            elif role == "date":
                if not _PURE_TIME_RE.match(c.text):
                    ref_parts.append(c.text)
            elif role == "other" and not c.money:
                desc_parts.append(c.text)
    commit()

    kept = [r for r in rows if r.is_opening or r.balance is not None or r.debit or r.credit or r.pending_mutasi]
    diag["dropped_noise_rows"] = len(rows) - len(kept)
    diag["rows"] = len(kept)
    diag["columns"] = [n["role"] for n in numeric]
    if kept:
        state.has_rows = True
    return kept, diag, page_text

def apply_dates(rows: Sequence[ExtractedRow], year: int | None) -> None:
    for row in rows:
        if row.date_parts is None:
            continue
        day, month, row_year = row.date_parts
        row_year = row_year or year
        if row_year is None:
            row.date = f"{day:02d}/{month:02d}"
        else:
            try:
                row.date = date(row_year, month, day)
            except ValueError:
                row.validation.append("date_invalid")
                row.date = f"{day:02d}/{month:02d}/{row_year}"

def printed_totals(text: str) -> dict[str, float]:
    out = {}
    for key, pat in (("credit", r"(?:TOTAL\s+)?(?:TRANSAKSI\s+)?(?:KREDIT|CREDIT|INCOMING|DEPOSIT(?:S)?)[^\d\n]{0,20}([\d.,]+)"),
                     ("debit", r"(?:TOTAL\s+)?(?:TRANSAKSI\s+)?(?:DEBET|DEBIT|OUTGOING|WITHDRAWAL(?:S)?)[^\d\n]{0,20}([\d.,]+)"),
                     ("opening", r"(?:SALDO\s+AWAL|OPENING\s+BALANCE|STARTING\s+BALANCE|BEGINNING\s+BALANCE)[^\d\n]{0,20}([\d.,]+)"),
                     ("closing", r"(?:SALDO\s+AKHIR|CLOSING\s+BALANCE|ENDING\s+BALANCE|ACCOUNT\s+CLOSING\s+BALANCE|SALDO\s+REKENING)[^\d\n]{0,20}([\d.,]+)")):
        m = re.search(pat, text.upper())
        if m and (a := parse_amount(m.group(1))):
            out[key] = a.value
    return out

def summary_band(lines: list[Line]) -> dict[str, float]:
    for i, line in enumerate(lines):
        t = line.text.upper()
        if (re.search(r"SALDO\s+AWAL|OPENING\s+BALANCE", t) and re.search(r"DEBE?T|DEBIT", t)
                and re.search(r"KREDIT|CREDIT", t) and re.search(r"SALDO\s+AKHIR|CLOSING", t)):
            for nxt in lines[i + 1:i + 4]:
                nums = [c for c in nxt.cells if c.money]
                if len(nums) == 4:
                    vals = [parse_amount(c.text) for c in nums]
                    if all(vals):
                        return dict(zip(("opening", "debit", "credit", "closing"), (v.value for v in vals)))
    return {}
