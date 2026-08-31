"""
اختبارات حقل الموافقة التسويقية ونافذة التواصل (PRD §19، D-021).

الحقلان لا يقرأهما شيء اليوم، فما يُختبَر هنا هو **صدق ما يُكتب**
وحده - وهو كل ما يمكن اختباره في تغيير بلا سلوك:

  1) المفردات: القيمة الوحيدة التي يكتبها مسار حيّ هي `none`.
  2) الهجرة: الصف القديم يأخذ `legacy_unknown` لا `none`، ونافذته
     تبقى فارغة لا مشتقّة من وقت الإنشاء.
  3) النافذة زمن لا حالة: تُكتب طابعاً، وتُحدَّث برسالة منها.
  4) لا سلوك تغيّر: الأهلية والقمع لا يريان العمودين إطلاقاً.
"""

import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import leads_store
from leads_store import (
    CONSENT_COLUMN,
    CONSENT_LEGACY_UNKNOWN,
    CONSENT_NONE,
    CONTACT_WINDOW_COLUMN,
    FIELDNAMES,
    LEAD_ID_COLUMN,
    OUTCOME_PENDING,
    STATE_PRICE_QUOTED,
    STATUS_REASON_COLUMN,
    TIMESTAMP_FORMAT,
    get_leads_eligible_for_first_followup,
    is_unbooked,
    record_booking_request,
    record_decline,
    record_hesitation,
    record_price_quote,
    save_lead,
)

SERVICE_BOTOX = "حقن البوتوكس"
SERVICE_BOTOX_PRICE = "120,000 دينار"

# بنية ما قبل §19: الأعمدة الحالية كلها ما عدا العمودين الجديدين.
V5_FIELDNAMES = [f for f in FIELDNAMES
                 if f not in (CONSENT_COLUMN, CONTACT_WINDOW_COLUMN)]


# ----------------------------------------------------------------- أدوات

def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_rows():
    return leads_store._read_all_rows()


def read_header(path):
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        return csv.DictReader(f).fieldnames


def v5_row(lead_id: str, **overrides) -> dict:
    """صف بالبنية السابقة لـ§19 - وهي بنية الملف الحقيقي قبل هذا التغيير."""
    row = {
        LEAD_ID_COLUMN: lead_id,
        "التاريخ والوقت": "2026-08-20 09:00:00",
        "معرف العميل": "111",
        "القناة": "telegram",
        "الخدمة المطلوبة": SERVICE_BOTOX,
        "الحالة": STATE_PRICE_QUOTED,
        STATUS_REASON_COLUMN: "price_quoted",
        "بيانات التواصل": "",
        "سعر الخدمة وقت الإنشاء": SERVICE_BOTOX_PRICE,
        "مرحلة المتابعة": "0",
        "تاريخ آخر متابعة": "",
        "نتيجة المتابعة": OUTCOME_PENDING,
    }
    row.update(overrides)
    return row


# --------------------------------------------- 1) مفردات ما يُكتب اليوم

def test_price_quote_row_claims_no_marketing_consent(isolated_leads_file):
    """
    الصف المكتوب اليوم يقول `none` - لم تُطلب موافقة تسويقية ولم
    تُمنَح. لا مسار في النظام يطلبها، فأي قيمة أخرى ادّعاء.
    """
    record_price_quote("777", SERVICE_BOTOX, "telegram")

    assert read_rows()[0][CONSENT_COLUMN] == CONSENT_NONE


def test_save_lead_row_claims_no_marketing_consent(isolated_leads_file):
    """مسار السقوط الآمن يكتب نفس القيمة: لا مسار امتياز يتجاوز §19."""
    save_lead("778", SERVICE_BOTOX, "telegram", STATE_PRICE_QUOTED)

    assert read_rows()[0][CONSENT_COLUMN] == CONSENT_NONE


