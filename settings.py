"""
إعداد التشغيل - Runtime Settings (PRD §19، §18)
==========================================================================
التوقيت وحدود القبول ووضع التشغيل لم تعد ثوابت في الكود. §19 صريح:
«التوقيت والقالب وقدرة القناة على حمل نص حر → معطيات لكل قناة، لا
ثوابت في الكود». هذا الملف يقرأ `config/runtime_config.json` عند بدء
التشغيل ويكون المصدر الوحيد لتلك القيم.

[لماذا ملف ثانٍ منفصل عن clinic_config.json]
`clinic_config.json` حقائق العيادة التجارية: الاسم والخدمات والأسعار.
تحرّره العيادة، وS1 يسمّيه مرجع السعر الوحيد. هذا الملف إعداد تشغيلي:
وضع التشغيل والتوقيت والحواجز. يحرّره المشغّل. الفصل ليس ترتيباً - في
ملف واحد تصير العيادة على بُعد ضغطة واحدة من قلب `mode` إلى `direct`،
وهو المفتاح الذي يحكم إنفاذ حواجز السلامة.

[الانتهاء عند الخطأ - وما الفرق بين الغياب والفساد]
مفتاح **غائب** يأخذ قيمة اليوم الافتراضية. هذا مشروع: الملف كله
اختياري، وغيابه يعني «شغّل بما كان مكتوباً في الكود قبل هذا الملف».

مفتاح **موجود لكنه فاسد** (نوع خاطئ، خارج المدى، وضع غير معروف) يوقف
البرنامج. لا يسقط إلى الافتراضي بصمت أبداً: السقوط الصامت هو ما ينتج
«ضبطنا النافذة على 6 وظل يرسل عند 24» - إعدادٌ يكذب على قارئه. الغياب
قرار، والفساد خطأ، ولا يُعامَلان بنفس الطريقة.

الأخطاء **تُجمع كلها** ثم تُعرض مرة واحدة. الخروج عند أول خطأ يجعل
ضبط عشرة مفاتيح عشر دورات تشغيل-فشل-تصحيح.

[مفاتيح `_` - التوثيق داخل الإعداد]
JSON لا يحمل تعليقات، ومن يبحث عن `first_followup_hours` المحذوف يجب
أن يجد سبب غيابه في الملف نفسه لا في الكود. لذلك كل مفتاح يبدأ بـ`_`
يُتجاهَل كتوثيق. المفاتيح **المجهولة** غير ذلك تُرفض: مفتاح مكتوب خطأً
(`silence_window_hour`) يُقرأ اليوم كأنه غير موجود، فيعمل النظام
بالافتراضي بينما يظن كاتبه أنه ضبطه.

[الحواجز والوضع - §18 مُنفَّذاً لا موصوفاً]
§18 يؤجّل S6 وS7 وS8 في وضع Concierge، ويشترط عودتها 🔴 «لحظة إرسال
النظام رسالة واحدة تلقائياً». هذا الشرط كان جملة في وثيقة لا يعرفها
الكود. الآن `mode: "direct"` بحواجز غير مبنية = رفض إقلاع.

الحواجز الثلاثة مسجّلة أدناه بعلامة «مبني؟». من يبني S8 يقلب علامتها
سطراً واحداً. لا كشف تلقائي: لا يوجد ما يُكتشف - الحواجز غير موجودة
أصلاً، والسجل الصريح أصدق من فحصٍ يوهم بأنه يفحص.

[لماذا يقع الحارس عند الاستيراد]
نفس سابقة `services.py`: أي مسار يستورد الإعداد يمرّ بالفحص. يشمل هذا
`check_followups.py` وهو تقرير للقراءة لا يرسل شيئاً - وذلك مقصود:
تقرير عن نظام لا يجوز أن يعمل أسوأ من لا تقرير، لأنه يبدو دليلاً.

[التحذير - درجة ثالثة بين الغياب والفساد]
الغياب يأخذ الافتراضي، والفساد يوقف الإقلاع. بينهما حالة ثالثة: إعداد
**صالح** يُنتج سلوكاً على الأرجح غير مقصود. لا يجوز رفضه - هو خيار
مشروع للمشغّل - ولا يجوز ابتلاعه صامتاً. يُطبع على stderr ويكمل الإقلاع.

اليوم له حالة واحدة: نافذة إرسال أضيق من نصف اليوم (انظر
`_warn_if_send_window_is_narrow`).
"""

