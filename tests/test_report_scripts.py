"""
التقريران يعملان حين تُوجَّه مخرجاتهما إلى ملف - لا في الطرفية وحدها.

كلاهما دليل Gate A: `lead_recovery_report.py` من `leads.csv`،
و`events_funnel.py` من `events.jsonl` وحده. وكلاهما عربي بالكامل،
بينما ترميز stdout الافتراضي على ويندوز cp1252 لحظة ألا يكون stdout
طرفية تفاعلية - أي عند كل `>` وكل أنبوب وكل استدعاء من سكربت آخر.
النتيجة كانت UnicodeEncodeError يقتل السكربت قبل أول سطر.

الخلل لا يظهر في اختبار داخل العملية: pytest يلتقط stdout بترميز
مستقل عن ترميز الطرفية. لذلك يُشغَّل كل تقرير **كعملية حقيقية**
مخرجاتها موجَّهة إلى ملف فعلي - وهو الاستعمال الذي يُقصد به تقرير.

[لماذا تُنزَع PYTHONIOENCODING من بيئة الابن]
ضبطها إلى utf-8 كان سيُخفي الخلل تماماً ويجعل الاختبار ينجح دائماً
بلا أن يقيس شيئاً. الابن يجب أن يواجه الافتراض المنصّي للمنصّة.

[لماذا cwd داخل tmp_path]
مسارا `leads.csv` و`events.jsonl` نسبيان، فمجلد العمل هو ما يحدد
أيّ ملف يُقرأ. تشغيل الابن داخل tmp_path يعني قراءة ملفين غير
موجودين - تقرير أصفار صالح - وأن أي كتابة نسبية غير متوقعة تقع
داخل المجلد المؤقت لا في المستودع. البيانات الحقيقية لا تُقرأ ولا
تُلمس.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REPORT_SCRIPTS = ["lead_recovery_report.py", "events_funnel.py"]


def run_report_to_file(script: str, tmp_path: Path) -> str:
    """يشغّل السكربت كعملية مستقلة ومخرجاته موجَّهة إلى ملف حقيقي."""
    out_file = tmp_path / f"{Path(script).stem}.txt"

    env = dict(os.environ)
    # الافتراض المنصّي، لا ترميز مُملى: هذا ما يجعل الاختبار ذا معنى.
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    with open(out_file, "wb") as sink:
        completed = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / script)],
            cwd=str(tmp_path),
            env=env,
            stdout=sink,
            stderr=subprocess.PIPE,
        )

    stderr = completed.stderr.decode("utf-8", errors="replace")
    assert completed.returncode == 0, f"{script} سقط عند التوجيه إلى ملف:\n{stderr}"
    assert "UnicodeEncodeError" not in stderr, stderr

    return out_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("script", REPORT_SCRIPTS)
def test_the_report_renders_to_a_file_without_raising(script, tmp_path):
    """
    الشرط الأدنى لدليل: أن يُنتِج شيئاً حين يُطلب منه ذلك بالطريقة
    التي يُستعمل بها التقرير فعلاً - موجَّهاً إلى ملف، لا معروضاً في
    طرفية مطوّر.
    """
    rendered = run_report_to_file(script, tmp_path)

    # ليس ملفاً فارغاً خرج بصمت: التقرير مرسوم فعلاً بمفردات §8
    assert "تقرير قمع الـLeads" in rendered
    assert "Qualified Leads:" in rendered
    assert "Potential Revenue:" in rendered
    assert "لا يُسمّى رقم «إيراداً» إلا عند الحضور" in rendered


def test_the_events_report_names_its_source_and_the_csv_report_does_not(tmp_path):
    """
    التقريران يتعايشان (§20 دليل Gate A)، فيجب أن يُميَّز أحدهما عن
    الآخر في مخرجاته. تقرير الأحداث يعلن مصدره في سطره الأول؛ ولولا
    ذلك لصار نصّان متطابقان لا يُعرف أيّهما من أي مخزن.
    """
    from_events = run_report_to_file("events_funnel.py", tmp_path)
    from_csv = run_report_to_file("lead_recovery_report.py", tmp_path)

    assert from_events.splitlines()[0].startswith("[المصدر:")
    assert "events.jsonl" in from_events.splitlines()[0]
    assert "لا leads.csv" in from_events.splitlines()[0]

    assert not from_csv.startswith("[المصدر:")


def test_neither_report_writes_anything_where_it_runs(tmp_path):
    """
    التقريران «قراءة فقط» بحسب ترويستيهما. تشغيلهما في مجلد فارغ
    يجب ألا يُنشئ فيه ملف بيانات - لا leads.csv ولا قفلاً ولا نسخة
    احتياطية. ادّعاء القراءة فقط يُختبر، لا يُصدَّق.
    """
    workspace = tmp_path / "فارغ"
    workspace.mkdir()

    for script in REPORT_SCRIPTS:
        run_report_to_file(script, workspace)

    created = {p.name for p in workspace.iterdir()}
    # مخرجات الاختبار نفسها فقط - لا أثر بيانات
    assert created == {"lead_recovery_report.txt", "events_funnel.txt"}, created
