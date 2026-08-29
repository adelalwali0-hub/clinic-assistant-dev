"""
اختبارات فخ awaiting_contact_info (التغيير #7، الجزء الأول - F9).

الثغرة التي تغلقها: **أي** رسالة تصل والجلسة تنتظر بيانات التواصل
كانت تُؤخذ حرفياً كبيانات تواصل وتُنشئ صف حجز. «شنو يعني بالضبط؟»
كانت تُسجَّل طلب حجز ببيانات تواصل = نص السؤال نفسه.

هذا **تلفيق** بيانات داخل leads.csv - الملف الذي تفترض بوابة Gate A
أنه جدير بالثقة - لا مجرد فقدان بيانات كما في F1.

خمس طبقات:
  1) القاعدة بمعزل عن كل شيء: عدّ الأرقام.
  2) القبول: بيانات تواصل حقيقية بكل صيغها العراقية ما زالت تعمل.
  3) الرفض: لا صف، لا حدث، لا تغيير حالة.
  4) الاسم المبدئي: الاسم أولاً ثم الرقم = صف كامل، لا رقم بلا اسم.
  5) مثال الـAudit حرفياً، والحدود المعروفة للقاعدة.
"""

from datetime import datetime

import pytest

import contact_info
import events
import leads_store
import variants
from business_logic import handle_message
from channel_interface import IncomingMessage
from storage import session_store

SERVICE_BOTOX = "حقن البوتوكس"

# الشكل العراقي الكامل: 11 رقماً تبدأ بـ07
REAL_PHONE = "07701234567"

# سؤال العميلة في مثال الـAudit حرفياً
AUDIT_QUESTION = "شنو يعني بالضبط؟"


def make_message(user_id: str, text: str) -> IncomingMessage:
    return IncomingMessage(
        channel="telegram", user_id=user_id, text=text,
        timestamp=datetime.now(), message_id=None,
    )


def say(user_id: str, text: str):
    return handle_message(make_message(user_id, text))


def at_contact_step(user_id: str):
    """يوصل الجلسة إلى awaiting_contact_info عبر شجرة القرار الحقيقية."""
    say(user_id, "كم سعر البوتوكس؟")
    say(user_id, "نعم")
    assert session_store.get_session(user_id)["state"] == \
        session_store.STATE_AWAITING_CONTACT_INFO


def all_rows():
    return leads_store._read_all_rows_unlocked()


def booking_rows():
    return [r for r in all_rows() if r["الحالة"] == leads_store.STATE_BOOKING_REQUESTED]


def of_type(event_type):
    return [e for e in events.read_all() if e["event_type"] == event_type]


# =================================================== 1) القاعدة وحدها

@pytest.mark.parametrize("text,expected", [
    (REAL_PHONE, True),                        # 11 رقماً
    ("سارة 07701234567", True),                # اسم + رقم
    ("سارة 0770 123 4567", True),              # بمسافات
    ("0770-123-4567", True),                   # بشرطات
    ("+964 770 123 4567", True),               # صيغة دولية
    ("009647701234567", True),                 # صيغة دولية أخرى
    ("٠٧٧٠١٢٣٤٥٦٧", True),                     # أرقام هندية عربية
    ("07701234567 سارة", True),                # الرقم أولاً
    ("014567890", True),                       # أرضي ببغداد - 9 أرقام
    (AUDIT_QUESTION, False),                   # صفر أرقام
    ("سارة", False),
    ("سارة 0770", False),                      # محاولة ناقصة - 4 أرقام
    ("عندي 2 جلسات", False),
    ("", False),
])
def test_the_acceptance_rule_counts_digits(text, expected):
    assert contact_info.looks_like_contact_info(text) is expected


def test_the_threshold_is_nine_digits_and_is_named():
    """
    الحد قيمة مسمّاة لا رقم سحري في شرط: تحته لا يوجد رقم تواصل عراقي
    صالح، وفوقه يبدأ الرفض الخاطئ.
    """
    assert contact_info.MIN_CONTACT_DIGITS == 9
    assert contact_info.looks_like_contact_info("1" * 9) is True
    assert contact_info.looks_like_contact_info("1" * 8) is False


# =================================================== 2) القبول لم يضعف

@pytest.mark.parametrize("contact", [
    REAL_PHONE,
    f"سارة {REAL_PHONE}",
    "سارة 0770 123 4567",
    "+964 770 123 4567",
    "٠٧٧٠١٢٣٤٥٦٧",
])
def test_real_contact_data_still_books(contact):
    """
    القاعدة لا تُضعِف الالتقاط: العميلة التي وصلت إلى هنا وافقت على
    الحجز فعلاً، ورفض بياناتها الحقيقية أسوأ من الثغرة نفسها.
    """
    user_id = f"u_ok_{abs(hash(contact))}"
    at_contact_step(user_id)

    decision = say(user_id, contact)

    assert decision.variant_id == "booking_request_ack.v1"
    assert decision.rule_decision == "confirm_booking"
    rows = booking_rows()
    assert len(rows) == 1
    assert rows[0]["بيانات التواصل"] == contact
    assert session_store.get_session(user_id)["state"] == session_store.STATE_IDLE


