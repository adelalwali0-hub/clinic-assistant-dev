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
"""

import json
import os
import shutil
import threading

DATA_DIR = "data"
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
BACKUP_FILE_STATUS_VOCABULARY = SESSIONS_FILE + ".backup-pre-status-vocabulary"

STATE_IDLE = "idle"
STATE_AWAITING_BOOKING_REPLY = "awaiting_booking_reply"
STATE_AWAITING_CONTACT_INFO = "awaiting_contact_info"

_LEGACY_STATES = {"awaiting_booking_confirmation": STATE_AWAITING_BOOKING_REPLY}

# lead_id: معرّف صف الـLead الذي أُنشئ لحظة عرض السعر. تحمله الجلسة
# ليُحدَّث نفس الصف عند كل رد لاحق. None في الجلسات الافتراضية وفي أي
# جلسة بدأت قبل إضافة هذا الحقل - وهي حالة يتعامل معها business_logic.
DEFAULT_SESSION = {"state": STATE_IDLE, "service": None, "lead_id": None}

_lock = threading.Lock()


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
    """
    with _lock:
        sessions = _read_all_sessions()
    session = sessions.get(user_id, DEFAULT_SESSION)
    return dict(session)


def update_session(user_id: str, **changes) -> dict:
    """
    مسار الكتابة لتعديل حقول محددة في جلسة العميلة. يدمج changes مع
    الحالة الحالية، يحفظ فوراً (Atomic + Lock)، ويرجع الحالة الكاملة
    بعد التحديث.
    """
    with _lock:
        sessions = _read_all_sessions()
        current = dict(sessions.get(user_id, DEFAULT_SESSION))
        current.update(changes)
        sessions[user_id] = current
        _write_all_sessions(sessions)
        return dict(current)


def clear_session(user_id: str) -> dict:
    """يعيد جلسة العميلة لحالتها الافتراضية (idle، لا خدمة) ويحفظها."""
    with _lock:
        sessions = _read_all_sessions()
        sessions[user_id] = dict(DEFAULT_SESSION)
        _write_all_sessions(sessions)
        return dict(sessions[user_id])