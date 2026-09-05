"""The real CSV must resolve 100% of its terms — zero unresolved is a hard requirement."""
import csv

from app.normalize import resolve_panel
from app.seeds import build_resolver_from_seed

from conftest import CSV_PATH, SEED_DIR


def test_csv_exists():
    assert CSV_PATH.exists(), f"source CSV missing: {CSV_PATH}"


def test_every_csv_row_resolves(resolver):
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    assert len(rows) > 300
    unresolved = []
    for r in rows:
        code, name, panel = r["代号"].strip(), r["项目名称"].strip(), r["检验组合"].strip()
        if "培养" in panel and code in {"ORG", "MDR", "GEN", "AMP", "ETP", "AMK", "TZP", "SAM", "CHL", "CAZ", "DAP", "LZD", "PEN"}:
            continue  # micro rows are handled by the culture path, not observations
        hit = resolver.resolve(code, name, panel, r["单位"].strip())
        if hit is None:
            unresolved.append((panel, code, name, r["单位"].strip()))
    assert unresolved == [], f"unresolved terms: {unresolved}"


def test_pct_and_ckmb_lands_on_separate_codes(resolver):
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    seen = set()
    for r in rows:
        hit = resolver.resolve(r["代号"].strip(), r["项目名称"].strip(),
                               r["检验组合"].strip(), r["单位"].strip())
        if r["代号"].strip() == "PCT":
            seen.add(hit)
    assert seen == {"PCT_PLT", "PCT_PROCAL"}, seen


def test_seed_resolver_matches_seed_files():
    r = build_resolver_from_seed(SEED_DIR)
    assert len(r.terms) >= 84
    assert r.resolve("PCT", None, "血常规", "%") == "PCT_PLT"
