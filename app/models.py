from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "persons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sex: Mapped[str | None] = mapped_column(String(1))
    birth_date: Mapped[str | None] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(Text)
    phase_mode: Mapped[str | None] = mapped_column(String(8))  # null=自动 / monitoring/followup/archive
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)


class SourceFile(Base):
    """报告原件（照片/PDF），挂在 source_report 下，溯源用。"""
    __tablename__ = "source_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_report_id: Mapped[int] = mapped_column(ForeignKey("source_reports.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False, default="image/jpeg")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)


class PendingUpload(Base):
    """家人自助上传的报告照片队列（待 agent 处理入库）。"""
    __tablename__ = "pending_uploads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/processing/done/error
    note: Mapped[str | None] = mapped_column(Text)  # 家属备注（如"今天的血气"）
    submitted_by: Mapped[str | None] = mapped_column(String(32))
    source_report_id: Mapped[int | None] = mapped_column(ForeignKey("source_reports.id"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)
    processed_at: Mapped[str | None] = mapped_column(String(32))


class ObservationRevision(Base):
    """指标值修正留痕。"""
    __tablename__ = "observation_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[int] = mapped_column(ForeignKey("observations.id", ondelete="CASCADE"), nullable=False)
    old_raw_value: Mapped[str] = mapped_column(String(255), nullable=False)
    new_raw_value: Mapped[str] = mapped_column(String(255), nullable=False)
    old_value_num: Mapped[float | None]
    new_value_num: Mapped[float | None]
    reason: Mapped[str | None] = mapped_column(String(255))
    edited_by: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)


class Condition(Base):
    """诊断（病）—— 一等公民：有生命周期（活动/好转/缓解/治愈/慢病），可跨多次住院。"""
    __tablename__ = "conditions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active")
    # active(活动) / improving(好转中) / resolved(缓解/治愈) / chronic(慢病/后遗症) / suspected(疑似)
    category: Mapped[str | None] = mapped_column(String(16))  # 主诊断/并发症/基础病
    onset_at: Mapped[str | None] = mapped_column(String(32))  # 发病/确诊（可日粒度）
    resolved_at: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)
    __table_args__ = (UniqueConstraint("person_id", "name"),)


class CanonicalTerm(Base):
    __tablename__ = "canonical_terms"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(16), nullable=False)  # CBC/COAG/IMMUNO/BIOCHEM/URINE/CARDIAC
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)  # numeric/categorical/text
    default_unit: Mapped[str | None] = mapped_column(String(32))
    loinc: Mapped[str | None] = mapped_column(String(16))


class TermAlias(Base):
    __tablename__ = "term_aliases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alias_code: Mapped[str | None] = mapped_column(String(64))
    alias_name: Mapped[str | None] = mapped_column(String(128))
    panel_hint: Mapped[str | None] = mapped_column(String(16))
    unit_hint: Mapped[str | None] = mapped_column(String(32))
    canonical_code: Mapped[str] = mapped_column(String(32), ForeignKey("canonical_terms.code"), nullable=False)
    __table_args__ = (UniqueConstraint("alias_code", "alias_name", "panel_hint", "unit_hint"),)


class UnitConversion(Base):
    __tablename__ = "unit_conversions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_code: Mapped[str | None] = mapped_column(String(32))  # null = global rule
    from_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    to_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    factor: Mapped[float] = mapped_column(Float, nullable=False)
    __table_args__ = (UniqueConstraint("canonical_code", "from_unit"),)


class ReferenceRange(Base):
    __tablename__ = "reference_ranges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_code: Mapped[str] = mapped_column(String(32), ForeignKey("canonical_terms.code"), nullable=False)
    sex: Mapped[str | None] = mapped_column(String(1))
    age_min: Mapped[float | None]
    age_max: Mapped[float | None]
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # numeric/categorical
    low: Mapped[float | None]
    high: Mapped[float | None]
    low_inclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    high_inclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    expected_value: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # curated/lab_report
    __table_args__ = (UniqueConstraint("canonical_code", "sex", "age_min", "age_max", "low", "high", "expected_value", "source"),)


class SourceReport(Base):
    __tablename__ = "source_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    hospital: Mapped[str | None] = mapped_column(String(128))
    report_no: Mapped[str | None] = mapped_column(String(64))
    panel: Mapped[str | None] = mapped_column(String(128))
    sampled_at: Mapped[str | None] = mapped_column(String(32))
    reported_at: Mapped[str | None] = mapped_column(String(32))
    encounter_id: Mapped[int | None] = mapped_column(ForeignKey("encounters.id"))
    provenance: Mapped[str] = mapped_column(String(16), default="api")  # upload_ocr/api/entry/csv_import
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)
    __table_args__ = (UniqueConstraint("person_id", "external_id"),)


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    source_report_id: Mapped[int] = mapped_column(ForeignKey("source_reports.id", ondelete="CASCADE"), nullable=False)
    canonical_code: Mapped[str | None] = mapped_column(String(32))
    effective_at: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_code: Mapped[str | None] = mapped_column(String(64))
    raw_name: Mapped[str | None] = mapped_column(String(128))
    raw_value: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_unit: Mapped[str | None] = mapped_column(String(32))
    raw_ref_range: Mapped[str | None] = mapped_column(String(64))
    raw_flag: Mapped[str | None] = mapped_column(String(16))
    value_num: Mapped[float | None]
    value_text: Mapped[str | None] = mapped_column(String(255))
    unit_source: Mapped[str | None] = mapped_column(String(32))
    unit_canonical: Mapped[str | None] = mapped_column(String(32))
    source_ref_low: Mapped[float | None]
    source_ref_high: Mapped[float | None]
    source_ref_low_inclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    source_ref_high_inclusive: Mapped[bool] = mapped_column(Boolean, default=True)
    source_ref_expected: Mapped[str | None] = mapped_column(String(64))
    flag_source: Mapped[str | None] = mapped_column(String(16))  # HIGH/LOW/NEG/POS/S/R/S_SUSPECT
    flag_computed: Mapped[str | None] = mapped_column(String(16))  # HIGH/LOW/ABNORMAL_CAT/NORMAL
    __table_args__ = (UniqueConstraint("person_id", "canonical_code", "effective_at", "source_report_id"),)


