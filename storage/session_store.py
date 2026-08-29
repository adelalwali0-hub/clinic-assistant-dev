"""
Session Store - طبقة تخزين مستقلة لحالة الجلسة
====================================================
المالك الوحيد لحالة الجلسة (session state). business_logic.py لا
يعدّل أي قاموس مباشرة - فقط يطلب قراءة أو تحديثاً من هنا. التخزين
الداخلي حالياً ملف JSON محلي (data/sessions.json)، وهذا تفصيل
تنفيذي مخفي بالكامل خلف هذا العقد - يمكن استبداله لاحقاً دون أي
تغيير في business_logic.py.

مسارات الكتابة الوحيدة المسموحة: update_session() وclear_session().
get_session() للقراءة فقط - يرجع نسخة مستقلة، لا الكائن الداخلي.

يعمل Offline بالكامل. Atomic Write (ملف مؤقت ثم استبدال ذري) +
threading.Lock يحميان من تلف الملف أو تعارض الكتابة المتزامنة.
ملف مفقود أو تالف لا يؤدي لانهيار النظام - يُعامَل كبداية نظيفة.

[مواءمة المفردات - PRD §8]
`awaiting_booking_confirmation` صار `awaiting_booking_reply`. الاسم
القديم كان يتصادم لفظياً مع Confirmed Booking في §8 (تأكيد الموظفة)،
فيقرأ قارئ الكود أن النظام ينتظر العيادة بينما هو ينتظر ردّ العميلة.

الترجمة تحدث عند القراءة (_migrate_states) لا بكتابة دفعة واحدة:
جلسة حيّة كُتبت بالاسم القديم تُقرأ صحيحة فوراً - لا تسقط إلى idle
ولا يُعاد تسعير الخدمة على عميلة وسط محادثة. أول كتابة لاحقة على
الملف تثبّت الاسم الجديد على القرص. تُؤخذ نسخة احتياطية مرة واحدة
قبل أول كتابة، كما في leads.csv.

[التغيير #7 - مهلة الجلسة | F9]
لم يكن للجلسات عمر: جلسة تنتظر جواباً بلا رسالة تالية تبقى منتظرة
**للأبد**، فتُقرأ رسالة العميلة بعد شهر جواباً على سؤال ميت، وينمو
data/sessions.json بلا حد بحالات ميتة.

الآن لكل جلسة `updated_at`، وجلسة غير `idle` تجاوز عمرها
SESSION_TTL_HOURS تُقرأ كجلسة افتراضية (idle) - فرسالتها التالية
تُعامَل معاملة رسالة جديدة تماماً.

الانتهاء **كسول**: يقع عند القراءة، بلا كانِس ولا جدولة. لا شيء في
هذا النظام يعمل بجدولة (D-013: المتابعات سكربتات يدوية)، وكانِسٌ لا
يُشغَّل لا يغيّر شيئاً.

الانتهاء **صامت**: لا رسالة تُرسَل. العميلة التي تعود بعد يوم تريد
جواب سؤالها لا شرحاً عن آلة حالاتنا - فتتلقى سعراً كأنها كتبت أول
مرة. ولهذا لا حاجة إلى صياغة جديدة لهذا المسار.

جلسة قديمة بلا `updated_at` يُشتقّ عمرها من تاريخ تعديل الملف
(mtime): حدٌّ **أعلى** لعمرها الحقيقي، فلا تنتهي جلسة أبكر مما
تستحق. هذا هو نفس مبدأ D-016: لا تُسقَط عميلة وسط محادثة بسبب هجرة.

SESSION_TTL_HOURS = 24 مساوية عمداً لـSILENCE_WINDOW_HOURS في
leads_store: عند تلك الساعة يكون الـLead قد عُومل صامتاً ودخل دورة
المتابعة، فجلسة تدّعي بعدها أن محادثة جارية تناقض سجل الـLeads.
القيمة ثابت مسمّى واحد: إن نُقلت نافذة الصمت إلى 6-12 ساعة
(PRD Q1، Gate C) تُنقل معها هنا بسطر واحد.
"""

import json
import os
import shutil
import threading
from datetime import datetime

DATA_DIR = "data"
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
BACKUP_FILE_STATUS_VOCABULARY = SESSIONS_FILE + ".backup-pre-status-vocabulary"

# انظر الترويسة: مساوية عمداً لـleads_store.SILENCE_WINDOW_HOURS.
# لا تُستورَد من هناك: طبقة التخزين لا تعرف طبقة الـLeads، والمساواة
# قرار موثّق لا اعتماد برمجي.
SESSION_TTL_HOURS = 24

STATE_IDLE = "idle"
STATE_AWAITING_BOOKING_REPLY = "awaiting_booking_reply"
STATE_AWAITING_CONTACT_INFO = "awaiting_contact_info"

