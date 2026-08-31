"""
اختبارات مواءمة مفردات الحالة مع PRD §8/D2 (F2) وإيقاف احتساب
الإيراد لحظة طلب الحجز (F3).

تغطي أربع طبقات:
  1) الهجرة: ترجمة القيم القديمة بلا فقد حقل وبلا اختراع تصنيف،
     مع نسخة احتياطية مرة واحدة، وidempotent.
  2) is_unbooked: نافذة الصمت، ومن يدخل المقام ومن لا يدخله.
  3) compute_funnel_metrics: الطبقات الأربع، وNone لما لا يُقاس.
  4) حالة الجلسة: جلسة حيّة بالاسم القديم تُقرأ صحيحة ولا يُعاد
     تسعير الخدمة على عميلة وسط محادثة.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import leads_store
from leads_store import (
    FIELDNAMES,
    LEAD_ID_COLUMN,
    OPEN_STATES,
    OUTCOME_EXPIRED,
    OUTCOME_ORGANIC,
    OUTCOME_PENDING,
    OUTCOME_RECOVERED,
    REASON_DECLINED,
    REASON_HESITANT,
    REASON_PRICE_QUOTED,
    SILENCE_WINDOW_HOURS,
    STATE_BOOKING_REQUESTED,
    STATE_DECLINED,
    STATE_LEGACY_UNKNOWN,
    STATE_PRICE_QUOTED,
    STATUS_REASON_COLUMN,
    compute_funnel_metrics,
    is_unbooked,
    record_booking_request,
    record_price_quote,
)

from business_logic import handle_message
from channel_interface import IncomingMessage
from lead_recovery_report import render_report
from services import find_service
from storage import session_store

SERVICE_BOTOX = "حقن البوتوكس"
SERVICE_BOTOX_PRICE = "120,000 دينار"

CREATED_AT = "2026-08-20 09:00:00"
NOW = datetime(2026, 8, 28, 14, 30, 0)


# ----------------------------------------------------------------- أدوات

def write_csv(path, fieldnames, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_rows():
    return leads_store._read_all_rows()


def legacy_row(lead_id: str, **overrides) -> dict:
    """صف بالبنية الحالية لكن بمفردات ما قبل §8 - وهي حالة الملف الحقيقي اليوم."""
    row = {
        LEAD_ID_COLUMN: lead_id,
        "التاريخ والوقت": CREATED_AT,
        "معرف العميل": "111",
        "القناة": "telegram",
        "الخدمة المطلوبة": SERVICE_BOTOX,
        "الحالة": "not_ready",
        STATUS_REASON_COLUMN: "",
        "بيانات التواصل": "",
        "سعر الخدمة وقت الإنشاء": SERVICE_BOTOX_PRICE,
        "مرحلة المتابعة": "0",
        "تاريخ آخر متابعة": "",
        "نتيجة المتابعة": OUTCOME_PENDING,
    }
    row.update(overrides)
    return row


def make_message(user_id: str, text: str, channel: str = "telegram") -> IncomingMessage:
    return IncomingMessage(channel=channel, user_id=user_id, text=text, timestamp=datetime.now())


class FrozenDatetime(datetime):
    frozen_at = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.frozen_at


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(leads_store, "datetime", FrozenDatetime)
    return FrozenDatetime.frozen_at


# ------------------------------------------------- 1) هجرة المفردات

def test_migration_maps_confirmed_to_booking_requested(isolated_leads_file):
    """
    `confirmed` كان يعني "أرسلت رقمها". Confirmed Booking في §8 يعني
    "أكّدت الموظفة". الترجمة تصحّح أخطر اسم في الملف.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [
        legacy_row("ld_a", **{"الحالة": "confirmed", "بيانات التواصل": "سارة 0770",
                              "نتيجة المتابعة": OUTCOME_RECOVERED}),
    ])

    row = read_rows()[0]
    assert row["الحالة"] == STATE_BOOKING_REQUESTED
    assert row["بيانات التواصل"] == "سارة 0770"
    assert row["نتيجة المتابعة"] == OUTCOME_RECOVERED


