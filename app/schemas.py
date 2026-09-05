from datetime import datetime

from pydantic import BaseModel, Field


class PersonIn(BaseModel):
    external_ref: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    sex: str | None = None
    birth_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str | None = None


class PersonOut(BaseModel):
    id: int
    external_ref: str
    name: str
    sex: str | None
    birth_date: str | None
    note: str | None


class ItemIn(BaseModel):
    code: str | None = None
    name: str | None = None
    value: str
    unit: str | None = None
    ref_range_text: str | None = None
    flag: str | None = None


class SusceptibilityIn(BaseModel):
    drug_code: str | None = None
    drug_name: str
    result: str  # S/R/I/SDD
    result_raw: str | None = None
    mic_text: str | None = None


class CultureIn(BaseModel):
    specimen: str
    sampled_at: str | None = None
    reported_at: str | None = None
    organism: str | None = None
    organism_comment: str | None = None
    mdr_text: str | None = None
    susceptibilities: list[SusceptibilityIn] = Field(default_factory=list)


class ReportIn(BaseModel):
    person_ref: str | None = None
    person_id: int | None = None
    external_id: str = Field(min_length=1, max_length=255)
    hospital: str | None = None
    report_no: str | None = None
    panel: str | None = None
    sampled_at: str | None = None
    reported_at: str | None = None
    encounter_id: int | None = None
    provenance: str | None = None  # upload_ocr/api/entry/csv_import（默认 api）
    items: list[ItemIn] = Field(default_factory=list)
    cultures: list[CultureIn] = Field(default_factory=list)


class IngestSummary(BaseModel):
    report_id: int
    created: bool
    items_total: int
    items_normalized: int
    items_unresolved: int
    cultures: int


class ObservationOut(BaseModel):
    id: int
    effective_at: str
    canonical_code: str | None
    name_cn: str | None
    value_num: float | None
    value_text: str | None
    unit_canonical: str | None
    unit_source: str | None
    raw_code: str | None
    raw_name: str | None
    raw_ref_range: str | None
    raw_flag: str | None
    flag_source: str | None
    flag_computed: str | None
    source_report_id: int


class TrendPoint(BaseModel):
    t: str
    value: float | None
    text: str | None
    flag: str | None


class TrendOut(BaseModel):
    code: str
    name_cn: str | None
    unit: str | None
    points: list[TrendPoint]
    first: float | None
    last: float | None
    delta: float | None
    delta_pct: float | None


class InsightIn(BaseModel):
    person_id: int | None = None
    person_ref: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    topic: str | None = None


class InsightOut(BaseModel):
    id: int
    person_id: int
    kind: str
    model: str
    response_md: str
    status: str
    created_at: str


class ClinicalEventIn(BaseModel):
    person_id: int | None = None
    person_ref: str | None = None
    occurred_at: str = Field(min_length=10)  # ISO（本地带时区或 UTC，入库归一化为 UTC）
    time_precision: str = "minute"  # approx/day/hour/minute
    kind: str  # onset/intervention/medication/culture_result/test_result/transfer/condition_change/surgery/other
    title: str = Field(min_length=1, max_length=160)
    detail: str | None = None
    source: str | None = None


class ClinicalEventOut(BaseModel):
    id: int
    person_id: int
    occurred_at: str
    time_precision: str
    kind: str
    title: str
    detail: str | None
    source: str | None
    created_at: str


class PersonFactIn(BaseModel):
    person_id: int | None = None
    person_ref: str | None = None
    category: str  # past_history/family_history/baseline/care/social
    title: str = Field(min_length=1, max_length=160)
    detail: str | None = None
    sort_order: int = 0


class PersonFactOut(BaseModel):
    id: int
    person_id: int
    category: str
    title: str
    detail: str | None
    sort_order: int
    created_at: str


class DiscussionIn(BaseModel):
    person_id: int | None = None
    person_ref: str | None = None
    author: str = Field(default="家属", max_length=32)
    category: str = "观察"  # 观察/问题/医生反馈/决定/其他
    content: str = Field(min_length=1, max_length=4000)


class DiscussionOut(BaseModel):
    id: int
    person_id: int
    author: str
    category: str
    content: str
    created_at: str


class ConditionIn(BaseModel):
    person_id: int | None = None
    person_ref: str | None = None
    name: str = Field(min_length=1, max_length=128)
    status: str = "active"  # active/improving/resolved/chronic/suspected
    category: str | None = None  # 主诊断/并发症/基础病
    onset_at: str | None = None
    resolved_at: str | None = None
    note: str | None = None
    source: str | None = None


class ConditionOut(BaseModel):
    id: int
    person_id: int
    name: str
    status: str
    category: str | None
    onset_at: str | None
    resolved_at: str | None
    note: str | None
    source: str | None
    created_at: str


class MappingIn(BaseModel):
    raw_code: str | None = None
    raw_name: str | None = None
    panel_hint: str | None = None
    unit_hint: str | None = None
    canonical_code: str