# [التغيير #6] رسالة العميلة طابقت أكثر من خدمة، فسُئلت أيّها تقصد
# والنظام ينتظر جوابها. حالة انتظار **تسبق** عرض السعر - لا Lead بعد.
STATE_AWAITING_SERVICE_DISAMBIGUATION = "awaiting_service_disambiguation"

_LEGACY_STATES = {"awaiting_booking_confirmation": STATE_AWAITING_BOOKING_REPLY}

# lead_id: معرّف صف الـLead الذي أُنشئ لحظة عرض السعر. تحمله الجلسة
# ليُحدَّث نفس الصف عند كل رد لاحق. None في الجلسات الافتراضية وفي أي
# جلسة بدأت قبل إضافة هذا الحقل - وهي حالة يتعامل معها business_logic.
#
# service_options: أسماء الخدمات المرشَّحة التي عُرضت على العميلة في
# سؤال التوضيح، بترتيب عرضها - فالرقم الذي ترسله يقود إلى ما رأته.
# أسماء لا كائنات: الاسم يبقى صالحاً لو أُعيد تحميل الإعداد وسط
# المحادثة، والكائن المنسوخ في الجلسة يصير سعراً قديماً بلا أن يُلاحَظ.
# لا هجرة لهذا الحقل ولا نسخة احتياطية: غيابه من جلسة قديمة يعني
# «لا خيارات معروضة»، وهو ما تقوله None بالضبط - فيُقرأ في كل موضع
# بصيغة `session.get("service_options") or []`.
# provisional_name: اسم أرسلته العميلة وحده قبل رقمها، بانتظار الرقم
# ليُدمَجا في بيانات تواصل واحدة. يعيش داخل awaiting_contact_info فقط،
# ولا يُكتب في أي صف بمفرده: صف بيانات تواصل باسم بلا رقم لا تستطيع
# العيادة العمل عليه. None في الجلسات الافتراضية وفي أي جلسة بدأت قبل
# إضافة هذا الحقل، ويُقرأ في كل موضع بصيغة
# `session.get("provisional_name")`.
#
# updated_at: طابع آخر كتابة على الجلسة (ISO محلي بلا منطقة زمنية -
# نفس اصطلاح leads_store وevents). عليه وحده تقوم المهلة. None في
# الجلسة الافتراضية وفي أي جلسة كُتبت قبل هذا الحقل، وعندها يُشتقّ
# العمر من mtime الملف - انظر الترويسة.
DEFAULT_SESSION = {
    "state": STATE_IDLE,
    "service": None,
    "lead_id": None,
    "service_options": None,
    "provisional_name": None,
    "updated_at": None,
}

_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _sessions_file_mtime() -> datetime | None:
    """
    تاريخ آخر تعديل للملف - عمرٌ بديل للجلسات التي لا تحمل طابعاً.
    None إن لم يوجد الملف: لا ملف يعني لا جلسات مخزَّنة أصلاً.
    """
    try:
        return datetime.fromtimestamp(os.path.getmtime(SESSIONS_FILE))
    except OSError:
        return None


def _age_hours(session: dict, fallback: datetime | None) -> float | None:
    """
    عمر الجلسة بالساعات، أو None حين يتعذّر تحديده إطلاقاً.

    None تعني «لا أعرف»، وكل مُستدعٍ أدناه يعامل «لا أعرف» على أنها
    **غير منتهية**: إسقاط جلسة لا نعرف عمرها يقطع محادثة حيّة على
    شكّ، وهو أسوأ من إبقاء جلسة ميتة تحرسها القاعدة الجديدة أصلاً.
    """
    stamp = session.get("updated_at")
    written_at = None
    if stamp:
        try:
            written_at = datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            written_at = None
    if written_at is None:
        written_at = fallback
    if written_at is None:
        return None
    return (datetime.now() - written_at).total_seconds() / 3600


def _is_expired(session: dict, fallback: datetime | None) -> bool:
    """
    جلسة idle لا تنتهي: هي أصلاً الحالة التي ينتهي إليها كل شيء، ولا
    فرق بين قراءتها وقراءة الافتراضية.
    """
    if session.get("state", STATE_IDLE) == STATE_IDLE:
        return False
    age = _age_hours(session, fallback)
    return age is not None and age > SESSION_TTL_HOURS


def _migrate_states(sessions: dict) -> dict:
    """
    ترجمة أسماء الحالات القديمة عند القراءة. حقول الجلسة الأخرى
    (service, lead_id) تمرّ كما هي حرفياً - جلسة وسط محادثة تُستأنف
    من حيث توقفت بالضبط.
    """
    for session in sessions.values():
        if isinstance(session, dict) and session.get("state") in _LEGACY_STATES:
            session["state"] = _LEGACY_STATES[session["state"]]
    return sessions


