"""
عزل الاختبارات عن البيانات الحية.

كل اختبار يعمل داخل tmp_path خاص به: leads.csv والقفل والنسخ
الاحتياطية وملف الجلسات كلها هناك. الملفات الحقيقية في جذر المشروع
(leads.csv و data/sessions.json) لا تُقرأ ولا تُكتب ولا تُنشأ من
الاختبارات إطلاقاً.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# جذر المشروع في sys.path حتى يعمل `import leads_store` من داخل tests/
sys.path.insert(0, str(PROJECT_ROOT))

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
    return leads_file


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