import json
import os
import sys

CONFIG_PATH = os.path.join("config", "runtime_config.json")

MODE_CONCIERGE = "concierge"
MODE_DIRECT = "direct"
_MODES = (MODE_CONCIERGE, MODE_DIRECT)

_MINUTES_PER_DAY = 24 * 60

# القيم الافتراضية = ما كان مكتوباً في الكود حرفياً قبل هذا الملف.
# تغييرها هنا يغيّر سلوك كل تركيب لا يضبط المفتاح صراحةً.
_DEFAULT_SILENCE_WINDOW_HOURS = 24      # كان leads_store.SILENCE_WINDOW_HOURS
_DEFAULT_SECOND_FOLLOWUP_HOURS = 72     # كان send_followups.SECOND_FOLLOWUP_HOURS
_DEFAULT_EXPIRE_AFTER_HOURS = 72        # كان send_followups.EXPIRE_AFTER_HOURS
_DEFAULT_MIN_CONTACT_DIGITS = 9         # كان contact_info.MIN_CONTACT_DIGITS

# حدّ أدنى مفروض في الكود لا في الإعداد. تسعة ليست تفضيلاً بل ادعاء عن
# فضاء الأرقام تثبته ترويسة contact_info: «تحت التسعة لا يوجد رقم تواصل
# عراقي صالح». مفتاح يهبط تحت قيمة يثبت الكود استحالتها لا ينتج إلا
# ضرراً - وهو ضرر F9 بعينه: صفوف حجز ملفّقة في leads.csv، الملف الذي
# تفترض بوابة Gate A أنه جدير بالثقة.
#
# والسقف لسبب مختلف: ترويسة contact_info تقول إن رفض بيانات تواصل
# حقيقية **أسوأ** من الثغرة. خطأ طباعي (99) يرفض كل عميلة إلى الأبد
# بصمت. الاتجاهان مكلفان، ويختلفان في النوع: الهبوط يكتب بيانات ملفّقة
# بلا أثر، والارتفاع يُنتج إعادة سؤال مرئية تستطيع العميلة تجاوزها.
MIN_CONTACT_DIGITS_FLOOR = 9
MIN_CONTACT_DIGITS_CEILING = 15

# حواجز §18 المؤجَّلة في Concierge والملزِمة في الوضع المباشر.
# القيمة = (الوصف، مبني؟). من يبني حاجزاً يقلب علامته هنا.
_RAILS_REQUIRED_FOR_DIRECT = (
    ("S6", "`automation_paused` لكل Lead", True),
    ("S7", "حد أقصى صارم لعدد الرسائل لكل Lead", False),
    ("S8", "ساعات إرسال آمنة", True),
)

# S6 لا يضيف اشتراط إعداد كما فعل S8 مع `send_window`، والفرق ليس سهواً:
# S8 عتبة بلا قيمة افتراضية صادقة - «أرسل في أي ساعة» ليست نافذة آمنة -
# فحاجزه يحتاج رقماً مضبوطاً ليمنع شيئاً. S6 آلية لا عتبة: تعمل كاملةً
# بلا أي مفتاح، وقيمتها تُكتب لكل عميلة على حدة لا في ملف إعداد.
#
# ⚠️ ما يبقى مفتوحاً في الوضع المباشر ليس الآلية بل مُشغِّلها: الإيقاف
# اليوم يضبطه المشغّل بعد قراءة الرسالة (pause_automation.py)، وفي
# الوضع المباشر لا أحد يقرأ. الآلية مبنية ومُختبَرة، ومصدرها البشري
# وحده لا يكفي يوم يُقلَب الوضع. D-023.

# مفاتيح تُقبل وتُتحقَّق ولا تُقرأ. §19: «لا يُبنى أي كود خاص بواتساب
# الآن. تُستوعب القيود في شكل المعطيات فقط». شكلها مثبَّت من اليوم حتى
# لا تحتاج S7 هجرة إعداد يوم تصير 🔴، بل قراءة مفتاح وحارساً.
#
# `send_window` كان هنا حتى بُني S8. خروجه من القائمة هو كامل الفرق بين
# «شكل مثبَّت» و«حاجز نافذ»: نفس المفتاح، نفس الشكل، بلا هجرة إعداد.
_RESERVED_CHANNEL_KEYS = ("max_messages_per_lead",)


