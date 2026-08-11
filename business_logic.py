"""
منطق العمل - سيناريو المحادثة + تسجيل كل استفسار (Leads) + جمع بيانات التواصل
================================================================================
استفسار عن خدمة/سعر -> عرض السعر -> سؤال عن الرغبة بالحجز -> عند
"نعم": يُطلب الاسم ورقم الهاتف -> يُسجَّل الحجز. عند "لا": يُسجَّل
الاستفسار كـ not_ready.

هذا هو مصدر الحقيقة الوحيد (Business Truth): الحالة، الأسعار،
الحجوزات، الـLeads. طبقة AI Understanding (لاحقاً) قد تساعد بفهم
النية، لكنها لا تُغيّر أي شيء هنا مباشرة - فقط هذا الملف يقرر.

معالج بحالة بسيطة في الذاكرة (In-Memory State) لكل مستخدم - كافٍ
للعرض التجريبي على Telegram.
"""

from channel_interface import IncomingMessage
from services import CENTER_NAME, find_service, services_list_text
from leads_store import save_lead

# حالة كل عميلة: idle | awaiting_booking_confirmation | awaiting_contact_info
_sessions: dict[str, dict] = {}

CONFIRM_WORDS = ["نعم", "اكيد", "أكيد", "ايوة", "إيوه", "يس", "yes", "موافق", "اوك", "أوك", "ok"]
DECLINE_WORDS = ["لا", "لأ", "مو حاليا", "مو الحين", "no"]


def get_session_state(user_id: str) -> str:
    """
    قراءة فقط - يستخدمها لاحقاً أي طرف خارجي (مثل AI Understanding)
    ليعرف حالة الجلسة الحالية دون أي صلاحية لتغييرها.
    """
    session = _sessions.get(user_id, {"state": "idle"})
    return session["state"]


def handle_message(message: IncomingMessage) -> str:
    user_id = message.user_id
    text = message.text.strip()
    session = _sessions.setdefault(user_id, {"state": "idle", "service": None})

    if session["state"] == "awaiting_contact_info":
        service_name = session["service"]["name"]
        session["state"] = "idle"
        session["service"] = None
        save_lead(
            user_id=user_id,
            service_name=service_name,
            channel=message.channel,
            status="confirmed",
            contact_info=text,
        )
        return (
            f"شكراً لك 🌸 تم تسجيل حجزك لخدمة {service_name} في {CENTER_NAME}.\n"
            f"سيتواصل معك فريقنا خلال وقت قصير لتأكيد الموعد المناسب."
        )

    if session["state"] == "awaiting_booking_confirmation":
        lowered = text.lower()
        service_name = session["service"]["name"]

        if any(word in lowered for word in CONFIRM_WORDS):
            session["state"] = "awaiting_contact_info"
            return "ممتاز! الرجاء إرسال اسمك ورقم هاتفك في رسالة واحدة لتأكيد الحجز."

        if any(word in lowered for word in DECLINE_WORDS):
            session["state"] = "idle"
            session["service"] = None
            save_lead(user_id=user_id, service_name=service_name, channel=message.channel, status="not_ready")
            return "تمام 🌸 إذا احتجتِ أي معلومة إضافية عن خدماتنا أنا موجودة."

        return "هل ترغبين بتأكيد حجز موعد؟ (نعم / لا)"

    service = find_service(text)
    if service:
        session["state"] = "awaiting_booking_confirmation"
        session["service"] = service
        return (
            f"سعر {service['name']} في {CENTER_NAME} هو {service['price']}.\n"
            f"هل ترغبين بحجز موعد؟"
        )

    return (
        f"أهلاً بك في {CENTER_NAME} 🌸\n"
        f"هذه خدماتنا المتوفرة حالياً:\n{services_list_text()}\n\n"
        f"أي خدمة تودين الاستفسار عن سعرها؟"
    )