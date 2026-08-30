"""
مكتبة الصياغات المعتمدة - Variant Library (PRD §16، D3)
========================================================================
كل نص يخرج من النظام إلى عميلة يعيش هنا، وهنا فقط. قبل هذا الملف
كانت النصوص حرفيات مبعثرة في business_logic.py وmessage_router.py
وsend_followups.py، فكان كل قاعدة صادرة تُكتب مرتين ولا يمكن ربط أي
رسالة بنتيجتها.

[الحدود الثابتة - §16 حرفياً]
كل Variant نص **معتمد بشرياً مسبقاً**. لا توليد حر · لا ML · لا
Bandits · لا Fine-tuning · لا اختيار آلي. هذا الملف مكتبة نصوص
ومعرّفات، ولا يحتوي أي منطق اختيار: `render()` تأخذ المعرّف الذي
قرره المُستدعي وتعيد نصه. من يقرر أي Variant يُرسَل هو شجرة القرار
في business_logic.py، وهي اليوم تملك خياراً واحداً لكل نية.

[ما هو variant_id]
سلسلة بالشكل `<intent>.v<n>`:
  - `intent` = الغرض الذي تخدمه الرسالة (وحدة المقارنة لاحقاً)
  - `.v<n>`  = الصياغة نفسها
مثال: `price_quote.v1`.

المعرّف يسمّي **صياغة واحدة بعينها**، لا "الرد الحالي لهذه النية".

[القاعدة الملزِمة: تعديل نص = معرّف جديد]
لا يُعاد استخدام معرّف لنص مختلف أبداً. حدث `RESPONSE_SENT` مكتوب
على القرص منذ أسبوع يقول "أُرسل price_quote.v1"؛ إن غُيّر نص
`price_quote.v1` اليوم صار ذلك الحدث يشير لنص لم يُرسَل قط، ولا شيء
لاحقاً يكشف ذلك. الصياغة الجديدة تأخذ `.v2`.

ولأن قاعدة كهذه تُنسى، يحمل كل حدث صادر `variant_hash` في حمولته:
بصمة **القالب** لا النص المُصاغ. النص المُصاغ يختلف بين عميلة وأخرى
(خدمة وسعر مختلفان) فبصمته لا تكشف شيئاً؛ بصمة القالب ثابتة لكل
رسائل الصياغة الواحدة، فتغيّرها في منتصف السجل دليل قاطع على تعديل
صامت. البصمة تكشف الخرق بعد وقوعه - لا تمنعه.

[حد الاثنين - §16]
حد أقصى Variant**ان** لكل نية؛ الثالث ممنوع (ثلاثة تقسّم عينة صغيرة
أصلاً). `_validate_registry()` ترفض الثالث لحظة الاستيراد، فلا يمكن
تجاوز الحد بالسهو.

اليوم لكل نية صياغة واحدة بالضبط. هذا التغيير يجعل النسب ممكناً،
ولا يبدأ أي تجربة.

[النصوص]
كل نص أدناه منقول حرفاً بحرف عن موضعه السابق - ولا حرف واحد تغيّر.
هذا التغيير ينقل النصوص ولا يعيد صياغتها: الصياغة قرار بشري (§16)،
ومراجعة الصياغة تُفصَل عن مراجعة البنية عمداً.

ملاحظة موثّقة: نص `booking_request_ack.v1` يقول "تم تسجيل حجزك"
بينما §8 يجعل ما وقع فعلاً `booking_requested` (طلب حجز، قبل تأكيد
الموظفة). التناقض حقيقي ومعروف، ويُترك هنا كما هو: تعديل نص يراه
عميل قرار بشري بصياغة جديدة (`.v2`)، لا أثر جانبي لإعادة هيكلة.
"""

import hashlib
from dataclasses import dataclass

VARIANT_ID_SEPARATOR = "."
MAX_VARIANTS_PER_INTENT = 2  # §16 - الثالث مرفوض


@dataclass(frozen=True)
class Variant:
    """
    صياغة معتمدة واحدة. مجمّدة (frozen): لا يعدّلها أي كود أثناء
    التشغيل - التعديل يحدث في هذا الملف بمراجعة بشرية، لا في الذاكرة.
    """
    variant_id: str
    intent: str
    template: str


# ------------------------------------------------- المسار الحي (ردود فورية)

