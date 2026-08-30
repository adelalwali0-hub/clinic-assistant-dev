"""
نقطة تشغيل النظام: Channel Layer + Business Logic + AI Understanding.

[Phase 3B] USE_AI_INTENT (متغير بيئة، افتراضياً false):
  false -> النظام Rule-Based بالكامل، ولا يُستدعى GPT/OpenAI إطلاقاً
           (سلوك 3A.1 حرفياً، بلا أي اتصال بـOpenAI)
  true  -> ai_intent يؤثر فقط داخل awaiting_booking_reply،
           وفقط للقيم الآمنة الثلاث (confirm_booking/decline/hesitant)

Rollback فوري: إعادة USE_AI_INTENT إلى false يعيد النظام بالكامل
لسلوكه القديم دون أي تعديل كود، ودون أي حاجة لـOPENAI_API_KEY.

ملاحظة معمارية: message_router.py لم يتغيّر إطلاقاً - كل التنسيق
بين GPT وBusiness Logic يحدث هنا فقط، داخل combined_handler الواحدة.
"""

import os
from telegram_channel import TelegramChannel
from channel_interface import ReplyDecision
from message_router import MessageRouter
from business_logic import handle_message, get_session_state
from ai_understanding import understand_message

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "الرجاء ضبط متغير البيئة TELEGRAM_BOT_TOKEN أولاً.\n"
        "راجع README.md لخطوات الحصول على التوكن من @BotFather."
    )

# [Phase 3B] القيم الآمنة الوحيدة المسموح لها بالتأثير على الرد الفعلي
AI_OVERRIDE_ALLOWED = {"confirm_booking", "decline", "hesitant"}

USE_AI_INTENT = os.environ.get("USE_AI_INTENT", "false").strip().lower() == "true"
print(f"[CONFIG] USE_AI_INTENT={USE_AI_INTENT}")


def combined_handler(message) -> ReplyDecision:
    """
    إذا USE_AI_INTENT=false: لا يُستدعى understand_message() إطلاقاً -
    صفر اتصال بـOpenAI، Rule-Based بالكامل فوراً.
    إذا USE_AI_INTENT=true: نفس سلوك Phase 3B حرفياً - استدعاء واحد
    لـGPT لكل رسالة، يُستخدم للتجاوز الآمن المحدود وللمقارنة معاً.

    [التغيير #5] تمرّر ReplyDecision كما هي إلى الموجّه: هي التي تحمل
    variant_id وlead_id إلى مسار الإرسال. `rule_decision` تُقرأ منها
    مباشرة بدل get_last_rule_decision - القيمة نفسها من موضعها لا من
    قاموس عام.
    """
    if not USE_AI_INTENT:
        decision = handle_message(message, ai_intent=None)
        print(f"[COMPARE] rule_action={decision.rule_decision} | ai_intent=skipped (USE_AI_INTENT=false) | match=N/A")
        return decision

    session_state = get_session_state(message.user_id)
    ai_result = understand_message(message.text, recent_history=[], session_state=session_state)
    ai_intent_raw = ai_result["intent"]

    ai_intent_for_decision = ai_intent_raw if ai_intent_raw in AI_OVERRIDE_ALLOWED else None

    decision = handle_message(message, ai_intent=ai_intent_for_decision)

    match = decision.rule_decision == ai_intent_raw
    print(f"[COMPARE] rule_action={decision.rule_decision} | ai_intent={ai_intent_raw} | match={match}")

    return decision


if __name__ == "__main__":
    channel = TelegramChannel(bot_token=BOT_TOKEN)
    router = MessageRouter(channel=channel, handler=combined_handler)
    router.run()