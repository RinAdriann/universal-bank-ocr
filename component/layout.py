import itertools
import math
import re
import statistics
from datetime import date
from difflib import get_close_matches
from typing import Any, Sequence

from .constants import ALL_ROLE_WORDS, MONTHS, NUMERIC_ROLES, OPENING_RE, PURE_TIME_RE, ROLE_KEYS, STOP_RE
from .parser import (
    clean_text, description_direction, detect_account, detect_bank, explicit_direction,
    is_int_amount, is_money, line_direction, match_date, norm_money, parse_amount, parse_time
)
from .structures import Cell, ExtractedRow, Layout, Line, State, Word

def median_height(words: Sequence[Word]) -> float:
    hs = [w.h for w in words if len(w.text) >= 2] or [w.h for w in words]
    return float(statistics.median(hs)) if hs else 10.0

def make_cell(words: list[Word]) -> Cell:
    text = " ".join(w.text for w in words)
    nx1 = words[-2].x1 if len(words) >= 2 and re.fullmatch(r"(DB|DR|CR|D|K|C)", words[-1].text.upper()) else words[-1].x1
    money = is_money(text)
    return Cell(text, words[0].x0, words[-1].x1, nx1, list(words), money=money, is_int=(not money) and is_int_amount(text))

def group_lines(words: Sequence[Word], u: float) -> list[Line]:
    groups: list[dict] = []
    for w in sorted(words, key=lambda w: (w.yc, w.x0)):
        if groups and abs(w.yc - groups[-1]["y"]) <= 0.55 * u:
            g = groups[-1]
            g["w"].append(w)
            g["y"] = sum(x.yc for x in g["w"]) / len(g["w"])
        else:
            groups.append({"y": w.yc, "w": [w]})
    lines = []
    for g in groups:
        cells, cur = [], []
        for w in sorted(g["w"], key=lambda w: w.x0):
            if cur and w.x0 - cur[-1].x1 > 0.8 * u:
                cells.append(make_cell(cur))
                cur = []
            cur.append(w)
        if cur:
            cells.append(make_cell(cur))
        lines.append(Line(cells, g["y"]))
    return lines

def cell_role(text: str) -> str | None:
    tokens = re.findall(r"[a-z/:]+", text.lower())
    if not tokens or len(text) > 40:
        return None
    for role, keys in ROLE_KEYS:
        if any(t in keys for t in tokens):
            return role
    for t in tokens:
        if len(t) >= 5 and (c := get_close_matches(t, ALL_ROLE_WORDS, n=1, cutoff=0.82)):
            return next(r for r, ks in ROLE_KEYS if c[0] in ks)
    return None

def is_header_line(line: Line) -> bool:
    roles = {r for c in line.cells if (r := cell_role(c.text))}
    return len(roles) >= 3 and bool(roles & NUMERIC_ROLES) and bool(roles & {"date", "desc"})

def find_header(lines: list[Line], u: float) -> tuple[int, int, list[tuple[float, str]]] | None:
    for i, line in enumerate(lines):
        if not is_header_line(line):
            continue
        j = i
        while j + 1 < len(lines) and is_header_line(lines[j + 1]) and lines[j + 1].y - lines[j].y <= 2.5 * u:
            j += 1
        found: dict[str, list[float]] = {}
        for k in range(i, j + 1):
            for c in lines[k].cells:
                if (role := cell_role(c.text)) in NUMERIC_ROLES:
                    b = found.setdefault(role, [])
                    idx = next((ix for ix, cx in enumerate(b) if abs(cx - c.xc) <= 3 * u), None)
                    if idx is not None:
                        b[idx] = (b[idx] + c.xc) / 2
                    else:
                        b.append(c.xc)
        return i, j, sorted((x, r) for r, xs in found.items() for x in xs)
    return None

def cluster(items: list, key, eps: float) -> list[list]:
    clusters: list[list] = []
    for it in sorted(items, key=key):
        if clusters and key(it) - key(clusters[-1][-1]) <= eps:
            clusters[-1].append(it)
        else:
            clusters.append([it])
    return clusters

