"""
اختبارات ترتيب الكتابة في مسار الحجز (التغيير #7، الجزء الثالث).

الملاحظة من تقرير الـAudit (القسم 3، «تناقض بين leads.csv و
sessions.json»): كان `clear_session` يسبق `record_booking_request`.
انقطاعُ تيار بين السطرين يترك جلسة idle بلا أي صف - الحجز يختفي كأنه
لم يقع، ولا شيء لاحقاً يكشفه.

بعد الترتيب الجديد أسوأ ما يقع بين السطرين هو جلسةٌ ما زالت تنتظر
بيانات التواصل بينما الصف مكتوب: العميلة تُسأل مرة أخرى، فترسل رقمها،
ويُحدَّث **نفس الصف** بنفس lead_id بلا صف ثانٍ.

«تُسأل مرة أخرى» خسارة محتملة؛ «اختفى الحجز» خسارة مؤكدة لا تُسترجَع.
"""

from datetime import datetime

import pytest

import business_logic
import events
import leads_store
from business_logic import handle_message
from channel_interface import IncomingMessage
from storage import session_store

REAL_PHONE = "07701234567"


def make_message(user_id: str, text: str) -> IncomingMessage:
    return IncomingMessage(
        channel="telegram", user_id=user_id, text=text,
        timestamp=datetime.now(), message_id=None,
    )


def say(user_id: str, text: str):
    return handle_message(make_message(user_id, text))


def at_contact_step(user_id: str):
    say(user_id, "كم سعر البوتوكس؟")
    say(user_id, "نعم")


def booking_rows():
    return [r for r in leads_store._read_all_rows_unlocked()
            if r["الحالة"] == leads_store.STATE_BOOKING_REQUESTED]


class PowerCut(RuntimeError):
    """انقطاع تيار مُصطنَع بين الكتابتين."""


def test_a_crash_after_the_row_is_written_does_not_lose_the_booking(monkeypatch):
    """
    الحالة التي وقع فيها الترتيب القديم: الصف يُكتب، ثم ينقطع التيار
    قبل مسح الجلسة. الحجز **موجود** - وهو ما يهم.
    """
    at_contact_step("u_cut")
    monkeypatch.setattr(
        session_store, "clear_session",
        lambda user_id: (_ for _ in ()).throw(PowerCut("انقطاع تيار مُصطنَع")),
    )

    with pytest.raises(PowerCut):
        say("u_cut", f"سارة {REAL_PHONE}")

    rows = booking_rows()
    assert len(rows) == 1
    assert rows[0]["بيانات التواصل"] == f"سارة {REAL_PHONE}"
    assert len(of_booking_events()) == 1


def of_booking_events():
    return [e for e in events.read_all() if e["event_type"] == events.BOOKING_REQUESTED]


def test_the_stale_session_after_such_a_crash_costs_only_one_extra_question(monkeypatch):
    """
    الجلسة بقيت تنتظر بيانات التواصل بعد الانقطاع. رسالة العميلة
    التالية تُحدِّث **نفس الصف** بنفس lead_id - لا صف ثانٍ ولا حجز
    مكرر.
    """
    at_contact_step("u_after")
    lead_id = session_store.get_session("u_after")["lead_id"]

    # ينقطع مرة واحدة ثم يعمل - بلا monkeypatch.undo(): الـundo يُلغي
    # كذلك تثبيتات العزل في conftest (نفس كائن monkeypatch للاختبار
    # كله)، فتصير الكتابة التالية على leads.csv وdata/sessions.json
    # الحقيقيين في جذر المشروع.
    real_clear = session_store.clear_session
    power = {"cut": True}

    def flaky_clear(user_id):
        if power["cut"]:
            power["cut"] = False
            raise PowerCut("انقطاع تيار مُصطنَع")
        return real_clear(user_id)

    monkeypatch.setattr(session_store, "clear_session", flaky_clear)

    with pytest.raises(PowerCut):
        say("u_after", f"سارة {REAL_PHONE}")

    # الجلسة ما زالت تنتظر - وهذه هي الكلفة كاملةً
    assert session_store.get_session("u_after")["state"] == \
        session_store.STATE_AWAITING_CONTACT_INFO

    say("u_after", f"سارة {REAL_PHONE}")

    rows = booking_rows()
    assert len(rows) == 1
    assert rows[0][leads_store.LEAD_ID_COLUMN] == lead_id
    assert session_store.get_session("u_after")["state"] == session_store.STATE_IDLE


def test_the_row_is_written_before_the_session_is_cleared(monkeypatch):
    """
    حارس الترتيب نفسه: لو عاد المسح ليسبق الكتابة لسقط هذا الاختبار،
    لا اختبارٌ عن انقطاع تيار نادر.
    """
    order = []

    real_record = business_logic.record_booking_request
    real_clear = session_store.clear_session

    def spy_record(**kwargs):
        order.append("record")
        return real_record(**kwargs)

    def spy_clear(user_id):
        order.append("clear")
        return real_clear(user_id)

    monkeypatch.setattr(business_logic, "record_booking_request", spy_record)
    monkeypatch.setattr(session_store, "clear_session", spy_clear)

    at_contact_step("u_order")
    order.clear()
    say("u_order", f"سارة {REAL_PHONE}")

    assert order == ["record", "clear"]


def test_the_happy_path_is_unchanged_by_the_reorder():
    """الترتيب تغيّر، والنتيجة لم تتغيّر: نفس الصف ونفس الرد ونفس الحدث."""
    at_contact_step("u_same")
    decision = say("u_same", f"سارة {REAL_PHONE}")

    assert decision.variant_id == "booking_request_ack.v1"
    assert decision.rule_decision == "confirm_booking"
    assert len(booking_rows()) == 1
    assert len(of_booking_events()) == 1

    session = session_store.get_session("u_same")
    assert session["state"] == session_store.STATE_IDLE
    assert session["service"] is None and session["lead_id"] is None
