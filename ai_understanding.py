"""
AI Understanding Layer
========================
يفهم نية العميلة من نص رسالتها + سياق قصير، ويُرجع تصنيفاً منظماً
(JSON) فقط. لا يقرر السعر، لا يغيّر حالة العميل، لا يسجّل حجزاً أو
Lead، ولا يرسل أي رسالة مباشرة. Business Logic يبقى مصدر الحقيقة
الوحيد - هذه الطبقة تفهم وتصنّف فقط.

يتطلب متغير البيئة OPENAI_API_KEY (لا يُقرأ أو يُطبع المفتاح نفسه
في أي مكان).
"""

import os
import json
from openai import OpenAI

MODEL_NAME = "gpt-4o-mini"

ALLOWED_INTENTS = {
    "price_inquiry",
    "confirm_booking",
    "decline",
    "hesitant",
    "ask_more_info",
    "other",
}

FALLBACK_RESULT = {"intent": "other", "service_mentioned": None}

SYSTEM_PROMPT = """أنت طبقة فهم لغوي فقط ضمن نظام لعيادات ومراكز تجميل عراقية. مهمتك الوحيدة: تصنيف نية رسالة العميلة - وليس الرد عليها أو اتخاذ أي قرار عن الأسعار أو الحجوزات.

أرجع JSON فقط بهذا الشكل بالضبط، بدون أي نص إضافي وبدون markdown:
{"intent": "<إحدى القيم: price_inquiry, confirm_booking, decline, hesitant, ask_more_info, other>", "service_mentioned": "<اسم الخدمة كما فهمتها من كلام العميلة أو null>"}

معاني النوايا، مع أمثلة عراقية شائعة (هذه أمثلة توضيحية لمعنى النية فقط، افهم القصد وليس الكلمة الحرفية):
- price_inquiry: تسأل عن سعر خدمة. مثل: "شكد سعر الفيلر؟"، "بكم البوتوكس؟"، "كم سعر التنظيف؟"
- confirm_booking: توافق على الحجز فعلاً. مثل: "إي احجزيلي"، "تمام خل نحجز"، "يلا احجزي"، "اكيد"
- decline: ترفض الحجز أو لا تريده حالياً برفض واضح. مثل: "لا شكراً"، "مو حالياً"
- hesitant: متردّدة أو تحتاج وقتاً للتفكير، ليس رفضاً نهائياً. مثل: "خلي أفكر وأردلك"، "مو هسه"، "يمكن بعدين"، "أشوف وأرجعلك"، "السعر شوي غالي"
- ask_more_info: تسأل عن تفاصيل الخدمة نفسها، وليس سعرها أو حجزها. مثل: "شنو يشمل الفيلر؟"، "ممكن أعرف التفاصيل؟"، "عندي سؤال"
- other: أي شيء آخر لا يتعلق بما سبق (تحية، كلام عام، غير مفهوم)

القاعدة الأهم: أرجع فقط JSON صالح بهذا الشكل بالضبط، بدون أي نص قبله أو بعده."""

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit(
                "الرجاء ضبط متغير البيئة OPENAI_API_KEY أولاً.\n"
                'مثال في PowerShell: $env:OPENAI_API_KEY="your_api_key"'
            )
        _client = OpenAI(api_key=api_key)
    return _client


def _build_user_prompt(current_text: str, recent_history: list, session_state: str) -> str:
    lines = []
    if recent_history:
        lines.append("آخر رسائل من نفس المحادثة (للسياق فقط):")
        for turn in recent_history:
            speaker = "العميلة" if turn.get("role") == "user" else "النظام"
            lines.append(f"{speaker}: {turn.get('text', '')}")
        lines.append("")
    lines.append(f"حالة الجلسة الحالية: {session_state}")
    lines.append(f'رسالة العميلة الحالية: "{current_text}"')
    return "\n".join(lines)


def _validate_and_normalize(parsed: dict) -> dict:
    """
    يتحقق أن intent ضمن القائمة المسموحة وأن service_mentioned إما
    نص أو None. أي خلل يُرجع FALLBACK_RESULT الآمن.
    """
    if not isinstance(parsed, dict):
        return dict(FALLBACK_RESULT)

    intent = parsed.get("intent")
    service_mentioned = parsed.get("service_mentioned")

    if intent not in ALLOWED_INTENTS:
        print(f"[AI Understanding] intent غير مسموح: {intent!r} - استخدام fallback")
        return dict(FALLBACK_RESULT)

    if service_mentioned is not None and not isinstance(service_mentioned, str):
        service_mentioned = None

    return {"intent": intent, "service_mentioned": service_mentioned}


def understand_message(current_text: str, recent_history: list = None, session_state: str = "idle") -> dict:
    """
    يُرجع دائماً {"intent": ..., "service_mentioned": ...}.
    لا يرمي أي استثناء أبداً - أي فشل (شبكة، JSON غير صالح، intent
    غير مسموح) يُرجع FALLBACK_RESULT الآمن حتى لا يتعطل النظام.
    """
    recent_history = recent_history or []
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(current_text, recent_history, session_state)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw_content = response.choices[0].message.content
        parsed = json.loads(raw_content)
        return _validate_and_normalize(parsed)

    except SystemExit:
        raise  # فشل ضبط المفتاح يجب أن يوقف البرنامج بوضوح، وليس أن يُخفى بـ fallback
    except Exception as e:
        print(f"[AI Understanding] خطأ أثناء الفهم: {e} - استخدام fallback")
        return dict(FALLBACK_RESULT)