@pytest.mark.parametrize("reason,expected_state", [
    (REASON_DECLINED, STATE_DECLINED),
    (REASON_PRICE_QUOTED, STATE_PRICE_QUOTED),
    (REASON_HESITANT, STATE_PRICE_QUOTED),
])
def test_migration_maps_not_ready_by_status_reason(isolated_leads_file, reason, expected_state):
    """
    `not_ready` كانت تُكتب للرفض الصريح وللصمت بعد التسعير معاً.
    status_reason هو الدليل الوحيد في الملف على أيهما - ويُستعمل.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [
        legacy_row("ld_b", **{STATUS_REASON_COLUMN: reason}),
    ])

    assert read_rows()[0]["الحالة"] == expected_state


def test_migration_maps_reasonless_not_ready_to_legacy_unknown(isolated_leads_file):
    """
    صف بلا status_reason: لا دليل في الملف على سبب حالته. تصنيفه
    `declined` أو `price_quoted` تخميناً كان سيكتب ادّعاءً في بيانات
    سنقيس عليها لاحقاً - وlegacy_unknown لا يدّعي شيئاً.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [legacy_row("ld_c")])

    assert read_rows()[0]["الحالة"] == STATE_LEGACY_UNKNOWN


def test_legacy_unknown_stays_followup_eligible_exactly_as_before(isolated_leads_file, frozen_clock):
    """
    الترجمة تمسّ الاسم وحده: صف كان مؤهلاً للمتابعة بـ`not_ready`
    يبقى مؤهلاً بـ`legacy_unknown`. لا Lead يسقط من دورة المتابعة
    بسبب تغيير أسماء.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [legacy_row("ld_d")])

    assert STATE_LEGACY_UNKNOWN in OPEN_STATES
    eligible = leads_store.get_leads_eligible_for_first_followup(SILENCE_WINDOW_HOURS)
    assert [r[LEAD_ID_COLUMN] for r in eligible] == ["ld_d"]


def test_migration_renames_organic_outcome(isolated_leads_file):
    """«أُغلق» يوحي بفرصة خاسرة؛ هي حجز ناجح بلا فضل للمتابعة (organic §9.1)."""
    write_csv(isolated_leads_file, FIELDNAMES, [
        legacy_row("ld_e", **{"الحالة": "confirmed", "نتيجة المتابعة": "أُغلق"}),
    ])

    assert read_rows()[0]["نتيجة المتابعة"] == OUTCOME_ORGANIC


def test_migration_leaves_unrecognised_values_verbatim(isolated_leads_file):
    """قيمة لا نعرفها (كتابة يدوية) تبقى كما تركها صاحبها - لا تُترجَم ولا تُمحى."""
    write_csv(isolated_leads_file, FIELDNAMES, [
        legacy_row("ld_f", **{"الحالة": "حالة_يدوية", "نتيجة المتابعة": "نتيجة_يدوية"}),
    ])

    row = read_rows()[0]
    assert row["الحالة"] == "حالة_يدوية"
    assert row["نتيجة المتابعة"] == "نتيجة_يدوية"


def test_migration_preserves_every_other_field(isolated_leads_file):
    """
    مواءمة المفردات تغيّر عمودي §8 وحدهما. كل حقل آخر يمرّ حرفياً.

    عمودا §19 مستثنيان لأن `legacy_row` لا يكتبهما أصلاً - وهجرتهما
    مختبَرة باسمها في test_consent_field.py.
    """
    original = legacy_row("ld_g", **{
        STATUS_REASON_COLUMN: REASON_DECLINED,
        "معرف العميل": "222", "القناة": "whatsapp",
        "سعر الخدمة وقت الإنشاء": "150,000 دينار",
        "مرحلة المتابعة": "2", "تاريخ آخر متابعة": "2026-08-24 08:30:00",
    })
    write_csv(isolated_leads_file, FIELDNAMES, [original])

    after = read_rows()[0]
    for field in FIELDNAMES:
        if field in ("الحالة", leads_store.CONSENT_COLUMN,
                     leads_store.CONTACT_WINDOW_COLUMN):
            continue
        assert after[field] == original[field], f"الحقل '{field}' تغيّر أثناء الهجرة"


def test_migration_takes_status_vocabulary_backup_once(isolated_leads_file):
    """لقطة ما قبل مواءمة المفردات، ولا تُدهَس بأي كتابة لاحقة."""
    backup = Path(leads_store.BACKUP_FILE_STATUS_VOCABULARY)
    write_csv(isolated_leads_file, FIELDNAMES, [legacy_row("ld_h")])
    content_before = Path(isolated_leads_file).read_bytes()

    assert not backup.exists()

    read_rows()  # تُشغّل الهجرة، وهي أول كتابة

    assert backup.exists()
    assert backup.read_bytes() == content_before

    record_price_quote(user_id="999", service_name=SERVICE_BOTOX, channel="telegram")
    assert backup.read_bytes() == content_before


def test_vocabulary_migration_is_idempotent(isolated_leads_file):
    """تشغيلها مجدداً لا يغيّر قيمة ولا يعيد كتابة الملف."""
    write_csv(isolated_leads_file, FIELDNAMES, [
        legacy_row("ld_i", **{STATUS_REASON_COLUMN: REASON_DECLINED}),
        legacy_row("ld_j", **{"الحالة": "confirmed", "نتيجة المتابعة": "أُغلق"}),
    ])

    first_pass = read_rows()
    content_after_first = Path(isolated_leads_file).read_bytes()

    for _ in range(3):
        assert read_rows() == first_pass

    assert Path(isolated_leads_file).read_bytes() == content_after_first


# ------------------------------------------------- 2) is_unbooked

def test_is_unbooked_only_after_silence_window(isolated_leads_file, frozen_clock):
    lead_id = record_price_quote(user_id="700", service_name=SERVICE_BOTOX, channel="telegram")
    row = read_rows()[0]
    assert row[LEAD_ID_COLUMN] == lead_id

    assert is_unbooked(row, NOW) is False
    assert is_unbooked(row, NOW + timedelta(hours=SILENCE_WINDOW_HOURS, minutes=-1)) is False
    assert is_unbooked(row, NOW + timedelta(hours=SILENCE_WINDOW_HOURS)) is True


def test_is_unbooked_excludes_declined_and_legacy_unknown(isolated_leads_file):
    """
    §7 يجعل DECLINED وUNBOOKED فرعين شقيقين لا متداخلين - وهي القراءة
    المعتمدة. مقام Recovery Rate يبقى الصامتات وحدهن.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [
        legacy_row("ld_k", **{STATUS_REASON_COLUMN: REASON_DECLINED}),   # -> declined
        legacy_row("ld_l"),                                              # -> legacy_unknown
        legacy_row("ld_m", **{STATUS_REASON_COLUMN: REASON_PRICE_QUOTED}),  # -> price_quoted
    ])

    long_after = datetime.strptime(CREATED_AT, leads_store.TIMESTAMP_FORMAT) + timedelta(days=30)
    unbooked = [r[LEAD_ID_COLUMN] for r in read_rows() if is_unbooked(r, long_after)]

    assert unbooked == ["ld_m"]


