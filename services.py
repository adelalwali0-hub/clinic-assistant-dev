"""
قاعدة معرفة الخدمات - تُقرأ من إعداد العيادة الخارجي
==========================================================================
اسم المركز والخدمات والأسعار لم تعد مكتوبة هنا مباشرة - تُقرأ من
config/clinic_config.json عند بدء التشغيل، حتى يمكن تجهيز عيادة
جديدة بتعديل ملف نصي واحد فقط، دون لمس أي كود Python.

إذا كان الملف مفقوداً أو JSON غير صالح أو ناقص البنية المطلوبة،
يتوقف البرنامج فوراً برسالة واضحة - لا fallback لبيانات افتراضية،
ولا تشغيل ببيانات فارغة أو وهمية.

find_service() وservices_list_text() لم يتغيّر منطقهما إطلاقاً -
نفس التوقيع ونفس السلوك تماماً كما كانا قبل هذا التعديل.
"""

import json
import os
import re

CONFIG_PATH = os.path.join("config", "clinic_config.json")


def _load_clinic_config() -> dict:
    if not os.path.isfile(CONFIG_PATH):
        raise SystemExit(
            f"ملف إعداد العيادة غير موجود: {CONFIG_PATH}\n"
            "الرجاء إنشاء هذا الملف قبل تشغيل النظام."
        )

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"ملف إعداد العيادة {CONFIG_PATH} يحتوي JSON غير صالح: {e}")

    if not isinstance(data, dict):
        raise SystemExit(f"ملف إعداد العيادة {CONFIG_PATH} يجب أن يكون كائن JSON (object) في المستوى الأعلى.")

    if "center_name" not in data or not isinstance(data["center_name"], str) or not data["center_name"].strip():
        raise SystemExit(f"ملف إعداد العيادة {CONFIG_PATH} يفتقد حقل 'center_name' نصياً صالحاً.")

    if "services" not in data or not isinstance(data["services"], list) or len(data["services"]) == 0:
        raise SystemExit(f"ملف إعداد العيادة {CONFIG_PATH} يفتقد حقل 'services' كقائمة غير فارغة.")

    for i, svc in enumerate(data["services"]):
        if not isinstance(svc, dict):
            raise SystemExit(f"العنصر رقم {i} داخل 'services' في {CONFIG_PATH} يجب أن يكون كائن JSON.")
        for field in ("name", "keywords", "price"):
            if field not in svc:
                raise SystemExit(f"العنصر رقم {i} داخل 'services' في {CONFIG_PATH} يفتقد الحقل '{field}'.")
        if not isinstance(svc["name"], str) or not svc["name"].strip():
            raise SystemExit(f"العنصر رقم {i} داخل 'services': حقل 'name' يجب أن يكون نصاً غير فارغ.")
        if not isinstance(svc["keywords"], list) or len(svc["keywords"]) == 0:
            raise SystemExit(f"العنصر رقم {i} داخل 'services': حقل 'keywords' يجب أن يكون قائمة غير فارغة.")
        if not isinstance(svc["price"], str) or not svc["price"].strip():
            raise SystemExit(f"العنصر رقم {i} داخل 'services': حقل 'price' يجب أن يكون نصاً غير فارغ.")

    return data


_config = _load_clinic_config()

CENTER_NAME = _config["center_name"]
SERVICES = _config["services"]


def normalize_arabic(text: str) -> str:
    """
    توحيد النص العربي قبل المطابقة:
    - إزالة "أل" التعريف من بداية كل كلمة (البشرة -> بشرة)
    - توحيد أشكال الألف والتاء المربوطة/الهاء الشائعة
    - إزالة المسافات الزائدة
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