def _is_doc_key(key: str) -> bool:
    """مفاتيح التوثيق داخل JSON - انظر الترويسة."""
    return key.startswith("_")


def _check_unknown_keys(obj: dict, allowed: tuple, where: str, errors: list) -> None:
    for key in obj:
        if _is_doc_key(key) or key in allowed:
            continue
        errors.append(
            f"{where}: مفتاح غير معروف '{key}'. "
            f"المسموح: {'، '.join(allowed)} (وأي مفتاح يبدأ بـ'_' توثيق يُتجاهَل)"
        )


def _positive_number(obj: dict, key: str, default, where: str, errors: list):
    """
    عدد موجب من الإعداد، أو الافتراضي عند الغياب.

    الغياب يُرجع الافتراضي. الوجود بنوع خاطئ أو قيمة غير موجبة خطأ
    يُجمع - لا سقوط صامت إلى الافتراضي. `bool` مرفوض صراحةً: True في
    بايثون عدد صحيح قيمته 1، وساعةٌ قيمتها `true` خطأ مكتوب لا نية.
    """
    if key not in obj:
        return default
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{where}: '{key}' يجب أن يكون عدداً، لا {type(value).__name__}")
        return default
    if value <= 0:
        errors.append(f"{where}: '{key}' يجب أن يكون أكبر من صفر، لا {value}")
        return default
    return value


def _load_file(errors: list) -> dict:
    """
    محتوى ملف الإعداد، أو {} إن لم يوجد.

    غياب الملف كله مشروع كغياب أي مفتاح: النظام يعمل بقيم اليوم. ملف
    موجود لكنه JSON فاسد خطأ - كتبه أحدهم ويظن أنه نافذ.
    """
    if not os.path.isfile(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"ملف إعداد التشغيل {CONFIG_PATH} يحتوي JSON غير صالح: {e}")
        return {}
    except OSError as e:
        errors.append(f"تعذّرت قراءة ملف إعداد التشغيل {CONFIG_PATH}: {e}")
        return {}
    if not isinstance(data, dict):
        errors.append(f"ملف إعداد التشغيل {CONFIG_PATH} يجب أن يكون كائن JSON في المستوى الأعلى.")
        return {}
    return data


def _read_mode(data: dict, errors: list) -> str:
    mode = data.get("mode", MODE_CONCIERGE)
    if mode not in _MODES:
        errors.append(
            f"'mode' قيمته '{mode}' وهي غير معروفة. المسموح: {'، '.join(_MODES)}"
        )
        return MODE_CONCIERGE
    return mode


def _read_channel(data: dict, errors: list) -> tuple:
    """
    القناة الوحيدة المُعدَّة وتوقيت متابعتها.

    التعشيش `channels.<اسم>.followup` شكل §19: التوقيت معطى لكل قناة لا
    ثابت عام. لكن الكود اليوم لا يوجّه بين قنوات - `leads_store` لا يعرف
    قناة أصلاً - فقناتان في الإعداد حالة لا يستطيع الكود تنفيذها.
    تُرفض صراحةً بدل أن تُقرأ إحداهما وتُهمَل الأخرى بصمت. هذا هو
    القيد المضاد في §19 حرفياً: تُستوعب القناة في شكل المعطيات، ولا
    يُبنى توجيه قنوات اليوم.
    """
    channels = data.get("channels")
    if channels is None:
        return "telegram", {}, None
    if not isinstance(channels, dict):
        errors.append("'channels' يجب أن يكون كائن JSON يربط اسم كل قناة بإعدادها.")
        return "telegram", {}, None

    names = [name for name in channels if not _is_doc_key(name)]
    if not names:
        errors.append("'channels' موجود لكنه بلا أي قناة. احذفه أو ضع قناة واحدة.")
        return "telegram", {}, None
    if len(names) > 1:
        errors.append(
            f"'channels' يحمل {len(names)} قنوات ({'، '.join(names)}) والكود اليوم لا "
            "يوجّه بين قنوات: توقيت المتابعة قيمة واحدة في leads_store. "
            "ضع قناة واحدة حتى يُبنى توجيه القنوات."
        )
        return names[0], {}, None

    name = names[0]
    channel = channels[name]
    if not isinstance(channel, dict):
        errors.append(f"'channels.{name}' يجب أن يكون كائن JSON.")
        return name, {}, None

    _check_unknown_keys(
        channel,
        ("followup", "send_window") + _RESERVED_CHANNEL_KEYS,
        f"'channels.{name}'",
        errors,
    )
    _validate_reserved(channel, name, errors)
    send_window = _read_send_window(channel, name, errors)

    followup = channel.get("followup", {})
    if not isinstance(followup, dict):
        errors.append(f"'channels.{name}.followup' يجب أن يكون كائن JSON.")
        return name, {}, send_window
    return name, followup, send_window