def test_the_booking_path_is_byte_for_byte_what_it_was():
    """المسار السعيد لم يتغيّر: نفس الصياغة، نفس الصف، نفس الحدث."""
    at_contact_step("u_happy")
    decision = say("u_happy", f"سارة {REAL_PHONE}")

    assert decision.text == variants.render(
        "booking_request_ack.v1", service_name=SERVICE_BOTOX,
        center_name=__import__("services").CENTER_NAME,
    )
    assert len(of_type(events.BOOKING_REQUESTED)) == 1


# =================================================== 3) الرفض لا يكتب شيئاً

def test_a_question_at_the_contact_step_creates_no_booking():
    """مثال الـAudit حرفياً - وهو جوهر F9."""
    at_contact_step("u_q")
    decision = say("u_q", AUDIT_QUESTION)

    assert decision.variant_id == "contact_info_reprompt.v1"
    assert decision.rule_decision == "contact_info_missing"
    assert booking_rows() == []
    assert of_type(events.BOOKING_REQUESTED) == []


def test_the_question_text_never_reaches_the_contact_column():
    """
    التلفيق بعينه: نص سؤال العميلة مكتوباً في خانة «بيانات التواصل».
    """
    at_contact_step("u_col")
    say("u_col", AUDIT_QUESTION)

    for row in all_rows():
        assert AUDIT_QUESTION not in row["بيانات التواصل"]
        assert row["بيانات التواصل"] == ""


def test_rejection_leaves_the_session_exactly_where_it_was():
    at_contact_step("u_stay")
    before = session_store.get_session("u_stay")

    say("u_stay", "ليش تسألون عن الرقم؟")

    after = session_store.get_session("u_stay")
    assert after["state"] == session_store.STATE_AWAITING_CONTACT_INFO
    assert after["lead_id"] == before["lead_id"]
    assert after["service"] == before["service"]


def test_rejection_emits_no_transition_event_only_the_outgoing_one():
    """
    لا حدث انتقال: لم يقع انتقال. إعادة السؤال رسالة صادرة، وحدثها هو
    RESPONSE_SENT الذي يُصدره مسار الإرسال - لا حدث جديد لهذه الحالة.
    """
    at_contact_step("u_ev")
    before = len(events.read_all())

    say("u_ev", AUDIT_QUESTION)

    assert len(events.read_all()) == before  # handle_message وحده لا يُرسل


def test_the_lead_row_stays_open_and_followable_after_a_rejection():
    """
    الصف يبقى price_quoted - مؤهلاً للمتابعة. الرفض ليس إغلاقاً، ولا
    يجوز أن يخرج الـLead من دورة المتابعة لأن رسالة لم تُفهَم.
    """
    at_contact_step("u_open")
    say("u_open", AUDIT_QUESTION)

    rows = all_rows()
    assert len(rows) == 1
    assert rows[0]["الحالة"] == leads_store.STATE_PRICE_QUOTED


def test_the_reprompt_is_attributable_to_the_lead():
    """
    «كم مرة أعدنا سؤال هذه العميلة؟» يُجاب من events.jsonl وحده: الرد
    يحمل lead_id الجلسة، فيحمله RESPONSE_SENT بعده.
    """
    at_contact_step("u_attr")
    session_lead = session_store.get_session("u_attr")["lead_id"]

    decision = say("u_attr", AUDIT_QUESTION)

    assert decision.lead_id == session_lead
    assert decision.lead_id.startswith("ld_")


def test_a_partial_number_is_rejected_and_not_kept_as_a_name():
    """
    «سارة 0770» محاولة رقم ناقصة لا اسم. لا تُحفَظ اسماً مبدئياً حتى
    لا تمحو اسماً صحيحاً أرسلته قبلها.
    """
    at_contact_step("u_part")
    say("u_part", "سارة")
    say("u_part", "0770")

    assert session_store.get_session("u_part")["provisional_name"] == "سارة"
    assert booking_rows() == []


# =================================================== 4) الاسم المبدئي

