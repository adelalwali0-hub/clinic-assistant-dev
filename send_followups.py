"""
إرسال متابعات Lead Recovery الفعلية (مرحلتان) عبر Telegram
=======================================================================
سكربت يُشغَّل يدوياً (أو دورياً عبر جدولة خارجية بسيطة - غير مطلوب
الآن) لتنفيذ دورة Lead Recovery كاملة في كل تشغيل:
  1) إرسال Follow-up 1 لكل Lead مؤهل (not_ready منذ 24 ساعة فأكثر)
  2) إرسال Follow-up 2 لكل Lead مؤهل (متابعة أولى منذ 72 ساعة فأكثر بلا حجز)
  3) تعليم أي Lead تجاوز المتابعة الثانية بوقت كافٍ كـ"منتهي"

لا يُرسل أي رسالة مرتين لنفس الـLead. لا تأثير على Business Logic أو
الحجوزات الحية - يعمل فقط على leads.csv بعد وقوع الأحداث.
"""

import os
from leads_store import (
    get_leads_eligible_for_first_followup,
    get_leads_eligible_for_second_followup,
    get_leads_to_expire,
    mark_followup_sent,
    mark_expired,
)
from telegram_channel import TelegramChannel
from channel_interface import OutgoingMessage

FIRST_FOLLOWUP_HOURS = 24
SECOND_FOLLOWUP_HOURS = 72
EXPIRE_AFTER_HOURS = 72

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("الرجاء ضبط متغير البيئة TELEGRAM_BOT_TOKEN أولاً.")


def build_followup_1_message(service_name: str, price: str) -> str:
    price_line = f" ({price})" if price else ""
    return (
        f"مرحباً 🌸 لاحظنا أنك استفسرتِ سابقاً عن {service_name}{price_line} "
        f"ولم يتم تأكيد الحجز بعد.\n"
        f"هل ما زلتِ مهتمة؟ يسعدنا مساعدتك بحجز موعد مناسب في أي وقت."
    )


def build_followup_2_message(service_name: str) -> str:
    return (
        f"🌸 آخر تذكير بخصوص {service_name} — إذا حابة تحجزين، "
        f"احنا جاهزين نساعدج بأي وقت يناسبج."
    )


def run_first_followups(channel: TelegramChannel) -> None:
    eligible = get_leads_eligible_for_first_followup(FIRST_FOLLOWUP_HOURS)
    if not eligible:
        print("Follow-up 1: لا توجد استفسارات مؤهلة حالياً.")
        return

    print(f"Follow-up 1: {len(eligible)} استفسار مؤهل.")
    for row in eligible:
        lead_id = row["lead_id"]
        user_id = row["معرف العميل"]
        service_name = row["الخدمة المطلوبة"]
        price = row.get("سعر الخدمة وقت الإنشاء", "")

        message_text = build_followup_1_message(service_name, price)
        success = channel.send_message(OutgoingMessage(user_id=user_id, text=message_text))

        if success:
            mark_followup_sent(lead_id=lead_id, new_stage="1")
            print(f"[SENT-1] -> {user_id} | {service_name}")
        else:
            print(f"[FAILED-1] -> {user_id} | {service_name} | لن يُعلَّم، سيُعاد المحاولة لاحقاً")


def run_second_followups(channel: TelegramChannel) -> None:
    eligible = get_leads_eligible_for_second_followup(SECOND_FOLLOWUP_HOURS)
    if not eligible:
        print("Follow-up 2: لا توجد استفسارات مؤهلة حالياً.")
        return

    print(f"Follow-up 2: {len(eligible)} استفسار مؤهل.")
    for row in eligible:
        lead_id = row["lead_id"]
        user_id = row["معرف العميل"]
        service_name = row["الخدمة المطلوبة"]

        message_text = build_followup_2_message(service_name)
        success = channel.send_message(OutgoingMessage(user_id=user_id, text=message_text))

        if success:
            mark_followup_sent(lead_id=lead_id, new_stage="2")
            print(f"[SENT-2] -> {user_id} | {service_name}")
        else:
            print(f"[FAILED-2] -> {user_id} | {service_name} | لن يُعلَّم، سيُعاد المحاولة لاحقاً")


def run_expire_pass() -> None:
    candidates = get_leads_to_expire(EXPIRE_AFTER_HOURS)
    if not candidates:
        print("Expiry: لا توجد استفسارات مستحقة الإنهاء حالياً.")
        return

    print(f"Expiry: {len(candidates)} استفسار سيُعلَّم كـ'منتهي'.")
    for row in candidates:
        mark_expired(lead_id=row["lead_id"])
        print(f"[EXPIRED] -> {row['معرف العميل']} | {row['الخدمة المطلوبة']}")


if __name__ == "__main__":
    channel = TelegramChannel(bot_token=BOT_TOKEN)
    run_first_followups(channel)
    run_second_followups(channel)
    run_expire_pass()