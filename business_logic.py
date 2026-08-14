"""
منطق العمل - سيناريو المحادثة + تسجيل كل استفسار (Leads) + جمع بيانات التواصل
================================================================================
استفسار عن خدمة/سعر -> عرض السعر -> سؤال عن الرغبة بالحجز -> عند
"نعم": يُطلب الاسم ورقم الهاتف -> يُسجَّل الحجز. عند "لا": يُسجَّل
الاستفسار كـ not_ready. عند تردد واضح: رد مخصص دون حسم.

هذا هو مصدر الحقيقة الوحيد (Business Truth): القرارات، الأسعار،
الحجوزات، الـLeads - في كل الحالات، بما فيها Phase 3B.

[Phase 4] حالة الجلسة لم تعد مخزَّنة هنا في dict بالذاكرة - أصبحت
مُدارة بالكامل عبر storage/session_store.py (المالك الوحيد لها،
يحفظها في data/sessions.json وتبقى بعد إعادة تشغيل البرنامج).
هذا الملف لا يعدّل أي قاموس جلسة مباشرة - فقط يطلب قراءة
(get_session) أو تحديثاً (update_session/clear_session). شجرة
القرار نفسها (_decide) لم تتغيّر إطلاقاً - نفس الفروع والردود
والشروط تماماً كما كانت.

[Phase 3B] handle_message() تقبل معامل اختياري ai_intent (افتراضياً
None). يُستخدم حصراً داخل awaiting_booking_confirmation، وفقط إذا
كانت قيمته إحدى الثلاث الآمنة: confirm_booking / decline / hesitant.

price_inquiry (حالة idle) وask_more_info يبقيان Rule-Based بالكامل
دائماً - خارج نطاق تأثير ai_intent.

rule_decision المُرجَع دائماً يعكس القرار الذي كانت ستتخذه القواعد
الثابتة بمفردها - لتتبع المقارنة. _last_rule_decisions يبقى
In-Memory فقط كما كان (لا يُحفَظ على القرص - قيمة تشخيصية مؤقتة).
"""

from channel_interface import IncomingMessage
from services import CENTER_NAME, find_service, services_list_text
from leads_store import save_lead
from storage import session_store

# آخر قرار Rule-Based (بمعزل عن AI) لكل مستخدم - In-Memory فقط، لا يُحفَظ
_last_rule_decisions: dict[str, str] = {}

CONFIRM_WORDS = ["نعم", "اكيد", "أكيد", "ايوة", "إيوه", "يس", "yes", "موافق", "اوك", "أوك", "ok"]
DECLINE_WORDS = ["لا", "لأ", "مو حاليا", "مو الحين", "no"]

HESITANT_WORDS = [
    "خلي أفكر", "خلي افكر", "أفكر", "افكر", "بعدين", "مو هسه",
    "أشوف", "اشوف", "أرجعلك", "ارجعلك", "أردلك", "اردلك", "يمكن",
]

HESITANT_REPLY = (
    "تمام 🌸 خذي وقتك براحتك، وإذا حبيتي تعرفين أي تفاصيل إضافية "
    "أو تقررين الحجز، إحنا موجودين لخدمتج."
)

AI_OVERRIDE_ALLOWED = {"confirm_booking", "decline", "hesitant"}


def get_session_state(user_id: str) -> str:
    """قراءة فقط - حالة الجلسة الحالية عبر session_store."""
    return session_store.get_session(user_id)["state"]


def get_last_rule_decision(user_id: str) -> str:
    """قراءة فقط - القرار الذي اتخذته القواعد الثابتة بمفردها، للتتبع فقط."""
    return _last_rule_decisions.get(user_id, "other")


def _decide(message: IncomingMessage, ai_intent: str | None) -> tuple[str, str]:
    """
    منطق القرار - نفس الفروع والشروط والردود تماماً كما كانت، مع
    استبدال كل قراءة/تعديل لحالة الجلسة باستدعاء session_store
    بدل التعامل المباشر مع dict في الذاكرة.
    """
    user_id = message.user_id
    text = message.text.strip()
    session = session_store.get_session(user_id)

    if session["state"] == "awaiting_contact_info":
        service_name = session["service"]["name"]
        session_store.clear_session(user_id)
        save_lead(
            user_id=user_id,
            service_name=service_name,
            channel=message.channel,
            status="confirmed",
            contact_info=text,
        )
        reply = (
            f"شكراً لك 🌸 تم تسجيل حجزك لخدمة {service_name} في {CENTER_NAME}.\n"
            f"سيتواصل معك فريقنا خلال وقت قصير لتأكيد الموعد المناسب."
        )
        return reply, "confirm_booking"

    if session["state"] == "awaiting_booking_confirmation":
        lowered = text.lower()
        service_name = session["service"]["name"]

        if any(word in lowered for word in CONFIRM_WORDS):
            rule_branch = "confirm_booking"
        elif any(word in lowered for word in DECLINE_WORDS):
            rule_branch = "decline"
        elif any(word in lowered for word in HESITANT_WORDS):
            rule_branch = "hesitant"
        else:
            rule_branch = "other"

        if ai_intent in AI_OVERRIDE_ALLOWED:
            effective_branch = ai_intent
        else:
            effective_branch = rule_branch

        if effective_branch == "confirm_booking":
            session_store.update_session(user_id, state="awaiting_contact_info")
            return "ممتاز! الرجاء إرسال اسمك ورقم هاتفك في رسالة واحدة لتأكيد الحجز.", rule_branch

        if effective_branch == "decline":
            session_store.clear_session(user_id)
            save_lead(user_id=user_id, service_name=service_name, channel=message.channel, status="not_ready")
            return "تمام 🌸 إذا احتجتِ أي معلومة إضافية عن خدماتنا أنا موجودة.", rule_branch

        if effective_branch == "hesitant":
            return HESITANT_REPLY, rule_branch

        return "هل ترغبين بتأكيد حجز موعد؟ (نعم / لا)", rule_branch

    service = find_service(text)
    if service:
        session_store.update_session(user_id, state="awaiting_booking_confirmation", service=service)
        reply = (
            f"سعر {service['name']} في {CENTER_NAME} هو {service['price']}.\n"
            f"هل ترغبين بحجز موعد؟"
        )
        return reply, "price_inquiry"

    reply = (
        f"أهلاً بك في {CENTER_NAME} 🌸\n"
        f"هذه خدماتنا المتوفرة حالياً:\n{services_list_text()}\n\n"
        f"أي خدمة تودين الاستفسار عن سعرها؟"
    )
    return reply, "other"


def handle_message(message: IncomingMessage, ai_intent: str | None = None) -> str:
    """
    واجهة مستقرة - نفس التوقيع وقيمة الإرجاع كما كانت. أي كود يعتمد
    عليها يستمر بالعمل دون أي تغيير.
    """
    reply_text, rule_decision = _decide(message, ai_intent)
    _last_rule_decisions[message.user_id] = rule_decision
    return reply_text