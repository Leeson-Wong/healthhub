"""Server-rendered person dashboard: GET /dashboard/{person_id} — no auth, zero frontend deps.

Layout (priority order): overview chart (THE centerpiece, interactive) → hero metrics by
organ system → abnormal table (severity-sorted, every row dated) → trends → cultures → AI.
All curves are interactive: hover/tap shows the exact sample time; tap again opens a detail modal.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import markdown

from app.insights import _age_years
from app.models import (
    CanonicalTerm, ClinicalEvent, Condition, CultureReport, CultureSusceptibility, DiscussionPost,
    Encounter, InsightReport, Medication, Observation, PendingUpload, Person, PersonFact,
    ReferenceRange, SourceFile, SourceReport, SymptomNote,
)

# 事件类型 → (颜色, 中文标签)
EVENT_STYLES: dict[str, tuple[str, str]] = {
    "onset": ("#dc2626", "起病"),
    "condition_change": ("#dc2626", "病情"),
    "intervention": ("#2563eb", "干预"),
    "culture_result": ("#9333ea", "病原"),
    "test_result": ("#7c3aed", "检验"),
    "medication": ("#0d9488", "用药"),
    "transfer": ("#ea580c", "转科"),
    "surgery": ("#0f766e", "手术"),
    "followup": ("#0d9488", "复查"),
    "other": ("#64748b", "事件"),
}

KIND_GLYPH = {"onset": "起", "condition_change": "病", "intervention": "干", "culture_result": "原",
              "test_result": "检", "medication": "药", "transfer": "转", "surgery": "术",
              "followup": "查", "other": "事"}

FACT_CATEGORY_NAMES = {
    "past_history": "既往史",
    "family_history": "家族史",
    "baseline": "基础状态",
    "care": "照护信息",
    "social": "院前/社会背景",
}

HERO_LAYOUT: list[tuple[str, list[str]]] = [
    ("感染与炎症", ["PCT_PROCAL", "CRP", "WBC", "SAA"]),
    ("肝胆", ["TBIL", "DBIL", "ALT", "AST", "GGT"]),
    ("心脏", ["TNI", "NT_PROBNP", "MYO"]),
    ("肾脏", ["CREA", "EGFR", "BUN"]),
    ("血液", ["PLT", "HGB", "LYM_ABS"]),
    ("凝血", ["DDIMER", "PT", "FIB"]),
]

CSS = """
/* ---------- design tokens ---------- */
:root {
  --bg:#f2f5f8; --card:#ffffff; --ink:#1b2733; --muted:#5f7182; --line:#dfe6ee; --chip:#e9eef5;
  --danger:#c0392b; --danger-bg:#faeae8; --warn:#a05a12; --warn-bg:#f9f0e1;
  --warn2:#8a7a1e; --warn2-bg:#f7f3dd;
  --ok:#2c7a4b; --ok-bg:#e7f3ec; --low:#1c62b5; --low-bg:#e8f1fb; --info:#0f6aad;
  --accent:var(--info);
  --shadow-1:0 1px 2px rgba(23,36,47,.05); --shadow-2:0 6px 24px rgba(23,36,47,.10);
  --glass:rgba(242,245,248,.92); --hov:rgba(23,36,47,.035);
  --high:var(--danger);
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0e141b; --card:#161e28; --ink:#d9e2ec; --muted:#8fa1b3; --line:#253140; --chip:#1c2836;
    --danger:#ff8f85; --danger-bg:#3a1f1e; --warn:#ffb95d; --warn-bg:#382811;
    --warn2:#d9cb6d; --warn2-bg:#31301a;
    --ok:#82cba0; --ok-bg:#173021; --low:#7db4f2; --low-bg:#16283d; --info:#6db3e8;
    --shadow-1:none; --shadow-2:none;
    --glass:rgba(14,20,27,.92); --hov:rgba(255,255,255,.045);
  }
}
* { box-sizing:border-box; margin:0; padding:0; }
html { scroll-behavior:smooth; }
body { font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif; background:var(--bg); color:var(--ink); }
:focus-visible { outline:2px solid var(--info); outline-offset:2px; border-radius:4px; }
.num, .mon .v, .hero .val, .vital-card .row1 .v, .trend-card .row1 .v, td.v,
table td:not(.when):not(:first-child) { font-variant-numeric: tabular-nums; }
@media (prefers-reduced-motion: reduce){ *{animation:none!important;transition:none!important;} }
.wrap { max-width:1100px; margin:0 auto; padding:26px 22px 56px; }
a { color:var(--info); }

/* ---------- header ---------- */
.top { display:flex; justify-content:space-between; align-items:flex-end; flex-wrap:wrap; gap:10px;
       border-bottom:2px solid var(--ink); padding-bottom:12px; margin-bottom:8px; }
h1 { font-size:24px; letter-spacing:.5px; }
.top .who { color:var(--muted); font-size:13px; margin-top:2px; }
.top .asof { text-align:right; font-size:12px; color:var(--muted); line-height:1.7; }
.top .asof b { color:var(--ink); font-size:15px; }

/* ---------- sticky nav ---------- */
.stickynav { position:sticky; top:0; z-index:40; background:var(--glass);
  -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px);
  border-bottom:1px solid var(--line); margin:0 -22px 0; padding:8px 22px;
  display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.stickynav .anchors { display:flex; gap:4px; overflow-x:auto; scrollbar-width:none; }
.stickynav .anchors::-webkit-scrollbar{ display:none; }
.stickynav .anchors a { color:var(--muted); text-decoration:none; font-size:12.5px; padding:4px 9px;
  border-radius:6px; white-space:nowrap; }
.stickynav .anchors a:hover { background:var(--chip); color:var(--ink); }
.stickynav .sp { flex:1; }
.stickynav a.wchip { background:var(--chip); border-radius:6px; padding:3px 10px; color:var(--muted);
  text-decoration:none; font-size:12px; }
