"""Rule-based normalization pipeline. Pure functions + an in-memory resolver."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------- text normalization ----------

_FW_RANGE = dict(zip(range(0xFF01, 0xFF5F), range(0x21, 0x7F)))


def to_halfwidth(s: str | None) -> str | None:
    if s is None:
        return None
    return s.translate(_FW_RANGE).replace("　", " ")


def norm_code(s: str | None) -> str | None:
    s = to_halfwidth(s)
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s or None


def norm_name(s: str | None) -> str | None:
    return norm_code(s)  # same treatment: halfwidth + collapse + upper


def norm_unit(s: str | None) -> str | None:
    s = to_halfwidth(s)
    if s is None:
        return None
    s = s.replace("μ", "u").replace("µ", "u").replace("U+00b5", "u")
    s = re.sub(r"\s+", "", s).upper()
    return s or None


# ---------- panel resolution ----------

PANEL_RULES: list[tuple[tuple[str, ...], str]] = [
    (("血常规",), "CBC"),
    (("凝血", "血凝", "纤溶"), "COAG"),
    (("尿常规", "尿液"), "URINE"),
    (("术前", "免疫", "输血"), "IMMUNO"),
    (("心肌", "钠尿", "BNP", "降钙素原"), "CARDIAC"),
    (("生命体征", "家庭监测", "体征监测"), "VITALS"),
    (("生活方式", "睡眠", "运动", "步数"), "LIFESTYLE"),
]
DEFAULT_PANEL = "BIOCHEM"


def resolve_panel(panel: str | None) -> str | None:
    p = norm_name(panel)
    if not p:
        return None
    for keywords, key in PANEL_RULES:
        if any(k.upper() in p for k in keywords):
            return key
    return DEFAULT_PANEL


# ---------- resolver ----------

@dataclass
class AliasRow:
    code: str | None
    name: str | None
    panel_hint: str | None
    unit_hint: str | None
    canonical: str

    @property
    def specificity(self) -> int:
        return sum(x is not None for x in (self.panel_hint, self.unit_hint))


@dataclass
class TermResolver:
    aliases: list[AliasRow] = field(default_factory=list)
    conversions: dict[tuple[str | None, str], tuple[float, str]] = field(default_factory=dict)
    terms: dict[str, dict] = field(default_factory=dict)  # canonical -> term dict

    def term(self, canonical: str) -> dict | None:
        return self.terms.get(canonical)

    def _match(self, attr: str, value: str | None, panel: str | None, unit: str | None) -> str | None:
        """Ladder: hint-specific first, then generic; unique-only."""
        if value is None:
            return None
        candidates = [a for a in self.aliases if getattr(a, attr) == value]
        if not candidates:
            return None
        panel_key = resolve_panel(panel)
        unit_key = norm_unit(unit)
        satisfied = [
            a for a in candidates
            if (a.panel_hint is None or a.panel_hint == panel_key)
            and (a.unit_hint is None or a.unit_hint == unit_key)
        ]
        if not satisfied:
            return None
        best = max(a.specificity for a in satisfied)
        finalists = {a.canonical for a in satisfied if a.specificity == best}
        if len(finalists) == 1:
            return finalists.pop()
        return None  # ambiguous

    def resolve(self, code: str | None, name: str | None, panel: str | None, unit: str | None) -> str | None:
        for attr, value in (("code", norm_code(code)), ("name", norm_name(name))):
            hit = self._match(attr, value, panel, unit)
            if hit:
                return hit
        return None

    def convert_unit(self, canonical: str | None, value: float, unit: str | None) -> tuple[float, str | None]:
        """Return (value, canonical_unit). Converts only when a factor is known."""
        default = self.terms.get(canonical or "", {}).get("default_unit")
        unit_key = norm_unit(unit)
        if unit_key is None or unit_key == norm_unit(default):
            return value, default
        for cc in (canonical, None):  # term-specific rule first, then global
            rule = self.conversions.get((cc, unit_key))
            if rule:
                factor, to_unit = rule
                return round(value * factor, 6), to_unit
        return value, unit  # no rule known: keep source unit, never guess


# ---------- value parsing ----------

_SEMI_QUANT = re.compile(r"^([1-4])\+?$")
_NUMERIC = re.compile(r"^[+-]?[\d,]*\.?\d+([eE][+-]?\d+)?$")

NEGATIVE_WORDS = {"阴性", "阴", "-", "NEG", "NEGATIVE"}
POSITIVE_WORDS = {"阳性", "阳", "+", "POS", "POSITIVE"}


def parse_value(raw, value_type: str = "numeric"):
    """Return (value_num, value_text). Semi-quant carries an ordinal in value_num (ordering only)."""
    s = to_halfwidth(str(raw)).strip() if raw is not None else ""
    if s == "":
        return None, None
    if s in NEGATIVE_WORDS:
        return None, "阴性"
    if s in POSITIVE_WORDS:
        return None, "阳性"
    m = _SEMI_QUANT.match(s)
    if m:
        return float(m.group(1)), f"{m.group(1)}+"
    if s in {"±", "TRACE", "微"}:
        return 0.5, "±"
    if _NUMERIC.match(s):
        num = float(s.replace(",", ""))
        if value_type == "categorical":
            return num, s
        return num, None
    return None, s  # free text (organisms, comments)


# ---------- reference range parsing ----------

_RANGE = re.compile(r"^([\d.]+)\s*(?:-+|~|—|–|至)\s*([\d.]+)$")
_BOUND = re.compile(r"^(<=|>=|≤|≥|<|>)\s*([\d.]+)$")


def parse_ref_range(text):
    """Return dict(kind, low, high, low_inclusive, high_inclusive, expected) or None."""
    s = to_halfwidth(str(text)).strip() if text else ""
    s = re.sub(r"\s+", "", s)
    if not s:
        return None
    if s in {"阴性", "阳性"}:
        return {"kind": "categorical", "low": None, "high": None,
                "low_inclusive": True, "high_inclusive": True, "expected": s}
    m = _RANGE.match(s)
    if m:
        return {"kind": "numeric", "low": float(m.group(1)), "high": float(m.group(2)),
                "low_inclusive": True, "high_inclusive": True, "expected": None}
    m = _BOUND.match(s)
    if m:
        op, num = m.group(1), float(m.group(2))
        if op in ("<", "≤", "<="):
            return {"kind": "numeric", "low": None, "high": num,
                    "low_inclusive": True, "high_inclusive": op in ("≤", "<="), "expected": None}
        return {"kind": "numeric", "low": num, "high": None,
                "low_inclusive": op in ("≥", ">="), "high_inclusive": True, "expected": None}
    return None


# ---------- flags ----------

_FLAG_MAP = {
    "↑": "HIGH", "H": "HIGH", "高": "HIGH",
    "↓": "LOW", "L": "LOW", "低": "LOW",
    "阴性": "NEG", "阳性": "POS",
    "S": "S", "R": "R", "I": "I", "SDD": "SDD", "S?": "S_SUSPECT",
}


def map_source_flag(flag) -> str | None:
    s = to_halfwidth(str(flag)).strip() if flag else ""
    return _FLAG_MAP.get(s)


# 保护性指标：高于参考上限是好事（免疫标记），不进异常表。
# 语义极性表 —— 数值方向 ≠ 好坏方向的指标都在这里。
PROTECTIVE_HIGH = {"HBSAB"}  # 乙肝表面抗体：阳性/高 = 有免疫力（疫苗或既往清除）


def compute_flag(value_num, value_text, ref: dict | None, term: dict | None, fallback_ranges: list[dict]) -> str | None:
    """Deterministic flag. Prefer the report's own range, then system ranges. None when no rule applies."""
    if ref is None or (ref.get("kind") == "numeric" and ref.get("low") is None and ref.get("high") is None):
        ref = _pick_system_range(term, fallback_ranges) if term else None
    if ref is None:
        return None
    if ref["kind"] == "categorical":
        expected = norm_name(ref.get("expected"))
        actual = norm_name(value_text)
        if actual is None:
            return None
        return "NORMAL" if actual == expected else "ABNORMAL_CAT"
    if value_num is None:
        return None
    low, high = ref.get("low"), ref.get("high")
    code = (term or {}).get("code")
    if low is not None:
        below = value_num < low or (value_num == low and not ref.get("low_inclusive", True))
        if below:
            return "LOW"
    if high is not None:
        above = value_num > high or (value_num == high and not ref.get("high_inclusive", True))
        if above:
            return "PROTECT" if code in PROTECTIVE_HIGH else "HIGH"
    return "NORMAL"