def _read_all_sessions() -> dict:
    if not os.path.isfile(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            print(f"[session_store] محتوى {SESSIONS_FILE} غير صالح (ليس كائناً) - بدء بجلسات فارغة")
            return {}
        return _migrate_states(data)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[session_store] فشل قراءة {SESSIONS_FILE}: {e} - بدء بجلسات فارغة")
        return {}


def _backup_once() -> None:
    """
    لقطة واحدة لما قبل مواءمة المفردات، قبل أول كتابة تثبّت الأسماء
    الجديدة على القرص. فشل النسخ لا يُوقف الكتابة - الجلسات الحية أهم.
    """
    if not os.path.isfile(SESSIONS_FILE) or os.path.exists(BACKUP_FILE_STATUS_VOCABULARY):
        return
    try:
        shutil.copy2(SESSIONS_FILE, BACKUP_FILE_STATUS_VOCABULARY)
        print(f"[session_store] نسخة احتياطية لما قبل مواءمة المفردات -> {BACKUP_FILE_STATUS_VOCABULARY}")
    except OSError as e:
        print(f"[session_store] تحذير: تعذّر إنشاء النسخة الاحتياطية {BACKUP_FILE_STATUS_VOCABULARY}: {e}")


def _prune_expired(sessions: dict, keep_user_id: str) -> dict:
    """
    يُسقط الجلسات المنتهية قبل الكتابة، فلا ينمو الملف بحالات ميتة.

    إسقاطها لا يغيّر أي سلوك: get_session تقرأها افتراضية أصلاً، وصفٌّ
    محذوف يُقرأ افتراضياً كذلك. `keep_user_id` استثناء واحد - الجلسة
    التي تُكتب الآن - حتى لا تسقط كتابة جارية بعمرها القديم.
    """
    fallback = _sessions_file_mtime()
    return {
        user_id: session
        for user_id, session in sessions.items()
        if user_id == keep_user_id
        or not isinstance(session, dict)
        or not _is_expired(session, fallback)
    }


def _write_all_sessions(sessions: dict) -> None:
    _backup_once()
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp_path = SESSIONS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, SESSIONS_FILE)  # استبدال ذري (Atomic)


def get_session(user_id: str) -> dict:
    """
    قراءة فقط - يرجع نسخة مستقلة من حالة جلسة العميلة، أو الحالة
    الافتراضية (idle، لا خدمة) إن لم توجد جلسة سابقة. التعديل على
    القاموس المُرجَع لا يُحفَظ تلقائياً - استخدم update_session أو
    clear_session للحفظ الفعلي.

    [التغيير #7] جلسة تجاوزت SESSION_TTL_HOURS تُرجَع افتراضية: هنا
    يقع الانتهاء، كسولاً وصامتاً. القراءة لا تكتب شيئاً - الملف يبقى
    كما هو حتى أول كتابة لاحقة، وهي التي تُسقط المنتهية فعلياً.
    """
    with _lock:
        sessions = _read_all_sessions()
        fallback = _sessions_file_mtime()
    session = sessions.get(user_id)
    if session is None or _is_expired(session, fallback):
        return dict(DEFAULT_SESSION)
    return dict(session)


def update_session(user_id: str, **changes) -> dict:
    """
    مسار الكتابة لتعديل حقول محددة في جلسة العميلة. يدمج changes مع
    الحالة الحالية، يحفظ فوراً (Atomic + Lock)، ويرجع الحالة الكاملة
    بعد التحديث.

    [التغيير #7] الدمج يقع على الحالة **الحيّة**: جلسة منتهية تُعامَل
    افتراضية هنا كما تُعامَل في القراءة، وإلا لأحيا تحديثٌ جزئي جلسةً
    ميتة بحقولها القديمة.
    """
    with _lock:
        sessions = _read_all_sessions()
        fallback = _sessions_file_mtime()
        stored = sessions.get(user_id)
        if stored is None or _is_expired(stored, fallback):
            stored = DEFAULT_SESSION
        current = dict(stored)
        current.update(changes)
        current["updated_at"] = _now_iso()
        sessions = _prune_expired(sessions, keep_user_id=user_id)
        sessions[user_id] = current
        _write_all_sessions(sessions)
        return dict(current)


def clear_session(user_id: str) -> dict:
    """يعيد جلسة العميلة لحالتها الافتراضية (idle، لا خدمة) ويحفظها."""
    with _lock:
        sessions = _read_all_sessions()
        sessions = _prune_expired(sessions, keep_user_id=user_id)
        sessions[user_id] = dict(DEFAULT_SESSION, updated_at=_now_iso())
        _write_all_sessions(sessions)
        return dict(sessions[user_id])