def test_a_name_then_a_number_produces_one_complete_row():
    """
    الحالة التي أضيف لأجلها الاسم المبدئي: صف برقم بلا اسم بيانات
    منقوصة تعمل عليها العيادة يدوياً.
    """
    at_contact_step("u_two")

    first = say("u_two", "سارة")
    assert first.variant_id == "contact_info_reprompt.v1"
    assert booking_rows() == []
    assert session_store.get_session("u_two")["provisional_name"] == "سارة"

    second = say("u_two", REAL_PHONE)

    assert second.variant_id == "booking_request_ack.v1"
    rows = booking_rows()
    assert len(rows) == 1
    assert rows[0]["بيانات التواصل"] == f"سارة {REAL_PHONE}"


def test_the_name_comes_first_in_the_stored_contact_string():
    """نفس ترتيب ask_contact_info.v1 («اسمك ورقم هاتفك») في الحالتين."""
    at_contact_step("u_order")
    say("u_order", "أم محمد")
    say("u_order", REAL_PHONE)

    assert booking_rows()[0]["بيانات التواصل"] == f"أم محمد {REAL_PHONE}"


def test_a_later_name_overwrites_an_earlier_one():
    """آخر ما أرسلته هو ما تقصده - لا تراكم أسماء."""
    at_contact_step("u_ow")
    say("u_ow", "سارة")
    say("u_ow", "لا، اسمي هدى")
    say("u_ow", REAL_PHONE)

    assert booking_rows()[0]["بيانات التواصل"] == f"لا، اسمي هدى {REAL_PHONE}"


def test_the_audit_question_is_overwritten_by_whatever_comes_next():
    """
    «شنو يعني بالضبط؟» تُحفَظ اسماً مبدئياً - ولا ضرر: لا تكتب صفاً،
    ويكتبها فوقها ما ترسله بعدها.
    """
    at_contact_step("u_ovw")
    say("u_ovw", AUDIT_QUESTION)
    say("u_ovw", "سارة")
    say("u_ovw", REAL_PHONE)

    contact = booking_rows()[0]["بيانات التواصل"]
    assert contact == f"سارة {REAL_PHONE}"
    assert AUDIT_QUESTION not in contact


def test_a_message_carrying_both_name_and_number_ignores_the_provisional():
    """من أرسلت الاثنين معاً لا يُضاف إلى بياناتها اسم مبدئي قديم."""
    at_contact_step("u_both")
    say("u_both", "سارة")
    say("u_both", f"هدى {REAL_PHONE}")

    # الاسم المبدئي يسبق، والرسالة كاملة كما أرسلتها - لا حذف لأي منهما
    assert booking_rows()[0]["بيانات التواصل"] == f"سارة هدى {REAL_PHONE}"


def test_the_provisional_name_is_cleared_after_the_booking():
    at_contact_step("u_clr")
    say("u_clr", "سارة")
    say("u_clr", REAL_PHONE)

    session = session_store.get_session("u_clr")
    assert session["state"] == session_store.STATE_IDLE
    assert session.get("provisional_name") is None


def test_the_provisional_name_never_reaches_a_row_on_its_own():
    """اسم بلا رقم لا يُكتب في أي صف - العيادة لا تستطيع الاتصال باسم."""
    at_contact_step("u_alone")
    say("u_alone", "سارة")

    assert booking_rows() == []
    for row in all_rows():
        assert row["بيانات التواصل"] == ""


# =================================================== 5) الحدود المعروفة

def test_a_number_written_in_words_is_rejected_as_documented():
    """
    حدّ معروف ومذكور في ترويسة contact_info: رقم مكتوب بالحروف
    يُرفَض. الاختبار يثبّت الحد بدل أن يُكتشَف في الإنتاج.
    """
    at_contact_step("u_words")
    decision = say("u_words", "صفر سبعة سبعة صفر واحد اثنين")

    assert decision.variant_id == "contact_info_reprompt.v1"
    assert booking_rows() == []


def test_a_subject_change_at_the_contact_step_is_a_known_gap():
    """
    ثغرة معروفة ومؤجَّلة (D-019): تغيير الموضوع هنا لا يحمل أرقاماً
    فيتلقى إعادة السؤال لا سعراً. الاختبار يوثّق السلوك الفعلي حتى
    يُلاحَظ تغيّره عمداً لا سهواً.
    """
    at_contact_step("u_switch")
    decision = say("u_switch", "طيب كم سعر التقشير؟")

    assert decision.variant_id == "contact_info_reprompt.v1"
    assert len(all_rows()) == 1  # لا Lead ثانٍ للتقشير


def test_the_reprompt_text_shows_the_expected_shape():
    """المثال في النص هو ما يقصر دورة إعادة السؤال - فليبقَ فيه."""
    assert variants.render("contact_info_reprompt.v1") == (
        "حتى أكمل حجزك أحتاج رقم هاتفك 🌸\n"
        "ترسلين اسمك ورقمك في رسالة وحدة، مثل: سارة 07701234567"
    )