def test_no_live_path_ever_writes_consent_other_than_none(isolated_leads_file):
    """
    دورة حياة كاملة (سعر ← تردد ← رفض ← حجز) لا تُنتج قيمة موافقة
    واحدة غير `none`. تسليم رقم الهاتف للحجز ليس إذناً تسويقياً -
    وهذا هو الخلط الذي وُجد العمودان لمنعه.
    """
    lead_id = record_price_quote("779", SERVICE_BOTOX, "telegram")
    record_hesitation(lead_id)
    record_decline(lead_id)
    record_booking_request(lead_id, "سارة 07701234567")

    assert read_rows()[0][CONSENT_COLUMN] == CONSENT_NONE


def test_she_messaged_first_is_not_a_consent_value(isolated_leads_file):
    """
    «راسلتنا أولاً» ليست قيمة في عمود الموافقة. هي نافذة خدمة، ومكانها
    العمود الزمني - لأن النافذة تنقضي وحدها بينما الموافقة تدوم.
    """
    record_price_quote("780", SERVICE_BOTOX, "telegram")
    row = read_rows()[0]

    assert row[CONSENT_COLUMN] == CONSENT_NONE
    assert row[CONTACT_WINDOW_COLUMN]  # الواقعة مسجَّلة، في عمودها


# ------------------------------------------------------------ 2) الهجرة

def test_migration_gives_pre_field_rows_legacy_unknown(isolated_leads_file):
    """
    صف كُتب قبل وجود العمود لم يُلاحَظ على هذا المحور إطلاقاً.
    `legacy_unknown` اعترافٌ بذلك؛ `none` كانت ستدّعي رصداً لم يقع
    (نفس منطق D-016).
    """
    write_csv(isolated_leads_file, V5_FIELDNAMES, [v5_row("ld_a")])

    row = read_rows()[0]
    assert row[CONSENT_COLUMN] == CONSENT_LEGACY_UNKNOWN
    assert row[CONSENT_COLUMN] != CONSENT_NONE


def test_migration_leaves_window_empty_and_does_not_derive_it(isolated_leads_file):
    """
    لا اشتقاق للنافذة من وقت الإنشاء: الإنشاء ≥ وقت رسالتها، فاشتقاقه
    يدفع انتهاء النافذة للأمام ويزعم نافذة مفتوحة بعد إغلاقها فعلاً.
    "" تقول "لا سجل" بصدق.
    """
    created = "2026-08-20 09:00:00"
    write_csv(isolated_leads_file, V5_FIELDNAMES,
              [v5_row("ld_b", **{"التاريخ والوقت": created})])

    row = read_rows()[0]
    assert row[CONTACT_WINDOW_COLUMN] == ""
    assert row[CONTACT_WINDOW_COLUMN] != created


def test_migration_preserves_every_pre_field_value(isolated_leads_file):
    """هجرة حافِظة للحقول: لا حقل من البنية السابقة يتغيّر بحرف."""
    original = v5_row("ld_c", **{
        "معرف العميل": "222", "القناة": "instagram",
        "سعر الخدمة وقت الإنشاء": "150,000 دينار",
        "مرحلة المتابعة": "2", "تاريخ آخر متابعة": "2026-08-24 08:30:00",
        "بيانات التواصل": "هدى 07709876543",
    })
    write_csv(isolated_leads_file, V5_FIELDNAMES, [original])

    after = read_rows()[0]
    for field in V5_FIELDNAMES:
        assert after[field] == original[field], f"الحقل '{field}' تغيّر أثناء الهجرة"


def test_migration_adds_both_columns_to_header(isolated_leads_file):
    write_csv(isolated_leads_file, V5_FIELDNAMES, [v5_row("ld_d")])
    read_rows()

    assert read_header(isolated_leads_file) == FIELDNAMES


def test_migration_is_idempotent(isolated_leads_file):
    """تشغيل الهجرة مرتين لا يغيّر قيمة ولا يعيد توليد معرّف."""
    write_csv(isolated_leads_file, V5_FIELDNAMES, [v5_row("ld_e")])

    first = read_rows()[0]
    second = read_rows()[0]

    assert first == second
    assert second[LEAD_ID_COLUMN] == "ld_e"
    assert second[CONSENT_COLUMN] == CONSENT_LEGACY_UNKNOWN