class UnresolvedTerm(Base):
    __tablename__ = "unresolved_terms"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_code: Mapped[str | None] = mapped_column(String(64))
    raw_name: Mapped[str | None] = mapped_column(String(128))
    panel: Mapped[str | None] = mapped_column(String(128))
    unit: Mapped[str | None] = mapped_column(String(32))
    sample_values: Mapped[str | None] = mapped_column(Text)  # JSON array
    first_seen: Mapped[str] = mapped_column(String(32), default=utcnow_iso)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/resolved
    suggested_canonical: Mapped[str | None] = mapped_column(String(32))
    suggested_by: Mapped[str | None] = mapped_column(String(64))
    suggested_at: Mapped[str | None] = mapped_column(String(32))


class CultureReport(Base):
    __tablename__ = "culture_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    source_report_id: Mapped[int | None] = mapped_column(ForeignKey("source_reports.id", ondelete="CASCADE"))
    specimen: Mapped[str] = mapped_column(String(128), nullable=False)
    sampled_at: Mapped[str | None] = mapped_column(String(32))
    reported_at: Mapped[str | None] = mapped_column(String(32))
    organism: Mapped[str | None] = mapped_column(String(255))
    organism_comment: Mapped[str | None] = mapped_column(String(255))
    mdr_text: Mapped[str | None] = mapped_column(String(255))
    raw_payload: Mapped[str | None] = mapped_column(Text)


class CultureSusceptibility(Base):
    __tablename__ = "culture_susceptibilities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    culture_report_id: Mapped[int] = mapped_column(ForeignKey("culture_reports.id", ondelete="CASCADE"), nullable=False)
    drug_code: Mapped[str | None] = mapped_column(String(32))
    drug_name: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(8), nullable=False)  # S/R/I/SDD
    result_raw: Mapped[str | None] = mapped_column(String(64))
    mic_text: Mapped[str | None] = mapped_column(String(64))


class Medication(Base):
    __tablename__ = "medications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    drug_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dose: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str | None] = mapped_column(String(32))
    frequency: Mapped[str | None] = mapped_column(String(64))
    route: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[str | None] = mapped_column(String(32))
    ended_at: Mapped[str | None] = mapped_column(String(32))
    time_precision: Mapped[str] = mapped_column(String(8), default="day")  # day/datetime
    # 状态语义来源：billed_ongoing/billed_ended=费用清单推断 / order=病历医嘱确认 / null=未知
    status_inference: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)


class SymptomNote(Base):
    __tablename__ = "symptom_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[int | None] = mapped_column(Integer)  # 1-5
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)


class Encounter(Base):
    __tablename__ = "encounters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="住院")  # 住院/门诊/随访
    hospital: Mapped[str | None] = mapped_column(String(128))
    department: Mapped[str | None] = mapped_column(String(64))
    doctor_name: Mapped[str | None] = mapped_column(String(64))
    doctor_phone: Mapped[str | None] = mapped_column(String(32))
    diagnosis: Mapped[str | None] = mapped_column(Text)
    admitted_at: Mapped[str | None] = mapped_column(String(32))
    discharged_at: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)


class ClinicalEvent(Base):
    """病程时间线事件：与检验曲线同轴展示的临床事件（起病/干预/用药/培养回报/转科）。"""
    __tablename__ = "clinical_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(32), nullable=False)  # UTC ISO，与其他表一致
    time_precision: Mapped[str] = mapped_column(String(16), default="minute")  # approx/day/hour/minute
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # onset/intervention/medication/culture_result/test_result/transfer/condition_change/surgery/other
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(64))  # 报告单/家属口述/医生
    pivotal: Mapped[int] = mapped_column(Integer, default=0)  # 1=关键转折，小图也钉线
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)
    __table_args__ = (UniqueConstraint("person_id", "occurred_at", "kind", "title"),)


class PersonFact(Base):
    """背景档案：既往史/家族史/基础状态等非时间线临床上下文。"""
    __tablename__ = "person_facts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    # past_history/family_history/baseline/care/social
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)


class DiscussionPost(Base):
    """病情讨论区：家属/照护者对病情的观察、问题、医生反馈、决定。"""
    __tablename__ = "discussion_posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    author: Mapped[str] = mapped_column(String(32), nullable=False, default="家属")
    category: Mapped[str] = mapped_column(String(16), nullable=False, default="观察")
    # 观察/问题/医生反馈/决定/其他
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)


class InsightReport(Base):
    __tablename__ = "insight_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # on_demand/daily
    report_date: Mapped[str | None] = mapped_column(String(10))  # local date for daily
    window_from: Mapped[str | None] = mapped_column(String(32))
    window_to: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_md: Mapped[str] = mapped_column(Text, nullable=False)
    response_md: Mapped[str] = mapped_column(Text, nullable=False)
    usage_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok/skipped/error
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow_iso)
