"""
نقطة تشغيل النظام: Channel Layer + Business Logic + AI Understanding.

[Phase 3B] USE_AI_INTENT (متغير بيئة، افتراضياً false):
  false -> النظام Rule-Based بالكامل (سلوك 3A.1 حرفياً، بلا أي تغيير)
  true  -> ai_intent يؤثر فقط داخل awaiting_booking_confirmation،
           وفقط للقيم الآمنة الثلاث (confirm_booking/decline/hesitant)

Rollback فوري: إعادة USE_AI_INTENT إلى false يعيد النظام بالكامل
لسلوكه القديم دون أي تعديل كود.

ملاحظة معمارية: message_router.py لم يتغيّر إطلاقاً في Phase 3B -
كل التنسيق بين GPT وBusiness Logic يحدث هنا فقط، داخل combined_handler
الواحدة، التي تُمرَّر للـRouter كأنها handler عادي بسيط - الـRouter
لا يعرف شيئاً عن وجود AI إطلاقاً.
"""

import os
from telegram_channel import TelegramChannel
from message_router import MessageRouter
from business_logic import handle_message, get_session_state, get_last_rule_decision
from ai_understanding import understand_message

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "الرجاء ضبط متغير البيئة TELEGRAM_BOT_TOKEN أولاً.\n"
        "راجع README.md لخطوات الحصول على التوكن من @BotFather."
    )

AI_OVERRIDE_ALLOWED = {"confirm_booking", "decline", "hesitant"}

USE_AI_INTENT = os.environ.get("USE_AI_INTENT", "false").strip().lower() == "true"
print(f"[CONFIG] USE_AI_INTENT={USE_AI_INTENT}")


def combined_handler(message) -> str:
    """
    يستدعي AI Understanding مرة واحدة فقط لكل رسالة (يُستخدم للغرضين
    معاً: كإشارة اختيارية لـBusiness Logic، وللمقارنة/التتبع) - ثم
    يستدعي handle_message مرة واحدة، ثم يطبع المقارنة، ثم يرجع الرد.
    """
    session_state = get_session_state(message.user_id)
    ai_result = understand_message(message.text, recent_history=[], session_state=session_state)
    ai_intent_raw = ai_result["intent"]

    ai_intent_for_decision = ai_intent_raw if (USE_AI_INTENT and ai_intent_raw in AI_OVERRIDE_ALLOWED) else None

    reply_text = handle_message(message, ai_intent=ai_intent_for_decision)

    rule_decision = get_last_rule_decision(message.user_id)
    match = rule_decision == ai_intent_raw
    print(f"[COMPARE] rule_action={rule_decision} | ai_intent={ai_intent_raw} | match={match}")

    return reply_text


if __name__ == "__main__":
    channel = TelegramChannel(bot_token=BOT_TOKEN)
    router = MessageRouter(channel=channel, handler=combined_handler)
    router.run()