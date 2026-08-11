"""
إرسال رسائل المتابعة الفعلية للاستفسارات غير المحسومة (Lost Leads)
=======================================================================
يقرأ كل استفسار مؤهل للمتابعة (من leads_store)، يرسل رسالة متابعة
حقيقية عبر Telegram (نفس القناة التي استُقبل منها الاستفسار)، ثم
يعلّم الاستفسار كـ"تمت متابعته" حتى لا تُرسل له نفس الرسالة مرتين.

هذا سكربت يُشغَّل يدوياً الآن (لاحقاً سيُشغَّل تلقائياً بشكل دوري -
خطوة قادمة منفصلة، غير مطلوبة الآن).
"""

import os
from leads_store import get_eligible_followups, mark_followed_up
from telegram_channel import TelegramChannel
from channel_interface import OutgoingMessage

HOURS_THRESHOLD_FOR_TEST = 1  # نفس القيمة المستخدمة في check_followups.py للاختبار

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("الرجاء ضبط متغير البيئة TELEGRAM_BOT_TOKEN أولاً.")


def build_followup_message(service_name: str) -> str:
    return (
        f"مرحباً 🌸 لاحظنا أنك استفسرتِ سابقاً عن {service_name} ولم يتم تأكيد الحجز بعد.\n"
        f"هل ما زلتِ مهتمة؟ يسعدنا مساعدتك بحجز موعد مناسب في أي وقت."
    )


if __name__ == "__main__":
    channel = TelegramChannel(bot_token=BOT_TOKEN)
    eligible = get_eligible_followups(HOURS_THRESHOLD_FOR_TEST)

    if not eligible:
        print("لا توجد استفسارات مؤهلة للمتابعة حالياً.")
    else:
        print(f"عدد الاستفسارات المؤهلة للمتابعة: {len(eligible)}\n")
        for row in eligible:
            user_id = row["معرف العميل"]
            service_name = row["الخدمة المطلوبة"]
            timestamp = row["التاريخ والوقت"]

            message_text = build_followup_message(service_name)
            outgoing = OutgoingMessage(user_id=user_id, text=message_text)
            success = channel.send_message(outgoing)

            if success:
                mark_followed_up(user_id=user_id, service_name=service_name, timestamp=timestamp)
                print(f"[SENT] -> {user_id} | {service_name} | تم تعليمه كمتابَع")
            else:
                print(f"[FAILED] -> {user_id} | {service_name} | لم يتم الإرسال، لن يُعلَّم كمتابَع")