"""
منطق العمل - سيناريو المحادثة + تسجيل كل استفسار (Leads) + جمع بيانات التواصل
================================================================================
استفسار عن خدمة/سعر -> عرض السعر -> سؤال عن الرغبة بالحجز -> عند
"نعم": يُطلب الاسم ورقم الهاتف -> يُسجَّل طلب الحجز. عند "لا": يُسجَّل
الـLead كـ declined. عند تردد واضح: رد مخصص دون حسم.

[مواءمة المفردات - PRD §8/D2] ما يكتبه هذا الملف عند تسليم البيانات
هو `booking_requested` - **طلب** حجز، لا حجز مؤكَّد. القيمة القديمة
`confirmed` كانت تعني "أرسلت رقمها" بينما Confirmed Booking في §8 هو
تأكيد الموظفة: حدث خارج حدود النظام لا يملك أي كود هنا كتابته.
وحالة الجلسة `awaiting_booking_confirmation` صارت
`awaiting_booking_reply` لنفس السبب - النظام ينتظر ردّ العميلة.

[PRD D1] سجل الـLead يُنشأ **لحظة الرد بالسعر**، لا عند تسليم بيانات
التواصل. العميلة التي تسأل عن سعر ثم تصمت لم تكن تترك أي أثر في
leads.csv ولا تدخل دورة المتابعة إطلاقاً - وهي الأغلبية الساحقة.
الآن يُكتب صفها فور عرض السعر ويصير مؤهلاً للمتابعة بعد نافذة الصمت.

lead_id المُرجَع يُحفَظ في الجلسة، فكل رد لاحق (موافقة، رفض، تردد)
يُحدِّث **نفس الصف** عبره ولا يُنشئ صفاً ثانياً. حين لا تحمل الجلسة
lead_id - جلسة بدأت قبل هذا التغيير - يسقط المسار بأمان إلى
save_lead بسلوكه السابق حرفياً.

شجرة القرار نفسها لم تتغيّر: نفس الفروع والشروط والردود تماماً.

[التغيير #5 - مسار الإرسال الموحّد] لم يبقَ في هذا الملف نص رد واحد:
كلها في variants.py، وكل فرع هنا يسمّي الصياغة التي اختارها بمعرّفها.
النصوص المُصاغة مطابقة حرفاً بحرف لما كانت عليه. `_decide` تُرجع
ReplyDecision (نص + variant_id + lead_id + rule_decision) بدل زوج،
فيصل معرّف الصياغة والـLead إلى موضع الإرسال ويصير كل صادر منسوباً.

هذا ليس اختياراً بين صياغات: لكل نية صياغة واحدة اليوم، ومنطق
الاختيار خارج نطاق هذا التغيير (§16 - لا توليد، لا اختيار آلي).

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
None). يُستخدم حصراً داخل awaiting_booking_reply، وفقط إذا
كانت قيمته إحدى الثلاث الآمنة: confirm_booking / decline / hesitant.

price_inquiry (حالة idle) وask_more_info يبقيان Rule-Based بالكامل
دائماً - خارج نطاق تأثير ai_intent.

rule_decision المُرجَع دائماً يعكس القرار الذي كانت ستتخذه القواعد
الثابتة بمفردها - لتتبع المقارنة. _last_rule_decisions يبقى
In-Memory فقط كما كان (لا يُحفَظ على القرص - قيمة تشخيصية مؤقتة).
"""

import variants
from channel_interface import IncomingMessage, ReplyDecision
from services import CENTER_NAME, find_service, services_list_text
from leads_store import (
    STATE_BOOKING_REQUESTED,
    STATE_DECLINED,
    record_booking_request,
    record_decline,
    record_hesitation,
    record_price_quote,
    save_lead,
)
from storage import session_store

# آخر قرار Rule-Based (بمعزل عن AI) لكل مستخدم - In-Memory فقط، لا يُحفَظ
_last_rule_decisions: dict[str, str] = {}

CONFIRM_WORDS = ["نعم", "اكيد", "أكيد", "ايوة", "إيوه", "يس", "yes", "موافق", "اوك", "أوك", "ok"]
DECLINE_WORDS = ["لا", "لأ", "مو حاليا", "مو الحين", "no"]

HESITANT_WORDS = [
    "خلي أفكر", "خلي افكر", "أفكر", "افكر", "بعدين", "مو هسه",
    "أشوف", "اشوف", "أرجعلك", "ارجعلك", "أردلك", "اردلك", "يمكن",
]

AI_OVERRIDE_ALLOWED = {"confirm_booking", "decline", "hesitant"}


def get_session_state(user_id: str) -> str:
    """قراءة فقط - حالة الجلسة الحالية عبر session_store."""
    return session_store.get_session(user_id)["state"]


def get_last_rule_decision(user_id: str) -> str:
    """قراءة فقط - القرار الذي اتخذته القواعد الثابتة بمفردها، للتتبع فقط."""
    return _last_rule_decisions.get(user_id, "other")