_LIVE_VARIANTS = [
    Variant(
        variant_id="price_quote.v1",
        intent="price_quote",
        template=(
            "سعر {service_name} في {center_name} هو {price}.\n"
            "هل ترغبين بحجز موعد؟"
        ),
    ),
    Variant(
        variant_id="services_list.v1",
        intent="services_list",
        template=(
            "أهلاً بك في {center_name} 🌸\n"
            "هذه خدماتنا المتوفرة حالياً:\n{services_list}\n\n"
            "أي خدمة تودين الاستفسار عن سعرها؟"
        ),
    ),
    Variant(
        variant_id="ask_contact_info.v1",
        intent="ask_contact_info",
        template="ممتاز! الرجاء إرسال اسمك ورقم هاتفك في رسالة واحدة لتأكيد الحجز.",
    ),
    Variant(
        variant_id="decline_ack.v1",
        intent="decline_ack",
        template="تمام 🌸 إذا احتجتِ أي معلومة إضافية عن خدماتنا أنا موجودة.",
    ),
    Variant(
        variant_id="hesitant_ack.v1",
        intent="hesitant_ack",
        template=(
            "تمام 🌸 خذي وقتك براحتك، وإذا حبيتي تعرفين أي تفاصيل إضافية "
            "أو تقررين الحجز، إحنا موجودين لخدمتج."
        ),
    ),
    Variant(
        variant_id="booking_reply_reprompt.v1",
        intent="booking_reply_reprompt",
        template="هل ترغبين بتأكيد حجز موعد؟ (نعم / لا)",
    ),
    Variant(
        variant_id="booking_request_ack.v1",
        intent="booking_request_ack",
        template=(
            "شكراً لك 🌸 تم تسجيل حجزك لخدمة {service_name} في {center_name}.\n"
            "سيتواصل معك فريقنا خلال وقت قصير لتأكيد الموعد المناسب."
        ),
    ),
    # رسالة الخطأ صادرة كأي صادر آخر، فتحمل معرّفاً كأي صادر آخر.
    # بلا معرّف تصير الرسالة الوحيدة التي لا تُعدّ - وهي بالذات التي
    # يجب أن يُلاحَظ تكاثرها في السجل.
    Variant(
        variant_id="error_fallback.v1",
        intent="error_fallback",
        template="عذراً، صار خطأ بسيط 🙏 حاولي ترسلين رسالتك مرة ثانية.",
    ),
]


# ------------------------------------------------------- مسار المتابعة

_FOLLOWUP_VARIANTS = [
    Variant(
        variant_id="followup_1.v1",
        intent="followup_1",
        # price_suffix يحسبه المُستدعي: " (السعر)" أو "" حين لا سعر.
        # منطق الشرط بقي حيث كان - المكتبة تحمل نصاً لا فروعاً.
        template=(
            "مرحباً 🌸 لاحظنا أنك استفسرتِ سابقاً عن {service_name}{price_suffix} "
            "ولم يتم تأكيد الحجز بعد.\n"
            "هل ما زلتِ مهتمة؟ يسعدنا مساعدتك بحجز موعد مناسب في أي وقت."
        ),
    ),
    Variant(
        variant_id="followup_2.v1",
        intent="followup_2",
        template=(
            "🌸 آخر تذكير بخصوص {service_name} — إذا حابة تحجزين، "
            "احنا جاهزين نساعدج بأي وقت يناسبج."
        ),
    ),
]


def _validate_registry(variants: list[Variant]) -> dict[str, Variant]:
    """
    يُنفَّذ لحظة الاستيراد. أي خرق يوقف الإقلاع بدل أن يمرّ صامتاً
    إلى الإنتاج - نفس تشدّد التحقق من config عند الإقلاع.
    """
    registry: dict[str, Variant] = {}
    per_intent: dict[str, list[str]] = {}

    for variant in variants:
        if variant.variant_id in registry:
            raise ValueError(f"variant_id مكرر: {variant.variant_id}")

        expected_prefix = variant.intent + VARIANT_ID_SEPARATOR + "v"
        if not variant.variant_id.startswith(expected_prefix):
            raise ValueError(
                f"variant_id لا يطابق نيّته: {variant.variant_id} "
                f"(المتوقع أن يبدأ بـ{expected_prefix})"
            )
        if not variant.variant_id[len(expected_prefix):].isdigit():
            raise ValueError(
                f"variant_id لا ينتهي برقم صياغة: {variant.variant_id}"
            )

        per_intent.setdefault(variant.intent, []).append(variant.variant_id)
        if len(per_intent[variant.intent]) > MAX_VARIANTS_PER_INTENT:
            raise ValueError(
                f"النية '{variant.intent}' تجاوزت الحد الأقصى "
                f"({MAX_VARIANTS_PER_INTENT}) - §16: ثلاث صياغات تقسّم "
                f"عينة صغيرة أصلاً"
            )

        registry[variant.variant_id] = variant

    return registry


_REGISTRY = _validate_registry(_LIVE_VARIANTS + _FOLLOWUP_VARIANTS)


def get(variant_id: str) -> Variant:
    """
    يُرجع الصياغة أو يرمي KeyError. الرمي مقصود: معرّف غير مسجَّل خطأ
    برمجي لا حالة تشغيل. في المسار الحي يقع الرمي داخل شجرة القرار،
    وMessageRouter يلتقطه ويرسل error_fallback.v1 - فالعميلة تتلقى
    رداً، ولا تُرسَل رسالة بمعرّف مختلَق.
    """
    return _REGISTRY[variant_id]


def render(variant_id: str, **params) -> str:
    """يُصيغ نص الصياغة المطلوبة. لا اختيار هنا - المُستدعي قرر."""
    return get(variant_id).template.format(**params)


def intent_of(variant_id: str) -> str | None:
    """نية الصياغة، أو None لمعرّف غير مسجَّل (لا ترمي - تُستدعى أثناء التسجيل)."""
    variant = _REGISTRY.get(variant_id)
    return variant.intent if variant else None


def template_hash(variant_id: str | None) -> str | None:
    """
    بصمة القالب (8 خانات ست عشرية) - انظر ترويسة الملف. None لمعرّف
    غير مسجَّل أو غائب: الحدث يُكتب ناقص البصمة ولا يسقط بسببها.
    """
    variant = _REGISTRY.get(variant_id) if variant_id else None
    if variant is None:
        return None
    return hashlib.sha256(variant.template.encode("utf-8")).hexdigest()[:8]


def all_variant_ids() -> list[str]:
    """كل المعرّفات المسجّلة بترتيب التعريف - للاختبارات والتقارير."""
    return list(_REGISTRY)
