"""
نقطة تشغيل النظام: Channel Layer + Business Logic (المصدر الحقيقي
للرد) + AI Understanding Layer (Shadow Mode - للاختبار فقط، لا يؤثر
على الرد المُرسَل للعميلة في هذه المرحلة).
"""

import os
from telegram_channel import TelegramChannel
from message_router import MessageRouter
from business_logic import handle_message, get_session_state
from ai_understanding import understand_message

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit(
        "الرجاء ضبط متغير البيئة TELEGRAM_BOT_TOKEN أولاً.\n"
        "راجع README.md لخطوات الحصول على التوكن من @BotFather."
    )


def ai_understand_shadow(message) -> None:
    """
    يستدعي AI Understanding Layer فقط لأغراض الاختبار والطباعة -
    لا يُستخدم ناتجه في الرد الفعلي بعد (هذا سيحدث في مرحلة قادمة
    منفصلة بعد مراجعة هذه المرحلة).
    """
    session_state = get_session_state(message.user_id)
    result = understand_message(message.text, recent_history=[], session_state=session_state)
    print(f"[AI] intent={result['intent']} | service={result['service_mentioned']}")


if __name__ == "__main__":
    channel = TelegramChannel(bot_token=BOT_TOKEN)
    router = MessageRouter(channel=channel, handler=handle_message, ai_understand=ai_understand_shadow)
    router.run()