def test_is_unbooked_includes_expired_lead(isolated_leads_file):
    """صف "منتهي" كان صامتاً ولم يحجز أبداً - وهو بالضبط ما يقيسه المقام."""
    write_csv(isolated_leads_file, FIELDNAMES, [
        legacy_row("ld_n", **{STATUS_REASON_COLUMN: REASON_PRICE_QUOTED,
                              "مرحلة المتابعة": "2", "نتيجة المتابعة": OUTCOME_EXPIRED}),
    ])

    long_after = datetime.strptime(CREATED_AT, leads_store.TIMESTAMP_FORMAT) + timedelta(days=30)
    assert is_unbooked(read_rows()[0], long_after) is True


def test_is_unbooked_excludes_booking_request(isolated_leads_file, frozen_clock):
    lead_id = record_price_quote(user_id="701", service_name=SERVICE_BOTOX, channel="telegram")
    assert record_booking_request(lead_id=lead_id, contact_info="سارة 0770") is True

    assert is_unbooked(read_rows()[0], NOW + timedelta(days=30)) is False


# --------------------------------------- 3) compute_funnel_metrics

def test_funnel_metrics_returns_none_for_unmeasurable_layers(isolated_leads_file, frozen_clock):
    """
    القاعدة الحمراء في §8: لا يُسمّى رقم إيراداً إلا عند الحضور.
    None لا صفر - الصفر قياسٌ، وNone غياب قياس.
    """
    record_price_quote(user_id="702", service_name=SERVICE_BOTOX, channel="telegram")

    metrics = compute_funnel_metrics()

    assert metrics["booked_revenue"] is None
    assert metrics["revenue"] is None
    assert metrics["recovered_completed_bookings"] is None
    assert "revenue_recovered" not in metrics
    assert "bookings_recovered" not in metrics


