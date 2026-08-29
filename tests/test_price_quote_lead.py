"""
اختبارات إنشاء الـLead لحظة عرض السعر (PRD F1/D1).

تغطي ثلاث طبقات:
  1) record_price_quote في leads_store: الإنشاء، الـidempotency لكل
     نيّة تجارية مفتوحة، والتفريق بين الخدمة/الهوية.
  2) record_booking_request وrecord_decline: تحديث نفس الصف عبر
     lead_id بدل إنشاء صف ثانٍ.
  3) business_logic.handle_message: أن lead_id المحفوظ في الجلسة
     يصل فعلاً من عرض السعر حتى الحجز أو الرفض أو التردد، وأن السقوط
     الآمن لـsave_lead يعمل حين لا تحمل الجلسة lead_id.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import leads_store
from leads_store import (
    FIELDNAMES,
    LEAD_ID_COLUMN,
    OUTCOME_ORGANIC,
    OUTCOME_PENDING,
    OUTCOME_RECOVERED,
    REASON_BOOKING_REQUESTED,
    REASON_DECLINED,
    REASON_HESITANT,
    REASON_PRICE_QUOTED,
    STATE_BOOKING_REQUESTED,
    STATE_DECLINED,
    STATE_PRICE_QUOTED,
    STATUS_REASON_COLUMN,
    record_booking_request,
    record_decline,
    record_price_quote,
)

import business_logic
from business_logic import handle_message
from channel_interface import IncomingMessage
from services import find_service
from storage import session_store

SERVICE_BOTOX = "حقن البوتوكس"
SERVICE_BOTOX_PRICE = "120,000 دينار"
SERVICE_LASER = "إزالة الشعر بالليزر (جلسة واحدة)"


# ----------------------------------------------------------------- أدوات

def write_csv(path, fieldnames, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_rows():
    return leads_store._read_all_rows()


def make_message(user_id: str, text: str, channel: str = "telegram") -> IncomingMessage:
    return IncomingMessage(channel=channel, user_id=user_id, text=text, timestamp=datetime.now())


class FrozenDatetime(datetime):
    """يجمّد datetime.now() على لحظة واحدة، قابلة للتقديم أثناء الاختبار."""
    frozen_at = datetime(2026, 8, 28, 14, 30, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.frozen_at


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(leads_store, "datetime", FrozenDatetime)
    return FrozenDatetime.frozen_at


# ------------------------------------------- 1) record_price_quote (leads_store)

def test_record_price_quote_writes_price_quoted_row():
    lead_id = record_price_quote(user_id="700", service_name=SERVICE_BOTOX, channel="telegram")

    rows = read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row[LEAD_ID_COLUMN] == lead_id
    assert row["الحالة"] == STATE_PRICE_QUOTED
    assert row[STATUS_REASON_COLUMN] == REASON_PRICE_QUOTED
    assert row["مرحلة المتابعة"] == "0"
    assert row["نتيجة المتابعة"] == OUTCOME_PENDING
    assert row["بيانات التواصل"] == ""


def test_record_price_quote_returns_prefixed_lead_id():
    lead_id = record_price_quote(user_id="701", service_name=SERVICE_BOTOX, channel="telegram")
    assert lead_id.startswith(leads_store.LEAD_ID_PREFIX)


def test_record_price_quote_snapshots_current_price():
    record_price_quote(user_id="702", service_name=SERVICE_BOTOX, channel="telegram")
    row = read_rows()[0]
    assert row["سعر الخدمة وقت الإنشاء"] == SERVICE_BOTOX_PRICE


def test_record_price_quote_idempotent_same_identity_same_service():
    first = record_price_quote(user_id="703", service_name=SERVICE_BOTOX, channel="telegram")
    second = record_price_quote(user_id="703", service_name=SERVICE_BOTOX, channel="telegram")

    assert first == second
    assert len(read_rows()) == 1


def test_record_price_quote_does_not_update_timestamp_on_reuse(frozen_clock, monkeypatch):
    first = record_price_quote(user_id="704", service_name=SERVICE_BOTOX, channel="telegram")
    original_stamp = read_rows()[0]["التاريخ والوقت"]

    monkeypatch.setattr(FrozenDatetime, "frozen_at", frozen_clock + timedelta(hours=2))
    second = record_price_quote(user_id="704", service_name=SERVICE_BOTOX, channel="telegram")

    assert second == first
    assert read_rows()[0]["التاريخ والوقت"] == original_stamp


def test_record_price_quote_new_row_for_different_service():
    first = record_price_quote(user_id="705", service_name=SERVICE_BOTOX, channel="telegram")
    second = record_price_quote(user_id="705", service_name=SERVICE_LASER, channel="telegram")

    assert first != second
    assert len(read_rows()) == 2


def test_record_price_quote_new_row_for_different_identity():
    base = record_price_quote(user_id="706", service_name=SERVICE_BOTOX, channel="telegram")
    diff_channel = record_price_quote(user_id="706", service_name=SERVICE_BOTOX, channel="whatsapp")
    diff_user = record_price_quote(user_id="707", service_name=SERVICE_BOTOX, channel="telegram")

    assert len({base, diff_channel, diff_user}) == 3
    assert len(read_rows()) == 3


def test_record_price_quote_new_row_after_lead_closed_by_booking():
    first = record_price_quote(user_id="708", service_name=SERVICE_BOTOX, channel="telegram")
    assert record_booking_request(lead_id=first, contact_info="سارة 0770") is True

    second = record_price_quote(user_id="708", service_name=SERVICE_BOTOX, channel="telegram")

    assert second != first
    assert len(read_rows()) == 2


def test_record_price_quote_picks_most_recent_open_lead_when_duplicates_exist(isolated_leads_file):
    older = {
        LEAD_ID_COLUMN: "ld_older", "التاريخ والوقت": "2026-08-20 09:00:00",
        "معرف العميل": "709", "القناة": "telegram", "الخدمة المطلوبة": SERVICE_BOTOX,
        "الحالة": STATE_PRICE_QUOTED, STATUS_REASON_COLUMN: REASON_PRICE_QUOTED,
        "بيانات التواصل": "", "سعر الخدمة وقت الإنشاء": SERVICE_BOTOX_PRICE,
        "مرحلة المتابعة": "0", "تاريخ آخر متابعة": "", "نتيجة المتابعة": "",
    }
    newer = dict(older, **{LEAD_ID_COLUMN: "ld_newer", "التاريخ والوقت": "2026-08-21 09:00:00"})
    write_csv(isolated_leads_file, FIELDNAMES, [older, newer])

    result = record_price_quote(user_id="709", service_name=SERVICE_BOTOX, channel="telegram")

    assert result == "ld_newer"
    assert len(read_rows()) == 2


# ------------------------------- 2) تحديث نفس الصف (record_booking_request / record_decline)

def test_record_booking_request_updates_same_row_and_marks_closed():
    lead_id = record_price_quote(user_id="710", service_name=SERVICE_BOTOX, channel="telegram")
    assert record_booking_request(lead_id=lead_id, contact_info="سارة 0770") is True

    rows = read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row[LEAD_ID_COLUMN] == lead_id
    assert row["الحالة"] == STATE_BOOKING_REQUESTED
    assert row[STATUS_REASON_COLUMN] == REASON_BOOKING_REQUESTED
    assert row["بيانات التواصل"] == "سارة 0770"
    assert row["نتيجة المتابعة"] == OUTCOME_ORGANIC


def test_record_booking_request_marks_recovered_after_followup():
    lead_id = record_price_quote(user_id="711", service_name=SERVICE_BOTOX, channel="telegram")
    assert leads_store.mark_followup_sent(lead_id=lead_id, new_stage="1") is True

    assert record_booking_request(lead_id=lead_id, contact_info="سارة 0770") is True

    row = read_rows()[0]
    assert row["نتيجة المتابعة"] == OUTCOME_RECOVERED
    assert leads_store.compute_funnel_metrics()["recovered_leads"] == 1


def test_record_booking_request_returns_false_for_unknown_lead_id():
    record_price_quote(user_id="712", service_name=SERVICE_BOTOX, channel="telegram")

    assert record_booking_request(lead_id="ld_غير_موجود", contact_info="سارة") is False
    assert record_booking_request(lead_id="", contact_info="سارة") is False

    row = read_rows()[0]
    assert row["الحالة"] == STATE_PRICE_QUOTED


def test_record_decline_moves_state_and_keeps_lead_followup_eligible():
    """
    الرفض الصريح صار حالة أولى الدرجة (§7)، لا سبباً في حقل جانبي.
    والصف يبقى مؤهلاً للمتابعة كما كان بالضبط (D-015 - تأجيل صريح).
    """
    lead_id = record_price_quote(user_id="713", service_name=SERVICE_BOTOX, channel="telegram")

    assert record_decline(lead_id) is True

    rows = read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["الحالة"] == STATE_DECLINED
    assert row["الحالة"] in leads_store.OPEN_STATES
    assert row[STATUS_REASON_COLUMN] == REASON_DECLINED
    assert row["نتيجة المتابعة"] == OUTCOME_PENDING


def test_price_quoted_lead_becomes_eligible_for_followup_after_window(frozen_clock, monkeypatch):
    lead_id = record_price_quote(user_id="714", service_name=SERVICE_BOTOX, channel="telegram")

    assert leads_store.get_leads_eligible_for_first_followup(hours_threshold=24) == []

    monkeypatch.setattr(FrozenDatetime, "frozen_at", frozen_clock + timedelta(hours=24, minutes=1))

    eligible = leads_store.get_leads_eligible_for_first_followup(hours_threshold=24)
    assert [r[LEAD_ID_COLUMN] for r in eligible] == [lead_id]


# ------------------------------------------- 3) business_logic.handle_message (تكامل)

def test_handle_message_price_inquiry_creates_lead_and_stores_id_in_session():
    reply = handle_message(make_message("800", "كم سعر البوتوكس؟")).text

    assert SERVICE_BOTOX_PRICE in reply
    rows = read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["الحالة"] == STATE_PRICE_QUOTED
    assert row[STATUS_REASON_COLUMN] == REASON_PRICE_QUOTED

    session = session_store.get_session("800")
    assert session["state"] == session_store.STATE_AWAITING_BOOKING_REPLY
    assert session["lead_id"] == row[LEAD_ID_COLUMN]


def test_handle_message_confirm_and_contact_info_updates_same_row():
    handle_message(make_message("801", "بوتوكس"))
    lead_id = session_store.get_session("801")["lead_id"]

    handle_message(make_message("801", "نعم"))
    handle_message(make_message("801", "سارة 0770 000 000"))

    rows = read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row[LEAD_ID_COLUMN] == lead_id
    assert row["الحالة"] == STATE_BOOKING_REQUESTED
    assert row[STATUS_REASON_COLUMN] == REASON_BOOKING_REQUESTED
    assert row["بيانات التواصل"] == "سارة 0770 000 000"

    assert session_store.get_session("801")["state"] == session_store.STATE_IDLE


def test_handle_message_decline_updates_same_row_no_new_lead():
    handle_message(make_message("802", "بوتوكس"))
    lead_id = session_store.get_session("802")["lead_id"]

    handle_message(make_message("802", "لا"))

    rows = read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row[LEAD_ID_COLUMN] == lead_id
    assert row["الحالة"] == STATE_DECLINED
    assert row[STATUS_REASON_COLUMN] == REASON_DECLINED

    assert session_store.get_session("802")["state"] == session_store.STATE_IDLE


def test_handle_message_hesitant_then_confirm_uses_same_lead():
    handle_message(make_message("803", "بوتوكس"))
    lead_id = session_store.get_session("803")["lead_id"]

    handle_message(make_message("803", "خلي أفكر"))
    assert session_store.get_session("803")["state"] == session_store.STATE_AWAITING_BOOKING_REPLY
    assert read_rows()[0][STATUS_REASON_COLUMN] == REASON_HESITANT
    # التردد إشارة لا حالة: الـLead يبقى price_quoted (لم تُجب بعد)
    assert read_rows()[0]["الحالة"] == STATE_PRICE_QUOTED

    handle_message(make_message("803", "نعم"))
    handle_message(make_message("803", "سارة 0770"))

    rows = read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row[LEAD_ID_COLUMN] == lead_id
    assert row["الحالة"] == STATE_BOOKING_REQUESTED


def test_handle_message_contact_info_without_session_lead_id_falls_back_to_save_lead():
    service = find_service("بوتوكس")
    session_store.update_session(
        "804", state=session_store.STATE_AWAITING_CONTACT_INFO, service=service
    )
    assert session_store.get_session("804")["lead_id"] is None

    handle_message(make_message("804", "سارة 0770"))

    rows = read_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["الحالة"] == STATE_BOOKING_REQUESTED
    assert row["بيانات التواصل"] == "سارة 0770"
    assert row[STATUS_REASON_COLUMN] == ""
