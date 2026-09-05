from app.normalize import (
    compute_flag, map_source_flag, norm_code, norm_unit, parse_ref_range, parse_value, resolve_panel,
)


def test_fullwidth_and_unit_normalization():
    assert norm_code("ｐｃｔ") == "PCT"
    assert norm_code(" 白细胞 ") == "白细胞"
    assert norm_unit("μmol/L") == "UMOL/L"
    assert norm_unit("ng/ml") == "NG/ML"
    assert norm_unit("１０＾９／Ｌ") == "10^9/L".upper()


def test_panel_resolution():
    assert resolve_panel("血常规-五分类") == "CBC"
    assert resolve_panel("纤溶二项+凝血全套") == "COAG"
    assert resolve_panel("血凝全套") == "COAG"
    assert resolve_panel("尿常规") == "URINE"
    assert resolve_panel("术前免疫筛查") == "IMMUNO"
    assert resolve_panel("心肌三项+降钙素原") == "CARDIAC"
    assert resolve_panel("降钙素原检测") == "CARDIAC"
    assert resolve_panel("生化全套(杂项+大肝功)") == "BIOCHEM"
    assert resolve_panel("肝功能+杂项全套") == "BIOCHEM"


def test_pct_collision_by_unit_and_panel(resolver):
    assert resolver.resolve("PCT", "血小板压积", "血常规", "%") == "PCT_PLT"
    assert resolver.resolve("PCT", "降钙素原", "心肌三项+降钙素原", "ng/ml") == "PCT_PROCAL"
    assert resolver.resolve("PCT", "降钙素原", "降钙素原检测", None) == "PCT_PROCAL"
    assert resolver.resolve(None, "降钙素原", None, None) == "PCT_PROCAL"
    assert resolver.resolve(None, "血小板压积", None, None) == "PCT_PLT"


def test_ckmb_split_by_unit(resolver):
    assert resolver.resolve("CKMB", "肌酸激酶同功酶(酶法)", "肝功能", "U/L") == "CKMB_ENZ"
    assert resolver.resolve("CKMB", "肌酸激酶同工酶", "心肌三项", "ng/ml") == "CKMB_MASS"
    assert resolver.resolve(None, "肌酸激酶同功酶(酶法)", None, None) == "CKMB_ENZ"


def test_wbc_vs_leu_by_panel(resolver):
    assert resolver.resolve("WBC", "白细胞", "血常规", "10^9/L") == "WBC"
    assert resolver.resolve(None, "白细胞", "尿常规", None) == "LEU"
    assert resolver.resolve(None, "白细胞", None, None) == "WBC"  # unique generic name


def test_misc_aliases(resolver):
    assert resolver.resolve("D-Dimer", None, None, None) == "DDIMER"
    assert resolver.resolve("TnI", "肌钙蛋白", None, "ng/ml") == "TNI"
    assert resolver.resolve("NT-proBNP", None, None, None) == "NT_PROBNP"
    assert resolver.resolve("Anti-TP", None, None, None) == "ANTI_TP"
    assert resolver.resolve("A/G", "白球比", None, None) == "AG_RATIO"
    assert resolver.resolve("PT%", None, None, None) == "PT_ACT"
    assert resolver.resolve("NEUT%", None, None, None) == "NEUT_PCT"
    assert resolver.resolve("eGFR", None, None, None) == "EGFR"
    assert resolver.resolve("HBsAg", None, None, None) == "HBSAG"


def test_unresolved_returns_none(resolver):
    assert resolver.resolve("XYZ999", "未知项目", "血常规", None) is None


def test_value_parsing():
    assert parse_value("3.81") == (3.81, None)
    assert parse_value("1+") == (1.0, "1+")
    assert parse_value("阴性") == (None, "阴性")
    assert parse_value("阳性") == (None, "阳性")
    assert parse_value("1,234.5") == (1234.5, None)
    assert parse_value("阪崎肠杆菌") == (None, "阪崎肠杆菌")
    num, text = parse_value("0.010", "categorical")
    assert (num, text) == (0.01, "0.010")


def test_ref_range_parsing():
    r = parse_ref_range("3.5-9.5")
    assert (r["low"], r["high"], r["kind"]) == (3.5, 9.5, "numeric")
    assert parse_ref_range("0.02--0.52")["high"] == 0.52
    r = parse_ref_range("≤0.1")
    assert r["high"] == 0.1 and r["high_inclusive"] is True and r["low"] is None
    r = parse_ref_range("<0.1")
    assert r["high"] == 0.1 and r["high_inclusive"] is False
    r = parse_ref_range(">90")
    assert r["low"] == 90 and r["low_inclusive"] is False
    r = parse_ref_range("≤114.5")
    assert r["high"] == 114.5 and r["high_inclusive"] is True
    assert parse_ref_range("阴性")["expected"] == "阴性"
    assert parse_ref_range("") is None
    assert parse_ref_range(None) is None


def test_flag_computation():
    # EGFR with >90 exclusive: 90 exactly is still below the exclusive lower bound
    ref = parse_ref_range(">90")
    assert compute_flag(59.18, None, ref, None, []) == "LOW"
    assert compute_flag(90.0, None, ref, None, []) == "LOW"
    assert compute_flag(90.01, None, ref, None, []) == "NORMAL"
    # inclusive high bound
    ref = parse_ref_range("≤0.1")
    assert compute_flag(0.1, None, ref, None, []) == "NORMAL"
    assert compute_flag(0.11, None, ref, None, []) == "HIGH"
    # no range at all -> None (honest NULL)
    assert compute_flag(1.0, None, None, None, []) is None
    # categorical
    ref = parse_ref_range("阴性")
    assert compute_flag(None, "1+", ref, None, []) == "ABNORMAL_CAT"
    assert compute_flag(None, "阴性", ref, None, []) == "NORMAL"


def test_source_flag_mapping():
    assert map_source_flag("↑") == "HIGH"
    assert map_source_flag("↓") == "LOW"
    assert map_source_flag("阴性") == "NEG"
    assert map_source_flag("阳性") == "POS"
    assert map_source_flag("S?") == "S_SUSPECT"
    assert map_source_flag("") is None
    assert map_source_flag(None) is None


def test_unit_conversion(resolver):
    v, u = resolver.convert_unit("GLU", 100.0, "mg/dL")
    assert abs(v - 5.551) < 1e-6 and u == "mmol/L"
    v, u = resolver.convert_unit("GLU", 6.9, "mmol/L")
    assert v == 6.9 and u == "mmol/L"
    v, u = resolver.convert_unit("CREA", 1.0, "mg/dL")
    assert abs(v - 88.4) < 1e-6 and u == "μmol/L"
    # unknown conversion: keep source unit, never guess
    v, u = resolver.convert_unit("CREA", 2.0, "g/L")
    assert v == 2.0 and u == "g/L"