def test_funnel_metrics_counts_each_layer_on_its_own_population(isolated_leads_file, frozen_clock):
    quoted = record_price_quote(user_id="703", service_name=SERVICE_BOTOX, channel="telegram")
    booked = record_price_quote(user_id="704", service_name=SERVICE_BOTOX, channel="telegram")
    recovered = record_price_quote(user_id="705", service_name=SERVICE_BOTOX, channel="telegram")

    record_booking_request(lead_id=booked, contact_info="سارة 0770")
    leads_store.mark_followup_sent(lead_id=recovered, new_stage="1")
    record_booking_request(lead_id=recovered, contact_info="هدى 0771")

    FrozenDatetime.frozen_at = NOW + timedelta(hours=SILENCE_WINDOW_HOURS + 1)
    try:
        metrics = compute_funnel_metrics()
    finally:
        FrozenDatetime.frozen_at = NOW

    price = 120000
    assert quoted  # الصامتة وحدها في المقام
    assert metrics["qualified_leads"] == 3
    assert metrics["unbooked_leads"] == 1
    assert metrics["booking_requests"] == 2
    assert metrics["recovered_leads"] == 1

    assert metrics["potential_revenue"] == 3 * price
    assert metrics["requested_revenue"] == 2 * price
    assert metrics["recovered_requested_revenue"] == price


def test_report_names_every_layer_and_states_the_red_rule(isolated_leads_file, frozen_clock):
    record_price_quote(user_id="706", service_name=SERVICE_BOTOX, channel="telegram")

    output = render_report(compute_funnel_metrics())

    for layer in ("Potential Revenue", "Requested Revenue", "Booked Revenue", "Revenue:"):
        assert layer in output

    assert "غير متاح" in output
    assert "لا يُسمّى رقم «إيراداً» إلا عند الحضور" in output
    assert "Recovered Completed Booking" in output


# ------------------------------------------- 4) حالة الجلسة القديمة

def test_live_session_with_legacy_state_is_read_and_does_not_requote(isolated_sessions_file):
    """
    جلسة حيّة كُتبت بالاسم القديم وسط محادثة: "نعم" يجب أن تتقدّم إلى
    طلب بيانات التواصل، لا أن تسقط إلى idle فيُعاد تسعير الخدمة.
    """
    service = find_service("بوتوكس")
    isolated_sessions_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_sessions_file.write_text(
        json.dumps({"900": {
            "state": "awaiting_booking_confirmation",
            "service": service,
            "lead_id": "ld_جلسة_قديمة",
        }}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert session_store.get_session("900")["state"] == session_store.STATE_AWAITING_BOOKING_REPLY

    reply = handle_message(make_message("900", "نعم")).text

    assert "اسمك ورقم هاتفك" in reply
    assert session_store.get_session("900")["state"] == session_store.STATE_AWAITING_CONTACT_INFO
    # وlead_id الجلسة لم يُفقد أثناء الترجمة
    assert session_store.get_session("900")["lead_id"] == "ld_جلسة_قديمة"


def test_session_store_backs_up_once_before_first_write(isolated_sessions_file):
    backup = Path(session_store.BACKUP_FILE_STATUS_VOCABULARY)
    isolated_sessions_file.parent.mkdir(parents=True, exist_ok=True)
    isolated_sessions_file.write_text(
        json.dumps({"901": {"state": "awaiting_booking_confirmation", "service": None, "lead_id": None}}),
        encoding="utf-8",
    )
    content_before = isolated_sessions_file.read_bytes()

    assert not backup.exists()

    session_store.update_session("902", state=session_store.STATE_IDLE)

    assert backup.exists()
    assert backup.read_bytes() == content_before

    session_store.clear_session("903")
    assert backup.read_bytes() == content_before
