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

[التغيير #6 - كشف الغموض | F6/S2/D5] رسالة تطابق أكثر من خدمة كانت
تُسعَّر بسعر **أولى** المطابقات بترتيب الإعداد: اختيار صامت لا يظهر في
أي سجل، ولا تعرف العميلة أنه وقع. الآن يتفرّع مسار idle على **عدد**
المطابقات:
  1     - مسار السعر كما كان حرفياً.
  0     - قائمة الخدمات كما كانت حرفياً.
  2 فأكثر - سؤال توضيح: أسماء المرشَّحين مرقَّمة، بلا سعر، والجلسة
            تنتقل إلى awaiting_service_disambiguation.

[لا Lead عند سؤال التوضيح - قرار صريح بكلفته]
لا يُكتب صف في leads.csv عند سؤال التوضيح؛ الـLead يُنشأ عند السعر
وحده عبر record_price_quote كما هو (D1). الكلفة مقبولة ومعروفة:
العميلة التي تُسأل ثم تصمت لا تترك صفاً ولا تدخل دورة المتابعة - هي
مرئية في events.jsonl عبر AMBIGUITY_ASKED وحده. البديل (صف بلا خدمة
محسومة) كان سيُدخل صفوفاً بلا خدمة إلى مقام كل نسبة تحويل ويُفسد
القياس الذي تقوم عليه البوابة. القرار مسجَّل في D-017.

[ai_intent لا يُقرأ في أي من الفرعين الجديدين]
كشف الغموض قرار قواعد ثابتة بالكامل - كالسعر تماماً (Phase 3B تحصر
تأثير AI في confirm_booking/decline/hesitant وحدها). لا سطر أدناه في
المسار الجديد يقرأ ai_intent.

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

import re

import events
import matching
import variants
from channel_interface import IncomingMessage, ReplyDecision
from services import (
    CENTER_NAME,
    find_service_by_name,
    find_services,
    service_options_text,
    services_list_text,
)
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

# سبب الغموض المُسجَّل في الحدث. اليوم مصدر واحد: تعدد الخدمات
# المطابقة لكلمات الرسالة. الحقل موجود منذ أول حدث ليُميَّز لاحقاً بين
# مصادر غموض أخرى دون أن ينقسم شكل السجل إلى ما قبل وما بعد.
AMBIGUITY_SOURCE_KEYWORD_MULTIPLICITY = "keyword_multiplicity"

RULE_DECISION_AMBIGUOUS_SERVICE = "ambiguous_service"

# الأرقام الهندية العربية (٠-٩) وامتدادها الفارسي (۰-۹) إلى ASCII.
# العميلة ترسل «٢» من لوحة مفاتيح عربية بينما القائمة معروضة بـ«2»؛
# رفض جوابها لاختلاف الرسم وحده يجعل سؤال التوضيح عائقاً لا مساعدة.
_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_BARE_NUMBER = re.compile(r"[0-9]+")


def _bare_number(text: str) -> int | None:
    """
    الرقم إن كانت الرسالة **رقماً مجرداً** لا غير، وإلا None.

    الاشتراط مقصود: «2» جواب على القائمة، بينما «عندي 2 جلسات ليزر»
    ليست اختياراً للخيار الثاني. رسالة تحمل رقماً وسط كلام تُترك
    لقاعدة المطابقة بعدها.
    """
    stripped = text.strip().translate(_ARABIC_INDIC_DIGITS)
    return int(stripped) if _BARE_NUMBER.fullmatch(stripped) else None


def _price_reply(message: IncomingMessage, service: dict) -> ReplyDecision:
    """
    مسار السعر - منطقه لم يتغيّر عمّا كان في فرع idle حرفياً: إنشاء
    الـLead لحظة الرد بالسعر (D1)، حفظ lead_id في الجلسة، ثم
    price_quote.v1.

    يعيش في دالة واحدة لأن له الآن مدخلين (تطابق وحيد من idle، وحسم
    الغموض بعد سؤال التوضيح) ويجب أن يبقيا فعلاً واحداً: مدخل ثانٍ
    ينسخ هذه الخطوات كان سيصير مسار سعر لا يُنشئ Lead بعد أول تعديل.

    `service_options=None` تُمسح هنا صراحةً: الخيارات المعروضة انتهت
    مهمتها لحظة حسم الخدمة، وبقاؤها في الجلسة يجعل رقماً يُرسَل بعد
    ذلك يعني شيئاً في محادثة لم تعد تعرض قائمة.
    """
    lead_id = record_price_quote(
        user_id=message.user_id,
        service_name=service["name"],
        channel=message.channel,
    )
    session_store.update_session(
        message.user_id,
        state=session_store.STATE_AWAITING_BOOKING_REPLY,
        service=service,
        lead_id=lead_id,
        service_options=None,
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


def _ask_which_service(message: IncomingMessage, candidates: list[dict]) -> ReplyDecision:
    """
    سؤال التوضيح: تنتقل الجلسة إلى انتظار الاختيار، ثم يُصدَر
    AMBIGUITY_ASKED، ثم يُرسَل السؤال.

    الترتيب مقصود وهو نفس اصطلاح leads_store: الحدث يُصدَر **بعد**
    نجاح التحديث وحده. update_session كتابة ذرّية ترمي عند الفشل، فلا
    يُكتب في السجل ادّعاء بسؤال لم تنتقل الجلسة لأجله. والاتجاه المختار
    عند الفشل هو ذاته: الحالة قد تسبق الحدث، ولا يسبقه العكس.

    لا Lead هنا و`lead_id=""` في الحدث - لا صف يُنسَب إليه بعد.
    """
    candidate_names = [service["name"] for service in candidates]

    session_store.update_session(
        message.user_id,
        state=session_store.STATE_AWAITING_SERVICE_DISAMBIGUATION,
        service_options=candidate_names,
    )
    events.emit(
        events.AMBIGUITY_ASKED,
        lead_id="",
        channel=message.channel,
        # نص الرسالة الوارد **لا** يُنسَخ هنا: سجل بالإلحاق فقط لا
        # يُحذف منه شيء، وأسماء المرشَّحين تكفي لكل تحليل لاحق
        # (أي خدمات تتزاحم، وكم مرة).
        payload={
            "user_id": message.user_id,
            "candidates": candidate_names,
            "candidate_count": len(candidate_names),
            "source": AMBIGUITY_SOURCE_KEYWORD_MULTIPLICITY,
        },
    )
    return ReplyDecision(
        text=variants.render(
            "ambiguity_question.v1",
            options_list=service_options_text(candidate_names),
        ),
        variant_id="ambiguity_question.v1",
        lead_id=None,
        rule_decision=RULE_DECISION_AMBIGUOUS_SERVICE,
    )


def _reprompt_which_service(option_names: list[str]) -> ReplyDecision:
    """
    إعادة السؤال بنفس الخيارات المعروضة سابقاً - بلا حدث جديد: لم
    يتغيّر شيء في مجموعة المرشَّحين، وإصدار AMBIGUITY_ASKED مرة أخرى
    كان سيجعل عدّ «كم مرة وقع غموض» يعدّ محاولات الفهم لا حالات الغموض.
    """
    return ReplyDecision(
        text=variants.render(
            "ambiguity_reprompt.v1",
            options_list=service_options_text(option_names),
        ),
        variant_id="ambiguity_reprompt.v1",
        lead_id=None,
        rule_decision=RULE_DECISION_AMBIGUOUS_SERVICE,
    )


def _resolve_disambiguation(message: IncomingMessage, session: dict) -> ReplyDecision:
    """
    حسم سؤال التوضيح. ثلاث قواعد بترتيب ثابت، وأول قاعدة تحسم تفوز:

      1) رقم مجرد داخل مدى القائمة -> الخيار المقابل. أرخص جواب على
         العميلة وأقلّه التباساً، فيُجرَّب أولاً.
      2) مطابقة بحدود الكلمة على **المرشَّحين وحدهم** (كلماتهم
         المفتاحية + أسماؤهم). حصر النطاق هو المقصود: «الوجه» قد تطابق
         خدمات كثيرة في الكتالوج، لكن المحادثة الجارية تدور حول ثلاث.
         تطابق وحيد -> السعر؛ أكثر من واحد -> إعادة السؤال بلا حدث.
      3) الكتالوج كاملاً - تغيير الموضوع وسط التوضيح. العميلة التي
         سُئلت عن الليزر ثم كتبت «بوتوكس» غيّرت سؤالها، ولا يصح أن
         يُقرأ جوابها داخل قائمة لم تعد تعنيها. واحدة -> السعر؛ أكثر
         -> سؤال توضيح **جديد** بمرشَّحين جدد (وحدث جديد: هذا غموض
         آخر)؛ صفر -> إعادة السؤال بالخيارات القائمة.

    خيار محفوظ اختفى اسمه من الإعداد (خدمة حُذفت وسط المحادثة) لا
    يُسعَّر ولا يُختلَق له سعر: يسقط إلى قاعدة الكتالوج (3).

    ai_intent لا يُقرأ هنا إطلاقاً - انظر ترويسة الملف.
    """
    text = message.text.strip()
    option_names = session.get("service_options") or []

    # (1) رقم مجرد
    number = _bare_number(text)
    if number is not None and 1 <= number <= len(option_names):
        chosen = find_service_by_name(option_names[number - 1])
        if chosen is not None:
            return _price_reply(message, chosen)

    # (2) مطابقة محصورة بالمرشَّحين
    live_options = [
        service for service in
        (find_service_by_name(name) for name in option_names)
        if service is not None
    ]
    narrowed = [
        service for service in live_options
        if matching.matches_any(text, list(service["keywords"]) + [service["name"]])
    ]
    if len(narrowed) == 1:
        return _price_reply(message, narrowed[0])
    if len(narrowed) >= 2:
        return _reprompt_which_service(option_names)

    # (3) الكتالوج كاملاً
    catalog_matches = find_services(text)
    if len(catalog_matches) == 1:
        return _price_reply(message, catalog_matches[0])
    if len(catalog_matches) >= 2:
        return _ask_which_service(message, catalog_matches)
    return _reprompt_which_service(option_names)


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

    if session["state"] == session_store.STATE_AWAITING_SERVICE_DISAMBIGUATION:
        return _resolve_disambiguation(message, session)

    # [التغيير #6] الفرع على **عدد** المطابقات لا على أولاها.
    matched_services = find_services(text)

    if len(matched_services) == 1:
        # PRD D1: الـLead يُنشأ هنا - لحظة الرد بالسعر - لا عند تسليم
        # البيانات. العميلة التي تسأل ثم تصمت تترك أثراً وتدخل دورة
        # المتابعة. lead_id يُحفظ في الجلسة ليُحدَّث نفس الصف لاحقاً.
        return _price_reply(message, matched_services[0])

    if len(matched_services) >= 2:
        return _ask_which_service(message, matched_services)

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