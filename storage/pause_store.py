"""
Pause Store - إيقاف الأتمتة لعميلة واحدة (PRD §18 حاجز S6)
========================================================================
المالك الوحيد لحالة «هل أوقفنا الأتمتة لهذه العميلة؟». التخزين ملف
JSON محلي (data/pauses.json)، وهو تفصيل تنفيذي مخفي خلف هذا العقد.

[لماذا ملف مستقل ولا يُدمَج في sessions.json أبداً]
sessions.json مرشَّح ظاهرياً: مفتاحه `user_id` نفسه، وعزله في
الاختبارات قائم، وكتابته الذرّية مكتوبة. ورغم ذلك دمجُهما خطأ لا
يُكتشَف إلا بعد وقوعه:

  **الجلسة تنتهي بعد SESSION_TTL_HOURS. الإيقاف لا ينتهي أبداً.**

`_prune_expired` تُسقط كل جلسة تجاوزت مهلتها عند أول كتابة لاحقة -
وهذا سلوك صحيح تماماً للجلسات. لو سكن الإيقاف هناك لمحاه أول كانِس
بعد أربع وعشرين ساعة: عميلة قالت «لا تراسلوني» تعود إلى دورة المتابعة
بصمت، بلا سطر سجل ولا استثناء ولا أي أثر يفسّر عودة الرسائل إليها.
الضرر يقع بعد يوم من القرار الصحيح، فلا يربطه أحد بسببه.

الفارق ليس في شكل البيانات بل في **عمرها**: الجلسة حالة محادثة جارية
تُنسى عمداً، والإيقاف قرار عميلة يدوم حتى تُلغيه بشرياً. ملفان لأن
لهما سياستَي احتفاظ متناقضتين - لا لأن لهما شكلين مختلفين. من يرى
مفتاحين متشابهين ويهمّ بدمجهما: هذه الفقرة هي الجواب.

[الهوية لا الـLead]
المفتاح `(channel, user_id)` - أي Customer في §6، لا Lead. عميلة لها
ثلاثة استفسارات مفتوحة قالت «لا تراسلوني» مرة واحدة: القرار يخصّها
هي، فيسري على الثلاثة وعلى أي استفسار رابع تفتحه غداً. تخزينه على
الهوية يجعل ذلك **بنيةً** لا قاعدةً تُطبَّق بانضباط في كل موضع كتابة.

لا دمج بين القنوات (§6): نفس الشخص على قناتين هويّتان، وإيقافه على
إحداهما لا يمسّ الأخرى - فالتعشيش هنا بالقناة أولاً.

[لا عملية جماعية]
لا دالة في هذا الملف تعدّل أكثر من هوية واحدة. لا `resume_all` ولا
مُحدِّد بالحرف البدل ولا مسار اختبار يلتفّ على ذلك. رفع الإيقاف يعني
استئناف مراسلة إنسانة طلبت التوقف، وهو قرار يُتخذ لها وحدها بمعرّفها
صراحةً - وسهولةُ رفعه جماعياً هي بالضبط ما يجعل رفعه بالخطأ ممكناً.

[الاحتفاظ بالسجل بعد الاستئناف]
الصف لا يُحذف عند الاستئناف؛ يصير `paused=false` بطابع `resumed_at`.
الحذف كان سيجعل «لم تطلب التوقف قط» و«طلبت ثم استُؤنفت» متطابقتين
أمام المشغّل، وهما مختلفتان تماماً حين يقرأ الصف مرة أخرى.

Atomic Write + threading.Lock، وملف مفقود أو تالف يُقرأ «لا إيقافات»:
نفس عقد session_store حرفياً. اتجاه الخطأ عند التلف مقصود ومعروف -
انظر `_read_all`.
"""

import json
import os
import threading
from datetime import datetime

DATA_DIR = "data"
PAUSES_FILE = os.path.join(DATA_DIR, "pauses.json")

# مصادر الإيقاف. اليوم واحد: المشغّل. القيمة تُكتب في حمولة الحدث لا
# في الملف وحده، فيبقى «من أوقف؟» مُجاباً من events.jsonl عند التحليل.
SOURCE_OPERATOR = "operator"

_lock = threading.Lock()


def _now_iso() -> str:
    """نفس اصطلاح events._now_iso وsession_store: محلي بلا منطقة زمنية."""
    return datetime.now().isoformat(timespec="microseconds")


def _read_all() -> dict:
    """
    كل الإيقافات المخزَّنة، أو {} عند غياب الملف أو تلفه.

    [اتجاه الخطأ عند التلف - وهو الاتجاه الخطر هنا]
    ملف تالف يُقرأ «لا إيقافات»، أي أن الأتمتة تستأنف. هذا معاكس
    لاتجاه الأمان الذي يختاره بقية النظام، ولا مفرّ منه: لا يمكن
    استنتاج قائمة الممنوعات من ملف غير مقروء.

    ولذلك تحديداً **يُطبع سطر صارخ** ولا يُبتلع الخطأ: التلف هنا حادثة
    تشغيلية تستدعي تدخّلاً بشرياً فورياً، لا حالة يمرّ بها البرنامج
    بهدوء كما يمرّ بجلسة مفقودة.
    """
    if not os.path.isfile(PAUSES_FILE):
        return {}
    try:
        with open(PAUSES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            print(
                f"[pause_store] !! محتوى {PAUSES_FILE} غير صالح (ليس كائناً). "
                "الأتمتة تستأنف لمن كنّ موقوفات - راجع الملف فوراً."
            )
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"[pause_store] !! فشل قراءة {PAUSES_FILE}: {e}. "
            "الأتمتة تستأنف لمن كنّ موقوفات - راجع الملف فوراً."
        )
        return {}


