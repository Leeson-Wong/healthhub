from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import app.dashboard as dash


def _obs(v, hours):
    t = datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=hours)
    return SimpleNamespace(effective_at=t.isoformat(timespec="seconds"), value_num=v,
                           flag_computed="HIGH" if v > 100 else "NORMAL",
                           value_text=None, raw_value=str(v))


def setup_module(module):
    dash._SPARK_TZ = ZoneInfo("Asia/Shanghai")


def test_decimate_keeps_endpoints_and_count():
    rows = [_obs(100 + i, i * 24) for i in range(365)]
    out = dash._decimate(rows, 60)
    assert len(out) == 60
    assert out[0] is rows[0] and out[-1] is rows[-1]
    # deterministic
    assert [o.value_num for o in dash._decimate(rows, 60)] == [o.value_num for o in dash._decimate(rows, 60)]


def test_decimate_small_passthrough():
    rows = [_obs(i, i) for i in range(10)]
    assert dash._decimate(rows, 60) == rows


def test_line_svg_reference_band_and_axes():
    rows = [_obs(v, i * 24) for i, v in enumerate([120, 125, 118, 130, 122, 128], start=1)]
    svg, stats = dash._line_svg(rows, 90, 139, "收缩压", "mmHg", "NORMAL")
    assert "<svg" in svg and "spark-pt" in svg
    assert "class='c-band'" in svg  # reference band present (theme-aware class)
    assert svg.count("class='c-grid'") == 5  # 5 y gridlines
    assert svg.count("class='c-tick'") == 11  # 5 y tick labels + 6 x date labels
    assert stats["n"] == 6 and stats["min"][0] == 118 and stats["max"][0] == 130
    assert abs(stats["mean"] - sum([120, 125, 118, 130, 122, 128]) / 6) < 1e-9


def test_line_svg_decimates_large_series():
    rows = [_obs(120 + (i % 20), i * 24) for i in range(365)]
    svg, stats = dash._line_svg(rows, 90, 139, "收缩压", "mmHg", "NORMAL", max_points=60)
    assert svg.count("class='pt spark-pt'") == 60
    assert stats["n"] == 365  # stats over full series, decimation only for rendering


def test_line_svg_single_bound_dashed_line():
    rows = [_obs(97 + i * 0.1, i) for i in range(5)]
    svg, _ = dash._line_svg(rows, None, 95, "血氧饱和度", "%", "NORMAL")
    assert "stroke-dasharray" in svg