def keep_big(clusters: list[list]) -> list[list]:
    if not clusters:
        return []
    mx = max(len(c) for c in clusters)
    thr = 1 if mx <= 2 else max(2, math.ceil(0.2 * mx))
    return [c for c in clusters if len(c) >= thr]

def edge(cell: Cell, align: str) -> float:
    return cell.num_x1 if align == "right" else cell.x0 if align == "left" else cell.xc

def best_match(centres: list[float], header: list[float]) -> list[int]:
    c, h = len(centres), len(header)
    best_cost, best = math.inf, None
    for sub in itertools.combinations(range(max(c, h)), min(c, h)):
        d = [centres[i] - header[sub[i]] for i in range(c)] if c <= h else [centres[sub[i]] - header[i] for i in range(h)]
        med = statistics.median(d)
        if (cost := sum(abs(x - med) for x in d)) < best_cost:
            best_cost, best = cost, list(sub)
    if c <= h:
        return best or []
    out = [-1] * c
    for hi, ci in enumerate(best or []):
        out[ci] = hi
    return out

def learn_numeric(money_cells: list[Cell], header_numeric: list[tuple[float, str]], u: float, page_w: float = 0.0) -> tuple[str, list[dict], float] | None:
    if not money_cells:
        return None
    best_align, best_cov, best_clusters = "right", -1.0, []
    for align in ("right", "left", "center"):
        kept = keep_big(cluster(money_cells, lambda c, a=align: edge(c, a), 0.7 * u))
        if (cov := sum(len(c) for c in kept) / len(money_cells)) > best_cov + 0.03:
            best_align, best_cov, best_clusters = align, cov, kept

    min_x = (min(x for x, _ in header_numeric) - 3 * u) if header_numeric else (0.4 * page_w if page_w > 0 else -math.inf)
    clusters = sorted([cl for cl in best_clusters if max(c.x1 for c in cl) > min_x], key=lambda cl: statistics.mean(edge(c, best_align) for c in cl))
    if not clusters:
        return None

    centres = [statistics.mean(edge(c, best_align) for c in cl) for cl in clusters]
    if header_numeric:
        roles: list[str | None] = [header_numeric[i][1] if i >= 0 else None for i in best_match(centres, [x for x, _ in header_numeric])]
        bal_xs = [x for x, r in header_numeric if r == "balance"]
        if bal_xs and len(clusters) >= 2:
            bi = min(range(len(centres)), key=lambda idx: abs(centres[idx] - max(bal_xs)))
            rm = len(clusters) - 1
            if bi != rm:
                roles[bi], roles[rm] = roles[rm], roles[bi]
            if roles[rm] != "balance":
                roles[rm] = "balance"
                for idx in range(rm):
                    if roles[idx] == "balance":
                        roles[idx] = "amount"
                        break
    elif len(clusters) == 1:
        roles = ["amount"]
    elif len(clusters) == 2:
        c2_m = sum(1 for c in clusters[1] if re.search(r" (DB|DR|CR|D|K|C)(?: |$)", c.text.upper()))
        roles = [None, "amount"] if c2_m >= 0.3 * len(clusters[1]) else ["amount", "balance"]
    else:
        roles = [None] * (len(clusters) - 3) + ["debit", "credit", "balance"]

    numeric = [{"role": r, "edge": statistics.mean(edge(c, best_align) for c in cl), "n": len(cl)} for cl, r in zip(clusters, roles) if r is not None]
    if not numeric:
        return None
    edges = sorted(n["edge"] for n in numeric)
    sep = min((b - a for a, b in zip(edges, edges[1:])), default=6 * u)
    return best_align, numeric, max(1.5 * u, min(sep / 2, 4 * u))

