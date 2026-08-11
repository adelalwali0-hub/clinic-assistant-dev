"""
قاعدة معرفة الخدمات - Luna Beauty Center (مركز افتراضي للعرض التجريبي)
==========================================================================
بيانات تجريبية بالكامل، تُستخدم فقط لإثبات قدرة النظام على تحويل
الاستفسارات إلى حجوزات. لاحقاً ستُستبدل بقاعدة بيانات حقيقية خاصة
بكل عيادة عميل.
"""

import re

CENTER_NAME = "Luna Beauty Center"

SERVICES = [
    {
        "name": "تنظيف بشرة",
        "keywords": ["تنظيف بشرة", "تنظيف وجه", "فيشل", "facial"],
        "price": "35,000 دينار",
    },
    {
        "name": "حقن البوتوكس",
        "keywords": ["بوتوكس", "botox"],
        "price": "120,000 دينار",
    },
    {
        "name": "حقن الفيلر",
        "keywords": ["فيلر", "filler"],
        "price": "150,000 دينار",
    },
    {
        "name": "إزالة الشعر بالليزر (جلسة واحدة)",
        "keywords": ["ليزر", "ازالة شعر", "إزالة شعر", "laser"],
        "price": "40,000 دينار",
    },
    {
        "name": "التقشير الكيميائي",
        "keywords": ["تقشير", "peeling"],
        "price": "50,000 دينار",
    },
]


def normalize_arabic(text: str) -> str:
    """
    توحيد النص العربي قبل المطابقة:
    - إزالة "أل" التعريف من بداية كل كلمة (البشرة -> بشرة)
    - توحيد أشكال الألف والتاء المربوطة/الهاء الشائعة
    - إزالة المسافات الزائدة
    هذا يحل مشكلة عدم التعرف على الكلمات بسبب اختلاف الصياغة
    الطبيعية التي تكتبها العميلات (مثل "تنظيف البشرة" مقابل
    "تنظيف بشرة" المسجّلة ككلمة مفتاحية).
    """
    normalized = text.strip().lower()
    normalized = re.sub(r"[إأآا]", "ا", normalized)
    normalized = re.sub(r"ة", "ه", normalized)
    words = normalized.split()
    words = [w[2:] if w.startswith("ال") and len(w) > 2 else w for w in words]
    return " ".join(words)


def find_service(text: str):
    """يبحث عن أول خدمة تُذكر كلماتها المفتاحية داخل نص الرسالة (بعد التوحيد)"""
    normalized_text = normalize_arabic(text)
    for service in SERVICES:
        for kw in service["keywords"]:
            if normalize_arabic(kw) in normalized_text:
                return service
    return None


def services_list_text() -> str:
    lines = [f"• {s['name']} — {s['price']}" for s in SERVICES]
    return "\n".join(lines)