"""
اختبارات مهلة الجلسة (التغيير #7، الجزء الثاني - F9).

الثغرة التي تغلقها: لم يكن للجلسات عمر إطلاقاً. جلسة تنتظر جواباً بلا
رسالة تالية تبقى منتظرة للأبد، فتُقرأ رسالة العميلة بعد شهر جواباً على
سؤال ميت - وينمو data/sessions.json بلا حد بحالات ميتة.

أربع طبقات:
  1) الانتهاء نفسه: العمر، الحد، وما لا ينتهي.
  2) الأثر على المحادثة: الرسالة التالية تُعامَل جديدة، بصمت.
  3) الجلسات القديمة بلا طابع: mtime حدٌّ أعلى، فلا تسقط محادثة حيّة.
  4) النمو: المنتهية تُسقَط عند الكتابة.
"""

import json
from datetime import datetime, timedelta

import pytest

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


def age_session(user_id: str, hours: float):
    """يُقدّم طابع الجلسة الزمني دون انتظار حقيقي - بكتابة مباشرة."""
    with open(session_store.SESSIONS_FILE, encoding="utf-8") as f:
        sessions = json.load(f)
    stamp = datetime.now() - timedelta(hours=hours)
    sessions[user_id]["updated_at"] = stamp.isoformat(timespec="microseconds")
    with open(session_store.SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False)


def write_raw_sessions(payload: dict):
    """يكتب ملف جلسات مباشرة - لمحاكاة ملف كُتب قبل هذا التغيير."""
    path = session_store.SESSIONS_FILE
    import os
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


# =================================================== 1) الانتهاء

def test_the_ttl_is_named_and_equals_the_silence_window():
    """
    المساواة قرار موثّق لا مصادفة: عند تلك الساعة يكون الـLead قد
    عُومل صامتاً ودخل دورة المتابعة، فجلسة تدّعي بعدها محادثة جارية
    تناقض سجل الـLeads.
    """
    assert session_store.SESSION_TTL_HOURS == 24
    assert session_store.SESSION_TTL_HOURS == leads_store.SILENCE_WINDOW_HOURS


def test_a_fresh_waiting_session_is_not_expired():
    say("u_fresh", "كم سعر البوتوكس؟")
    assert session_store.get_session("u_fresh")["state"] == \
        session_store.STATE_AWAITING_BOOKING_REPLY


@pytest.mark.parametrize("hours,expected_state", [
    (1, session_store.STATE_AWAITING_BOOKING_REPLY),
    (23, session_store.STATE_AWAITING_BOOKING_REPLY),
    (25, session_store.STATE_IDLE),
    (24 * 30, session_store.STATE_IDLE),
])
def test_a_waiting_session_expires_past_the_ttl(hours, expected_state):
    user_id = f"u_age_{hours}"
    say(user_id, "كم سعر البوتوكس؟")
    age_session(user_id, hours)

    assert session_store.get_session(user_id)["state"] == expected_state


def test_the_contact_step_expires_too():
    """
    أخطر الحالتين: جلسة تنتظر بيانات تواصل بلا مهلة كانت تبتلع كل
    رسالة لاحقة إلى الأبد.
    """
    say("u_ci", "كم سعر البوتوكس؟")
    say("u_ci", "نعم")
    assert session_store.get_session("u_ci")["state"] == \
        session_store.STATE_AWAITING_CONTACT_INFO

    age_session("u_ci", 25)

    assert session_store.get_session("u_ci")["state"] == session_store.STATE_IDLE


def test_the_disambiguation_state_expires_too():
    say("u_dis", "كم سعر البوتوكس؟")
    age_session("u_dis", 25)
    assert session_store.get_session("u_dis")["state"] == session_store.STATE_IDLE


def test_an_expired_session_returns_every_default_field():
    """الجلسة المنتهية تُقرأ افتراضية كاملة - لا بقايا حقول من الميتة."""
    say("u_full", "كم سعر البوتوكس؟")
    say("u_full", "نعم")
    age_session("u_full", 100)

    session = session_store.get_session("u_full")
    assert session == dict(session_store.DEFAULT_SESSION)
    assert session["service"] is None
    assert session["lead_id"] is None
    assert session["provisional_name"] is None


def test_an_idle_session_never_expires_because_it_has_nothing_to_expire():
    say("u_idle", "كم سعر البوتوكس؟")
    say("u_idle", "لا")  # clear_session -> idle
    age_session("u_idle", 24 * 365)

    assert session_store.get_session("u_idle")["state"] == session_store.STATE_IDLE


def test_every_write_stamps_the_session():
    say("u_stamp", "كم سعر البوتوكس؟")
    stamped = session_store.get_session("u_stamp")["updated_at"]
    assert stamped and datetime.fromisoformat(stamped)

    session_store.clear_session("u_stamp")
    assert session_store.get_session("u_stamp")["updated_at"] is not None


def test_a_corrupt_timestamp_does_not_expire_the_session():
    """
    طابع تالف يعني «لا أعرف العمر»، و«لا أعرف» تُقرأ **غير منتهية**:
    إسقاط جلسة على شكّ يقطع محادثة حيّة.
    """
    say("u_bad", "كم سعر البوتوكس؟")
    with open(session_store.SESSIONS_FILE, encoding="utf-8") as f:
        sessions = json.load(f)
    sessions["u_bad"]["updated_at"] = "ليس تاريخاً"
    write_raw_sessions(sessions)

    assert session_store.get_session("u_bad")["state"] == \
        session_store.STATE_AWAITING_BOOKING_REPLY