def detect_start(line: Line, page_w: float) -> tuple[int, tuple, str, tuple[int, int, int] | None] | None:
    for idx, cell in enumerate(line.cells[:3]):
        if (idx > 0 and not all(re.fullmatch(r"\d{1,4}\.?", c.text) for c in line.cells[:idx])) or cell.x0 > 0.55 * page_w:
            return None
        if (cell.text.upper().startswith("PEND") and len(cell.text) <= 6) or cell.text.rstrip(".").lower() in MONTHS:
            return idx, (None, None, None), "", None
        if m := match_date(cell.text):
            parts, end = m
            tm = next((parse_time(s) for s in re.findall(r"(?:[01]?\d|2[0-3])[:.][0-5]\d(?:[:.][0-5]\d)?(?:\s*(?:AM|PM|WIB|WITA|WIT))?", cell.text[:end], re.I)), None)
            return idx, parts, cell.text[end:].strip(), tm
    return None

def process_page(words: list[Word], page_w: float, page_no: int, state: State) -> tuple[list[ExtractedRow], dict, str]:
    diag: dict[str, Any] = {"page": page_no, "rows": 0}
    if not words:
        return [], diag, ""
    u = median_height(words)
    lines = group_lines(words, u)
    page_text = "\n".join(l.text for l in lines)
    if band := summary_band(lines):
        state.printed.update(band)

    header = find_header(lines, u)
    first = header[1] + 1 if header else 0
    if header:
        state.header_numeric, state.layout = header[2], None
    diag["header"] = bool(header)

    starts = {i: s for i in range(first, len(lines)) if (s := detect_start(lines[i], page_w))}
    first_start = min(starts) if starts else None

    h_end = header[0] if header else (first_start if first_start is not None else len(lines))
    meta_text = "\n".join(l.text for l in lines[:h_end])
    non_row_text = "\n".join(l.text for idx, l in enumerate(lines) if idx not in starts)
    if acc := (detect_account(meta_text) or detect_account(non_row_text)):
        state.account = acc
    if not state.bank and (bnk := (detect_bank(meta_text) or detect_bank(non_row_text))):
        state.bank = bnk
    diag["account"] = state.account
    diag["bank"] = state.bank

    end = next((i for i in range((first_start or 0) + 1, len(lines)) if first_start is not None and i not in starts and STOP_RE.match(lines[i].text)), len(lines))
    region = list(range(first, end))

    date_x1 = max((lines[i].cells[starts[i][0]].x1 for i in starts), default=0.0)
    money_cells = [c for i in region for c in lines[i].cells if c.money and c.x0 > date_x1]
    layout = state.layout
    if (learned := learn_numeric(money_cells, state.header_numeric, u, page_w) if money_cells else None) and (len(starts) >= 3 or layout is None):
        layout = Layout(*learned, [])
    elif layout is None:
        return [], diag, page_text
    align, numeric, tol = layout.align, layout.numeric, layout.tol

    for i in region:
        for c in lines[i].cells:
            if not (c.money or c.is_int):
                c.role = None
                continue
            e = edge(c, align)
            best = min(numeric, key=lambda n: abs(n["edge"] - e), default=None)
            n = best if best and abs(best["edge"] - e) <= (tol if c.money else 0.5 * u) else None
            c.role = n["role"] if n else None
            if n:
                c.money = True

    n_start = len(starts)
    text_cells = [c for i, (di, _, _, _) in starts.items() for j, c in enumerate(lines[i].cells) if j > di and not (c.money and c.role)]
    date_starts = [lines[i].cells[di].x0 for i, (di, _, _, _) in starts.items()]
    text_cols: list[dict] = layout.text_cols if (layout.text_cols and n_start < 3) else []
    if not text_cols and date_starts:
        thr = 1 if n_start <= 2 else max(2, math.ceil(0.15 * n_start))
        clusters = [c for c in cluster(text_cells, lambda c: c.x0, 0.8 * u) if len(c) >= thr]
        date_start = statistics.median(date_starts)
        cols = [{"start": statistics.median(c.x0 for c in cl), "chars": sum(len(c.text) for c in cl)} for cl in clusters if statistics.median(c.x0 for c in cl) > date_start + 0.8 * u]
        if cols:
            desc = max(cols, key=lambda c: c["chars"])
            for c in cols:
                c["role"] = "desc" if c is desc else ("ref" if c["start"] < desc["start"] else "extra")
        text_cols = [{"start": date_start, "role": "date"}] + sorted(cols, key=lambda c: c["start"])
        layout.text_cols = text_cols
    state.layout = layout

    def text_role(cell: Cell) -> str:
        role = next((col["role"] for col in reversed(text_cols) if col["start"] - 1.0 * u <= cell.x0), None)
        return "desc" if (role in (None, "other") and not cell.money and not PURE_TIME_RE.match(cell.text)) else (role or "other")

    rows: list[ExtractedRow] = []
    cur: ExtractedRow | None = None
    cur_y, closed = 0.0, True
    desc_p, ref_p, extra_p, raw_p = [], [], [], []

    def commit() -> None:
        nonlocal cur
        if cur is None:
            return
        cur.description = clean_text(" ".join(desc_p)).upper()
        cur.reference = clean_text(" ".join(ref_p)).upper()
        cur.extra = clean_text(" ".join(extra_p)).upper()
        cur.raw_text = clean_text(" ".join(raw_p)).upper()
        explicit_dir = explicit_direction(cur.raw_text)
        narrative_dir = explicit_dir or description_direction(cur.description)
        if cur.pending_mutasi is not None and narrative_dir:
            if cur.pending_dir and cur.pending_dir != narrative_dir:
                cur.validation.append("direction_corrected")
            cur.pending_dir = narrative_dir
        rows.append(cur)
        cur = None

    def put_numeric(row: ExtractedRow, cell: Cell, direction: str | None) -> None:
        if (amt := parse_amount(cell.text)) is None:
            return
        role = cell.role
        if role == "balance":
            if row.balance is not None:
                row.validation.append("multiple_balances")
                return
            norm = norm_money(cell.text)
            row.balance = -abs(amt.value) if (norm.startswith(("-", "(")) or norm.endswith(("DB", "DR", "-"))) else amt.value
        elif role in ("debit", "credit") and amt.value != 0:
            if getattr(row, role) is not None:
                row.validation.append("multiple_amounts")
            else:
                setattr(row, role, abs(amt.value))
        elif role == "amount" and amt.value != 0:
            if row.pending_mutasi is not None:
                row.validation.append("multiple_amounts")
            else:
                row.pending_mutasi, row.pending_dir = abs(amt.value), amt.direction or direction

    for i in region:
        line = lines[i]
        if i in starts:
            commit()
            di, parts, rest, tm = starts[i]
            if tm is None and rest and PURE_TIME_RE.match(rest):
                tm = parse_time(rest)
            cur = ExtractedRow(page=page_no, raw_text="", date_parts=parts if parts[0] else None, account=state.account, bank=state.bank, time=tm)
            if parts[0] is None:
                cur.validation.append("pending_transaction")
            desc_p, ref_p, extra_p, raw_p = ([rest] if rest and not PURE_TIME_RE.match(rest) else []), [], [], [line.cells[di].text]
            closed, cells = False, line.cells[di + 1:]
        else:
            text = line.text
            if OPENING_RE.match(text) and not state.has_rows and not rows and first_start is not None and i < first_start:
                bal = [c for c in line.cells if c.money and c.role == "balance"] or [c for c in line.cells if c.money][-1:]
                if bal and (amt := parse_amount(bal[-1].text)):
                    rows.append(ExtractedRow(
                        page=page_no, raw_text=clean_text(text).upper(),
                        description=(clean_text(" ".join(c.text for c in line.cells if not c.money)) or "SALDO AWAL").upper(),
                        balance=amt.value, is_opening=True, account=state.account, bank=state.bank,
                    ))
                continue
            if cur is None or closed:
                continue
            if STOP_RE.match(text) or line.y - cur_y > 2.6 * u + 2:
                closed = True
                continue
            cells = line.cells
        line_dir, cur_y = line_direction(cells), line.y
        raw_p.append(line.text if i not in starts else " ".join(c.text for c in cells))
        for c in cells:
            if cur.time is None and PURE_TIME_RE.match(c.text):
                cur.time = parse_time(c.text)
                continue
            if c.money and c.role:
                put_numeric(cur, c, line_dir)
            elif c.money and c.role is None and cur.pending_mutasi is None and (amount := parse_amount(c.text)):
                cur.pending_mutasi, cur.pending_dir = abs(amount.value), amount.direction or line_dir
                cur.validation.append("amount_column_inferred")
            else:
                role = text_role(c)
                if role in ("desc", "ref") or (role == "other" and not c.money):
                    desc_p.append(c.text)
                elif role == "extra":
                    extra_p.append(c.text)
                elif role == "date" and not PURE_TIME_RE.match(c.text):
                    ref_p.append(c.text)
    commit()

    kept = [r for r in rows if r.is_opening or r.balance is not None or r.debit or r.credit or r.pending_mutasi]
    diag.update({"dropped_noise_rows": len(rows) - len(kept), "rows": len(kept), "columns": [n["role"] for n in numeric]})
    if kept:
        state.has_rows = True
    return kept, diag, page_text