def _pick_system_range(term: dict, ranges: list[dict]) -> dict | None:
    code = term.get("code")
    candidates = [r for r in ranges if r.get("canonical") == code]
    if not candidates:
        return None
    sex = term.get("_person_sex")
    age = term.get("_person_age")
    def score(r):
        s = 0
        if r.get("sex") == sex:
            s += 2
        if r.get("age_min") is not None or r.get("age_max") is not None:
            if age is None:
                s -= 5
            elif r.get("age_min") is not None and age < r["age_min"]:
                s -= 5
            elif r.get("age_max") is not None and age > r["age_max"]:
                s -= 5
            else:
                s += 1
        return s
    best = max(candidates, key=score)
    if score(best) < 0:
        return None
    return {"kind": best.get("kind", "numeric"), "low": best.get("low"), "high": best.get("high"),
            "low_inclusive": True, "high_inclusive": True, "expected": best.get("expected")}


# ---------- full item pipeline ----------

def normalize_item(resolver: TermResolver, *, code, name, value, unit, ref_range_text, flag,
                   panel=None, person_sex=None, person_age=None) -> dict:
    canonical = resolver.resolve(code, name, panel, unit)
    out = {
        "canonical_code": canonical,
        "raw_code": code, "raw_name": name, "raw_value": str(value), "raw_unit": unit,
        "raw_ref_range": ref_range_text, "raw_flag": flag,
        "value_num": None, "value_text": None,
        "unit_source": unit, "unit_canonical": unit,
        "source_ref_low": None, "source_ref_high": None,
        "source_ref_low_inclusive": True, "source_ref_high_inclusive": True,
        "source_ref_expected": None,
        "flag_source": map_source_flag(flag),
        "flag_computed": None,
    }
    term = resolver.term(canonical) if canonical else None
    value_type = term.get("value_type", "numeric") if term else "numeric"
    value_num, value_text = parse_value(value, value_type)
    if value_num is not None:
        value_num, unit_canonical = resolver.convert_unit(canonical, value_num, unit)
        out["unit_canonical"] = unit_canonical
    out["value_num"], out["value_text"] = value_num, value_text

    ref = parse_ref_range(ref_range_text)
    if ref:
        out["source_ref_low"] = ref["low"]
        out["source_ref_high"] = ref["high"]
        out["source_ref_low_inclusive"] = ref["low_inclusive"]
        out["source_ref_high_inclusive"] = ref["high_inclusive"]
        out["source_ref_expected"] = ref["expected"]

    if canonical:
        tdict = dict(term)
        tdict["_person_sex"] = person_sex
        tdict["_person_age"] = person_age
        ranges = getattr(resolver, "range_rows", [])
        out["flag_computed"] = compute_flag(value_num, value_text, ref, tdict, ranges)
    return out
