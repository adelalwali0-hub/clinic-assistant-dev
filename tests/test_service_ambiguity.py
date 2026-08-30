"""
اختبارات كشف الغموض في الخدمة (التغيير #6 - F6/S2/D5).

الثغرة التي تغلقها: رسالة تطابق أكثر من خدمة كانت تُسعَّر بسعر **أولى**
المطابقات بترتيب الإعداد - اختيار صامت لا يظهر في أي سجل ولا تعرف
العميلة أنه وقع.

الإعداد الحي لا يحتوي غموضاً اليوم، ولا يصح أن يُصنَع فيه غموض من أجل
اختبار: هو بيانات عيادة حقيقية. لذلك كل اختبار هنا يستبدل
`services.SERVICES` بإعداد ثلاثي الليزر عبر monkeypatch - الاستبدال
يزول بنهاية الاختبار، ولا يُلمَس config/clinic_config.json إطلاقاً.

الاستبدال على `services.SERVICES` وحده يكفي: `find_services` و
`find_service_by_name` و`services_list_text` تقرأ المتغيّر العام لحظة
الاستدعاء، و`leads_store._lookup_current_price` تستورده داخل جسمها.

ست طبقات:
  1) التعدد: أكثر من مطابقة = سؤال، لا سعر ولا صف.
  2) الحدث: AMBIGUITY_ASKED بشكله وحمولته.
  3) الحسم: بالرقم، بالكلمة المفتاحية، بالاسم، وبتغيير الموضوع.
  4) عدم الحسم: إعادة السؤال.
  5) المطابقة بحدود الكلمة: بالليزر/لليزر نعم، ليزرات لا.
  6) التسلسل الكامل: غموض -> سعر -> طلب حجز، من events.jsonl وحده.
"""

from datetime import datetime

import pytest

import business_logic
import events
import leads_store
import services
import variants
from business_logic import handle_message
from channel_interface import IncomingMessage
from storage import session_store

# ثلاث خدمات تشترك في كلمة «ليزر» - وهذا بالضبط شكل الغموض في عيادة
# حقيقية: نفس التقنية بمواضع مختلفة وأسعار مختلفة جداً (20 ألفاً مقابل
# 120 ألفاً)، فالاختيار الصامت بأولى المطابقات ليس خطأً صغيراً.
#
# الكلمة المشتركة «ليزر» **ليست** في أي اسم عن قصد: لو كانت، لصار كل
# جواب باسم كامل غامضاً من جديد، ولما أمكن اختبار الحسم بالاسم أصلاً.
LASER_FACE = "إزالة شعر الوجه"
LASER_FULL_BODY = "إزالة شعر الجسم كامل"
LASER_UNDERARM = "إزالة شعر تحت الإبط"

THREE_LASER_SERVICES = [
    {
        "name": LASER_FACE,
        "keywords": ["ليزر", "وجه"],
        "price": "30,000 دينار",
    },
    {
        "name": LASER_FULL_BODY,
        "keywords": ["ليزر", "جسم كامل", "بادي"],
        "price": "120,000 دينار",
    },
    {
        "name": LASER_UNDERARM,
        "keywords": ["ليزر", "ابط", "تحت الابط"],
        "price": "20,000 دينار",
    },
    {
        "name": "حقن البوتوكس",
        "keywords": ["بوتوكس", "botox"],
        "price": "150,000 دينار",
    },
]

# كل خدمات الليزر الثلاث تحمل هذه الكلمة، ولا تحملها الرابعة.
AMBIGUOUS_TEXT = "كم سعر الليزر؟"
LASER_NAMES = [LASER_FACE, LASER_FULL_BODY, LASER_UNDERARM]


@pytest.fixture(autouse=True)
def three_laser_catalog(monkeypatch):
    """
    إعداد ثلاثي الليزر بدل الإعداد الحي - لكل اختبار في هذا الملف.
    autouse لأن كل اختبار هنا بلا استثناء يحتاجه، ونسيانه في واحد منها
    كان سيجعله يمرّ على بيانات العيادة الحقيقية.
    """
    monkeypatch.setattr(services, "SERVICES", THREE_LASER_SERVICES)
    # «ليزر» وحدها تطابق ثلاثاً: شرط كل اختبار أدناه، مثبَّت هنا مرة
    # واحدة حتى لا يمرّ الملف كله بصمت لو انكسرت المطابقة نفسها.
    assert len(services.find_services(AMBIGUOUS_TEXT)) == 3
    return THREE_LASER_SERVICES