.stickynav a.wchip.on { background:var(--info); color:#fff; }
section { margin-top:22px; scroll-margin-top:64px; }
h2 { font-size:14px; color:var(--info); margin-bottom:10px; letter-spacing:.5px; }
h2 small { color:var(--muted); font-weight:400; margin-left:8px; }

/* day chips (kept compact) */
nav.days { display:flex; gap:6px; flex-wrap:wrap; padding:6px 0 4px; font-size:12px; color:var(--muted); }
nav.days span { background:var(--chip); border-radius:4px; padding:2px 8px; }

/* ---------- collapsible overview ---------- */
details.ovbox { margin-top:22px; background:var(--card); border:1px solid var(--line);
  border-radius:12px; box-shadow:var(--shadow-1); }
details.ovbox > summary { cursor:pointer; list-style:none; padding:12px 16px; display:flex;
  align-items:center; gap:10px; flex-wrap:wrap; }
details.ovbox > summary::-webkit-details-marker { display:none; }
details.ovbox > summary:hover { background:var(--hov); border-radius:12px; }
details.ovbox .arrow { transition:transform .15s; color:var(--muted); font-size:11px; }
details.ovbox[open] .arrow { transform:rotate(90deg); }
details.ovbox .ovtitle { font-size:14px; font-weight:600; color:var(--info); }
details.ovbox .ovsub { color:var(--muted); font-size:12px; }
details.ovbox .ovhint { margin-left:auto; color:var(--muted); font-size:11.5px; }
details.ovbox .ovbody { padding:0 16px 10px; }

/* ---------- charts (theme-aware via classes) ---------- */
.c-grid { stroke:var(--line); }
.c-tick { fill:var(--muted); }
.c-band { fill:var(--ok); opacity:.09; }
.c-bandline { stroke:var(--ok); opacity:.55; }
.c-line-ok { stroke:var(--info); }
.c-line-high { stroke:var(--danger); }
.c-line-low { stroke:var(--low); }
.c-dot-ok { fill:var(--info); } .c-dot-high { fill:var(--danger); } .c-dot-low { fill:var(--low); }
.c-dot-s3 { fill:var(--danger); } .c-dot-s2 { fill:var(--warn); }
.c-dot-s1 { fill:var(--warn2); } .c-dot-ok2 { fill:var(--ok); }
.overview { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px 6px;
            box-shadow:var(--shadow-1); }
.oscroll { overflow-x:auto; -webkit-overflow-scrolling:touch;
  mask-image:linear-gradient(to right,transparent 0,#000 12px,#000 calc(100% - 12px),transparent 100%);
  -webkit-mask-image:linear-gradient(to right,transparent 0,#000 12px,#000 calc(100% - 12px),transparent 100%); }
.oscroll svg { width:100%; min-width:620px; height:auto; display:block; overflow:visible; }
.legend { display:flex; gap:14px; flex-wrap:wrap; font-size:11.5px; color:var(--muted); padding:6px 2px 8px; }
.legend i { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:4px; vertical-align:-1px; }
.legend .l-s3{background:var(--danger)} .legend .l-s2{background:var(--warn)}
.legend .l-s1{background:var(--warn2)} .legend .l-ok{background:var(--ok)}

/* horizontal-scroll wrappers for wide tables on narrow screens */
.tscroll { overflow-x:auto; -webkit-overflow-scrolling:touch;
  mask-image:linear-gradient(to right,transparent 0,#000 8px,#000 calc(100% - 8px),transparent 100%);
  -webkit-mask-image:linear-gradient(to right,transparent 0,#000 8px,#000 calc(100% - 8px),transparent 100%); }
.tscroll > table { min-width:840px; }

/* ---------- vitals monitor strip ---------- */
.monrow { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }
.mon { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 12px 8px;
       box-shadow:var(--shadow-1); transition:box-shadow .15s, transform .15s; }
.mon:hover { box-shadow:var(--shadow-2); transform:translateY(-1px); }
.mon .k { font-size:12px; color:var(--muted); }
.mon .v { font-size:24px; font-weight:700; line-height:1.3; }
.mon .v small { font-size:11px; font-weight:400; color:var(--muted); margin-left:3px; }
.mon .v.HIGH { color:var(--danger); } .mon .v.LOW { color:var(--low); }
.mon .w { font-size:10.5px; color:var(--muted); margin-top:2px; }

/* ---------- vitals trend cards ---------- */
.vital-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:12px; }
@media (max-width:960px){ .vital-grid { grid-template-columns:1fr; } }
.vital-card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px;
              box-shadow:var(--shadow-1); transition:box-shadow .15s, transform .15s; }
.vital-card:hover { box-shadow:var(--shadow-2); transform:translateY(-1px); }
.vital-card .row1 { display:flex; justify-content:space-between; align-items:baseline; font-size:14px; }
.vital-card .row1 .n { font-weight:600; cursor:pointer;
  text-decoration:underline dotted var(--muted); text-underline-offset:3px; }
.vital-card .row1 .v { font-weight:700; font-size:17px; }
.vital-card .meta { color:var(--muted); font-size:11.5px; margin-top:4px; }
.vital-card svg { width:100%; min-width:0; height:auto; margin-top:6px; }

/* ---------- symptoms ---------- */
.symp { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:8px 12px;
  margin-bottom:8px; font-size:13px; display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
.symp .when { color:var(--muted); font-size:12px; white-space:nowrap; }
.symp .sev { letter-spacing:2px; color:var(--danger); font-size:10px; }

/* ---------- hero lab cards ---------- */
.group-title { font-size:13px; font-weight:600; margin:10px 0 6px; }
.group-title em { font-style:normal; color:var(--muted); font-weight:400; font-size:11px; margin-left:6px; }
.hero-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(168px,1fr)); gap:10px; }
.hero { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 12px 8px;
        box-shadow:var(--shadow-1); transition:box-shadow .15s, transform .15s; }
.hero:hover { box-shadow:var(--shadow-2); transform:translateY(-1px); }
.hero .name { font-size:12px; color:var(--muted); display:flex; justify-content:space-between; }
.hero .name > span:first-child { cursor:pointer;
  text-decoration:underline dotted var(--muted); text-underline-offset:3px; }
.hero .name .tag { font-size:10px; border-radius:3px; padding:0 4px; }
.tag.HIGH { background:var(--danger-bg); color:var(--danger); }
.tag.LOW { background:var(--low-bg); color:var(--low); }
.tag.NORMAL { background:var(--ok-bg); color:var(--ok); }
.tag.PROTECT { background:var(--ok-bg); color:var(--ok); }
.PROTECT { color:var(--ok); }
.tag.ABNORMAL_CAT { background:var(--danger-bg); color:var(--danger); }
.tag.NONE { background:var(--chip); color:var(--muted); }
.hero .val { font-size:26px; font-weight:700; line-height:1.25; margin-top:2px; }
.hero .val small { font-size:12px; font-weight:400; color:var(--muted); margin-left:3px; }
.hero .val.HIGH { color:var(--danger); } .hero .val.LOW { color:var(--low); }
.hero .delta { font-size:11px; margin-top:2px; }
.hero .delta .good { color:var(--ok); } .hero .delta .bad { color:var(--danger); }
.hero .delta .flat { color:var(--muted); }
.hero .when { font-size:10.5px; color:var(--muted); margin-top:3px; }
.sparkwrap svg { width:100%; height:auto; margin-top:4px; display:block; overflow:visible; }

/* ---------- tables ---------- */
table { width:100%; border-collapse:collapse; font-size:13px; background:var(--card);
        border:1px solid var(--line); border-radius:8px; overflow:hidden; box-shadow:var(--shadow-1); }
th { text-align:left; color:var(--muted); font-weight:500; padding:7px 10px; background:var(--chip);
     border-bottom:1px solid var(--line); white-space:nowrap; position:sticky; top:0; }
td { padding:7px 10px; border-bottom:1px solid var(--line); }
tbody tr:nth-child(even) td { background:var(--hov); }
tr:last-child td { border-bottom:none; }
td.when, th.when { color:var(--muted); font-size:12px; white-space:nowrap; }
.HIGH { color:var(--danger); font-weight:700; }
.LOW { color:var(--low); font-weight:700; }
.NORMAL { color:var(--ok); }
.ABNORMAL_CAT { color:var(--danger); font-weight:700; }
.sev { display:inline-block; min-width:34px; text-align:center; border-radius:4px; font-size:11px; padding:1px 4px; }
.sev.s3 { background:var(--danger-bg); color:var(--danger); }
.sev.s2 { background:var(--warn-bg); color:var(--warn); }
.sev.s1 { background:var(--warn2-bg); color:var(--warn2); }
.badge { display:inline-block; border-radius:4px; padding:0 5px; font-size:10.5px; background:var(--chip);
         color:var(--muted); margin-left:6px; }

/* ---------- lab trend cards ---------- */
.trend-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:10px; }
.trend-card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:9px 12px;
              box-shadow:var(--shadow-1); transition:box-shadow .15s, transform .15s; }
.trend-card:hover { box-shadow:var(--shadow-2); transform:translateY(-1px); }
.trend-card .row1 { display:flex; justify-content:space-between; align-items:baseline; font-size:13px; }
.trend-card .row1 .n { font-weight:600; cursor:pointer;
  text-decoration:underline dotted var(--muted); text-underline-offset:3px; }
.trend-card .row1 .v { font-weight:700; font-size:15px; }
.trend-card .meta { color:var(--muted); font-size:11px; margin-top:2px; }

/* ---------- cultures & insight ---------- */
.ev-line { pointer-events:none; }
.vsnap .monrow { margin: 10px 0 2px; }
.srccard { display:flex; align-items:center; gap:12px; text-decoration:none; color:var(--ink);
  background: color-mix(in srgb, var(--card) 76%, transparent);
  -webkit-backdrop-filter: blur(16px) saturate(1.5); backdrop-filter: blur(16px) saturate(1.5);
  border:1px solid color-mix(in srgb, var(--ink) 8%, transparent); border-radius:16px;
  padding:14px 18px; margin-top:22px; }
.srccard b { font-size:15.5px; }
.srccard span { color:var(--muted); font-size:13.5px; }
.srccard .go { margin-left:auto; font-size:22px; color:var(--accent); }
.exportrow { display:flex; gap:14px; flex-wrap:wrap; margin-top:6px; }
.exportrow a { font-size:13px; color:var(--accent); text-decoration:none; font-weight:600; }
.htl { position:relative; display:flex; gap:0; overflow-x:auto; padding:6px 8px 10px;
  border-radius:18px; scrollbar-width:thin; }
.htl-axis { position:absolute; left:0; right:0; top:92px; height:1px;
  background:linear-gradient(to right, transparent, color-mix(in srgb, var(--ink) 25%, transparent) 6%,
    color-mix(in srgb, var(--ink) 25%, transparent) 94%, transparent); pointer-events:none; }
.htl-node { position:relative; flex:0 0 168px; display:flex; flex-direction:column; align-items:center;
  background:none; border:none; cursor:pointer; padding:0 6px; text-align:center; color:inherit;
  min-height:150px; }
.htl-date { font-family:ui-monospace,monospace; font-size:12.5px; font-weight:700; color:var(--kc); }
.htl-title { font-size:13.5px; line-height:1.35; margin-top:3px; display:-webkit-box;
  -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; min-height:54px; }
.htl-dot { width:34px; height:34px; border-radius:50%; margin-top:8px; display:flex;
  align-items:center; justify-content:center; font-size:13px; font-weight:700;
  background:var(--card); border:2px solid var(--kc); color:var(--kc);
  box-shadow:0 2px 8px color-mix(in srgb, var(--kc) 30%, transparent); transition:transform .2s; }
.htl-node:hover .htl-dot { transform:scale(1.18); }
.htl-node.piv .htl-dot { background:var(--kc); color:var(--bg); width:38px; height:38px; }
.htl-kind { margin-top:7px; font-size:12px; font-weight:600; color:var(--muted); letter-spacing:1px; }
.htl.only-piv .htl-node:not(.piv) { display:none; }
.htl-bar { display:flex; align-items:center; justify-content:flex-end; gap:8px; margin-bottom:4px; }
.htl-nav button { border:1px solid var(--line); background:color-mix(in srgb, var(--card) 76%, transparent);
  color:var(--ink); border-radius:8px; width:34px; height:34px; font-size:17px; cursor:pointer; }
.htl + details summary { cursor:pointer; }
.phasebar { border-radius:10px; padding:10px 14px; margin:10px 0 0; font-size:14px; font-weight:600;
  display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.phasebar .phsub { font-weight:500; font-size:13px; }
.phasebar .phsrc { font-weight:400; font-size:11.5px; opacity:.75; }
.phsw { margin-left:auto; display:flex; gap:4px; }
.phbtn { border:1px solid currentColor; background:transparent; color:inherit; border-radius:6px;
  padding:2px 9px; font-size:12px; cursor:pointer; opacity:.75; }
.phbtn:hover { opacity:1; }
.phbtn.on { background:var(--ink); border-color:var(--ink); color:var(--bg); opacity:1; font-weight:700; }
.mon-ph { background:var(--danger-bg); color:var(--danger); }
.fol-ph { background:var(--ok-bg); color:var(--ok); }
.arc-ph { background:var(--chip); color:var(--muted); font-weight:500; }
.mslist { display:flex; flex-direction:column; gap:6px; }
.ms { display:flex; align-items:baseline; gap:10px; padding:8px 12px; background:var(--card);
      border:1px solid var(--line); border-left:3px solid var(--accent); border-radius:0 8px 8px 0; flex-wrap:wrap; }
.ms.done { border-left-color:var(--ok); opacity:.62; }
.msd { font-family:ui-monospace,monospace; font-size:12.5px; color:var(--muted); }
.ms .eta { margin-left:auto; font-size:13px; font-weight:700; color:var(--accent); }
.ms .eta.ok { color:var(--ok); font-weight:400; }
.ms .tld { width:100%; font-size:13px; color:var(--muted); }
.oncegrid { display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
.once { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 11px; font-size:13px; }
.once b { font-weight:600; margin-right:5px; }
.once i { font-style:normal; font-size:11px; font-weight:700; border:1px solid; border-radius:5px; padding:0 5px; margin-left:4px; }
.once i.PROTECT, .once.PROTECT i { color:var(--ok); border-color:var(--ok); }
.once i.HIGH { color:var(--danger); border-color:var(--danger); }
.once i.LOW { color:var(--low); border-color:var(--low); }
.once em { font-style:normal; color:var(--muted); font-size:11px; margin-left:7px; }
.srcchip { display:inline-block; font-size:10.5px; border:1px solid var(--line); border-radius:6px; padding:1px 6px; color:var(--muted); text-decoration:none; white-space:nowrap; }
.condgrid { display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:10px; }
.cond { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
.condh { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.condh b { font-size:14.5px; }
.condst { font-size:11px; font-weight:700; border:1.5px solid; border-radius:999px; padding:1px 9px; white-space:nowrap; }
.condcat { font-size:11px; color:var(--muted); border:1px solid var(--line); border-radius:6px; padding:0 6px; }
.condm { margin-top:6px; font-size:13px; color:var(--muted); line-height:1.5; }
.wchip { display:inline-block; font-size:10px; font-weight:700; border-radius:5px; padding:0 6px; border:1px solid; }
.wchip.w3 { color:#dc2626; border-color:#dc2626; background:rgba(220,38,38,.08); }
.wchip.w2 { color:#d97706; border-color:#d97706; background:rgba(217,119,6,.08); }
.wchip.w1 { color:var(--muted); border-color:var(--line); }
.dprec { display:inline-block; font-size:9.5px; border:1px solid #d97706; color:#d97706; border-radius:4px; padding:0 3px; margin-left:4px; vertical-align:middle; }
a.srcchip:hover { border-color: var(--accent); color: var(--accent); }
.pendbox { background:#fffbeb; color:#92400e; border:1px solid #fde68a; border-radius:10px; padding:10px 14px; margin:8px 0; font-size:14px; }
@media (prefers-color-scheme: dark) { .pendbox { background:#3b2f06; color:#fde047; border-color:#713f12; } }
.discform { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
.discform input, .discform select { padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:inherit; font-size:14px; }
.discform input { width:160px; }
.discform textarea { flex:1 1 100%; min-height:70px; padding:10px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:inherit; font-size:14px; resize:vertical; }
.discform button { padding:8px 22px; border:none; border-radius:8px; background:var(--accent); color:#fff; font-size:14px; font-weight:600; cursor:pointer; }
.discform button:disabled { opacity:0.6; cursor:default; }
.disclist { display:flex; flex-direction:column; gap:8px; }
.disc { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
.dcat { font-size:11px; font-weight:600; border:1px solid; border-radius:999px; padding:1px 8px; margin-right:8px; white-space:nowrap; }
.dauth { font-weight:700; font-size:13px; margin-right:8px; }
.dtime { font-family:ui-monospace,monospace; font-size:11.5px; color:var(--muted); }
.dcontent { margin-top:6px; font-size:14px; white-space:pre-wrap; line-height:1.55; }
.ev-lab { pointer-events:none; font-weight:600; paint-order:stroke; stroke:var(--card,#fff); stroke-width:3px; stroke-linejoin:round; }
.tline { display:flex; flex-direction:column; gap:2px; }
.tlev { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; padding:7px 10px; border-left:3px solid var(--line); border-radius:0 8px 8px 0; background:var(--card); }
.tlev:hover { filter:brightness(0.985); }
.tlk { font-size:11px; font-weight:600; border:1px solid; border-radius:999px; padding:1px 8px; white-space:nowrap; }
.tlt { font-family:ui-monospace,monospace; font-size:12px; color:var(--muted); white-space:nowrap; }
.tln { font-weight:600; }
.tld { width:100%; font-size:13px; color:var(--muted); margin-top:2px; white-space:pre-wrap; }
.tls { font-size:11px; opacity:0.8; }
.ev-approx { color:var(--muted); }
.bggrid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:10px; }
.bgcat { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
.bgch { font-size:13px; font-weight:700; color:var(--muted); margin-bottom:6px; }
.bgch em { font-style:normal; font-weight:400; opacity:0.7; margin-left:6px; }
.bgp { padding:4px 0; border-top:1px dashed var(--line); font-size:13.5px; }
.bgp:first-of-type { border-top:none; }
.culture { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px 14px;
           margin-bottom:10px; box-shadow:var(--shadow-1); }
.culture .head { font-size:13px; margin-bottom:6px; }
.mdr { color:var(--danger); font-weight:600; }
.drug { display:inline-block; margin:2px 4px 2px 0; font-size:12px; border-radius:4px; padding:2px 7px; }
.drug.R { background:var(--danger-bg); color:var(--danger); }
.drug.S { background:var(--ok-bg); color:var(--ok); }
.drug.SDD { background:var(--warn2-bg); color:var(--warn2); }
.insight { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 16px; }
.insight .meta { font-size:12px; color:var(--muted); margin-bottom:8px; }
.insight-md h1,.insight-md h2,.insight-md h3 { font-size:14px; margin:12px 0 6px; }
.insight-md p,.insight-md li { font-size:13px; line-height:1.75; }
.insight-md ul,.insight-md ol { padding-left:20px; }
.insight-md table { margin:8px 0; font-size:12.5px; }
.insight-md blockquote { border-left:3px solid var(--line); padding-left:10px; color:var(--muted); margin:8px 0; }
footer { text-align:center; color:var(--muted); font-size:12px; margin-top:26px; }
.empty { color:var(--muted); font-size:13px; background:var(--card); border:1px dashed var(--line);
        border-radius:8px; padding:14px; }
.empty a { color:var(--info); }

/* ---------- interactive points ---------- */
g.pt { cursor:pointer; }
g.pt .dot { transition: r .1s; }
g.pt:hover .dot, g.pt.on .dot { stroke:var(--ink); stroke-width:1.6; }

/* ---------- per-indicator detail ---------- */
.idetail { display:none; }
.dblock { margin:0 0 14px; }
.dblock h4 { font-size:13px; color:var(--info); margin-bottom:5px; }
.dblock p { font-size:13px; line-height:1.75; margin-bottom:4px; }
.dblock table { min-width:360px; }

/* ---------- tooltip ---------- */
#tip { position:absolute; z-index:60; display:none; max-width:280px; background:var(--ink); color:var(--bg);
       border-radius:8px; padding:8px 11px; font-size:12.5px; line-height:1.6; pointer-events:none;
       box-shadow:var(--shadow-2); }
#tip span { color:var(--muted); }

/* ---------- modal ---------- */
#modal { position:fixed; inset:0; z-index:50; display:none; background:rgba(12,20,30,.5);
         -webkit-backdrop-filter:blur(2px); backdrop-filter:blur(2px);
         padding:24px; overflow:auto; }
#modal.open { display:block; }
.mcard { background:var(--card); border:1px solid var(--line); border-radius:12px; max-width:860px;
         margin:24px auto; padding:18px 20px 20px; box-shadow:var(--shadow-2); overflow-x:auto;
         animation:mpop .16s ease-out; }
@keyframes mpop { from { transform:scale(.97); opacity:0; } to { transform:scale(1); opacity:1; } }
.mcard table { min-width:540px; }
.mcard h3 { font-size:15px; margin-bottom:10px; }
.mcard .sub { color:var(--muted); font-size:12px; margin:-6px 0 10px; }
.mclose { float:right; border:none; background:var(--chip); color:var(--ink); border-radius:6px;
          padding:4px 12px; font-size:13px; cursor:pointer; min-height:32px; }
td.mwhen, .mcard td.when { white-space:nowrap; color:var(--muted); font-size:12px; }

/* ---------- FAB (mobile quick entry) ---------- */
.fab { position:fixed; right:18px; bottom:calc(18px + env(safe-area-inset-bottom)); z-index:45;
  width:52px; height:52px; border-radius:50%; background:var(--info); color:#fff; display:none;
  align-items:center; justify-content:center; text-decoration:none; font-size:26px; font-weight:300;
  box-shadow:var(--shadow-2); }
.fab:hover { background:var(--info); filter:brightness(1.1); }
@media (max-width:640px){ .fab { display:flex; } }
.fab2 { bottom:calc(82px + env(safe-area-inset-bottom)); font-size:20px; }

/* ---------- responsive ---------- */
@media (max-width:640px){
  .wrap { padding:16px 12px 48px; }
  .stickynav { margin:0 -12px 0; padding:7px 12px; }
  h1 { font-size:20px; }
  .top { flex-direction:column; align-items:flex-start; gap:6px; }
  .top .asof { text-align:left; }
  nav.days { font-size:11px; padding-bottom:8px; }
  .hero-grid { grid-template-columns:repeat(auto-fill,minmax(146px,1fr)); gap:8px; }
  .hero .val { font-size:22px; }
  .trend-grid { grid-template-columns:1fr; }
  section { margin-top:16px; }
  .oscroll svg { min-width:560px; }
  .insight { overflow-x:auto; }
}
@media (min-width:641px) and (max-width:960px){
  .wrap { padding:22px 16px 36px; }
  .stickynav { margin:0 -16px 0; padding:8px 16px; }
  .oscroll svg { min-width:600px; }
}

/* ---------- print (share with doctors) ---------- */
@media print {
  body { background:#fff; color:#111; }
  .wrap { max-width:100%; padding:0; }
  .stickynav, .fab, #tip, #modal, nav.days, .mclose, .ovhint { display:none !important; }
  section, details.ovbox, .culture, .mon, .hero, .trend-card, .vital-card { break-inside:avoid;
    box-shadow:none; border-color:#bbb; }
  details.ovbox { border:1px solid #bbb; }
  details.ovbox > summary { padding:8px 10px; }
  .overview { box-shadow:none; }
  * { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  a { color:#111; text-decoration:none; }
  h2 { color:#111; }
  table { box-shadow:none; }
}

/* ════════ 2026-08 液态玻璃 · 大字版刷新（Apple less-is-more）════════
   原则：玻璃=模糊+半透明+发丝边；层级靠留白与字重不靠线框；正文≥15px、辅助≥12.5px；
   触控≥44px 主操作；color-mix 不支持时优雅回退为原实底。 */
html { font-size:17px; }
body { font-size:1rem; line-height:1.62; -webkit-tap-highlight-color:transparent; }
body::before { content:""; position:fixed; inset:0; z-index:-1; pointer-events:none;
  background:
    radial-gradient(640px 420px at 88% -6%, color-mix(in srgb, var(--info) 9%, transparent), transparent 62%),
    radial-gradient(560px 420px at -8% 34%, color-mix(in srgb, var(--ok) 7%, transparent), transparent 60%),
    var(--bg); }
h1 { font-size:27px; letter-spacing:.2px; }
.top .who { font-size:14px; }
.top .asof { font-size:13px; }
h2 { font-size:15.5px; }
h2 small { font-size:12.5px; margin-left:10px; }
.stickynav .anchors a { font-size:14px; padding:6px 11px; }
.stickynav a.wchip { font-size:13px; padding:5px 12px; }
nav.days { font-size:13px; }
details.ovbox .ovtitle { font-size:15px; }
details.ovbox .ovsub, details.ovbox .ovhint { font-size:13px; }
.legend { font-size:12.5px; }
.mon .k { font-size:13px; }
.mon .v { font-size:24px; }
.mon .v small { font-size:12px; }
.mon .w { font-size:12px; }
.vital-card .row1 { font-size:15px; }
.vital-card .meta, .trend-card .meta { font-size:12.5px; }
.hero .name span:first-child { font-size:16px; }
.hero .val { font-size:27px; }
.hero .delta { font-size:13px; }
.hero .when { font-size:12.5px; }
.trend-card .row1 .v { font-size:16px; }
.tlev .tln { font-size:15.5px; }
.tlev .tlt { font-size:13px; }
.tld { font-size:14px !important; }
.bgp { font-size:14.5px; }
.condh b { font-size:16px; }
.condm { font-size:14px; }
.disc .dcontent { font-size:15.5px; }
.dauth { font-size:14px; }
.dtime { font-size:12.5px; }
.msd { font-size:13.5px; }
.ms { font-size:14.5px; }
table { font-size:15px; }
table th { font-size:13px; }
td.when, .mwhen { font-size:13px; }
.sub { font-size:13.5px; }
.phasebar { font-size:15px; border-radius:16px; }
.phbtn { padding:5px 13px; font-size:13px; border-radius:8px; min-height:32px; }
.discform button { min-height:48px; }
.discform input, .discform select, .discform textarea { font-size:15.5px; min-height:48px; }
.discform textarea { min-height:84px; }
.fab { width:56px; height:56px; border-radius:18px; font-size:26px; }

/* 玻璃表面：卡片统一 18px 圆角 + 模糊 + 发丝边（不支持则保持原实底） */
.hero, .trend-card, .vital-card, .culture, .disc, .cond, .ms, .overview,
details.ovbox, .bgcat, .tlev, .pendbox, .phasebar, .fab, .discform {
  background: color-mix(in srgb, var(--card) 76%, transparent);
  -webkit-backdrop-filter: blur(18px) saturate(1.6);
  backdrop-filter: blur(18px) saturate(1.6);
  border: 1px solid color-mix(in srgb, var(--ink) 8%, transparent);
}
.hero, .trend-card, .vital-card, .culture, .disc, .cond, .ms, .overview,
details.ovbox, .bgcat, .tlev { border-radius:18px; box-shadow:var(--shadow-1); }
.fab { border:none; background: color-mix(in srgb, var(--info) 82%, transparent);
  -webkit-backdrop-filter: blur(14px); backdrop-filter: blur(14px); }
.stickynav { -webkit-backdrop-filter:blur(20px) saturate(1.7); backdrop-filter:blur(20px) saturate(1.7); }
#tip { -webkit-backdrop-filter:blur(20px) saturate(1.6); backdrop-filter:blur(20px) saturate(1.6); }
.phasebar.mon-ph { background: color-mix(in srgb, var(--danger) 10%, transparent); }
.phasebar.fol-ph { background: color-mix(in srgb, var(--ok) 12%, transparent); }
.phasebar.arc-ph { background: color-mix(in srgb, var(--ink) 6%, transparent); }
section { margin-top:26px; }
.wrap { padding:26px 20px 64px; }
@media (max-width:640px){
  h1 { font-size:24px; }
  .hero .val { font-size:24px; }
  .wrap { padding:20px 14px 72px; }
  .mon .v { font-size:21px; }
}
@media (prefers-reduced-motion: reduce){ .fab, .phbtn, button { transition:none !important; } }

"""

JS = r"""
(function(){
  var tip=document.getElementById('tip'), modal=document.getElementById('modal'), mcard=document.getElementById('mcard');
  var active=null;
  function esc(s){var d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML;}
  function showTip(g){
    var html;
    if(g.classList.contains('spark-pt')){
      html='<b>'+esc(g.dataset.name)+'</b><br>'+esc(g.dataset.v)+' '+esc(g.dataset.unit||'')+
           (g.dataset.flag&&g.dataset.flag!=='NORMAL'&&g.dataset.flag?'（'+esc(g.dataset.flag)+'）':'')+
           '<br><span>采样 '+esc(g.dataset.t)+'</span>';
    }else{
      html='<b>'+esc(g.dataset.t)+' 采样</b><br>异常 '+esc(g.dataset.c)+' / '+esc(g.dataset.n)+' 项'+
           (g.dataset.w?'<br>最偏离：'+esc(g.dataset.w):'')+'<br><span>再点一次查看本次全部指标</span>';
    }
    tip.innerHTML=html; tip.style.display='block';
    var r=g.getBoundingClientRect(), tw=tip.offsetWidth, th=tip.offsetHeight;
    var x=r.left+window.scrollX+r.width/2-tw/2;
    var y=r.top+window.scrollY-th-10; if(y<window.scrollY+4){y=r.bottom+window.scrollY+8;}
    x=Math.max(window.scrollX+4,Math.min(x,window.scrollX+document.documentElement.clientWidth-tw-6));
    tip.style.left=x+'px'; tip.style.top=y+'px';
  }
  function closeTip(){tip.style.display='none'; if(active){active.classList.remove('on');} active=null;}
  function openModal(html){mcard.innerHTML=html; modal.classList.add('open');}
  function closeModal(){modal.classList.remove('open');}
  modal.addEventListener('click', function(e){ if(e.target===modal) closeModal(); });
  document.addEventListener('keydown', function(e){ if(e.key==='Escape'){closeModal();closeTip();} });
  document.addEventListener('click', function(e){
    if(!e.target.closest('g.pt') && !e.target.closest('#modal') && !e.target.closest('.mclose')){ closeTip(); }
  });
  function openDetail(g){
    if(g.classList.contains('spark-pt')){
      var card=g.closest('.hero')||g.closest('.trend-card');
      var det=card && card.querySelector('.idetail');
      if(det){ openModal(det.innerHTML); return; }
      var wrap=g.closest('.sparkwrap');
      var series=[]; try{series=JSON.parse(wrap.getAttribute('data-series'));}catch(e){}
      var rows=series.map(function(p){
        return '<tr><td class="mwhen">'+esc(p[0])+'</td><td class="'+esc(p[2])+'">'+esc(p[1])+' '+esc(g.dataset.unit||'')+
               '</td><td class="'+esc(p[2])+'">'+(esc(p[2])||'-')+'</td></tr>';}).join('');
      openModal('<button class="mclose" onclick="document.getElementById(\'modal\').classList.remove(\'open\')">关闭</button>'+
        '<h3>'+esc(g.dataset.name)+' · 全部采样</h3>'+
        '<table><tr><th>采样时间</th><th>值</th><th>状态</th></tr>'+rows+'</table>');
    }else{
      var pid=g.getAttribute('data-pid'), fu=encodeURIComponent(g.getAttribute('data-fu'));
      openModal('<button class="mclose" onclick="document.getElementById(\'modal\').classList.remove(\'open\')">关闭</button>'+
        '<h3>'+esc(g.dataset.t)+' · 本次采样</h3><div class="sub">加载中…</div>');
      fetch('/observations?person_id='+pid+'&date_from='+fu+'&date_to='+fu).then(function(r){return r.json();}).then(function(list){
        list.sort(function(a,b){
          var s=function(x){return x.flag_computed&&x.flag_computed!=='NORMAL'?0:1;};
          return s(a)-s(b)||String(a.name_cn||'').localeCompare(String(b.name_cn||''));
        });
        var rows=list.map(function(o){
          var val=(o.value_num!==null&&o.value_num!==undefined)?o.value_num:(o.value_text||o.raw_value||'');
          return '<tr><td>'+esc(o.name_cn||o.canonical_code)+'<span class="badge">'+esc(o.canonical_code||'')+'</span></td>'+
            '<td class="'+esc(o.flag_computed||'')+'">'+esc(val)+' '+esc(o.unit_canonical||'')+'</td>'+
            '<td class="mwhen">'+esc(o.raw_ref_range||'-')+'</td>'+
            '<td class="'+esc(o.flag_computed||'')+'">'+esc(o.flag_computed||'-')+'</td>'+
            '<td>'+esc(o.flag_source||'-')+'</td></tr>';}).join('');
        openModal('<button class="mclose" onclick="document.getElementById(\'modal\').classList.remove(\'open\')">关闭</button>'+
          '<h3>'+esc(g.dataset.t)+' · 本次采样全部指标</h3><div class="sub">异常在前 · 共 '+list.length+' 项</div>'+
          '<table><tr><th>指标</th><th>值</th><th>参考范围</th><th>计算</th><th>原始</th></tr>'+rows+'</table>');
      }).catch(function(){
        openModal('<h3>加载失败，请重试</h3>');
      });
    }
  }
  document.querySelectorAll('g.pt').forEach(function(g){
    g.addEventListener('mouseenter', function(){ if(!active) showTip(g); });
    g.addEventListener('mouseleave', function(){ if(!active) tip.style.display='none'; });
    g.addEventListener('click', function(e){
      e.stopPropagation();
      if(active===g){ closeTip(); openDetail(g); }
      else{ closeTip(); active=g; g.classList.add('on'); showTip(g); }
    });
  });
  document.querySelectorAll('.hero .name > span:first-child, .trend-card .row1 .n, .vital-card .row1 .n').forEach(function(el){
    el.addEventListener('click', function(e){
      e.stopPropagation();
      var card = el.closest('.hero') || el.closest('.trend-card') || el.closest('.vital-card');
      var det = card && card.querySelector('.idetail');
      if(det) openModal(det.innerHTML);
    });
  });
})();
"""


def esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def _fmt(o: Observation) -> str:
    if o.value_num is not None:
        return f"{o.value_num:g}"
    return o.value_text or o.raw_value


def _flag_of(o: Observation) -> str:
    return o.flag_computed or ""


def _severity(last: Observation) -> float:
    v = last.value_num
    if v is None:
        return 1.5 if last.flag_computed == "ABNORMAL_CAT" else 0
    lo, hi = last.source_ref_low, last.source_ref_high
    if last.flag_computed == "HIGH" and hi:
        return v / hi if hi > 0 else 1
    if last.flag_computed == "LOW" and lo:
        return lo / v if v > 0 else 1
    return 1


def _event_x(times: list[str], occurred_at: str) -> float | None:
    """事件时间在（按时间排序的）数据点序列上的归一化位置 0..1，越界返回 None。"""
    import bisect
    i = bisect.bisect_left(times, occurred_at)
    if i <= 0 and occurred_at < times[0]:
        return None
    if i >= len(times):
        return None
    if times[i] == occurred_at:
        return i / (len(times) - 1) if len(times) > 1 else 0.5
    if i == 0:
        return 0.0
    # 落在 i-1 与 i 之间：按时间戳线性插值（x 轴是等距索引，取最近即可的折中）
    t0, t1 = times[i - 1], times[i]
    try:
        d0 = (datetime.fromisoformat(occurred_at) - datetime.fromisoformat(t0)).total_seconds()
        d1 = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds()
        frac = d0 / d1 if d1 else 0.5
    except ValueError:
        frac = 0.5
    return (i - 1 + frac) / (len(times) - 1) if len(times) > 1 else 0.5


def _spark_svg(rows: list[Observation], flag: str, name: str, unit: str,
               events: list | None = None) -> str:
    """Interactive sparkline: hover/tap a dot → tooltip with exact sample time; tap twice → modal."""
    vals = [r.value_num for r in rows if r.value_num is not None]
    dated = [r for r in rows if r.value_num is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(vals)
    w, h, pad = 200.0, 34.0, 5.0
    pts, dots = [], []
    for i, r in enumerate(dated):
        x = pad + i * ((w - 2 * pad) / (n - 1))
        y = h - 6 - (r.value_num - lo) / span * (h - 12)
        pts.append(f"{x:.1f},{y:.1f}")
        dots.append(
            f"<g class='pt spark-pt' data-t='{esc(_lt(r.effective_at))}' data-v='{esc(_fmt(r))}' "
            f"data-name='{esc(name)}' data-unit='{esc(unit)}' data-flag='{esc(_flag_of(r))}'>"
            f"<circle class='dot {_spark_dot_class(_flag_of(r) or flag)}' cx='{x:.1f}' cy='{y:.1f}' "
            f"r='{3.2 if i == n - 1 else 2.4}' opacity='{1 if i == n - 1 else 0.55}'/>"
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='9' fill='transparent'/></g>")
    ev_parts = []
    if events:
        times = [r.effective_at for r in dated]
        for ev in events:
            frac = _event_x(times, ev.occurred_at)
            if frac is None:
                continue
            color, _lab = EVENT_STYLES.get(ev.kind, EVENT_STYLES["other"])
            ex = pad + frac * (w - 2 * pad)
            ev_parts.append(
                f"<line class='ev-line' x1='{ex:.1f}' y1='1' x2='{ex:.1f}' y2='{h - 1:.1f}' "
                f"stroke='{color}'><title>{esc(_lt(ev.occurred_at))} · {esc(ev.title)}</title></line>")
    return (f"<svg viewBox='0 0 {w:.0f} {h:.0f}'>"
            f"<polyline class='{_spark_line_class(flag)}' points='{' '.join(pts)}' fill='none' "
            f"stroke-width='2' stroke-linejoin='round' stroke-linecap='round' opacity='0.8'/>"
            f"{''.join(ev_parts)}{''.join(dots)}</svg>")


def _spark_line_class(flag: str) -> str:
    if flag in ("HIGH", "ABNORMAL_CAT"):
        return "c-line-high"
    return "c-line-low" if flag == "LOW" else "c-line-ok"


def _spark_dot_class(flag: str) -> str:
    if flag in ("HIGH", "ABNORMAL_CAT"):
        return "c-dot-high"
    return "c-dot-low" if flag == "LOW" else "c-dot-ok"


_SPARK_TZ = None  # set per-request by render_dashboard


def _lt(utc_iso: str) -> str:
    try:
        return datetime.fromisoformat(utc_iso).astimezone(_SPARK_TZ).strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return utc_iso[:16]


_BG_CACHE: dict[str, dict] = {}


def _backgrounds(seed_dir: str) -> dict:
    import pathlib
    p = str(pathlib.Path(seed_dir) / "term_backgrounds.json")
    if p not in _BG_CACHE:
        try:
            _BG_CACHE[p] = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        except OSError:
            _BG_CACHE[p] = {}
    return _BG_CACHE[p]


_CATEGORY_FALLBACK = {
    "CBC": "血液常规检验指标，反映感染、贫血与凝血细胞状态。",
    "COAG": "凝血功能指标，反映止血与纤溶系统的平衡。",
    "IMMUNO": "感染免疫筛查指标。",
    "BIOCHEM": "生化指标，反映肝、肾、代谢与炎症状态。",
    "URINE": "尿液常规检验指标。",
    "CARDIAC": "心脏相关标志物。",
}


def _dist_outside(v: float, lo, hi) -> float:
    if lo is not None and v < lo:
        return lo - v
    if hi is not None and v > hi:
        return v - hi
    return 0.0


def _analysis_html(rows: list, term: dict) -> str:
    """Deterministic per-indicator analysis of this person's series (no LLM)."""
    n = len(rows)
    first, last = rows[0], rows[-1]
    unit = last.unit_canonical or ""
    ref = last.raw_ref_range or ""
    ps = []
    ps.append(f"<p>窗口内共 <b>{n}</b> 次采样（{_lt(first.effective_at)} ~ {_lt(last.effective_at)}）。</p>")
    fl = _flag_of(last)
    if fl == "HIGH":
        sev = _severity(last)
        deg = "重度" if sev >= 3 else ("中度" if sev >= 1.5 else "轻度")
        ps.append(f"<p>当前 <b class='HIGH'>{_fmt(last)} {esc(unit)}</b>（{_lt(last.effective_at)}），"
                  f"高于参考上限约 <b>{sev:.1f} 倍</b>（{deg}偏离；参考 {esc(ref) or '未提供'}）。</p>")
    elif fl == "LOW":
        lo = last.source_ref_low
        ratio = f"约为下限的 {last.value_num / lo:.0%}" if lo and last.value_num else ""
        ps.append(f"<p>当前 <b class='LOW'>{_fmt(last)} {esc(unit)}</b>（{_lt(last.effective_at)}），"
                  f"低于参考下限{('，' + ratio) if ratio else ''}（参考 {esc(ref) or '未提供'}）。</p>")
    elif fl == "ABNORMAL_CAT":
        ps.append(f"<p>当前为 <b class='ABNORMAL_CAT'>{esc(last.value_text or _fmt(last))}</b>（{_lt(last.effective_at)}），"
                  f"与预期结果（{esc(last.source_ref_expected or '阴性')}）不符。</p>")
    elif fl == "NORMAL":
        ps.append(f"<p>当前 <b class='NORMAL'>{_fmt(last)} {esc(unit)}</b>（{_lt(last.effective_at)}）处于参考范围内。</p>")
    else:
        ps.append(f"<p>当前值 {_fmt(last)} {esc(unit)}（{_lt(last.effective_at)}），暂无参考范围可比。</p>")

    nums = [r for r in rows if r.value_num is not None]
    if len(nums) >= 2:
        peak = max(nums, key=lambda r: r.value_num)
        if peak is not nums[-1] and peak.value_num:
            drop = (nums[-1].value_num - peak.value_num) / peak.value_num * 100
            if abs(drop) >= 0.5:
                ps.append(f"<p>窗口峰值 {peak.value_num:g}（{_lt(peak.effective_at)}），"
                          f"现值较峰值 <b>{drop:+.1f}%</b>。</p>")
        if nums[0].value_num:
            d0 = (nums[-1].value_num - nums[0].value_num) / nums[0].value_num * 100
            if abs(d0) >= 0.5:
                ps.append(f"<p>较窗口首值（{nums[0].value_num:g}）整体变化 <b>{d0:+.1f}%</b>。</p>")

    any_abnormal_before = any(_flag_of(r) in ("HIGH", "LOW", "ABNORMAL_CAT") for r in rows[:-1])
    if n == 1:
        summary = "仅一次采样，待复查对比。"
    elif fl in ("HIGH", "LOW", "ABNORMAL_CAT"):
        if len(nums) >= 2:
            d_prev = _dist_outside(nums[-2].value_num, nums[-2].source_ref_low, nums[-2].source_ref_high)
            d_now = _dist_outside(nums[-1].value_num, nums[-1].source_ref_low, nums[-1].source_ref_high)
            if d_now < d_prev:
                summary = "仍超出参考范围，但较前次<b>向正常回落</b>。"
            elif d_now > d_prev:
                summary = "仍异常且较前次<b>偏离加重</b>，建议重点关注。"
            else:
                summary = "持续异常，暂未见明显回落。"
        else:
            summary = "持续异常，暂未见明显回落。"
    elif any_abnormal_before:
        summary = "此前曾异常，<b>目前已回到参考范围</b>。"
    else:
        summary = "窗口内各次采样均处于参考范围。"
    ps.append(f"<p><b>小结：{summary}</b></p>")
    return "".join(ps)


def _detail_html(rows: list, term, bg: dict, report_meta: dict | None = None) -> str:
    """Full modal body: background intro + rule-based analysis + dated history."""
    code = term["code"]
    info = bg.get(code) or {}
    what = info.get("what") or _CATEGORY_FALLBACK.get(term.get("category"), "")
    last_fl = _flag_of(rows[-1])
    close_btn = ("<button class=\"mclose\" onclick=\"document.getElementById('modal').classList.remove('open')\">关闭</button>")
    unit = rows[-1].unit_canonical or ""

    def src_cell(r):
        if not report_meta:
            return ""
        m = report_meta.get(r.source_report_id)
        if not m:
            return ""
        chip = f"<a class='srcchip' href='{m['href']}' target='_blank'>{m['label']}</a>" if m.get("href") \
            else f"<span class='srcchip'>{m['label']}</span>"
        return f"<td class='mwhen'>{chip}</td>"

    head_src = "<th>来源</th>" if report_meta else ""
    hist = "".join(
        f"<tr><td class='mwhen'>{_lt(r.effective_at)}</td>"
        f"<td class='{esc(_flag_of(r))}'>{esc(_fmt(r))} {esc(unit)}</td>"
        f"<td class='{esc(_flag_of(r))}'>{esc({'PROTECT': '保护性↑'}.get(_flag_of(r)) or _flag_of(r) or '-')}</td>"
        f"<td class='mwhen'>{esc(r.raw_ref_range or '-')}</td>{src_cell(r)}</tr>"
        for r in reversed(rows))
    interp = []
    if last_fl == "LOW":
        if info.get("low"):
            interp.append(f"<p><b>降低时：</b>{esc(info['low'])}</p>")
        if info.get("high"):
            interp.append(f"<p><b>升高时：</b>{esc(info['high'])}</p>")
    else:
        if info.get("high"):
            interp.append(f"<p><b>升高/阳性时：</b>{esc(info['high'])}</p>")
        if last_fl == "NORMAL" and info.get("low"):
            interp.append(f"<p><b>降低时：</b>{esc(info['low'])}</p>")
    return (
        f"{close_btn}<h3>{esc(term['name_cn'])}<span class='badge'>{esc(code)}</span>"
        f"<span class='badge'>{esc(term.get('default_unit') or '')}</span></h3>"
        f"<div class='dblock'><h4>背景介绍</h4><p>{esc(what)}</p>{''.join(interp)}</div>"
        f"<div class='dblock'><h4>单项分析（规则生成 · 仅供参考）</h4>{_analysis_html(rows, term)}</div>"
        f"<div class='dblock'><h4>全部采样（{len(rows)} 次，最新在前）</h4>"
        f"<table><tr><th>采样时间</th><th>值</th><th>状态</th><th>参考范围</th>{head_src}</tr>{hist}</table></div>")


def _decimate(rows: list, max_points: int = 60) -> list:
    """Deterministic uniform sampling; always keeps first and last."""
    n = len(rows)
    if n <= max_points:
        return list(rows)
    step = (n - 1) / (max_points - 1)
    out, last_i = [], -1
    for k in range(max_points):
        i = round(k * step)
        if i != last_i:
            out.append(rows[i])
            last_i = i
    return out


def _line_svg(rows: list, ref_lo, ref_hi, name: str, unit: str, flag: str,
              max_points: int = 90, height: float = 170.0,
              events: list | None = None) -> tuple[str, dict]:
    """Real-axis interactive line chart with reference band. Returns (svg, stats)."""
    nums = [r for r in rows if r.value_num is not None]
    shown = _decimate(nums, max_points)
    n = len(shown)
    if n == 0:
        return "", {}
    padL, padR, padT, padB = 46, 16, 14, 26
    width = min(2000.0, max(560.0, 80 + n * 16))
    vals = [r.value_num for r in shown]
    lo_candidates = vals + ([ref_lo] if ref_lo is not None else [])
    hi_candidates = vals + ([ref_hi] if ref_hi is not None else [])
    v_lo, v_hi = min(lo_candidates), max(hi_candidates)
    if v_hi == v_lo:
        v_hi = v_lo + 1
    margin = (v_hi - v_lo) * 0.08
    v_lo -= margin
    v_hi += margin

    def Y(v):
        return padT + (height - padT - padB) * (1 - (v - v_lo) / (v_hi - v_lo))

    def X(i):
        return padL + i * ((width - padL - padR) / (n - 1)) if n > 1 else width / 2

    parts = []
    if ref_lo is not None and ref_hi is not None and ref_hi > ref_lo:
        parts.append(f"<rect class='c-band' x='{padL}' y='{Y(ref_hi):.1f}' width='{width - padL - padR:.1f}' "
                     f"height='{max(0.0, Y(ref_lo) - Y(ref_hi)):.1f}'/>")
    elif ref_lo is not None or ref_hi is not None:
        bound = ref_hi if ref_hi is not None else ref_lo
        parts.append(f"<line class='c-bandline' x1='{padL}' y1='{Y(bound):.1f}' x2='{width - padR:.1f}' "
                     f"y2='{Y(bound):.1f}' stroke-dasharray='4 4'/>")
    # y ticks (5)
    for k in range(5):
        v = v_lo + (v_hi - v_lo) * k / 4
        y = Y(v)
        parts.append(f"<line class='c-grid' x1='{padL}' y1='{y:.1f}' x2='{width - padR:.1f}' y2='{y:.1f}'/>"
                     f"<text class='c-tick' x='{padL - 6}' y='{y + 3.5:.1f}' text-anchor='end' "
                     f"font-size='10.5'>{v:.6g}</text>")
    # line + points
    if n > 1:
        pts = " ".join(f"{X(i):.1f},{Y(r.value_num):.1f}" for i, r in enumerate(shown))
        parts.append(f"<polyline class='{_spark_line_class(flag)}' points='{pts}' fill='none' "
                     f"stroke-width='2' stroke-linejoin='round' stroke-linecap='round' opacity='0.85'/>")
    xstep = max(1, -(-n // 10))
    for i, r in enumerate(shown):
        x, y = X(i), Y(r.value_num)
        fl = _flag_of(r) or flag
        parts.append(
            f"<g class='pt spark-pt' data-t='{esc(_lt(r.effective_at))}' data-v='{r.value_num:g}' "
            f"data-name='{esc(name)}' data-unit='{esc(unit)}' data-flag='{esc(fl)}'>"
            f"<circle class='dot {_spark_dot_class(fl)}' cx='{x:.1f}' cy='{y:.1f}' "
            f"r='{3 if i == n - 1 else 2.3}' opacity='{1 if i == n - 1 else 0.6}'/>"
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='10' fill='transparent'/></g>")
        if i % xstep == 0 or i == n - 1:
            parts.append(f"<text class='c-tick' x='{x:.1f}' y='{height - 8:.1f}' text-anchor='middle' "
                         f"font-size='10.5'>{_lt(r.effective_at)[:5]}</text>")
    stats = {}
    if vals:
        mean = sum(vals) / len(vals)
        rmin = min(shown, key=lambda r: r.value_num)
        rmax = max(shown, key=lambda r: r.value_num)
        stats = {"mean": mean, "min": (rmin.value_num, _lt(rmin.effective_at)),
                 "max": (rmax.value_num, _lt(rmax.effective_at)), "n": len(nums)}
    if events:
        times = [r.effective_at for r in shown]
        for k, ev in enumerate(events):
            frac = _event_x(times, ev.occurred_at)
            if frac is None:
                continue
            color, lab = EVENT_STYLES.get(ev.kind, EVENT_STYLES["other"])
            ex = padL + frac * (width - padL - padR)
            parts.append(
                f"<line class='ev-line' x1='{ex:.1f}' y1='{padT}' x2='{ex:.1f}' "
                f"y2='{height - padB:.1f}' stroke='{color}' stroke-width='1.5' "
                f"stroke-dasharray='5 3' opacity='0.7'>"
                f"<title>{esc(_lt(ev.occurred_at))} · [{lab}] {esc(ev.title)}"
                + (f" — {esc(ev.detail)}" if ev.detail else "") + "</title></line>")
            short = ev.title[:6]
            ly = padT + 10 + (k % 2) * 11
            parts.append(
                f"<text class='ev-lab' x='{ex:.1f}' y='{ly}' text-anchor='middle' "
                f"font-size='9' fill='{color}'>{esc(short)}</text>")
    return (f"<svg viewBox='0 0 {width:.0f} {height:.0f}'>{''.join(parts)}</svg>", stats)


def _overview_svg(by_code: dict, terms: dict, person_id: int) -> str:
    """The centerpiece: per-sample summary — abnormal count line, dots colored by worst deviation."""
    times = sorted({r.effective_at for rows in by_code.values() for r in rows})
    stats = []
    for t in times:
        cnt = tot = 0
        worst_ratio, worst_name = 0.0, ""
        for code, rows in by_code.items():
            row = next((r for r in rows if r.effective_at == t), None)
            if row is None:
                continue
            tot += 1
            if row.flag_computed in ("HIGH", "LOW", "ABNORMAL_CAT"):
                cnt += 1
                sev = _severity(row)
                if sev > worst_ratio:
                    worst_ratio, worst_name = sev, f"{terms[code].name_cn} {sev:.1f}×"
        stats.append({"t": t, "c": cnt, "n": tot, "w": worst_name, "sev": worst_ratio if cnt else 0})

    n = len(stats)
    padL, padR, padT, padB = 34, 18, 16, 34
    width = max(660.0, padL + padR + n * 78)
    ymax = max(3, max((s["c"] for s in stats), default=0) + 1)
    # adaptive height (capped); sparse y-label stepping keeps ticks >=24 units apart, no overlap
    height = min(430.0, max(280.0, padT + padB + (ymax + 1) * 18.0))
    y_step = max(1, -(-(ymax + 1) // max(1, int((height - padT - padB) // 24))))
    def X(i):
        return padL + i * ((width - padL - padR) / (n - 1)) if n > 1 else (width) / 2
    def Y(c):
        return padT + (height - padT - padB) * (1 - c / ymax)

    grid = "".join(
        f"<line class='c-grid' x1='{padL}' y1='{Y(k):.1f}' x2='{width - padR:.1f}' y2='{Y(k):.1f}'/>"
        f"<text class='c-tick' x='{padL - 6}' y='{Y(k) + 3.5:.1f}' text-anchor='end' font-size='10.5'>{k}</text>"
        for k in range(0, ymax + 1, y_step))

    labels, dots, line_pts = [], [], []
    for i, s in enumerate(stats):
        x, y = X(i), Y(s["c"])
        if s["c"]:
            line_pts.append(f"{x:.1f},{y:.1f}")
        else:
            line_pts.append(f"{x:.1f},{Y(0):.1f}")
        dot_cls = ("c-dot-s3" if s["sev"] >= 3 else "c-dot-s2" if s["sev"] >= 1.5
                   else "c-dot-s1" if s["c"] else "c-dot-ok2")
        dots.append(
            f"<g class='pt main-pt' data-t='{esc(_lt(s['t']))}' data-c='{s['c']}' data-n='{s['n']}' "
            f"data-w='{esc(s['w'])}' data-fu='{esc(s['t'])}' data-pid='{person_id}'>"
            f"<circle class='dot {dot_cls}' cx='{x:.1f}' cy='{y:.1f}' r='6'/>"
            f"<circle cx='{x:.1f}' cy='{y:.1f}' r='14' fill='transparent'/></g>")
        if n <= 14 or i % 2 == 0:
            labels.append(f"<text class='c-tick' x='{x:.1f}' y='{height - 12:.1f}' text-anchor='middle' "
                          f"font-size='10.5'>{_lt(s['t'])[:5]}</text>")

    return (f"<svg viewBox='0 0 {width:.0f} {height:.0f}'>{grid}"
            f"<polyline class='c-line-ok' points='{' '.join(line_pts)}' fill='none' stroke-width='2.5' "
            f"stroke-linejoin='round' opacity='0.85'/>{''.join(dots)}{''.join(labels)}</svg>")


def _delta_html(rows: list[Observation]) -> str:
    if len(rows) < 2:
        return "<span class='flat'>—</span>"
    a, b = rows[-2], rows[-1]
    if a.value_num is None or b.value_num is None or a.value_num == 0:
        return "<span class='flat'>—</span>"
    pct = (b.value_num - a.value_num) / a.value_num * 100
    if abs(pct) < 0.05:
        return "<span class='flat'>持平</span>"
    order = {"NORMAL": 0, "": 0, None: 0, "LOW": 1, "HIGH": 1, "ABNORMAL_CAT": 1}
    fa, fb = _flag_of(a), _flag_of(b)
    if order.get(fb, 1) > order.get(fa, 1):
        cls, label = "bad", "恶化"
    elif order.get(fb, 1) < order.get(fa, 1):
        cls, label = "good", "改善"
    else:
        cls, label = "flat", ""
    arrow = "↑" if pct > 0 else "↓"
    return f"<span class='{cls}'>{arrow} {abs(pct):.1f}%{(' · ' + label) if label else ''}</span>"


def _sev_chip(sev: float) -> str:
    level = 3 if sev >= 3 else (2 if sev >= 1.5 else 1)
    label = {3: "重度", 2: "中度", 1: "轻度"}[level]
    return f"<span class='sev s{level}'>{label}</span>"


# 临床关注度（curated）：高=变化需医生优先关注；中=需随访；未列=低（按偏离度排）
CLINICAL_WEIGHT = {
    "PCT_PROCAL": 3, "CRP": 3, "TNI": 3, "NT_PROBNP": 3, "PLT": 3, "CREA": 3, "LACT": 3,
    "WBC": 2, "NEUT_PCT": 2, "TBIL": 2, "DBIL": 2, "DDIMER": 2, "FIB": 2, "K": 2, "NA": 2,
    "SAA": 2, "ALT": 1, "AST": 1, "GGT": 1, "ALB": 2, "BUN": 1, "LYM_ABS": 1,
    "HGB": 2, "GLU": 1, "EGFR": 1, "HR": 2, "TEMP": 2, "SPO2": 3, "BP_SYS": 2, "BP_DIA": 1,
}

VITALS_ORDER = ["BP_SYS", "BP_DIA", "HR", "SPO2", "TEMP", "RR", "WEIGHT", "GLU_HOME", "SLEEP_HRS", "STEPS"]
WINDOW_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def render_dashboard(person: Person, tz_name: str, db, seed_dir: str = "./seed",
                     window: str = "auto") -> str:
    global _SPARK_TZ
    from zoneinfo import ZoneInfo as _ZI
    _SPARK_TZ = _ZI(tz_name)
    tz = _SPARK_TZ
    bg = _backgrounds(seed_dir)

    obs_all = (db.query(Observation).filter_by(person_id=person.id)
               .order_by(Observation.effective_at).all())
    has_vitals = any(o.canonical_code in VITALS_ORDER for o in obs_all)
    # ---- 相位检测：监护(住院中+数据新鲜) / 康复随访 / 档案 ----
    now_local = datetime.now(tz)
    _active_enc = db.query(Encounter).filter(
        Encounter.person_id == person.id, Encounter.discharged_at.is_(None)).first()
    _last_obs_dt = None
    if obs_all:
        try:
            _last_obs_dt = datetime.fromisoformat(max(o.effective_at for o in obs_all)).astimezone(tz)
        except ValueError:
            pass
    if _active_enc and _last_obs_dt and (now_local - _last_obs_dt).days <= 10:
        _auto_phase = "monitoring"
    elif _last_obs_dt and (now_local - _last_obs_dt).days <= 120:
        _auto_phase = "followup"
    else:
        _auto_phase = "archive"
    _manual_phase = (getattr(person, "phase_mode", None) or "").strip() or None
    _auto_or_manual = "手动" if _manual_phase else "自动"
    phase = _manual_phase or _auto_phase
    if window == "auto":
        if phase != "monitoring":
            window = "all"
        else:
            window = "30d" if has_vitals else "all"
    if window not in WINDOW_DAYS:
        window = "all"
    wf_iso = None
    if window in WINDOW_DAYS:
        wf_iso = ((datetime.now(tz) - timedelta(days=WINDOW_DAYS[window]))
                  .astimezone(tz).astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"))

    def in_window(rows):
        if wf_iso is None:
            return rows
        return [r for r in rows if r.effective_at >= wf_iso]

    def local(s: str | None) -> str:
        if not s:
            return "-"
        try:
            return datetime.fromisoformat(s).astimezone(tz).strftime("%m-%d %H:%M")
        except ValueError:
            return s[:16]

    obs = obs_all
    terms = {t.code: t for t in db.query(CanonicalTerm).all()}
    report_count = db.query(SourceReport).filter_by(person_id=person.id).count()
    events = (db.query(ClinicalEvent).filter_by(person_id=person.id)
              .order_by(ClinicalEvent.occurred_at).all())
    facts = (db.query(PersonFact).filter_by(person_id=person.id)
             .order_by(PersonFact.category, PersonFact.sort_order, PersonFact.id).all())

    by_code: dict[str, list[Observation]] = {}
    for o in obs:
        if o.canonical_code:
            by_code.setdefault(o.canonical_code, []).append(o)

    last_any = max((o.effective_at for o in obs), default=None)
    days = sorted({o.effective_at[:10] for o in obs}, reverse=True)
    day_points = {d: sum(1 for o in obs if o.effective_at[:10] == d) for d in days}
    unresolved = sum(1 for o in obs if o.canonical_code is None)

    win_chips = "".join(
        f"<a class='wchip{' on' if window == w else ''}' href='/dashboard/{person.id}?window={w}'>{label}</a>"
        for w, label in (("7d", "7天"), ("30d", "30天"), ("90d", "90天"), ("all", "全部")))

    head = (
        f"<div class='top'><div><h1>{esc(person.name)}</h1>"
        f"<div class='who'>{esc(person.sex or '?')} · {_age_years(person)} · "
        f"{len(obs)} 条指标 / {report_count} 份报告"
        + (f" · <span style='color:var(--danger)'>{unresolved} 条待映射</span>" if unresolved else "")
        + "</div></div>"
        f"<div class='asof'>数据截至<br><b>{local(last_any)}</b><br>共 {len(days)} 天</div></div>"
        + "<nav class='stickynav'><div class='anchors'>"
        + f"<a href='#sec-cond'>诊断</a><a href='#sec-events'>病程</a><a href='#sec-labs'>检验</a><a href='#sec-meds'>用药</a>"
          f"<a href='#sec-disc'>讨论</a><a href='#sec-vitals-trend'>体征</a><a href='#sec-enc'>就诊</a><a href='#sec-ai'>AI 分析</a>"
        + "</div><div class='sp'></div>" + win_chips + "</nav>"
        f"<nav class='days'>"
        + "".join(f"<span>{d} · {n}条</span>" for d, n in day_points.items()) + "</nav>")

    # ---- vitals & long-term sections (built first; rendered above lab blocks) ----
    details: dict[str, str] = {}

    # 溯源：report_id → 来源标签 + 原件链接
    PROV_LABEL = {"upload_ocr": "📷拍照", "api": "推送", "entry": "录入", "csv_import": "导入"}
    _src_reports = db.query(SourceReport).filter_by(person_id=person.id).all()
    _first_file: dict[int, int] = {}
    for _sf in db.query(SourceFile).order_by(SourceFile.id).all():
        _first_file.setdefault(_sf.source_report_id, _sf.id)
    report_meta = {}
    for _sr in _src_reports:
        fid = _first_file.get(_sr.id)
        report_meta[_sr.id] = {
            "label": f"{(_sr.panel or _sr.external_id)[:12]}·{PROV_LABEL.get(_sr.provenance or 'api', _sr.provenance)}",
            "href": f"/files/{fid}" if fid else None,
        }

    def detail_for(code: str, rows: list) -> str:
        t = terms[code]
        tdict = {"code": t.code, "name_cn": t.name_cn, "category": t.category,
                 "default_unit": t.default_unit}
        shown = rows[-200:]
        body = _detail_html(shown, tdict, bg, report_meta)
        if len(shown) < len(rows):
            body += f"<div class='sub'>共 {len(rows)} 条记录，弹窗仅展示最近 200 条</div>"
        return body

    ref_rows = db.query(ReferenceRange).filter(ReferenceRange.canonical_code.in_(VITALS_ORDER)).all()
    ref_map: dict[str, tuple] = {}
    for r in ref_rows:
        if r.sex is None:
            ref_map.setdefault(r.canonical_code, (r.low, r.high))
        elif r.sex == person.sex:
            ref_map[r.canonical_code] = (r.low, r.high)

    def band_for(code: str, rows: list):
        last = rows[-1]
        if last.source_ref_low is not None or last.source_ref_high is not None:
            return last.source_ref_low, last.source_ref_high
        return ref_map.get(code, (None, None))

    # monitor strip: latest reading per key vital within window
    monitor_cards = []
    bp_s = in_window(by_code.get("BP_SYS", []))
    bp_d = in_window(by_code.get("BP_DIA", []))
    if bp_s or bp_d:
        s = bp_s[-1] if bp_s else None
        d = bp_d[-1] if bp_d else None
        flags = [_flag_of(x) for x in (s, d) if x]
        fl = "HIGH" if "HIGH" in flags else ("LOW" if "LOW" in flags else ("NORMAL" if flags else "NONE"))
        val = f"{(_fmt(s) if s else '—')}/{(_fmt(d) if d else '—')}"
        when = local(max((x.effective_at for x in (s, d) if x), default=None))
        monitor_cards.append(
            f"<div class='mon'><div class='k'>血压</div><div class='v {esc(fl)}'>{esc(val)}"
            f"<small>mmHg</small></div><div class='w'>{esc(when)}</div></div>")
    for code in ["HR", "SPO2", "TEMP", "RR", "WEIGHT"]:
        rows = in_window(by_code.get(code, []))
        if not rows:
            continue
        last = rows[-1]
        fl = _flag_of(last) or "NONE"
        monitor_cards.append(
            f"<div class='mon'><div class='k'>{esc(terms[code].name_cn)}</div>"
            f"<div class='v {esc(_flag_of(last))}'>{esc(_fmt(last))}"
            f"<small>{esc(last.unit_canonical or '')}</small></div>"
            f"<div class='w'>{local(last.effective_at)}</div></div>")
    monitor_html = (f"<div class='monrow'>{''.join(monitor_cards)}</div>" if monitor_cards
                    else "<div class='empty'>暂无生命体征记录 · <a href='/entry'>去录入</a> 或由上游服务推送</div>")

    # long-term trend charts per vital (window-filtered)
    vital_cards = []
    for code in VITALS_ORDER:
        rows_all_c = by_code.get(code, [])
        if not rows_all_c:
            continue
        wrows = in_window(rows_all_c) or rows_all_c[-1:]  # never empty for chart context
        lo, hi = band_for(code, wrows)
        fl = _flag_of(wrows[-1])
        svg, stats = _line_svg(wrows, lo, hi, terms[code].name_cn,
                               wrows[-1].unit_canonical or "", fl, events=events)
        stats_html = ""
        if stats:
            stats_html = (f"均值 {stats['mean']:.6g} · 最低 {stats['min'][0]:g}（{stats['min'][1]}） · "
                          f"最高 {stats['max'][0]:g}（{stats['max'][1]}） · {stats['n']} 次")
        vital_cards.append(
            f"<div class='vital-card'><div class='row1'><span class='n'>{esc(terms[code].name_cn)}"
            f"<span class='badge'>{esc(code)}</span></span>"
            f"<span class='v {esc(fl)}'>{esc(_fmt(wrows[-1]))} "
            f"<small style='font-weight:400;color:var(--muted)'>{esc(wrows[-1].unit_canonical or '')}</small></span></div>"
            f"<div class='oscroll'>{svg}</div>"
            f"<div class='meta'>{stats_html}</div>"
            f"<div class='idetail'>{detail_for(code, rows_all_c)}</div></div>")
    vitals_html = (f"<div class='vital-grid'>{''.join(vital_cards)}</div>" if vital_cards else
                   "<div class='empty'>暂无可绘制的生命体征/健康指标数据 · <a href='/entry'>先录一组</a></div>")

    # medications
    meds = (db.query(Medication).filter(Medication.person_id == person.id)
            .order_by(Medication.ended_at.isnot(None), Medication.started_at.desc(),
                      Medication.id.desc()).all())
    if meds:
        day_gran = sum(1 for m in meds if (m.time_precision or "day") == "day")

        def _med_when(s, m):
            if not s:
                return "-"
            mark = "" if (m.time_precision or "day") == "datetime" else "<span class='dprec' title='日粒度：来自费用清单，具体时刻待病历核实'>日</span>"
            return f"{esc(s[:10])}{mark}"

        def _med_status(m):
            if m.ended_at:
                return ("<span style='color:var(--muted)'>已停</span>"
                        + ("<span class='dprec' title='据费用清单推断'>费</span>"
                           if m.status_inference == "billed_ended" else ""))
            if m.status_inference == "order":
                return "<b style=color:var(--ok)>在用</b><span class='dprec' title='病历医嘱确认'>嘱</span>"
            if m.status_inference == "billed_ongoing":
                return "<b style=color:var(--ok)>在用</b><span class='dprec' title='费用清单最后一天仍计费推断，医嘱状态待病历确认'>费?</span>"
            return "<b style=color:var(--ok)>在用</b>"

        med_rows = "".join(
            f"<tr{' style=opacity:.45' if m.ended_at else ''}><td>{esc(m.drug_name)}</td>"
            f"<td>{esc((m.dose or '') + ((' ' + m.unit) if m.unit else '') or '-')}</td>"
            f"<td>{esc(m.frequency or '-')}</td>"
            f"<td class='when'>{_med_when(m.started_at, m)}</td>"
            f"<td class='when'>{_med_status(m)}</td></tr>"
            for m in meds)
        meds_html = (
            f"<div class='sub' style='margin-bottom:6px'>📅 <b>{day_gran}</b> 条用药记录的时间为"
            f"<b>日粒度</b>（来自费用清单：日期可靠、时刻未知，标「日」）—— 拿到整理病历的医嘱单后"
            f"升级为精确时刻，对后续就诊医生更有参考价值 · 「费」=据费用清单推断、「费?」=计费仍在但医嘱状态待确认、「嘱」=病历医嘱确认</div>"
            "<div class='tscroll'><table><tr><th>药品</th><th>剂量</th><th>频次</th>"
            f"<th class='when'>开始</th><th class='when'>状态</th></tr>{med_rows}</table></div>")
    else:
        meds_html = "<div class='empty'>暂无用药记录 · API <code>POST /medications</code> 添加</div>"

    # symptoms (recent, window not applied — always show recent 10)
    sym = (db.query(SymptomNote).filter(SymptomNote.person_id == person.id)
           .order_by(SymptomNote.occurred_at.desc()).limit(10).all())
    if sym:
        sym_html = "".join(
            f"<div class='symp'><span class='when'>{local(s.occurred_at)}</span>"
            + (f"<span class='sev s{s.severity}'>{'●' * (s.severity or 1)}</span>" if s.severity else "")
            + f"<span>{esc(s.text)}</span></div>" for s in sym)
    else:
        sym_html = "<div class='empty'>暂无症状记录 · <a href='/entry'>在录入页记录症状</a></div>"

    # encounters
    encs = (db.query(Encounter).filter(Encounter.person_id == person.id)
            .order_by(Encounter.admitted_at.desc(), Encounter.id.desc()).all())
    if encs:
        enc_cards = []
        for e in encs:
            tel = (f" · <a href='tel:{esc(e.doctor_phone)}' style='color:var(--accent)'>"
                   f"{esc(e.doctor_phone)}</a>") if e.doctor_phone else ""
            enc_cards.append(
                f"<div class='culture'><div class='head'>"
                f"<b>{esc(e.kind)}</b> · {esc(e.hospital or '')} {esc(e.department or '')} · "
                f"{esc((e.admitted_at or '')[:10])} ~ {esc((e.discharged_at or '在院')[:10])}<br>"
                + (f"主治医生：<b>{esc(e.doctor_name)}</b>{tel}<br>" if e.doctor_name else "")
                + (f"诊断：{esc(e.diagnosis)}" if e.diagnosis else "")
                + (f"<br>{esc(e.note)}" if e.note else "")
                + "</div></div>")
        encs_html = "".join(enc_cards)
    else:
        encs_html = "<div class='empty'>暂无就诊/住院档案 · API <code>POST /encounters</code> 添加（含主治医生姓名/电话）</div>"

    # ---- 诊断档案（Condition 一等公民）----
    conds = (db.query(Condition).filter_by(person_id=person.id)
             .order_by(Condition.status.desc(), Condition.onset_at.desc()).all())
    COND_STYLE = {
        "active": ("#dc2626", "活动中"),
        "improving": ("#d97706", "好转中"),
        "resolved": ("#16a34a", "已缓解"),
        "chronic": ("#2563eb", "慢病/长期"),
        "suspected": ("#64748b", "疑似"),
    }
    cond_cards = []
    for c_ in conds:
        color, lab = COND_STYLE.get(c_.status, ("#64748b", c_.status))
        day_n = ""
        if c_.onset_at:
            try:
                d0 = datetime.fromisoformat((c_.onset_at or "")[:10] + "T00:00:00+08:00")
                n = (datetime.now(tz) - d0).days + 1
                if n > 0:
                    day_n = f" · 第 <b>{n}</b> 天"
            except ValueError:
                pass
        cond_cards.append(
            f"<div class='cond'><div class='condh'>"
            f"<span class='condst' style='border-color:{color};color:{color}'>{lab}</span>"
            f"<b>{esc(c_.name)}</b>"
            + (f"<span class='condcat'>{esc(c_.category)}</span>" if c_.category else "")
            + "</div>"
            f"<div class='condm'>起病 {esc((c_.onset_at or '?')[:10])}{day_n}"
            + (f" → 缓解 {esc((c_.resolved_at or '?')[:10])}" if c_.resolved_at else "")
            + (f"<br>{esc(c_.note)}" if c_.note else "")
            + (f"<span class='tls'> · 来源：{esc(c_.source)}</span>" if c_.source else "")
            + "</div></div>")
    conditions_html = (
        f"<section id='sec-cond'><h2>诊断档案<small>病是有生命周期的：活动 → 好转 → 缓解，慢病长期相伴"
        f"（API POST /conditions，状态变更 PATCH）</small></h2>"
        + (f"<div class='condgrid'>{''.join(cond_cards)}</div>" if cond_cards
           else "<div class='empty'>暂无诊断记录 · API <code>POST /conditions</code> 添加</div>")
        + "</section>") if conds else ""

    # ---- 待处理上传 + 来源账本 ----
    pend_rows = (db.query(PendingUpload).filter_by(person_id=person.id)
                 .order_by(PendingUpload.id.desc()).all())
    pend_open = [u for u in pend_rows if u.status in ("pending", "processing")]
    pending_html = ""
    if pend_open:
        pending_html = (
            f"<div class='pendbox'>📥 有 <b>{len(pend_open)}</b> 份上传待入库"
            f"（{sum(1 for u in pend_open if u.status == 'pending')} 份排队中 · "
            f"{sum(1 for u in pend_open if u.status == 'processing')} 份处理中）"
            f" · <a href='/upload?person_id={person.id}'>继续上传</a></div>")

    _n_files_total = db.query(SourceFile).count()
    _n_pend = sum(1 for u in pend_rows if u.status in ("pending", "processing"))
    ledger_html = (
        f"<a class='srccard' href='/sources?person_id={person.id}'>"
        f"<b>🗂 数据管理台</b>"
        f"<span>{len(_src_reports)} 份报告 · {_n_files_total} 张原件 · {_n_pend} 待处理 · 修正留痕</span>"
        f"<span class='go'>›</span></a>"
        f"<div class='exportrow'><a href='/export/csv/{person.id}'>⬇ CSV（Excel）</a>"
        f"<a href='/export/fhir/{person.id}'>⬇ FHIR（机器可读）</a>"
        f"<a href='/print/{person.id}' target='_blank'>🖨 一页纸</a></div>")

    # ---- 相位横幅 + 复查里程碑 ----
    _enc_days = ""
    if _active_enc and _active_enc.admitted_at:
        try:
            _d0 = datetime.fromisoformat(_active_enc.admitted_at[:10] + "T00:00:00+08:00")
            _enc_days = f" · 住院第 <b>{(now_local - _d0).days + 1}</b> 天"
        except ValueError:
            pass
    fut_events = []
    for _e in events:
        try:
            _et = datetime.fromisoformat(_e.occurred_at).astimezone(tz)
        except ValueError:
            continue
        if _et >= now_local:
            _e._eta = (_et - now_local).days
            fut_events.append(_e)
    fut_events.sort(key=lambda e: e.occurred_at)
    _PH_META = {"monitoring": ("🏥 监护", "mon-ph"), "followup": ("🏡 随访", "fol-ph"),
                "archive": ("📚 档案", "arc-ph")}
    _badge, _ph_cls = _PH_META.get(phase, ("📚 档案", "arc-ph"))
    _switch = ""
    for _m, _lab in (("auto", "自动"), ("monitoring", "监护"), ("followup", "随访"), ("archive", "档案")):
        _on = " on" if ((_m == "auto" and not _manual_phase) or _manual_phase == _m) else ""
        _switch += (f"<button class='phbtn{_on}' "
                    f"onclick=\"phSet('{_m}')\">{_lab}</button>")
    _sub = {"monitoring": f"{_enc_days} · 数据截至 {local(last_any)} · 共 {len(days)} 天" if phase == "monitoring" else "",
            "followup": (f"下次：<b>{esc(fut_events[0].title)}</b>（{fut_events[0]._eta} 天后）" if fut_events else "康复期"),
            "archive": "全时间轴视图"}[phase]
    _banner = (f"<div class='phasebar {_ph_cls}'><b>{_badge}</b><span class='phsub'>{_sub}</span></div>"
               f"<div class='phrow'><a href='/print/{person.id}' target='_blank' class='plink'>🖨 病情摘要（给医生）</a><a href='/speak?person_id={person.id}' class='plink' style='font-size:15px'>🗣 表达板（眨眼交流）</a><span class='phsrc'>相位 · {_auto_or_manual}判定</span>"
               f"<span class='phsw'>{_switch}</span></div>"
               f"<script>function phSet(m){{fetch('/persons/{person.id}/phase',{{method:'PATCH',"
               f"headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{mode:m}})}})"
               f".then(function(){{location.reload();}});}}</script>")

    enc_rail = ""
    if phase != "monitoring":
        _encs = (db.query(Encounter).filter_by(person_id=person.id)
                 .order_by(Encounter.admitted_at.desc()).all())
        if _encs:
            _cards = "".join(
                f"<div class='htlcard' style='--kc:var(--accent)'>"
                f"<div class='hc-head'><span class='hc-kind'>{esc(e.kind or '就诊')}</span>"
                f"<span class='hc-date'>{esc((e.admitted_at or '?')[:10])}"
                + (f"~{esc((e.discharged_at or '在院')[:10])}" if e.discharged_at or e.admitted_at else "")
                + "</span></div>"
                f"<div class='hc-title'>{esc(e.hospital or '')}</div>"
                + (f"<div class='hc-detail'>{esc(e.department or '')}"
                   + (f" · {esc(e.doctor_name)}" if e.doctor_name else "") + "</div>" if e.department else "")
                + (f"<div class='hc-detail'>{esc(e.diagnosis or '')}</div>" if e.diagnosis else "")
                + "</div>"
                for e in _encs)
            enc_rail = (f"<section id='sec-enc-rail'><h2>就诊履历<small>跨院档案 —— 每段就诊一张卡</small></h2>"
                        f"<div class='htlcards'>{_cards}</div></section>")

    milestone_html = ""
    if phase != "monitoring":
        past_fu = [e for e in reversed(events)
                   if e.kind == "followup" and e not in fut_events][-4:]
        rows = []
        for e in fut_events:
            color, lab = EVENT_STYLES.get(e.kind, EVENT_STYLES["other"])
            rows.append(f"<div class='ms up'><span class='msd' style='color:{color}'>{esc(e.occurred_at[:10])}</span>"
                        f"<b>{esc(e.title)}</b><span class='eta'>{e._eta} 天后</span>"
                        + (f"<div class='tld'>{esc(e.detail)}</div>" if e.detail else "") + "</div>")
        for e in past_fu:
            rows.append(f"<div class='ms done'><span class='msd'>{esc(e.occurred_at[:10])}</span>"
                        f"<span>{esc(e.title)}</span><span class='eta ok'>已完成</span></div>")
        if rows:
            milestone_html = (f"<section id='sec-ms'><h2>复查与随访计划<small>到点提醒 · API "
                              f"POST /events kind=followup 添加</small></h2><div class='mslist'>{''.join(rows)}</div></section>")

    # ---- 病情讨论区 ----
    posts = (db.query(DiscussionPost).filter_by(person_id=person.id)
             .order_by(DiscussionPost.created_at.desc(), DiscussionPost.id.desc())
             .limit(100).all())
    DISC_COLORS = {"观察": "var(--accent)", "问题": "#d97706", "医生反馈": "#0d9488",
                   "决定": "#dc2626", "其他": "var(--muted)"}
    post_html = "".join(
        f"<div class='disc'><span class='dcat' style='border-color:{DISC_COLORS.get(p.category, 'var(--muted)')};"
        f"color:{DISC_COLORS.get(p.category, 'var(--muted)')}'>{esc(p.category)}</span>"
        f"<span class='dauth'>{esc(p.author)}</span><span class='dtime'>{local(p.created_at)}</span>"
        f"<div class='dcontent'>{esc(p.content)}</div></div>"
        for p in posts) or "<div class='empty'>还没有讨论 · 下面第一条留给你们</div>"
    discussion_html = (
        f"<section id='sec-disc'><h2>病情讨论区<small>家属观察 · 问题 · 医生原话 · 决定 —— 手机可直接发</small></h2>"
        + "<div class='discform'><input id='disc-author' placeholder='署名（如：儿子/姐姐）' maxlength='16'>"
        + "<select id='disc-cat'><option>观察</option><option>问题</option><option>医生反馈</option>"
          "<option>决定</option><option>其他</option></select>"
        + "<textarea id='disc-content' placeholder='今天观察到的 / 想问医生的 / 医生说的原话 / 家庭决定…' maxlength='4000'></textarea>"
        + f"<button id='disc-send' data-pid='{person.id}'>发布</button></div>"
        + f"<div class='disclist'>{post_html}</div>"
        + "<script>"
        + "(function(){var b=document.getElementById('disc-send');"
        + "var a=document.getElementById('disc-author');"
        + "try{a.value=localStorage.getItem('disc-author')||'';}catch(e){}"
        + "b.addEventListener('click',function(){"
        + "var c=document.getElementById('disc-content');"
        + "if(!c.value.trim()){return;}"
        + "b.disabled=true;b.textContent='发布中…';"
        + "fetch('/discussion',{method:'POST',headers:{'Content-Type':'application/json'},"
        + "body:JSON.stringify({person_id:b.dataset.pid*1,author:a.value.trim()||'家属',"
        + "category:document.getElementById('disc-cat').value,content:c.value.trim()})})"
        + ".then(function(r){if(!r.ok){throw new Error('HTTP '+r.status);}return r.json();})"
        + ".then(function(){try{localStorage.setItem('disc-author',a.value.trim());}catch(e){}"
        + "location.reload();})"
        + ".catch(function(e){b.disabled=false;b.textContent='发布（重试：'+e.message+'）';});"
        + "});})();"
        + "</script></section>")

    # ---- 病程时间线（事件，倒序）+ 背景档案 ----
    if events:
        # 横向病程轴（borrowed from decision.ai war-room timeline）
        nodes = []
        for i, e in enumerate(events):
            color, lab = EVENT_STYLES.get(e.kind, EVENT_STYLES["other"])
            glyph = KIND_GLYPH.get(e.kind, "事")
            piv = bool(getattr(e, "pivotal", 0))
            approx = " ≈" if e.time_precision in ("approx", "day") else ""
            detail_html = (f"<p class='tld'>{esc(e.detail)}</p>" if e.detail else "") +                 (f"<p class='tls'>来源：{esc(e.source)}</p>" if e.source else "")
            nodes.append(
                f"<button class='htl-node{' piv' if piv else ''}' data-idx='{i}' "
                f"style='--kc:{color}' onclick=\"htlDetail({i})\">"
                f"<span class='htl-date'>{_lt(e.occurred_at)}{approx}</span>"
                f"<span class='htl-title'>{esc(e.title)}</span>"
                f"<span class='htl-dot'><i>{glyph}</i></span>"
                f"<span class='htl-kind'>{lab}</span></button>")
            nodes.append(
                f"<div id='htl-det-{i}' style='display:none'>"
                f"<b style='color:{color}'>[{lab}]</b> {esc(e.title)}"
                f"<span class='mwhen'> · {local(e.occurred_at)}{approx}</span>{detail_html}</div>")
        timeline_html = (
            f"<section id='sec-events'><h2>病程时间线<small>{len(events)} 个事件 · "
            f"关键转折同步钉在趋势图上 · 点节点看详情</small></h2>"
            f"<div class='htl-bar'><div class='phsw htl-zoom' style='margin-left:0'>"
            f"<button class='phbtn' onclick=\"htlZoom(this,'piv')\">关键</button>"
            f"<button class='phbtn on' onclick=\"htlZoom(this,'all')\">全部</button></div>"
            f"<div class='htl-nav'><button onclick=\"htlScroll(-360)\">‹</button>"
            f"<button onclick=\"htlScroll(360)\">›</button></div></div>"
            f"<div class='htl' id='htl'><div class='htl-axis'></div>{''.join(nodes)}</div>"
            f"<script>function htlZoom(b,m){{var h=document.getElementById('htl');"
            f"h.classList.toggle('only-piv',m==='piv');"
            f"b.parentNode.querySelectorAll('.phbtn').forEach(function(x){{x.classList.remove('on');}});"
            f"b.classList.add('on');}}"
            f"function htlScroll(d){{document.getElementById('htl').scrollBy({{left:d,behavior:'smooth'}});}}"
            f"function htlDetail(i){{openModal(document.getElementById('htl-det-'+i).innerHTML);}}"
f"</script>")
    else:
        timeline_html = ""

    if facts:
        by_cat: dict[str, list] = {}
        for f_ in facts:
            by_cat.setdefault(f_.category, []).append(f_)
        cat_blocks = []
        for cat, items in by_cat.items():
            lis = "".join(
                f"<div class='bgp'><b>{esc(x.title)}</b>"
                + (f"<div class='tld'>{esc(x.detail)}</div>" if x.detail else "") + "</div>"
                for x in items)
            cat_blocks.append(
                f"<div class='bgcat'><div class='bgch'>{esc(FACT_CATEGORY_NAMES.get(cat, cat))}"
                f"<em>{len(items)}</em></div>{lis}</div>")
        facts_html = (f"<section id='sec-bg'><h2>背景档案<small>解读数据的关键上下文 · "
                      f"既往史/家族史/基础状态</small></h2>"
                      f"<div class='bggrid'>{''.join(cat_blocks)}</div></section>")
    else:
        facts_html = ""

    # 体征快照：有数据才显示，紧跟页面头部（不算大区块）
    vitals_snapshot_html = (
        f"<section id='sec-vitals' class='vsnap'><h2 style='display:none'></h2>{monitor_html}</section>"
        if monitor_cards else "")

    MAJOR_EVENT_KINDS = {"condition_change", "intervention", "culture_result"}
    major_events = [e for e in events if e.kind in MAJOR_EVENT_KINDS]
    pivotal_events = [e for e in events if getattr(e, "pivotal", 0)]

    # 体征趋势：稀疏（<4 项有数据）自动折叠，等监护仪数据接入后展开
    _n_vitals = sum(1 for _c in VITALS_ORDER if in_window(by_code.get(_c, [])))
    _open = " open" if _n_vitals >= 4 else ""
    vitals_trend_html = (
        f"<details class='ovbox' id='sec-vitals-trend'{_open}><summary><span class='arrow'>▶</span>"
        f"<span class='ovtitle'>生命体征趋势</span>"
        f"<span class='ovsub'>{_n_vitals} 项 · 绿色带=参考范围"
        + (" · 数据稀疏，待监护仪接入后常驻" if _n_vitals < 4 else "") + "</span></summary>"
        f"<div class='ovbody' style='padding:0'>{vitals_html}</div></details>")

    long_term_html = (
        f"<section id='sec-meds'><h2>用药记录<small>在用在前，停用淡化 · 「日」=费用清单日粒度</small></h2>{meds_html}</section>"
        f"{vitals_trend_html}"
        f"<section><h2>症状记录<small>最近 10 条</small></h2>{sym_html}</section>"
        f"<section id='sec-enc'><h2>就诊 · 住院档案<small>含主治医生联系方式</small></h2>{encs_html}</section>")

    # ---- overview centerpiece (collapsed by default) ----
    sample_count = len({o.effective_at for o in obs})
    overview = (
        "<details class='ovbox'>"
        "<summary><span class='arrow'>▶</span><span class='ovtitle'>病情总览曲线</span>"
        f"<span class='ovsub'>{sample_count} 次采样 · 每次异常指标数 · 点击展开</span>"
        "<span class='ovhint'>悬停/点击圆点显示采样时间，再点一次看该次全部指标</span></summary>"
        "<div class='ovbody'>"
        f"<div class='overview' style='border:none;padding:0;'><div class='oscroll'>{_overview_svg(by_code, terms, person.id)}</div><div class='legend'>"
        "<span><i class='l-s3'></i>存在 ≥3× 重度偏离</span>"
        "<span><i class='l-s2'></i>≥1.5×</span>"
        "<span><i class='l-s1'></i>其他异常</span>"
        "<span><i class='l-ok'></i>全部正常</span>"
        "<span style='margin-left:auto'>Y 轴 = 异常指标数</span></div></div></div></details>")

    # ---- hero groups ----
    hero_html = []
    for group_name, codes in HERO_LAYOUT:
        cards = []
        for code in codes:
            rows = by_code.get(code)
            if not rows:
                continue
            term = terms[code]
            last = rows[-1]
            fl = _flag_of(last) or "NONE"
            unit = last.unit_canonical or ""
            series = json.dumps([[_lt(r.effective_at), _fmt(r), _flag_of(r)]
                                 for r in rows if r.value_num is not None], ensure_ascii=False)
            cards.append(
                f"<div class='hero'><div class='name'><span>{esc(term.name_cn)}</span>"
                f"<span class='tag {esc(fl)}'>{esc({'NONE': '无参考', 'PROTECT': '保护性↑'}.get(fl, fl))}</span></div>"
                f"<div class='val {esc(_flag_of(last))}'>{esc(_fmt(last))}<small>{esc(unit)}</small></div>"
                f"<div class='delta'>前值 {esc(_fmt(rows[-2]) if len(rows) > 1 else '—')} → {_delta_html(rows)}</div>"
                f"<div class='sparkwrap' data-series='{esc(series)}'>{_spark_svg(rows, _flag_of(last), term.name_cn, unit, pivotal_events)}</div>"
                f"<div class='when'>采样 {local(last.effective_at)}</div>"
                f"<div class='idetail'>{detail_for(code, rows)}</div></div>")
        if cards:
            hero_html.append(f"<div class='group-title'>{group_name}<em>{len(cards)} 项</em></div>"
                             f"<div class='hero-grid'>{''.join(cards)}</div>")

    # ---- abnormal table ----
    abnormal = []
    for code, rows in by_code.items():
        last = rows[-1]
        if last.flag_computed in ("HIGH", "LOW", "ABNORMAL_CAT"):
            abnormal.append((code, rows, _severity(last)))
    # 排序：临床关注度 × 偏离度（不再纯按倍数）
    abnormal.sort(key=lambda x: (CLINICAL_WEIGHT.get(x[0], 1), x[2]), reverse=True)
    _W_LABEL = {3: "高", 2: "中", 1: "—"}

    def _weight_chip(code):
        w = CLINICAL_WEIGHT.get(code, 1)
        cls = "w3" if w == 3 else ("w2" if w == 2 else "w1")
        return f"<span class='wchip {cls}'>{_W_LABEL[w]}</span>"

    ab_rows = "".join(
        f"<tr><td>{_weight_chip(code)}</td><td>{_sev_chip(sev)}</td>"
        f"<td>{esc(terms[code].name_cn)}<span class='badge'>{esc(code)}</span></td>"
        f"<td class='when'>{local(rows[-1].effective_at)}</td>"
        f"<td class='{esc(_flag_of(rows[-1]))}'>{esc(_fmt(rows[-1]))} {esc(rows[-1].unit_canonical or '')}</td>"
        f"<td class='when'>{esc(rows[-1].raw_ref_range or '-')}</td>"
        f"<td class='{esc(_flag_of(rows[-1]))}'>{esc(_flag_of(rows[-1]))}</td>"
        f"<td>{esc(rows[-1].flag_source or '-')}</td>"
        f"<td class='when'>{esc(_fmt(rows[-2]) if len(rows) > 1 else '-')}@{local(rows[-2].effective_at) if len(rows) > 1 else '-'}</td>"
        f"<td>{_delta_html(rows)}</td></tr>"
        for code, rows, sev in abnormal)
    abnormal_html = (f"<div class='empty'>当前无计算异常项。</div>" if not abnormal else
                     "<div class='tscroll'><table><tr><th>关注</th><th>程度</th><th>指标</th><th class='when'>采样时间</th><th>最新值</th>"
                     "<th class='when'>参考范围</th><th>计算</th><th>原始</th><th class='when'>前值@时间</th><th>变化</th></tr>"
                     + ab_rows + "</table></div>")

    # ---- trends ----
    cat_names = {"BIOCHEM": "生化 · 肝肾功能", "CBC": "血常规", "CARDIAC": "心脏标志物",
                 "COAG": "凝血与纤溶", "URINE": "尿常规", "IMMUNO": "免疫筛查"}
    trend_sections = []
    for cat in ("BIOCHEM", "CBC", "CARDIAC", "COAG", "URINE", "IMMUNO"):
        cat_codes = [c for c, t in terms.items() if t.category == cat and in_window(by_code.get(c, []))]
        codes = [c for c in cat_codes if len(in_window(by_code.get(c, []))) >= 2]
        singles = [c for c in cat_codes if len(in_window(by_code.get(c, []))) == 1]
        if not codes and not singles:
            continue
        cards = []
        for code in codes:
            rows = in_window(by_code[code])
            term = terms[code]
            last = rows[-1]
            fl = _flag_of(last)
            unit = last.unit_canonical or ""
            series = json.dumps([[_lt(r.effective_at), _fmt(r), _flag_of(r)]
                                 for r in rows if r.value_num is not None], ensure_ascii=False)
            cards.append(
                f"<div class='trend-card'><div class='row1'><span class='n'>{esc(term.name_cn)}"
                f"<span class='badge'>{esc(code)}</span></span>"
                f"<span class='v {esc(fl)}'>{esc(_fmt(last))} <small style='font-weight:400;color:var(--muted)'>{esc(unit)}</small></span></div>"
                f"<div class='sparkwrap' data-series='{esc(series)}'>{_spark_svg(rows, fl, term.name_cn, unit, pivotal_events)}</div>"
                f"<div class='meta'>{local(rows[0].effective_at)} → {local(rows[-1].effective_at)} · {len(rows)} 次采样</div>"
                f"<div class='idetail'>{detail_for(code, rows)}</div></div>")
        # 单次检查：紧凑条 —— 一次性 panel（免疫筛查等）不再隐形
        singles_html = ""
        if singles:
            chips = []
            for code in singles:
                last = in_window(by_code[code])[-1]
                fl = _flag_of(last) or "NONE"
                fl_lab = {"PROTECT": "保护性↑", "NORMAL": "", "NONE": ""}.get(fl, fl)
                chips.append(
                    f"<span class='once {esc(fl)}'><b>{esc(terms[code].name_cn)}</b> "
                    f"{esc(_fmt(last))} {esc(last.unit_canonical or '')}"
                    + (f" <i>{esc(fl_lab)}</i>" if fl_lab else "")
                    + f"<em>{local(last.effective_at)}</em></span>")
            singles_html = f"<div class='oncegrid'>{''.join(chips)}</div>"
        sub = (f"{len(codes)} 项趋势" if codes else "") + \
              (((" · " if codes else "") + f"{len(singles)} 项单次") if singles else "")
        trend_sections.append(f"<section><h2>{cat_names.get(cat, cat)}<small>{sub}</small></h2>"
                              + (f"<div class='trend-grid'>{''.join(cards)}</div>" if cards else "")
                              + singles_html + "</section>")

    # ---- cultures ----
    culture_html = ""
    cultures = db.query(CultureReport).filter_by(person_id=person.id).all()
    if cultures:
        blocks = []
        for c in cultures:
            susc = db.query(CultureSusceptibility).filter_by(culture_report_id=c.id).all()
            drugs = "".join(f"<span class='drug {esc(s.result)}'>{esc(s.drug_name)} {esc(s.result)}"
                            + (f"·{esc(s.mic_text.replace('MIC', '').strip())}" if s.mic_text else "")
                            + "</span>" for s in susc)
            blocks.append(
                f"<div class='culture'><div class='head'><b>{esc(c.specimen)}</b> ·"
                f" 采样 {local(c.sampled_at)} · 报告 {local(c.reported_at)}<br>"
                f"<b style='font-size:14px'>{esc(c.organism or '未检出')}</b> {esc(c.organism_comment or '')} "
                + (f"<span class='mdr'>{esc(c.mdr_text)}</span>" if c.mdr_text else "")
                + f"</div><div style='margin-top:6px'>{drugs}</div></div>")
        _organisms = {c.organism for c in cultures if c.organism and c.organism != "未检出"}
        _evo_note = ""
        if len(cultures) > 1:
            _evo_note = ("<div class='sub' style='margin-bottom:8px'>⚠ 多源病原学证据并存（"
                         + " / ".join(sorted(_organisms))
                         + "）。系统不做裁决 —— 以<b>最终鉴定+药敏</b>为准；各来源的采样部位、方法、时间如下，点击原件可回溯。</div>")
        culture_html = f"<section><h2>病原学证据（培养/药敏/mNGS）<small>{len(cultures)} 份 · 证据并存时不裁决</small></h2>{_evo_note}{''.join(blocks)}</section>"

    # ---- AI insight ----
    insight_html = ""
    latest = (db.query(InsightReport).filter_by(person_id=person.id, status="ok")
              .order_by(InsightReport.id.desc()).first())
    if latest:
        body = markdown.markdown(latest.response_md, extensions=["tables"])
        total_ins = db.query(InsightReport).filter_by(person_id=person.id).count()
        insight_html = (
            f"<section id='sec-ai'><h2>AI 参考分析<small>数据仅供参考，不构成医疗建议，请遵主管医生意见</small></h2>"
            f"<div class='insight'><div class='meta'>{esc(latest.model)} · 生成于 {local(latest.created_at)}"
            f" · 窗口 {esc(latest.window_from[:10] if latest.window_from else '')}~{esc(latest.window_to[:10] if latest.window_to else '')}"
            f" · 历史共 {total_ins} 条</div>"
            f"<div class='insight-md'>{body}</div></div></section>")

    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta name='color-scheme' content='light dark'>"
            f"<title>{esc(person.name)} · 健康面板</title>"
            f"<link rel='manifest' href='/manifest.webmanifest'>"
            f"<meta name='theme-color' content='#0f6aad'>"
            f"<link rel='apple-touch-icon' href='/icon-180.png'>"
            f"<meta name='apple-mobile-web-app-capable' content='yes'>"
            f"<style>{CSS}</style></head><body><div class='wrap'>"
            + head + _banner + pending_html + vitals_snapshot_html + conditions_html
            + (enc_rail + milestone_html + f"<details class='ovbox' id='sec-stay'><summary><span class='arrow'>▶</span>"
               f"<span class='ovtitle'>本次住院档案（{len(events)} 个事件 · 完整时间线与总览）</span>"
               f"<span class='ovsub'>出院后折叠保存 · 点开回溯全程</span></summary>"
               f"<div class='ovbody'>{timeline_html}{overview}</div></details>"
               if phase != "monitoring" else timeline_html + overview)
            + f"<section id='sec-labs'><h2>关键检验指标<small>按系统分组 · 每点可查采样时间 · 点击两次看该指标全部历史</small></h2>{''.join(hero_html)}</section>"
            + f"<section><h2>当前异常指标<small>按偏离参考程度排序 · 全时段</small></h2>{abnormal_html}</section>"
            + long_term_html
            + "".join(trend_sections) + culture_html + insight_html
            + discussion_html + facts_html + ledger_html
            + "<footer>HealthHub · 数据仅供参考，不构成医疗建议</footer>"
            + "</div>"
            + f"<a class='fab' href='/entry' aria-label='录入数据'>+</a>"
            + f"<a class='fab fab2' href='/upload?person_id={person.id}' aria-label='上传报告单'>📷</a>"
            + "<div id='tip'></div><div id='modal'><div class='mcard' id='mcard'></div></div>"
            + f"<script>{JS}</script></body></html>")
