"""
اختبارات مطابقة كلمات الرد بحدود الكلمة (التغيير #6، الجزء الثاني).

`_decide` كانت تطابق CONFIRM/DECLINE/HESITANT بالسلسلة الفرعية، فـ
`"لا" in "يلا نشوف"` صحيحة: رسالة ودودة تُقرأ رفضاً، فيُغلَق ملف
العميلة على رفض لم يقع وتُسجَّل DECLINED في سجل لا يُحذف منه شيء.
الخطأ صامت تماماً - لا استثناء ولا سطر سجل.

القوائم نفسها لم تتغيّر: ما تغيّر أن الكلمة تعني نفسها لا أي كلمة
تحتويها.
"""

from datetime import datetime

import pytest

import leads_store
from business_logic import handle_message
from channel_interface import IncomingMessage
from storage import session_store


def make_message(user_id: str, text: str) -> IncomingMessage:
    return IncomingMessage(
        channel="telegram", user_id=user_id, text=text,
        timestamp=datetime.now(), message_id=None,
    )


def reply_after_quote(user_id: str, answer: str):
    """يمرّ بعرض السعر أولاً حتى تكون الجلسة في انتظار ردّ الحجز."""
    handle_message(make_message(user_id, "كم سعر البوتوكس؟"))
    return handle_message(make_message(user_id, answer))


@pytest.mark.parametrize("answer,expected_variant,expected_rule", [
    # الرفض الذي لم يقع: «يلا» تحتوي «لا» ولم تعد تعنيها
    ("يلا نشوف", "booking_reply_reprompt.v1", "other"),
    ("بلا زحمة", "booking_reply_reprompt.v1", "other"),
    # والرفض الحقيقي ما زال رفضاً
    ("لا", "decline_ack.v1", "decline"),
    ("لا شكراً", "decline_ack.v1", "decline"),
    ("مو حاليا", "decline_ack.v1", "decline"),
    # والموافقة ما زالت موافقة، ولا تقع داخل كلمة أخرى
    ("نعم اكيد", "ask_contact_info.v1", "confirm_booking"),
    ("منعم", "booking_reply_reprompt.v1", "other"),
    # والتردد ما زال تردداً (كلمة مفتاحية من كلمتين)
    ("خلي أفكر شوية", "hesitant_ack.v1", "hesitant"),
])
def test_reply_words_match_whole_words_only(answer, expected_variant, expected_rule):
    decision = reply_after_quote(f"u_b_{abs(hash(answer))}", answer)
    assert decision.variant_id == expected_variant
    assert decision.rule_decision == expected_rule


def test_a_friendly_yalla_no_longer_closes_the_lead_as_declined():
    """
    الأثر التجاري للخطأ الصامت: الصف كان يُقفل على `declined` والجلسة
    تُمسح، فتضيع محادثة جارية. الآن يبقى الصف مفتوحاً بحالته كما هي.
    """
    decision = reply_after_quote("u_yalla", "يلا نشوف")

    row = [r for r in leads_store._read_all_rows_unlocked()
           if r[leads_store.LEAD_ID_COLUMN] == decision.lead_id][0]
    assert row["الحالة"] == leads_store.STATE_PRICE_QUOTED
    # والجلسة ما زالت تنتظر الردّ، لم تُمسح
    assert session_store.get_session("u_yalla")["state"] == \
        session_store.STATE_AWAITING_BOOKING_REPLY