def make_message(user_id: str, text: str) -> IncomingMessage:
    return IncomingMessage(
        channel="telegram", user_id=user_id, text=text,
        timestamp=datetime.now(), message_id=None,
    )


def say(user_id: str, text: str):
    return handle_message(make_message(user_id, text))


def of_type(event_type):
    return [e for e in events.read_all() if e["event_type"] == event_type]


def all_rows():
    return leads_store._read_all_rows_unlocked()


# =================================================== 1) التعدد يمنع الحسم

def test_one_match_still_quotes_the_price_unchanged():
    """المسار القديم لم يتغيّر: مطابقة وحيدة = سعر فوراً."""
    decision = say("u_one", "كم سعر البوتوكس؟")

    assert decision.variant_id == "price_quote.v1"
    assert decision.rule_decision == "price_inquiry"
    assert "150,000 دينار" in decision.text
    assert session_store.get_session("u_one")["state"] == \
        session_store.STATE_AWAITING_BOOKING_REPLY


def test_zero_matches_still_lists_the_services_unchanged():
    """المسار القديم الآخر لم يتغيّر: لا مطابقة = قائمة الخدمات."""
    decision = say("u_zero", "شلونكم؟")

    assert decision.variant_id == "services_list.v1"
    assert decision.rule_decision == "other"
    assert decision.lead_id is None
    assert session_store.get_session("u_zero")["state"] == session_store.STATE_IDLE


def test_multiple_matches_ask_instead_of_picking_the_first():
    """
    جوهر التغيير: ثلاث مطابقات لا تُحسم بأولاها. لو عادت الثغرة لصار
    الرد price_quote.v1 بسعر «ليزر الوجه» - أول عنصر في الإعداد.
    """
    decision = say("u_amb", AMBIGUOUS_TEXT)

    assert decision.variant_id == "ambiguity_question.v1"
    assert decision.rule_decision == "ambiguous_service"
    assert decision.lead_id is None
    for name in LASER_NAMES:
        assert name in decision.text


def test_the_question_carries_names_and_no_price_at_all():
    """
    سؤال التوضيح **لا يحمل سعراً**. رسالة تحمل سعراً هي رد بالسعر مهما
    كانت نيّتها - وهنا لم تُحسَم الخدمة بعد ولم يُنشأ Lead، فتصير
    العميلة مسعَّرة بلا أثر: نفس الفراغ الذي جاء التغيير ليغلقه.
    """
    text = say("u_price_free", AMBIGUOUS_TEXT).text

    for service in THREE_LASER_SERVICES:
        assert service["price"] not in text
    assert "دينار" not in text
    # وفي القالب نفسه، لا في هذه الصياغة وحدها
    assert "{price" not in variants.get("ambiguity_question.v1").template
    assert "price" not in variants.get("ambiguity_reprompt.v1").template


def test_the_question_writes_no_lead_row_and_no_lead_event():
    """
    Option B بكلفتها المقبولة (D-017): لا صف في leads.csv عند سؤال
    التوضيح. الـLead يُنشأ عند السعر وحده.
    """
    say("u_norow", AMBIGUOUS_TEXT)

    assert all_rows() == []
    assert of_type(events.LEAD_CREATED) == []
    assert of_type(events.PRICE_QUOTED) == []


def test_the_session_moves_to_the_waiting_state_with_the_options_it_showed():
    session_state = say("u_state", AMBIGUOUS_TEXT) and session_store.get_session("u_state")

    assert session_state["state"] == session_store.STATE_AWAITING_SERVICE_DISAMBIGUATION
    assert session_state["service_options"] == LASER_NAMES
    assert session_state["service"] is None
    assert session_state["lead_id"] is None


def test_the_options_are_numbered_in_config_order():
    """
    الترقيم يطابق ترتيب الإعداد، وثباته هو ما يجعل «2» تعني نفس الخدمة
    في كل مرة.
    """
    assert services.service_options_text(LASER_NAMES) == (
        f"1. {LASER_FACE}\n2. {LASER_FULL_BODY}\n3. {LASER_UNDERARM}"
    )