def _write_all(pauses: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = PAUSES_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(pauses, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, PAUSES_FILE)  # استبدال ذري (Atomic)


def _valid_identity(channel: str, user_id: str) -> bool:
    """
    الهوية الكاملة شرط لكل عملية. هوية ناقصة ليست «كل الهويات» بل خطأ
    برمجي: قناة فارغة أو معرّف فارغ يطابقان كل شيء أو لا شيء بحسب
    التنفيذ، وكلاهما سلوك لا يجوز أن يقع صامتاً على حاجز سلامة.
    """
    return bool((channel or "").strip()) and bool((user_id or "").strip())


def is_paused(channel: str, user_id: str) -> bool:
    """هل الأتمتة موقوفة لهذه الهوية الآن؟ قراءة فقط."""
    record = get_pause(channel, user_id)
    return bool(record and record.get("paused"))


def get_pause(channel: str, user_id: str) -> dict | None:
    """
    صف الإيقاف كاملاً (نسخة مستقلة)، أو None إن لم يوجد صف لهذه الهوية
    إطلاقاً. الصف الموجود بـ`paused=false` ليس None: هي طلبت التوقف
    ثم استُؤنفت بشرياً، وهذا مختلف عن «لم تطلب قط».
    """
    if not _valid_identity(channel, user_id):
        return None
    with _lock:
        pauses = _read_all()
    record = pauses.get(channel, {}).get(user_id)
    return dict(record) if isinstance(record, dict) else None


def paused_identity_set() -> set:
    """
    كل الهويات الموقوفة الآن، كمجموعة `(channel, user_id)`.

    قراءة واحدة لكل مرور على الـLeads: دوال الأهلية تمرّ على كل صف في
    leads.csv، واستدعاء `is_paused` لكل صف كان سيفتح الملف مرة لكل صف.
    """
    with _lock:
        pauses = _read_all()
    return {
        (channel, user_id)
        for channel, identities in pauses.items()
        if isinstance(identities, dict)
        for user_id, record in identities.items()
        if isinstance(record, dict) and record.get("paused")
    }


def paused_identities() -> list:
    """
    الهويات الموقوفة الآن مرتبةً، لعرض المشغّل. قراءة فقط.
    `[(channel, user_id, record), ...]` ولا نص عميلة في أي منها (S12).
    """
    with _lock:
        pauses = _read_all()
    rows = [
        (channel, user_id, dict(record))
        for channel, identities in sorted(pauses.items())
        if isinstance(identities, dict)
        for user_id, record in sorted(identities.items())
        if isinstance(record, dict) and record.get("paused")
    ]
    return rows


def pause(channel: str, user_id: str, source: str = SOURCE_OPERATOR) -> bool:
    """
    يوقف الأتمتة لهوية واحدة. يُرجع True إن وقع الإيقاف الآن، وFalse
    إن كانت موقوفة أصلاً (أو كانت الهوية ناقصة).

    الفرق يهمّ المُستدعي: الحدث يُصدَر على الانتقال وحده، فطلبٌ ثانٍ
    من نفس العميلة لا يضيف إيقافاً ثانياً لم يقع.
    """
    if not _valid_identity(channel, user_id):
        return False
    with _lock:
        pauses = _read_all()
        identities = pauses.setdefault(channel, {})
        existing = identities.get(user_id)
        if isinstance(existing, dict) and existing.get("paused"):
            return False
        identities[user_id] = {
            "paused": True,
            "paused_at": _now_iso(),
            "source": source,
            "resumed_at": None,
        }
        _write_all(pauses)
        return True


def resume(channel: str, user_id: str) -> bool:
    """
    يرفع الإيقاف عن هوية **واحدة معيَّنة بمعرّفها**. يُرجع True إن كانت
    موقوفة فاستُؤنفت الآن، وFalse إن لم تكن موقوفة أصلاً.

    لا يوجد - ولن يوجد - نظير جماعي لهذه الدالة. انظر ترويسة الملف.

    الصف يبقى بعد الاستئناف بـ`paused=false` وطابع `resumed_at`،
    فيميّز المشغّل بين «لم تطلب التوقف قط» و«طلبت ثم استُؤنفت».
    """
    if not _valid_identity(channel, user_id):
        return False
    with _lock:
        pauses = _read_all()
        record = pauses.get(channel, {}).get(user_id)
        if not (isinstance(record, dict) and record.get("paused")):
            return False
        record["paused"] = False
        record["resumed_at"] = _now_iso()
        _write_all(pauses)
        return True
