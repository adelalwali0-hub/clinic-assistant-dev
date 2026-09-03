"""
عزل الاختبارات عن البيانات الحية.

كل اختبار يعمل داخل tmp_path خاص به: leads.csv والقفل والنسخ
الاحتياطية وملف الجلسات كلها هناك. الملفات الحقيقية في جذر المشروع
(leads.csv و data/sessions.json) لا تُقرأ ولا تُكتب ولا تُنشأ من
الاختبارات إطلاقاً.
"""

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# جذر المشروع في sys.path حتى يعمل `import leads_store` من داخل tests/
sys.path.insert(0, str(PROJECT_ROOT))

import events  # noqa: E402
import leads_store  # noqa: E402
from storage import session_store  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_leads_file(tmp_path, monkeypatch):
    """
    يوجّه كل مسارات leads_store إلى tmp_path. autouse: لا يمكن لاختبار
    أن ينسى هذا العزل عن طريق الخطأ.

    الـchdir لجذر المشروع لأن services.py يقرأ config/clinic_config.json
    بمسار نسبي، و_lookup_current_price داخل save_lead يستورده. مسارات
    leads كلها مطلقة، فلا يؤثر chdir عليها.
    """
    monkeypatch.chdir(PROJECT_ROOT)
    leads_file = tmp_path / "leads.csv"
    monkeypatch.setattr(leads_store, "LEADS_FILE", str(leads_file))
    monkeypatch.setattr(leads_store, "LOCK_FILE", str(leads_file) + ".lock")
    monkeypatch.setattr(leads_store, "BACKUP_FILE", str(leads_file) + ".backup-pre-lead-id")
    monkeypatch.setattr(
        leads_store, "BACKUP_FILE_PRICE_QUOTE", str(leads_file) + ".backup-pre-price-quote-lead"
    )
    monkeypatch.setattr(
        leads_store, "BACKUP_FILE_STATUS_VOCABULARY", str(leads_file) + ".backup-pre-status-vocabulary"
    )
    monkeypatch.setattr(
        leads_store, "BACKUP_FILE_CONSENT", str(leads_file) + ".backup-pre-consent"
    )
    return leads_file


