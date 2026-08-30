"""
إرسال متابعات Lead Recovery الفعلية (مرحلتان) عبر Telegram
=======================================================================
سكربت يُشغَّل يدوياً (أو دورياً عبر جدولة خارجية بسيطة - غير مطلوب
الآن) لتنفيذ دورة Lead Recovery كاملة في كل تشغيل:
  1) إرسال Follow-up 1 لكل Lead مؤهل (صامت منذ 24 ساعة فأكثر)
  2) إرسال Follow-up 2 لكل Lead مؤهل (متابعة أولى منذ 72 ساعة فأكثر بلا حجز)
  3) تعليم أي Lead تجاوز المتابعة الثانية بوقت كافٍ كـ"منتهي"

لا يُرسل أي رسالة مرتين لنفس الـLead. لا تأثير على Business Logic أو
الحجوزات الحية - يعمل فقط على leads.csv بعد وقوع الأحداث.

[التغيير #5] هذا الملف لم يعد يستدعي `channel.send_message` مباشرة
ولم يعد يملك أي نص: الإرسال عبر outbound.send (نفس مسار الردود
الحية)، والنصوص في variants.py بمعرّفَي `followup_1.v1` و
`followup_2.v1`. المعرّف يُمرَّر إلى mark_followup_sent فيحمله حدث
FOLLOWUP_SENT، وتصير كل متابعة صادرة منسوبة إلى صياغتها.

الحدث الصادر لهذا المسار هو FOLLOWUP_SENT وحده (§5)، ولهذا يُمرَّر
`event_type=None` إلى outbound.send: الرسالة الواحدة تُنتج حدثاً
واحداً، لا حدثين باسمين مختلفين لنفس الفعل.
"""

import os
from leads_store import (
    get_leads_eligible_for_first_followup,
    get_leads_eligible_for_second_followup,
    get_leads_to_expire,
    mark_followup_sent,
    mark_expired,
)
import outbound
import variants
from telegram_channel import TelegramChannel
from channel_interface import OutgoingMessage

FIRST_FOLLOWUP_HOURS = 24
SECOND_FOLLOWUP_HOURS = 72
EXPIRE_AFTER_HOURS = 72

FOLLOWUP_1_VARIANT = "followup_1.v1"
FOLLOWUP_2_VARIANT = "followup_2.v1"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("الرجاء ضبط متغير البيئة TELEGRAM_BOT_TOKEN أولاً.")


def build_followup_1_message(service_name: str, price: str) -> str:
    """
    الشرط بقي هنا لا في المكتبة: variants.py يحمل نصوصاً لا فروعاً.
    النص الناتج مطابق حرفاً بحرف لما كان.
    """
    price_suffix = f" ({price})" if price else ""
    return variants.render(
        FOLLOWUP_1_VARIANT, service_name=service_name, price_suffix=price_suffix
    )


def build_followup_2_message(service_name: str) -> str:
    return variants.render(FOLLOWUP_2_VARIANT, service_name=service_name)


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
        success = outbound.send(
            channel,
            OutgoingMessage(
                user_id=user_id, text=message_text, variant_id=FOLLOWUP_1_VARIANT
            ),
            lead_id=lead_id,
            event_type=None,  # FOLLOWUP_SENT يُصدره mark_followup_sent تحت القفل
        )

        if success:
            mark_followup_sent(lead_id=lead_id, new_stage="1", variant_id=FOLLOWUP_1_VARIANT)
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
        success = outbound.send(
            channel,
            OutgoingMessage(
                user_id=user_id, text=message_text, variant_id=FOLLOWUP_2_VARIANT
            ),
            lead_id=lead_id,
            event_type=None,  # FOLLOWUP_SENT يُصدره mark_followup_sent تحت القفل
        )

        if success:
            mark_followup_sent(lead_id=lead_id, new_stage="2", variant_id=FOLLOWUP_2_VARIANT)
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