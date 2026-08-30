"""
قاعدة معرفة الخدمات - تُقرأ من إعداد العيادة الخارجي
==========================================================================
اسم المركز والخدمات والأسعار لم تعد مكتوبة هنا مباشرة - تُقرأ من
config/clinic_config.json عند بدء التشغيل، حتى يمكن تجهيز عيادة
جديدة بتعديل ملف نصي واحد فقط، دون لمس أي كود Python.

إذا كان الملف مفقوداً أو JSON غير صالح أو ناقص البنية المطلوبة،
يتوقف البرنامج فوراً برسالة واضحة - لا fallback لبيانات افتراضية،
ولا تشغيل ببيانات فارغة أو وهمية.

[التغيير #6 - كشف الغموض] `find_service()` غيّرت عقدها عن قصد: كانت
تُرجع **أول** خدمة تطابق، فرسالة تذكر كلمة تشترك فيها ثلاث خدمات
كانت تُسعَّر بسعر أولاها بترتيب الإعداد - قراراً صامتاً بلا أي أثر.
صارت تُرجع خدمة **فقط عند التطابق الوحيد**، وNone عند صفر أو أكثر من
واحدة. `find_services()` الجديدة تُرجع كل المطابقات، فمن يحتاج التمييز
بين «لا شيء» و«أكثر من واحد» يملكه الآن صراحةً.

المطابقة نفسها انتقلت إلى matching.py: صارت بحدود الكلمة بدل السلسلة
الفرعية - انظر ترويسة ذلك الملف. `normalize_arabic` تعيش هناك وتُعاد
تصديرها من هنا، فكل `from services import normalize_arabic` قائم كما
كان.
"""

import json
import os

import matching
from matching import normalize_arabic  # noqa: F401 - إعادة تصدير: انظر الترويسة

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


def find_services(text: str) -> list[dict]:
    """
    كل الخدمات التي تُذكر إحدى كلماتها المفتاحية في النص، بترتيب
    الإعداد. الترتيب هو ترتيب config/clinic_config.json حرفياً: هو
    الترتيب الذي سيراه العميل في سؤال التوضيح، وثباته يجعل الرقم الذي
    ترسله العميلة («2») يعني نفس الخدمة في كل مرة.

    الخدمة الواحدة تظهر مرة واحدة مهما تعددت كلماتها المطابقة.
    """
    return [
        service for service in SERVICES
        if matching.matches_any(text, service["keywords"])
    ]


def find_service(text: str):
    """
    الخدمة المقصودة، أو None.

    None تعني **لا خدمة واحدة مؤكَّدة**: إما لم تُذكر أي خدمة، أو
    ذُكرت أكثر من واحدة فلا يملك هذا الملف حسم أيّها المقصودة. من
    يحتاج التمييز بين الحالتين يستدعي find_services().
    """
    found = find_services(text)
    return found[0] if len(found) == 1 else None


def find_service_by_name(name: str):
    """
    الخدمة صاحبة هذا الاسم بالضبط، أو None إن لم تعد موجودة في
    الإعداد. الجلسة تحفظ أسماء لا كائنات، وهذه هي بوابة العودة من
    الاسم المحفوظ إلى الخدمة الحيّة بسعرها الحالي - فلو حُذفت خدمة من
    الإعداد وسط محادثة عادت None ولم يُعرَض سعر خدمة ملغاة.
    """
    for service in SERVICES:
        if service["name"] == name:
            return service
    return None


def services_list_text() -> str:
    lines = [f"• {s['name']} — {s['price']}" for s in SERVICES]
    return "\n".join(lines)


def service_options_text(service_names: list[str]) -> str:
    """
    قائمة الخيارات المرقّمة لسؤال التوضيح - **أسماء بلا أسعار**.

    السعر غائب عمداً: سؤال التوضيح يسبق تحديد الخدمة، وعرض ثلاثة
    أسعار فيه يجعل الرسالة رداً بالسعر عملياً - فتصير العميلة مسعَّرة
    دون أن يُنشأ لها Lead، وهو بالضبط الفراغ الذي يغلقه هذا التغيير.
    السعر يُعرض في price_quote.v1 وحده، بعد أن تُحسَم الخدمة.

    الترقيم يبدأ من 1 ويطابق ترتيب القائمة المحفوظة في الجلسة، فرقم
    ترسله العميلة يقود إلى نفس العنصر الذي رأته.
    """
    return "\n".join(f"{i}. {name}" for i, name in enumerate(service_names, 1))