# =================================================== 2) الحدث

def test_ambiguity_asked_shape_and_payload():
    say("u_ev", AMBIGUOUS_TEXT)

    asked = of_type(events.AMBIGUITY_ASKED)
    assert len(asked) == 1
    event = asked[0]

    # لا Lead بعد - المعرّف فارغ لا مختلَق
    assert event["lead_id"] == ""
    assert event["channel"] == "telegram"
    assert event["variant_id"] is None  # انتقال حالة لا رسالة
    assert event["payload"] == {
        "user_id": "u_ev",
        "candidates": LASER_NAMES,
        "candidate_count": 3,
        "source": "keyword_multiplicity",
    }


def test_ambiguity_asked_never_copies_the_raw_message_text():
    """سجل بالإلحاق فقط لا يُحذف منه شيء - فلا يُنسَخ إليه نص العميلة."""
    say("u_raw", "ابغى ليزر ورقمي 07701234567")

    payload = of_type(events.AMBIGUITY_ASKED)[0]["payload"]
    assert "07701234567" not in str(payload)
    assert "ابغى" not in str(payload)


def test_ambiguity_asked_is_emitted_once_per_ambiguity_not_per_attempt():
    """
    إعادة السؤال ليست غموضاً جديداً: حدث ثانٍ كان سيجعل عدّ حالات
    الغموض يعدّ محاولات الفهم.
    """
    say("u_once", AMBIGUOUS_TEXT)
    say("u_once", "منو؟")
    say("u_once", "ما فهمت")

    assert len(of_type(events.AMBIGUITY_ASKED)) == 1


# =================================================== 3) الحسم

@pytest.mark.parametrize("answer,expected_name,expected_price", [
    ("1", LASER_FACE, "30,000 دينار"),
    ("2", LASER_FULL_BODY, "120,000 دينار"),
    ("3", LASER_UNDERARM, "20,000 دينار"),
    ("٢", LASER_FULL_BODY, "120,000 دينار"),   # رقم هندي عربي
    (" 3 ", LASER_UNDERARM, "20,000 دينار"),   # مسافات حول الرقم
])
def test_a_bare_number_resolves_to_the_option_it_showed(answer, expected_name, expected_price):
    say("u_num", AMBIGUOUS_TEXT)
    decision = say("u_num", answer)

    assert decision.variant_id == "price_quote.v1"
    assert expected_name in decision.text
    assert expected_price in decision.text
    assert decision.lead_id and decision.lead_id.startswith("ld_")


def test_a_number_outside_the_range_does_not_resolve():
    say("u_range", AMBIGUOUS_TEXT)
    decision = say("u_range", "7")

    assert decision.variant_id == "ambiguity_reprompt.v1"
    assert all_rows() == []


def test_a_keyword_of_one_candidate_resolves_it():
    say("u_kw", AMBIGUOUS_TEXT)
    decision = say("u_kw", "تحت الابط")

    assert decision.variant_id == "price_quote.v1"
    assert LASER_UNDERARM in decision.text


def test_the_full_service_name_resolves_it():
    """الاسم نفسه جواب مقبول - القائمة تعرض أسماء، فمن ينسخ اسماً يُفهَم."""
    say("u_name", AMBIGUOUS_TEXT)
    decision = say("u_name", LASER_FULL_BODY)

    assert decision.variant_id == "price_quote.v1"
    assert "120,000 دينار" in decision.text


def test_resolution_creates_exactly_one_lead_row_at_the_price():
    """
    الصف يُكتب عند السعر لا قبله: سؤال وجواب واحد = صف واحد، لا صفران
    ولا صفر.
    """
    say("u_row", AMBIGUOUS_TEXT)
    assert all_rows() == []

    decision = say("u_row", "2")

    rows = all_rows()
    assert len(rows) == 1
    assert rows[0]["الخدمة المطلوبة"] == LASER_FULL_BODY
    assert rows[0][leads_store.LEAD_ID_COLUMN] == decision.lead_id
    assert len(of_type(events.LEAD_CREATED)) == 1


