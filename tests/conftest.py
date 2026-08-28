"""
عزل الاختبارات عن البيانات الحية.

كل اختبار يعمل داخل tmp_path خاص به: leads.csv والقفل والنسخة
الاحتياطية كلها هناك. ملف leads.csv الحقيقي في جذر المشروع لا
يُقرأ ولا يُكتب ولا يُنشأ من الاختبارات إطلاقاً.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# جذر المشروع في sys.path حتى يعمل `import leads_store` من داخل tests/
sys.path.insert(0, str(PROJECT_ROOT))

import leads_store  # noqa: E402


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
    return leads_file
