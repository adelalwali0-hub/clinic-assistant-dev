"""
إثبات أن حارس العزل في conftest.py يمسك الهروب فعلاً.

حارس لا نراه يفشل ليس حارساً. لكن اختباراً يهرب من العزل داخل هذه
المجموعة سيُشعل الحارس ويُحمّر المجموعة كلها - فالإثبات يجري في pytest
داخلي في عملية منفصلة: نسخة حرفية من conftest.py الحقيقي في مجلد مؤقت،
ومعها اختبارات تهرب عمداً، ثم نقرأ ما قاله الحارس.

الاختبارات الهاربة في الداخل لا تكتب شيئاً: تهرب ثم تقف. لا شيء يقترب
من البيانات الحية حتى ونحن نثبت أن الاقتراب مكشوف.
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFTEST = PROJECT_ROOT / "tests" / "conftest.py"

# conftest يحسب جذر المشروع من موقعه. النسخة تعيش في مجلد مؤقت، فيُستبدل
# هذا التعبير وحده بمسار مطلق - وكل ما عداه، والحارس منه، حرفي.
_ROOT_EXPR = "Path(__file__).resolve().parent.parent"

_ESCAPING_TESTS = '''
import events
import leads_store
from storage import session_store


def test_undo_reverts_every_isolated_path(monkeypatch):
    # الحادثة الأصلية: نسخة monkeypatch مشتركة مع فكسترات العزل، فالـundo
    # يُرجع المسارات كلها إلى الملفات الحيّة.
    monkeypatch.undo()
    assert True


def test_undo_then_the_test_itself_fails(monkeypatch):
    # الحادثة كما وقعت تماماً: هرب ثم فشل قبل أي كتابة. فشل الاختبار لا
    # يُسقط الحارس - الهروب يُبلَّغ عنه كذلك.
    monkeypatch.undo()
    assert False, "فشل مقصود"


def test_one_path_repointed_outside(monkeypatch):
    # هروب مسار واحد: يجب أن يُسمّى هو، لا غيره.
    monkeypatch.setattr(events, "EVENTS_FILE", "events.jsonl")
    assert True


def test_clean_test_is_not_flagged():
    # الشاهد. ومروره يثبت ترتيب الهدم: لو هُدم الحارس بعد monkeypatch
    # لكانت المسارات عادت إلى الحقيقي قبل فحصه، ولأبلغ عن هذا الاختبار
    # النظيف - وعن كل اختبار في المجموعة.
    assert leads_store.LEADS_FILE and session_store.SESSIONS_FILE
'''


def _run_inner_pytest(tmp_path):
    """ينسخ conftest الحقيقي ومعه اختبارات هاربة، ويشغّل pytest عليها."""
    source = REAL_CONFTEST.read_text(encoding="utf-8")
    assert source.count(_ROOT_EXPR) == 1, (
        "conftest.py لم يعد يحسب PROJECT_ROOT بالتعبير المتوقع؛ "
        "حدّث _ROOT_EXPR وإلا صار هذا الإثبات يختبر نسخة مكسورة."
    )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    # Path(...) لا str: الحارس يقارن مجلد العمل بـPROJECT_ROOT، ونسخة
    # نصية منه كانت ستُفشل المقارنة دائماً وتُلفّق بلاغاً في النسخة.
    (sandbox / "conftest.py").write_text(
        source.replace(_ROOT_EXPR, f"Path({str(PROJECT_ROOT)!r})"), encoding="utf-8"
    )
    (sandbox / "test_escapes.py").write_text(_ESCAPING_TESTS, encoding="utf-8")

    env = dict(os.environ, PYTHONIOENCODING="utf-8", COLUMNS="200")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(sandbox)],
        cwd=str(sandbox),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return completed.stdout.decode("utf-8", errors="replace")


def test_guard_catches_every_deliberate_escape(tmp_path):
    out = _run_inner_pytest(tmp_path)

    # ثلاثة هاربين، ثلاثة أخطاء عند الهدم - لا أربعة: النظيف لم يُبلَّغ عنه.
    assert "3 errors" in out, out
    assert "3 passed" in out, out
    # والاختبار الذي فشل بنفسه فشل - الحارس لم يبتلع نتيجته.
    assert "1 failed" in out, out

    for escaping in (
        "test_undo_reverts_every_isolated_path",
        "test_undo_then_the_test_itself_fails",
        "test_one_path_repointed_outside",
    ):
        assert f"teardown of {escaping}" in out, out
    # ولا بلاغ كاذب على الاختبار النظيف - وهذا أيضاً برهان ترتيب الهدم:
    # حارس يُهدَم بعد monkeypatch كان سيُبلّغ عن كل اختبار، نظيفاً كان أو لا.
    assert "teardown of test_clean_test_is_not_flagged" not in out, out


def test_guard_names_which_path_escaped(tmp_path):
    out = _run_inner_pytest(tmp_path)

    # الـundo يُرجع الجميع، فيجب أن يُسمّى الجميع.
    for attr in (
        "LEADS_FILE",
        "LOCK_FILE",
        "BACKUP_FILE",
        "BACKUP_FILE_PRICE_QUOTE",
        "BACKUP_FILE_STATUS_VOCABULARY",
        "DATA_DIR",
        "SESSIONS_FILE",
        "EVENTS_FILE",
    ):
        assert attr in out, f"الحارس لم يسمِّ {attr}\n{out}"

    # ويُسمّى المسار الحيّ الذي كان سيُصاب، لا الاسم المجرّد فقط.
    assert "leads.csv" in out, out
    assert "sessions.json" in out, out
    # ومجلد العمل يعود مع الـundo كذلك، والحارس يلتقطه.
    assert "مجلد العمل" in out, out