def test_resolution_clears_the_stored_options_and_opens_the_booking_reply():
    say("u_clear", AMBIGUOUS_TEXT)
    say("u_clear", "1")

    session = session_store.get_session("u_clear")
    assert session["state"] == session_store.STATE_AWAITING_BOOKING_REPLY
    assert session["service"]["name"] == LASER_FACE
    assert (session.get("service_options") or []) == []


def test_changing_the_subject_mid_clarification_answers_the_new_subject():
    """
    العميلة سُئلت عن الليزر ثم كتبت «بوتوكس»: غيّرت سؤالها. قراءة
    جوابها داخل قائمة لم تعد تعنيها كانت ستردّ بسعر ليزر على سؤال عن
    البوتوكس.
    """
    say("u_switch", AMBIGUOUS_TEXT)
    decision = say("u_switch", "طيب كم سعر البوتوكس؟")

    assert decision.variant_id == "price_quote.v1"
    assert "حقن البوتوكس" in decision.text
    assert "150,000 دينار" in decision.text
    assert all_rows()[0]["الخدمة المطلوبة"] == "حقن البوتوكس"


def test_a_stored_option_gone_from_the_catalog_falls_to_the_catalog_rule(monkeypatch):
    """
    خدمة حُذفت من الإعداد وسط المحادثة: الرقم الذي يشير إليها لا يُسعَّر
    ولا يُختلَق له سعر - يسقط إلى قاعدة الكتالوج.
    """
    say("u_gone", AMBIGUOUS_TEXT)
    monkeypatch.setattr(
        services, "SERVICES",
        [s for s in THREE_LASER_SERVICES if s["name"] != LASER_FULL_BODY],
    )

    decision = say("u_gone", "2")  # كان «ليزر الجسم كامل»

    assert decision.variant_id == "ambiguity_reprompt.v1"
    assert all_rows() == []


# =================================================== 4) عدم الحسم

def test_an_unrecognized_answer_reprompts_with_the_same_options():
    say("u_re", AMBIGUOUS_TEXT)
    decision = say("u_re", "ما فهمت شنو تقصدين")

    assert decision.variant_id == "ambiguity_reprompt.v1"
    assert decision.rule_decision == "ambiguous_service"
    assert decision.lead_id is None
    for name in LASER_NAMES:
        assert name in decision.text
    assert all_rows() == []
    # والجلسة تبقى في نفس الانتظار بنفس الخيارات
    session = session_store.get_session("u_re")
    assert session["state"] == session_store.STATE_AWAITING_SERVICE_DISAMBIGUATION
    assert session["service_options"] == LASER_NAMES


def test_an_answer_matching_two_candidates_reprompts_and_does_not_pick_one():
    """«ليزر» من جديد ما زالت تطابق الثلاث - وما زالت لا تُحسَم."""
    say("u_re2", AMBIGUOUS_TEXT)
    decision = say("u_re2", "ليزر")

    assert decision.variant_id == "ambiguity_reprompt.v1"
    assert all_rows() == []


def test_the_reprompt_carries_no_price_either():
    say("u_re3", AMBIGUOUS_TEXT)
    text = say("u_re3", "هاه؟").text

    for service in THREE_LASER_SERVICES:
        assert service["price"] not in text
    assert "دينار" not in text


def test_a_new_ambiguity_mid_clarification_asks_a_fresh_question(monkeypatch):
    """
    تغيير الموضوع إلى موضوع غامض هو غموض **جديد**: قائمة جديدة، جلسة
    محدَّثة بها، وحدث AMBIGUITY_ASKED ثانٍ بمرشَّحين مختلفين.
    """
    two_peels = THREE_LASER_SERVICES + [
        {"name": "تقشير كيميائي", "keywords": ["تقشير"], "price": "50,000 دينار"},
        {"name": "تقشير كريستالي", "keywords": ["تقشير", "كرستال"],
         "price": "70,000 دينار"},
    ]
    monkeypatch.setattr(services, "SERVICES", two_peels)

    say("u_fresh", AMBIGUOUS_TEXT)
    decision = say("u_fresh", "طيب التقشير؟")

    assert decision.variant_id == "ambiguity_question.v1"
    assert session_store.get_session("u_fresh")["service_options"] == [
        "تقشير كيميائي", "تقشير كريستالي",
    ]
    asked = of_type(events.AMBIGUITY_ASKED)
    assert len(asked) == 2
    assert asked[1]["payload"]["candidates"] == ["تقشير كيميائي", "تقشير كريستالي"]
    assert all_rows() == []