def _validate_reserved(channel: dict, name: str, errors: list) -> None:
    """
    المفاتيح المحجوزة: تُتحقَّق ولا تُقرأ (S7 غير مبني).

    التحقق رغم عدم القراءة مقصود: إعداد يمرّ اليوم صامتاً ثم ينكسر يوم
    يُقرأ هو فخ مؤجَّل. شكلها يُثبَّت وهي فارغة المفعول.
    """
    if "max_messages_per_lead" in channel:
        value = channel["max_messages_per_lead"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            errors.append(
                f"'channels.{name}.max_messages_per_lead' يجب أن يكون عدداً صحيحاً موجباً "
                "(محجوز لـS7 - يُتحقَّق ولا يُقرأ بعد)"
            )


def _read_send_window(channel: dict, name: str, errors: list):
    """
    نافذة الإرسال الآمنة (S8) - دقيقتان من منتصف الليل، أو None.

    [الغياب ليس نافذة مفتوحة بالصدفة - إنه سلوك اليوم حرفياً]
    غياب المفتاح يعني «لا قيد»، تماماً كما كان الحال قبل هذا الحاجز.
    هذا هو نفس عقد بقية الملف: الغياب يأخذ قيمة اليوم. لكنه يعني أيضاً
    أن S8 **مبني وغير نافذ** - ولهذا يشترط `_check_mode_rails` وجود
    النافذة صراحةً في الوضع المباشر: حاجز بلا قيمة مضبوطة لا يمنع شيئاً.

    [الحدّان المتساويان يوقفان الإقلاع]
    `from == to` تُكتب بها النافذة الصفرية (لا إرسال أبداً) والنافذة
    الممتدة على اليوم كله (إرسال دائم) بنفس الشكل تماماً، ولا يملك
    الكود ما يميّز بينهما. أي تفسير نختاره يكون تخميناً لنيّة كاتبها،
    وأحد التفسيرين يُسكت النظام إلى الأبد بصمت.
    """
    if "send_window" not in channel:
        return None

    window = channel["send_window"]
    where = f"'channels.{name}.send_window'"
    if not isinstance(window, dict):
        errors.append(f"{where} يجب أن يكون كائن JSON بمفتاحَي 'from' و'to'")
        return None

    _check_unknown_keys(window, ("from", "to"), where, errors)

    edges = {}
    for edge in ("from", "to"):
        if edge not in window:
            errors.append(f"{where} يفتقد '{edge}'")
        elif not _is_hhmm(window[edge]):
            errors.append(f"{where}.{edge} يجب أن يكون وقتاً بصيغة HH:MM، لا {window[edge]!r}")
        else:
            edges[edge] = _hhmm_to_minutes(window[edge])

    if len(edges) != 2:
        return None

    if edges["from"] == edges["to"]:
        errors.append(
            f"{where}: 'from' و'to' متساويان ({window['from']!r}) - وبهذا الشكل نفسه "
            "تُكتب النافذة الصفرية والنافذة الممتدة على اليوم كله، ولا يميّز الكود "
            "بينهما. احذف 'send_window' كله ليصير الإرسال بلا قيد، أو اجعل الحدّين مختلفين."
        )
        return None

    return (edges["from"], edges["to"])


def _is_hhmm(value) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hh, mm = value[:2], value[3:]
    if not (hh.isdigit() and mm.isdigit()):
        return False
    return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59


def _hhmm_to_minutes(value: str) -> int:
    return int(value[:2]) * 60 + int(value[3:])


def _read_session_ttl(data: dict, silence_window, errors: list):
    """
    مهلة الجلسة. الافتراضي = نافذة الصمت نفسها، لا الرقم 24.

    كانت المساواة تعليقاً في ترويسة session_store يحرسه انضباط بشري:
    ثابتان في ملفين يجب أن يتحركا معاً. الآن الافتراضي مشتقّ، والاشتراط
    مفروض.

    الاتجاه الخطر واحد: TTL **أكبر** من نافذة الصمت. بينهما تكون العميلة
    قد دخلت دورة المتابعة (عُوملت صامتة، وربما خرجت لها متابعة) بينما
    جلستها ما زالت تدّعي محادثة جارية - فيُقرأ ردّها جواباً على سؤال
    حيّ وقد سبقته متابعة تناقضه. العكس (TTL أصغر) مشروع: تُعامَل رسالتها
    معاملة رسالة جديدة قبل أن تُصبح مؤهلة للمتابعة، وهو سلوك مرئي لا
    يناقض سجل الـLeads.

    الاشتقاق هنا لا في session_store: تلك طبقة تخزين لا تعرف طبقة
    الـLeads (انظر ترويستها)، فالربط يقع في الإعداد ويقرأ منه الاثنان.
    """
    session = data.get("session")
    if session is None:
        return silence_window
    if not isinstance(session, dict):
        errors.append("'session' يجب أن يكون كائن JSON.")
        return silence_window

    _check_unknown_keys(session, ("ttl_hours",), "'session'", errors)
    ttl = _positive_number(session, "ttl_hours", silence_window, "'session'", errors)

    if ttl > silence_window:
        errors.append(
            f"'session.ttl_hours' = {ttl:g} أكبر من نافذة الصمت {silence_window:g}. "
            "بينهما تكون العميلة في دورة المتابعة بينما جلستها تدّعي محادثة جارية. "
            f"اجعلها {silence_window:g} أو أقل."
        )
    return ttl


def _read_min_contact_digits(data: dict, errors: list) -> int:
    contact = data.get("contact")
    if contact is None:
        return _DEFAULT_MIN_CONTACT_DIGITS
    if not isinstance(contact, dict):
        errors.append("'contact' يجب أن يكون كائن JSON.")
        return _DEFAULT_MIN_CONTACT_DIGITS

    _check_unknown_keys(contact, ("min_digits",), "'contact'", errors)
    if "min_digits" not in contact:
        return _DEFAULT_MIN_CONTACT_DIGITS

    value = contact["min_digits"]
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(
            f"'contact.min_digits' يجب أن يكون عدداً صحيحاً، لا {type(value).__name__}"
        )
        return _DEFAULT_MIN_CONTACT_DIGITS
    if not (MIN_CONTACT_DIGITS_FLOOR <= value <= MIN_CONTACT_DIGITS_CEILING):
        errors.append(
            f"'contact.min_digits' = {value} خارج المدى المسموح "
            f"[{MIN_CONTACT_DIGITS_FLOOR}, {MIN_CONTACT_DIGITS_CEILING}]. "
            f"تحت {MIN_CONTACT_DIGITS_FLOOR} تُقبل رسائل ليست أرقام تواصل فتُكتب صفوف "
            "حجز ملفّقة في leads.csv (F9)؛ وفوق السقف تُرفض أرقام حقيقية."
        )
        return _DEFAULT_MIN_CONTACT_DIGITS
    return value


def _rails_blocking_direct() -> list:
    return [f"{code} ({label})" for code, label, built in _RAILS_REQUIRED_FOR_DIRECT if not built]


def _check_mode_rails(mode: str, send_window, errors: list) -> None:
    """
    §18 مُنفَّذاً: لا وضع مباشر بحواجز غير مبنية - ولا بحاجز مبنيٍّ فارغ.

    في Concierge يضغط «إرسال» إنسان، فS6 وS7 مؤجَّلان بقرار موثّق.
    التأجيل مشروط بشكل التشغيل لا بالوقت، ويسقط عند أول رسالة آلية.

    الشرط الثاني وُلد مع S8: قلبُ علامة «مبني؟» وحده كان سيسمح للوضع
    المباشر بالإقلاع بلا `send_window` في الإعداد - أي بحاجز حاضر في
    الكود ولا يمنع شيئاً، وهو أسوأ من حاجز غائب لأنه يُقرأ في §18
    مغلقاً. الحاجز يُبنى مرة، وتُشترط قيمته في كل تركيب يرسل بنفسه.
    """
    if mode != MODE_DIRECT:
        return

    missing = _rails_blocking_direct()
    if missing:
        errors.append(
            f"mode = '{MODE_DIRECT}' وهذه الحواجز غير مبنية: {'، '.join(missing)}.\n"
            "  §18: تأجيلها مشروط بوضع Concierge حيث يرسل إنسان. لحظة إرسال النظام "
            "رسالة واحدة تلقائياً تعود إلى 🔴 فوراً،\n"
            "  ولا يمر Gate B بأي منها مفتوحاً. ابنِ الحواجز، أو أبقِ "
            f"mode = '{MODE_CONCIERGE}'."
        )

    if send_window is None:
        errors.append(
            f"mode = '{MODE_DIRECT}' بلا 'channels.<القناة>.send_window'. S8 مبني، "
            "لكن غياب النافذة يعني «أرسل في أي ساعة» -\n"
            "  وهو الحاجز حاضراً في الكود وفارغاً في الأثر. دليل Gate B للوضع "
            "المباشر «لا رسالة خارج الساعات الآمنة» (§20)\n"
            "  لا يمكن إثباته بلا ساعات مضبوطة. اضبط النافذة، أو أبقِ "
            f"mode = '{MODE_CONCIERGE}'."
        )


def _warn_if_send_window_is_narrow(send_window, silence_window, warnings: list) -> None:
    """
    نافذة يقضي النظام خارجها من اليوم أكثر مما يقضي داخلها.

    [لماذا تحذير لا خطأ]
    نافذة ضيقة خيار مشروع للعيادة: ساعتان بعد الظهر قرار تشغيلي لا خطأ
    مطبعي. الرفض هنا كان سيجعل الإعداد يملي على العيادة ساعات عملها.

    [ولماذا لا تمرّ صامتة]
    الضرر ليس في المنع - المؤجَّل ينتظر ولا يسقط، ولا يصير أي Lead غير
    مؤهل: الأهلية بلا حدّ أعلى، والصف يبقى في المرحلة 0 حتى يُرسل. الضرر
    أن **الرقم المضبوط يكفّ عن الوصف**: من يضبط متابعة عند 24 ساعة مع
    نافذة أربع ساعات يحصل على متابعة تصل بين 24 و44 ساعة. لا شيء آخر في
    النظام يقول له ذلك - لا التقرير ولا الاختبارات ولا الملف نفسه.

    [العتبة: المغلق أكثر من المفتوح]
    ليست «أضيق من نافذة الصمت»: نافذة الصمت 24 ساعة اليوم، فأي نافذة
    إرسال أضيق منها - بما فيها 09:00-21:00 المرفوعة في المستودع - كانت
    ستُطلق التحذير عند كل إقلاع. تحذير يعمل على الإعداد الافتراضي ضجيج
    يُتجاهَل في أسبوع. «المغلق أكثر من المفتوح» بلا ثابت مُختار، ويصف
    نفسه بجملة واحدة، ويترك 09:00-21:00 خارجه بالضبط.
    """
    if send_window is None:
        return

    open_minutes = (send_window[1] - send_window[0]) % _MINUTES_PER_DAY
    closed_minutes = _MINUTES_PER_DAY - open_minutes
    if closed_minutes <= open_minutes:
        return

    max_hold = closed_minutes / 60
    warnings.append(
        f"نافذة الإرسال مفتوحة {open_minutes / 60:g} ساعة ومغلقة {max_hold:g} ساعة من اليوم.\n"
        f"  المتابعة المستحقة عند بداية الإغلاق تنتظر حتى {max_hold:g} ساعة، فالمتابعة "
        f"المضبوطة عند {silence_window:g} ساعة تصل فعلياً بين {silence_window:g} "
        f"و{silence_window + max_hold:g} ساعة.\n"
        "  لا رسالة تسقط ولا Lead يصير غير مؤهل - لكن الرقم المضبوط يكفّ عن وصف ما يحدث. "
        "وسّع النافذة أو اخفض نافذة الصمت إن لم يكن هذا مقصوداً."
    )


def _load() -> dict:
    errors: list = []
    warnings: list = []
    data = _load_file(errors)

    _check_unknown_keys(data, ("mode", "channels", "session", "contact"), "المستوى الأعلى", errors)

    mode = _read_mode(data, errors)
    channel_name, followup, send_window = _read_channel(data, errors)

    where = f"'channels.{channel_name}.followup'"
    _check_unknown_keys(
        followup,
        ("silence_window_hours", "second_followup_hours", "expire_after_hours"),
        where,
        errors,
    )
    silence_window = _positive_number(
        followup, "silence_window_hours", _DEFAULT_SILENCE_WINDOW_HOURS, where, errors
    )
    second_followup = _positive_number(
        followup, "second_followup_hours", _DEFAULT_SECOND_FOLLOWUP_HOURS, where, errors
    )
    expire_after = _positive_number(
        followup, "expire_after_hours", _DEFAULT_EXPIRE_AFTER_HOURS, where, errors
    )

    session_ttl = _read_session_ttl(data, silence_window, errors)
    min_contact_digits = _read_min_contact_digits(data, errors)

    _check_mode_rails(mode, send_window, errors)
    _warn_if_send_window_is_narrow(send_window, silence_window, warnings)

    if errors:
        raise SystemExit(
            f"إعداد التشغيل {CONFIG_PATH} غير صالح ({len(errors)} خطأ):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    # بعد اجتياز الأخطاء لا قبله: تحذير يُطبع ثم يوقفه خطأ في سطر آخر
    # ضجيجٌ في مخرجات فشل، ويُقرأ كأنه سبب الفشل.
    for warning in warnings:
        print(f"[CONFIG-WARN] {warning}", file=sys.stderr)

    return {
        "mode": mode,
        "channel_name": channel_name,
        "silence_window_hours": silence_window,
        "second_followup_hours": second_followup,
        "expire_after_hours": expire_after,
        "session_ttl_hours": session_ttl,
        "min_contact_digits": min_contact_digits,
        "send_window": send_window,
    }


_settings = _load()

MODE = _settings["mode"]
CHANNEL_NAME = _settings["channel_name"]

# نافذة الصمت هي **نفسها** عتبة أهلية المتابعة الأولى. كانتا رقمين
# باسمين (`SILENCE_WINDOW_HOURS` و`FIRST_FOLLOWUP_HOURS`) في ملفين،
# وleads_store يقول صراحةً إنهما شيء واحد. الاسمان كانا يسمحان بانفراط
# صامت: يُضبط أحدهما ويُنسى الآخر فتصير المتابعة تُرسل لعميلة لم تُعامَل
# صامتة بعد. اسم واحد لا يمكن أن ينفرط.
SILENCE_WINDOW_HOURS = _settings["silence_window_hours"]
SECOND_FOLLOWUP_HOURS = _settings["second_followup_hours"]
EXPIRE_AFTER_HOURS = _settings["expire_after_hours"]
SESSION_TTL_HOURS = _settings["session_ttl_hours"]
MIN_CONTACT_DIGITS = _settings["min_contact_digits"]

# نافذة الإرسال الآمنة (S8): (بداية، نهاية) بالدقائق من منتصف الليل،
# أو None حين لا نافذة مضبوطة - أي «بلا قيد»، وهو سلوك ما قبل الحاجز.
#
# القيمة **دقائق لا نص**: المقارنة الزمنية تقع في كل تكرار من حلقة
# المتابعة، وتحليل "09:00" في كل مرة عملٌ مكرَّر يمكن أن يفشل مرتين
# بتفسيرين. التحليل مرة واحدة عند الإقلاع، والمقارنة عدد ضد عدد.
#
# القاعدة التي تقرأها ليست هنا بل في `outbound.is_inside_send_window`:
# هذا الملف يقرأ الإعداد ولا يعرف الوقت. انظر ترويسة outbound.py.
SEND_WINDOW = _settings["send_window"]