def apply_dates(rows: Sequence[ExtractedRow], year: int | None) -> None:
    num_rows = [r for r in rows if r.date_parts and len(r.date_parts) == 4]
    dmy_votes = sum(1 for r in num_rows if r.date_parts[0] > 12)
    mdy_votes = sum(1 for r in num_rows if r.date_parts[1] > 12)
    if dmy_votes == mdy_votes == 0 and len(num_rows) >= 2:
        xs = {r.date_parts[0] for r in num_rows}
        ys = {r.date_parts[1] for r in num_rows}
        is_mdy = len(xs) < len(ys)
    else:
        is_mdy = mdy_votes > dmy_votes

    for row in rows:
        if row.date_parts is None:
            continue
        if len(row.date_parts) == 4:
            x, y, row_year, _ = row.date_parts
            day, month = (y, x) if (y > 12 or (is_mdy and x <= 12)) else (x, y)
            row.date_parts = (day, month, row_year)
        else:
            day, month, row_year = row.date_parts
        ry = row_year or year
        if ry is None:
            row.date = f"{day:02d}/{month:02d}"
        else:
            try:
                row.date = date(ry, month, day)
            except ValueError:
                row.validation.append("date_invalid")
                row.date = f"{day:02d}/{month:02d}/{ry}"

def printed_totals(text: str) -> dict[str, float]:
    out, upper = {}, text.upper()
    for key, pat in (
        ("credit", r"(?:TOTAL\s+)?(?:TRANSAKSI\s+)?(?:KREDIT|CREDIT|INCOMING|DEPOSIT(?:S)?)[^\d\n]{0,20}([\d.,]+)"),
        ("debit", r"(?:TOTAL\s+)?(?:TRANSAKSI\s+)?(?:DEBE?T|DEBIT|OUTGOING|WITHDRAWAL(?:S)?)[^\d\n]{0,20}([\d.,]+)"),
        ("opening", r"(?:SALDO\s+AWAL|OPENING\s+BALANCE|STARTING\s+BALANCE|BEGINNING\s+BALANCE)[^\d\n]{0,20}([\d.,]+)"),
        ("closing", r"(?:SALDO\s+AKHIR|CLOSING\s+BALANCE|ENDING\s+BALANCE|ACCOUNT\s+CLOSING\s+BALANCE|SALDO\s+REKENING)[^\d\n]{0,20}([\d.,]+)"),
    ):
        if (m := re.search(pat, upper)) and (a := parse_amount(m.group(1))):
            out[key] = a.value
    return out

def summary_band(lines: list[Line]) -> dict[str, float]:
    for i, line in enumerate(lines):
        t = line.text.upper()
        if re.search(r"SALDO\s+AWAL|OPENING\s+BALANCE", t) and re.search(r"DEBE?T|DEBIT", t) and re.search(r"KREDIT|CREDIT", t) and re.search(r"SALDO\s+AKHIR|CLOSING", t):
            for nxt in lines[i + 1:i + 4]:
                nums = [c for c in nxt.cells if c.money]
                if len(nums) == 4 and all(vals := [parse_amount(c.text) for c in nums]):
                    return dict(zip(("opening", "debit", "credit", "closing"), (v.value for v in vals if v)))
    return {}