# =================================================== 5) المطابقة بحدود الكلمة

@pytest.mark.parametrize("text,should_match", [
    ("ليزر", True),
    ("بالليزر", True),      # سابقة «بال»
    ("لليزر", True),        # سابقة «لل»
    ("والليزر", True),      # سابقة «وال»
    ("الليزر", True),       # «ال» التعريف تُزال في التوحيد
    ("ليزرات", False),      # لاحقة - المطابقة تنتهي عند نهاية الوحدة
    ("ليزري", False),
    ("سليزر", False),       # «س» ليست من مجموعة السوابق
])
def test_clitic_prefixes_match_but_suffixes_do_not(text, should_match):
    matched = [s["name"] for s in services.find_services(f"كم سعر {text}؟")]
    assert (matched == LASER_NAMES) is should_match


@pytest.mark.parametrize("text,keyword,expected", [
    ("يلا نشوف", "لا", False),      # كلمة من حرفين: مساواة فقط
    ("لا شكرا", "لا", True),
    ("بلا زحمة", "لا", False),
    ("نعم اكيد", "نعم", True),
    ("منعم", "نعم", False),         # «م» ليست من مجموعة السوابق
])
def test_short_keywords_get_no_clitic_allowance(text, keyword, expected):
    import matching
    assert matching.matches(text, keyword) is expected


# =================================================== 6) الثبات والتسلسل

@pytest.mark.parametrize("ai_intent", [None, "confirm_booking", "decline", "hesitant",
                                       "price_inquiry", "شيء غريب"])
def test_the_ambiguity_branches_ignore_ai_intent(ai_intent):
    """
    كشف الغموض قرار قواعد ثابتة بالكامل (Phase 3B تحصر تأثير AI في
    ثلاث نوايا داخل awaiting_booking_reply وحدها). أي قيمة لـai_intent
    تُنتج نفس القرار حرفياً - في السؤال وفي الحسم معاً.
    """
    user_id = f"u_ai_{abs(hash(str(ai_intent)))}"

    asked = handle_message(make_message(user_id, AMBIGUOUS_TEXT), ai_intent=ai_intent)
    assert asked.variant_id == "ambiguity_question.v1"
    assert asked.rule_decision == "ambiguous_service"

    resolved = handle_message(make_message(user_id, "3"), ai_intent=ai_intent)
    assert resolved.variant_id == "price_quote.v1"
    assert LASER_UNDERARM in resolved.text


def test_full_sequence_ambiguity_then_price_then_booking_from_events_alone():
    """
    المحادثة الكاملة مقروءة من events.jsonl وحده: سُئلت، اختارت،
    سُعِّرت، طلبت الحجز - وترتيب الأحداث يحكي القصة بلا leads.csv وبلا
    الجلسة.
    """
    for text in (AMBIGUOUS_TEXT, "2", "نعم", "سارة 0770 000 000"):
        say("u_seq", text)

    log = events.read_all()
    assert [e["event_type"] for e in log] == [
        events.AMBIGUITY_ASKED,
        events.LEAD_CREATED,
        events.PRICE_QUOTED,
        events.BOOKING_REQUESTED,
    ]

    # الغموض سبق وجود الـLead، وكل ما بعده على نفس الـLead
    assert log[0]["lead_id"] == ""
    lead_ids = {e["lead_id"] for e in log[1:]}
    assert len(lead_ids) == 1

    # والخدمة المحسومة هي التي اختارتها بالرقم، لا أولى المرشَّحين
    assert log[2]["payload"]["service_name"] == LASER_FULL_BODY
    assert log[0]["payload"]["candidates"][1] == LASER_FULL_BODY


def test_business_logic_records_the_ambiguity_as_its_own_rule_decision():
    """
    القرار الظاهر للمقارنة هو `ambiguous_service` - لا `other` ولا
    `price_inquiry`: لا هو سؤال بلا خدمة، ولا هو سعر عُرض.
    """
    say("u_rd", AMBIGUOUS_TEXT)
    assert business_logic.get_last_rule_decision("u_rd") == "ambiguous_service"