def test_blank_consent_in_correct_header_is_healed(isolated_leads_file):
    """
    "" ليست قيمة في مفردات الموافقة - هي عمود لم يُملأ. تُقرأ
    `legacy_unknown` لنفس السبب: لا رصد وقع على هذا الصف.
    """
    write_csv(isolated_leads_file, FIELDNAMES,
              [v5_row("ld_f", **{CONSENT_COLUMN: "", CONTACT_WINDOW_COLUMN: ""})])

    assert read_rows()[0][CONSENT_COLUMN] == CONSENT_LEGACY_UNKNOWN


def test_blank_window_alone_does_not_rewrite_the_file(isolated_leads_file):
    """
    نافذة فارغة قيمة نهائية مشروعة. لو أشعلت الهجرة لأُعيدت كتابة
    الملف عند كل قراءة بلا نهاية.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [v5_row(
        "ld_g", **{CONSENT_COLUMN: CONSENT_LEGACY_UNKNOWN, CONTACT_WINDOW_COLUMN: ""})])
    mtime_before = Path(isolated_leads_file).stat().st_mtime_ns

    read_rows()

    assert Path(isolated_leads_file).stat().st_mtime_ns == mtime_before


def test_migration_takes_consent_backup_once(isolated_leads_file):
    """لقطة ما قبل §19، ولا تُدهَس بأي كتابة لاحقة."""
    backup = Path(leads_store.BACKUP_FILE_CONSENT)
    write_csv(isolated_leads_file, V5_FIELDNAMES, [v5_row("ld_h")])

    read_rows()
    assert backup.is_file()
    snapshot = backup.read_text(encoding="utf-8-sig")
    assert CONSENT_COLUMN not in snapshot  # لقطة ما *قبل* العمود فعلاً

    record_price_quote("999", SERVICE_BOTOX, "telegram")
    assert backup.read_text(encoding="utf-8-sig") == snapshot


# -------------------------------------- 3) النافذة زمن لا حالة (§19)

def test_window_is_a_timestamp_not_a_status(isolated_leads_file):
    """
    القيمة طابع زمني قابل للحساب. حقل نصي يقول "مفتوحة" يصير كذباً
    بعد ساعة، والسؤال الوحيد الذي يهم ("هل وقعت المتابعة داخل
    النافذة؟") لا يُجاب إلا بحساب زمني.
    """
    record_price_quote("781", SERVICE_BOTOX, "telegram")
    value = read_rows()[0][CONTACT_WINDOW_COLUMN]

    parsed = datetime.strptime(value, TIMESTAMP_FORMAT)
    assert abs((datetime.now() - parsed).total_seconds()) < 120


def test_window_opens_at_row_creation(isolated_leads_file):
    """الصف يُكتب رداً على رسالتها، فلحظة الإنشاء هي أقرب ما نملك."""
    record_price_quote("782", SERVICE_BOTOX, "telegram")
    row = read_rows()[0]

    assert row[CONTACT_WINDOW_COLUMN] == row["التاريخ والوقت"]


@pytest.mark.parametrize("act", [
    lambda lead_id: record_hesitation(lead_id),
    lambda lead_id: record_decline(lead_id),
    lambda lead_id: record_booking_request(lead_id, "سارة 07701234567"),
])
def test_her_later_message_reopens_the_window(isolated_leads_file, act):
    """
    النافذة تُفتح برسالة منها لا برضاها: الرفض يفتحها كما يفتحها
    الحجز. وطابع الإنشاء لا يتحرّك - ساعة المتابعة تبقى كما كانت.
    """
    lead_id = record_price_quote("783", SERVICE_BOTOX, "telegram")
    stale = "2026-08-01 08:00:00"
    leads_store._update_lead_row(lead_id, {CONTACT_WINDOW_COLUMN: stale})
    created_before = read_rows()[0]["التاريخ والوقت"]

    act(lead_id)

    row = read_rows()[0]
    assert row[CONTACT_WINDOW_COLUMN] != stale
    assert row["التاريخ والوقت"] == created_before


def test_window_stamp_is_a_lower_bound_not_a_claim_of_openness(isolated_leads_file):
    """
    رسالة واردة لا تكتب صفاً (سؤال سعر مكرر على Lead قائم) لا تُحرّك
    الطابع. فالنافذة المحسوبة منه تُغلق مبكراً لا متأخراً - اتجاه
    الخطأ الذي لا يسمح بإرسال رسالة لا نملك حق إرسالها.
    """
    lead_id = record_price_quote("784", SERVICE_BOTOX, "telegram")
    stale = "2026-08-01 08:00:00"
    leads_store._update_lead_row(lead_id, {CONTACT_WINDOW_COLUMN: stale})

    assert record_price_quote("784", SERVICE_BOTOX, "telegram") == lead_id
    assert read_rows()[0][CONTACT_WINDOW_COLUMN] == stale


# ------------------------------------------------------ 4) لا سلوك تغيّر

def test_followup_eligibility_ignores_both_columns(isolated_leads_file):
    """
    صفّان متطابقان إلا في عمودي §19 يتصرّفان تصرفاً واحداً: لا شيء
    يقرأهما ليقرر (D-021). أول قارئ لهما قرارٌ يُتخذ صراحةً، لا
    يتسلل داخل هذا التغيير.
    """
    old = (datetime.now() - timedelta(hours=48)).strftime(TIMESTAMP_FORMAT)
    write_csv(isolated_leads_file, FIELDNAMES, [
        v5_row("ld_none", **{
            "معرف العميل": "301", "التاريخ والوقت": old,
            CONSENT_COLUMN: CONSENT_NONE, CONTACT_WINDOW_COLUMN: old}),
        v5_row("ld_legacy", **{
            "معرف العميل": "302", "التاريخ والوقت": old,
            CONSENT_COLUMN: CONSENT_LEGACY_UNKNOWN, CONTACT_WINDOW_COLUMN: ""}),
    ])

    eligible = {row[LEAD_ID_COLUMN] for row in get_leads_eligible_for_first_followup()}
    assert eligible == {"ld_none", "ld_legacy"}


def test_unbooked_ignores_both_columns(isolated_leads_file):
    """مقام القياس لا يتغيّر بقيمة الموافقة: لا حقل جديد يدخل §8."""
    old = (datetime.now() - timedelta(hours=48)).strftime(TIMESTAMP_FORMAT)
    granted_shape = v5_row("ld_i", **{
        "التاريخ والوقت": old, CONSENT_COLUMN: CONSENT_NONE,
        CONTACT_WINDOW_COLUMN: old})
    legacy_shape = dict(granted_shape, **{
        CONSENT_COLUMN: CONSENT_LEGACY_UNKNOWN, CONTACT_WINDOW_COLUMN: ""})

    assert is_unbooked(granted_shape) == is_unbooked(legacy_shape) is True


def test_no_module_reads_the_new_columns_yet(isolated_leads_file):
    """
    حارس نصّي على القرار لا على الشرح: لا وحدة إرسال أو متابعة أو
    تقرير تذكر العمودين. حين يُقرأ أولهما، هذا الاختبار هو ما يجب أن
    يُعدَّل عمداً - فيصير القرار مرئياً في الـdiff.
    """
    root = Path(__file__).resolve().parent.parent
    consumers = ["send_followups.py", "check_followups.py", "business_logic.py",
                 "lead_recovery_report.py", "events_funnel.py", "events.py",
                 "outbound.py", "settings.py"]

    for name in consumers:
        source = (root / name).read_text(encoding="utf-8")
        assert CONSENT_COLUMN not in source, f"{name} صار يذكر عمود الموافقة"
        assert CONTACT_WINDOW_COLUMN not in source, f"{name} صار يذكر عمود النافذة"
