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
"""

import json
import os
import threading

DATA_DIR = "data"
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")

DEFAULT_SESSION = {"state": "idle", "service": None}

_lock = threading.Lock()


def _read_all_sessions() -> dict:
    if not os.path.isfile(SESSIONS_FILE):
        return {}
    try:
        with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            print(f"[session_store] محتوى {SESSIONS_FILE} غير صالح (ليس كائناً) - بدء بجلسات فارغة")
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"[session_store] فشل قراءة {SESSIONS_FILE}: {e} - بدء بجلسات فارغة")
        return {}


def _write_all_sessions(sessions: dict) -> None:
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