# =================================================== 2) أثرها على المحادثة

def test_the_next_message_after_expiry_is_treated_as_brand_new():
    """
    جوهر الإصلاح: رسالة العميلة بعد شهر لا تُقرأ جواباً على سؤال ميت.
    «بوتوكس» تُنتج سعراً، لا محاولة تفسيرها كردّ حجز.
    """
    say("u_new", "كم سعر البوتوكس؟")
    say("u_new", "نعم")           # صارت تنتظر بيانات التواصل
    age_session("u_new", 24 * 30)

    decision = say("u_new", "كم سعر التقشير؟")

    assert decision.variant_id == "price_quote.v1"
    assert "التقشير الكيميائي" in decision.text


def test_expiry_is_silent_and_sends_nothing_extra():
    """
    الانتهاء صامت: لا صياغة له ولا رسالة. العميلة تتلقى جواب سؤالها
    وحده - رد واحد لرسالة واحدة.
    """
    say("u_silent", "كم سعر البوتوكس؟")
    age_session("u_silent", 100)

    decision = say("u_silent", "كم سعر البوتوكس؟")

    assert decision.variant_id == "price_quote.v1"


def test_an_expired_contact_step_cannot_swallow_a_phone_number_into_a_booking():
    """
    رقم يصل بعد انتهاء الجلسة ليس بيانات تواصل لسؤال ميت: لا يُنشئ
    حجزاً، ويُعامَل رسالة جديدة (بلا خدمة -> قائمة الخدمات).
    """
    say("u_late", "كم سعر البوتوكس؟")
    say("u_late", "نعم")
    age_session("u_late", 48)

    decision = say("u_late", f"سارة {REAL_PHONE}")

    assert decision.variant_id == "services_list.v1"
    rows = [r for r in leads_store._read_all_rows_unlocked()
            if r["الحالة"] == leads_store.STATE_BOOKING_REQUESTED]
    assert rows == []


def test_an_update_after_expiry_does_not_revive_the_dead_fields():
    """
    تحديث جزئي على جلسة منتهية كان سيُحييها بحقولها القديمة (خدمة
    قديمة، lead_id قديم) لو دُمج على المخزَّن بدل الافتراضي.
    """
    say("u_rev", "كم سعر البوتوكس؟")
    dead_lead = session_store.get_session("u_rev")["lead_id"]
    age_session("u_rev", 100)

    session_store.update_session("u_rev", provisional_name="سارة")

    session = session_store.get_session("u_rev")
    assert session["state"] == session_store.STATE_IDLE
    assert session["service"] is None
    assert session["lead_id"] is None and dead_lead is not None
    assert session["provisional_name"] == "سارة"


# =================================================== 3) جلسات بلا طابع

def test_a_legacy_session_written_just_now_is_not_expired():
    """
    جلسة كُتبت قبل هذا التغيير (بلا updated_at) يُشتقّ عمرها من mtime
    الملف - حدٌّ **أعلى** لعمرها الحقيقي. ملف كُتب للتو = جلسة حيّة،
    فلا تُقطع محادثة جارية بسبب هجرة (نفس مبدأ D-016).
    """
    write_raw_sessions({"u_legacy": {
        "state": "awaiting_contact_info",
        "service": {"name": "حقن البوتوكس", "keywords": ["بوتوكس"], "price": "1"},
        "lead_id": "ld_قديم",
    }})

    session = session_store.get_session("u_legacy")
    assert session["state"] == session_store.STATE_AWAITING_CONTACT_INFO
    assert session["lead_id"] == "ld_قديم"


def test_a_legacy_session_in_an_old_file_does_expire():
    """وحين يكون الملف نفسه قديماً، لا شيء يفلت من المهلة بلا نهاية."""
    import os
    write_raw_sessions({"u_old": {"state": "awaiting_contact_info",
                                  "service": None, "lead_id": "ld_قديم"}})
    old = (datetime.now() - timedelta(hours=100)).timestamp()
    os.utime(session_store.SESSIONS_FILE, (old, old))

    assert session_store.get_session("u_old")["state"] == session_store.STATE_IDLE


def test_a_missing_sessions_file_is_a_clean_start_not_an_expiry_error():
    assert session_store.get_session("u_none") == dict(session_store.DEFAULT_SESSION)


# =================================================== 4) نمو الملف

def test_expired_sessions_are_dropped_on_the_next_write():
    """
    data/sessions.json كان ينمو بلا حد بحالات ميتة. الإسقاط لا يغيّر
    أي سلوك: المنتهية تُقرأ افتراضية، والمحذوفة تُقرأ افتراضية كذلك.
    """
    say("u_keep", "كم سعر البوتوكس؟")
    say("u_drop", "كم سعر البوتوكس؟")
    age_session("u_drop", 100)

    session_store.update_session("u_keep", provisional_name="سارة")

    with open(session_store.SESSIONS_FILE, encoding="utf-8") as f:
        stored = json.load(f)
    assert "u_drop" not in stored
    assert "u_keep" in stored


def test_the_session_being_written_is_never_pruned_by_its_own_age():
    """استثناء الجلسة الجارية: كتابة عليها لا تُسقطها بعمرها القديم."""
    say("u_self", "كم سعر البوتوكس؟")
    age_session("u_self", 100)

    session_store.update_session("u_self", provisional_name="سارة")

    with open(session_store.SESSIONS_FILE, encoding="utf-8") as f:
        stored = json.load(f)
    assert stored["u_self"]["provisional_name"] == "سارة"
    assert stored["u_self"]["state"] == session_store.STATE_IDLE
