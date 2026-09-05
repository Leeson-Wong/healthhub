import sqlite3

db = sqlite3.connect('/data/healthhub.db')

n1 = db.execute(
    "UPDATE clinical_events SET detail = REPLACE(detail, "
    "'注意：8/21 mNGS 未检出阪崎（疑误鉴定或已被清除），最终以鉴定+药敏为准。', "
    "'初报结果；8/21 mNGS 未检出阪崎（两法结果不一致，以最终鉴定+药敏为准）。') "
    "WHERE title LIKE '%阪崎%'").rowcount
n2 = db.execute(
    "UPDATE encounters SET note = REPLACE(note, "
    "'血培养曾报阪崎肠杆菌（疑误鉴定）', "
    "'血培养初报阪崎肠杆菌，与 mNGS 结果不一致，以最终鉴定为准')").rowcount

db.execute(
    "INSERT OR IGNORE INTO culture_reports "
    "(person_id, source_report_id, specimen, sampled_at, reported_at, organism, organism_comment, mdr_text, raw_payload) "
    "VALUES (2, NULL, '外周血 mNGS（宏基因组+靶向联合）', '2026-08-19T16:00:00+00:00', '2026-08-20T04:00:00+00:00', "
    "'屎肠球菌(相对丰度51%) + 大肠杆菌(13.5%) + 肺炎克雷伯菌(9.7%)', "
    "'报告编号 NY26003825 · 检出耐药基因 NDM-1/NDM-5、CTX-M-15/-55/-14、TEM', "
    "'耐药基因检出（表型待药敏确认）：碳青霉烯金属酶 NDM + ESBL CTX-M 家族', '{}')")
db.commit()

print('events fixed:', n1, '| encounter fixed:', n2)
print('mNGS:', db.execute("SELECT organism FROM culture_reports WHERE specimen LIKE '%mNGS%'").fetchone())
print('meds inference:', db.execute("SELECT status_inference, COUNT(*) FROM medications GROUP BY status_inference").fetchall())