def _decide(message: IncomingMessage, ai_intent: str | None) -> ReplyDecision:
    """
    منطق القرار - نفس الفروع والشروط والردود تماماً كما كانت، مع
    استبدال كل قراءة/تعديل لحالة الجلسة باستدعاء session_store
    بدل التعامل المباشر مع dict في الذاكرة.

    [التغيير #5] كل فرع صار يُرجع ReplyDecision بدل زوج (نص، قرار):
    النص نفسه حرفياً، مضافاً إليه `variant_id` الصياغة التي أنتجته
    و`lead_id` الـLead الذي يخصّه - وهما ما يحتاجه مسار الإرسال لنسب
    الرسالة الصادرة إلى نتيجتها. الفروع والشروط لم يتغيّر منها شيء،
    ولا حرف من أي نص: النصوص انتقلت إلى variants.py كما هي.

    `lead_id` يُقرأ من الجلسة **قبل** clear_session في الفرعين
    اللذين يغلقانها - لولا ذلك لضاعت نسبة أهم رسالتين في المحادثة.
    """
    user_id = message.user_id
    text = message.text.strip()
    session = session_store.get_session(user_id)

    if session["state"] == session_store.STATE_AWAITING_CONTACT_INFO:
        service_name = session["service"]["name"]
        lead_id = session.get("lead_id")
        session_store.clear_session(user_id)
        if not (lead_id and record_booking_request(lead_id=lead_id, contact_info=text)):
            # سقوط آمن: جلسة بدأت قبل هذا التغيير فلا تحمل lead_id، أو
            # صفّها اختفى من الملف. السلوك السابق حرفياً - صف جديد -
            # أفضل من ضياع الحجز.
            lead_id = save_lead(
                user_id=user_id,
                service_name=service_name,
                channel=message.channel,
                status=STATE_BOOKING_REQUESTED,
                contact_info=text,
            )
        return ReplyDecision(
            text=variants.render(
                "booking_request_ack.v1",
                service_name=service_name,
                center_name=CENTER_NAME,
            ),
            variant_id="booking_request_ack.v1",
            lead_id=lead_id,
            rule_decision="confirm_booking",
        )

    if session["state"] == session_store.STATE_AWAITING_BOOKING_REPLY:
        lowered = text.lower()
        service_name = session["service"]["name"]
        lead_id = session.get("lead_id")

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
            session_store.update_session(user_id, state=session_store.STATE_AWAITING_CONTACT_INFO)
            return ReplyDecision(
                text=variants.render("ask_contact_info.v1"),
                variant_id="ask_contact_info.v1",
                lead_id=lead_id,
                rule_decision=rule_branch,
            )

        if effective_branch == "decline":
            session_store.clear_session(user_id)
            # الرفض يُحدِّث صف عرض السعر القائم، لا يُنشئ صفاً ثانياً.
            # الحالة تصير declined، وهي داخل OPEN_STATES فيبقى الصف
            # مؤهلاً للمتابعة تماماً كما كان (D-015).
            if not (lead_id and record_decline(lead_id)):
                lead_id = save_lead(user_id=user_id, service_name=service_name,
                                    channel=message.channel, status=STATE_DECLINED)
            return ReplyDecision(
                text=variants.render("decline_ack.v1"),
                variant_id="decline_ack.v1",
                lead_id=lead_id,
                rule_decision=rule_branch,
            )

        if effective_branch == "hesitant":
            # التردد لا يحسم شيئاً - تُسجَّل الإشارة والصف يبقى كما هو،
            # حالته price_quoted (لم تُجب بعد) ومؤهلاً للمتابعة.
            # الجلسة تبقى مفتوحة كما كانت تماماً.
            if lead_id:
                record_hesitation(lead_id)
            return ReplyDecision(
                text=variants.render("hesitant_ack.v1"),
                variant_id="hesitant_ack.v1",
                lead_id=lead_id,
                rule_decision=rule_branch,
            )

        return ReplyDecision(
            text=variants.render("booking_reply_reprompt.v1"),
            variant_id="booking_reply_reprompt.v1",
            lead_id=lead_id,
            rule_decision=rule_branch,
        )

    service = find_service(text)
    if service:
        # PRD D1: الـLead يُنشأ هنا - لحظة الرد بالسعر - لا عند تسليم
        # البيانات. العميلة التي تسأل ثم تصمت تترك أثراً وتدخل دورة
        # المتابعة. lead_id يُحفظ في الجلسة ليُحدَّث نفس الصف لاحقاً.
        lead_id = record_price_quote(
            user_id=user_id,
            service_name=service["name"],
            channel=message.channel,
        )
        session_store.update_session(
            user_id,
            state=session_store.STATE_AWAITING_BOOKING_REPLY,
            service=service,
            lead_id=lead_id,
        )
        return ReplyDecision(
            text=variants.render(
                "price_quote.v1",
                service_name=service["name"],
                center_name=CENTER_NAME,
                price=service["price"],
            ),
            variant_id="price_quote.v1",
            lead_id=lead_id,
            rule_decision="price_inquiry",
        )

    # لا Lead هنا: لم يُذكر أي سعر ولم يُنشأ أي صف. lead_id=None حالة
    # صحيحة يتعامل معها مسار الإرسال، لا نقص فيه.
    return ReplyDecision(
        text=variants.render(
            "services_list.v1",
            center_name=CENTER_NAME,
            services_list=services_list_text(),
        ),
        variant_id="services_list.v1",
        lead_id=None,
        rule_decision="other",
    )


def handle_message(message: IncomingMessage, ai_intent: str | None = None) -> ReplyDecision:
    """
    [التغيير #5] تُرجع ReplyDecision بدل `str`.

    قيمة الإرجاع تغيّرت عن قصد: النص وحده لا يكفي مسار الإرسال الموحّد،
    فهو يحتاج `variant_id` ليعرف أي صياغة أُرسلت و`lead_id` ليعرف
    لِمَن. تمريرهما عبر قناة جانبية (قاموس عام كـ_last_rule_decisions)
    كان سيخفي مُدخَلات الإرسال عن موضع الاستدعاء ويكسر عند التوازي.

    `.text` هو نفس السلسلة السابقة حرفاً بحرف.
    """
    decision = _decide(message, ai_intent)
    _last_rule_decisions[message.user_id] = decision.rule_decision
    return decision