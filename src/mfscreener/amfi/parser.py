from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_CATEGORY_RE = re.compile(r"^([^()]+)\((.+)\)\s*$")


def parse_plan_option(plan_raw: str | None, option_raw: str | None) -> tuple[str, str]:
    plan = (plan_raw or "").strip().lower()
    option = (option_raw or "").strip().lower()
    if plan in ("direct", "regular"):
        plan_norm = plan
    elif "direct" in plan:
        plan_norm = "direct"
    elif "regular" in plan:
        plan_norm = "regular"
    else:
        plan_norm = "unknown"

    if "growth" in option:
        option_norm = "growth"
    elif "idcw" in option or "dividend" in option or "div" in option:
        option_norm = "idcw"
    else:
        option_norm = "other" if option else "unknown"
    return plan_norm, option_norm


def parse_category_line(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("Scheme Code"):
        return None
    if ";" in line:
        return None
    m = _CATEGORY_RE.match(line)
    if m:
        return m.group(2).strip()
    if line.endswith("Fund") or "Scheme" in line:
        return line
    return None


def family_key(amc: str, base_name: str) -> str:
    base = re.sub(
        r"\s*-\s*(direct|regular).*$",
        "",
        base_name,
        flags=re.IGNORECASE,
    )
    base = re.sub(r"\s+(growth|idcw|dividend).*$", "", base, flags=re.IGNORECASE)
    return f"{amc.strip().lower()}::{base.strip().lower()}"


@dataclass(frozen=True)
class NavAllRow:
    scheme_code: int
    isin_growth: str | None
    isin_reinvest: str | None
    scheme_name: str
    amc: str
    plan: str
    option: str
    sebi_category: str | None
    category_raw: str | None
    nav: float
    nav_date: datetime


def parse_navall(text: str) -> tuple[list[NavAllRow], datetime | None]:
    rows: list[NavAllRow] = []
    current_amc = ""
    current_category: str | None = None
    category_raw: str | None = None
    latest_file_date: datetime | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith("Mutual Fund"):
            current_amc = line.replace("Mutual Fund", "").strip()
            continue
        cat = parse_category_line(line)
        if cat is not None and ";" not in line:
            current_category = cat
            category_raw = line
            continue
        if ";" not in line:
            continue
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 8:
            continue
        try:
            scheme_code = int(parts[0])
            nav = float(parts[6])
        except (ValueError, IndexError):
            continue
        try:
            nav_date = datetime.strptime(parts[7], "%d-%b-%Y")
        except ValueError:
            continue
        plan, option = parse_plan_option(parts[4] if len(parts) > 4 else None, parts[5] if len(parts) > 5 else None)
        isin_g = parts[1] or None
        isin_r = parts[2] or None
        if isin_g and len(isin_g) < 10:
            isin_g = None
        if isin_r and len(isin_r) < 10:
            isin_r = None
        if latest_file_date is None or nav_date > latest_file_date:
            latest_file_date = nav_date
        rows.append(
            NavAllRow(
                scheme_code=scheme_code,
                isin_growth=isin_g,
                isin_reinvest=isin_r,
                scheme_name=parts[3],
                amc=current_amc,
                plan=plan,
                option=option,
                sebi_category=current_category,
                category_raw=category_raw,
                nav=nav,
                nav_date=nav_date,
            )
        )
    return rows, latest_file_date