@pytest.fixture(autouse=True)
def isolated_events_file(tmp_path, monkeypatch):
    """
    يوجّه سجل الأحداث إلى tmp_path كذلك.

    autouse لنفس سبب العزلين الآخرين، وبإلحاح أكبر: events.jsonl ملف
    بالإلحاق فقط لا يُقتطع أبداً، فاختبار واحد ينسى العزل يُلوّث السجل
    الحقيقي بأحداث مختلَقة ولا مسار لسحبها بعدها.

    كل اختبار يبدأ بملف غير موجود - وهذه حالة صحيحة تتعامل معها
    events.read_all بإرجاع قائمة فارغة.
    """
    monkeypatch.setattr(events, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
    return tmp_path / "events.jsonl"


@pytest.fixture(autouse=True)
def isolated_sessions_file(tmp_path, monkeypatch):
    """
    يوجّه session_store إلى tmp_path كذلك.

    ضروري منذ أن صارت اختبارات business_logic تمرّ بشجرة القرار: هي
    تستدعي update_session/clear_session، وبلا هذا العزل كانت ستكتب في
    data/sessions.json الحقيقي في جذر المشروع - أي تفسد جلسات عميلات
    حقيقيات وسط محادثة جارية.

    autouse لنفس سبب العزل الأول: لا يُنسى.
    """
    data_dir = tmp_path / "data"
    monkeypatch.setattr(session_store, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(session_store, "SESSIONS_FILE", str(data_dir / "sessions.json"))
    monkeypatch.setattr(
        session_store,
        "BACKUP_FILE_STATUS_VOCABULARY",
        str(data_dir / "sessions.json") + ".backup-pre-status-vocabulary",
    )
    return data_dir / "sessions.json"


# ------------------------------------------------- حارس العزل (بعد كل اختبار)

#: كل مسار بيانات تكتبه المنظومة. الفكسترات الثلاث أعلاه توجّهها كلها إلى
#: tmp_path، والحارس أدناه يتحقق أنها بقيت هناك حتى نهاية الاختبار.
_ISOLATED_PATHS = (
    (leads_store, "LEADS_FILE"),
    (leads_store, "LOCK_FILE"),
    (leads_store, "BACKUP_FILE"),
    (leads_store, "BACKUP_FILE_PRICE_QUOTE"),
    (leads_store, "BACKUP_FILE_STATUS_VOCABULARY"),
    (session_store, "DATA_DIR"),
    (session_store, "SESSIONS_FILE"),
    (session_store, "BACKUP_FILE_STATUS_VOCABULARY"),
    (events, "EVENTS_FILE"),
)


def _escape_reason(value, tmp_root):
    """
    يصف كيف خرج المسار من tmp_path، أو None إن كان بداخلها.

    المسار النسبي خروج بحد ذاته: يُحلّ مقابل مجلد العمل - أي جذر
    المشروع - فيصيب الملف الحقيقي.
    """
    if not isinstance(value, (str, os.PathLike)):
        return f"ليس مساراً: {value!r}"
    path = Path(value)
    if not path.is_absolute():
        return f"مسار نسبي {str(value)!r} ← {Path.cwd() / path}"
    resolved = path.resolve()
    try:
        resolved.relative_to(tmp_root)
    except ValueError:
        return f"{str(value)!r} ← {resolved}"
    return None


def _assert_paths_inside(tmp_root):
    """يجمع كل الهاربين ويفشل مرة واحدة مسمّياً كلاً منهم."""
    escapes = [
        f"  {module.__name__}.{attr} = {reason}"
        for module, attr in _ISOLATED_PATHS
        if (reason := _escape_reason(getattr(module, attr, None), tmp_root)) is not None
    ]
    # الـchdir يعود مع الـundo كذلك، ومجلد العمل هو ما يجعل المسار النسبي خطراً.
    if Path.cwd() != PROJECT_ROOT:
        escapes.append(f"  مجلد العمل = {Path.cwd()} (المتوقع {PROJECT_ROOT})")

    assert not escapes, (
        "انكسر عزل الاختبارات: خرجت مسارات من tmp_path إلى البيانات الحية.\n"
        + "\n".join(escapes)
        + f"\n  tmp_path = {tmp_root}"
    )


# السبب حادثة حقيقية: اختبار نادى monkeypatch.undo() في وسطه. pytest يشارك
# نسخة monkeypatch واحدة مع الفكسترات أعلاه، فالـundo أعاد LEADS_FILE
# وSESSIONS_FILE وEVENTS_FILE والـchdir إلى الحقيقي. بقية الاختبار صارت تشير
# إلى leads.csv وdata/sessions.json الحيَّين. نجا الملف الحقيقي بالصدفة وحدها:
# الاختبار فشل عند assert قبل أن يبلغ أي كتابة. هذا الحارس يجعل الخطأ
# مستحيلاً بدل أن يكون محظوظاً.
#
# الفكسترات الثلاث تُطلب كوسائط لا لقيمها بل لترتيب الهدم: طلب فكسترة يضمن
# تهيئتها قبلنا، وpytest يهدم بترتيب معكوس - فنُهدَم نحن أولاً، قبل أن يُرجع
# monkeypatch المسارات الحقيقية. الاعتماد على ترتيب التعريف في الملف كان
# سيجعل الحارس نفسه هشّاً.
#
# لا باب خلفي: ما من اختبار يحتاج مساراً خارج tmp_path. والشرح هنا لا في
# docstring عمداً - pytest يطبع جسم الفكسترة عند الفشل، فتُدفن الرسالة تحته.
@pytest.fixture(autouse=True)
def paths_never_escape_tmp_path(
    tmp_path, isolated_leads_file, isolated_events_file, isolated_sessions_file
):
    """بعد كل اختبار: كل مسارات البيانات ما زالت داخل tmp_path."""
    yield
    _assert_paths_inside(tmp_path.resolve())
