"""
اختبارات `lead_id` المستقر ومفتاح الهوية المركّب (PRD D3/D4).

تغطي أربعة أسئلة:
  1) هل يبقى lead_id ثابتاً عبر كل مسارات الكتابة وعبر عمليات منفصلة؟
  2) هل يتصادم استفساران في نفس الثانية لنفس العميل؟
  3) هل تحافظ الهجرة على كل بيانات الصفوف الموجودة؟
  4) هل يفصل مفتاح الهوية بين نفس المعرّف على قناتين مختلفتين؟
"""

import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

import leads_store
from leads_store import (
    FIELDNAMES,
    LEAD_ID_COLUMN,
    OUTCOME_EXPIRED,
    OUTCOME_ORGANIC,
    OUTCOME_PENDING,
    OUTCOME_RECOVERED,
    STATE_BOOKING_REQUESTED,
    STATE_LEGACY_UNKNOWN,
    STATE_PRICE_QUOTED,
    mark_expired,
    mark_followup_sent,
    save_lead,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SERVICE = "ليزر إزالة الشعر"
OTHER_SERVICE = "بوتوكس"

V1_FIELDNAMES = [
    "التاريخ والوقت", "معرف العميل", "القناة", "الخدمة المطلوبة",
    "الحالة", "بيانات التواصل", "تمت المتابعة",
]

V2_FIELDNAMES = [
    "التاريخ والوقت", "معرف العميل", "القناة", "الخدمة المطلوبة",
    "الحالة", "بيانات التواصل", "سعر الخدمة وقت الإنشاء",
    "مرحلة المتابعة", "تاريخ آخر متابعة", "نتيجة المتابعة",
]


# ----------------------------------------------------------------- أدوات

def write_csv(path, fieldnames, rows):
    """يكتب ملف leads.csv ببنية قديمة محددة، كما لو كتبته نسخة سابقة من الكود."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def read_rows():
    return leads_store._read_all_rows()


class FrozenDatetime(datetime):
    """
    يجمّد datetime.now() على لحظة واحدة بدقة الثانية - نفس دقة
    "التاريخ والوقت" في الملف. strptime وبقية السلوك تُورَّث كما هي.
    """
    frozen_at = datetime(2026, 8, 28, 14, 30, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.frozen_at


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(leads_store, "datetime", FrozenDatetime)
    return FrozenDatetime.frozen_at


# ------------------------------------------- 1) ثبات lead_id عبر الجلسات

def test_save_lead_returns_prefixed_unique_id():
    first = save_lead(user_id="900", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    second = save_lead(user_id="901", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)

    assert first.startswith(leads_store.LEAD_ID_PREFIX)
    assert second.startswith(leads_store.LEAD_ID_PREFIX)
    assert first != second

    assert [row[LEAD_ID_COLUMN] for row in read_rows()] == [first, second]


def test_lead_id_never_changes_through_any_write_path(isolated_leads_file):
    """
    كل مسار كتابة على الملف يُشغَّل بالتتابع على نفس الصف: قراءة،
    متابعة أولى، متابعة ثانية، إنهاء، ثم كتابة صف جديد لعميل آخر
    (تُعيد كتابة الملف كاملاً). المعرّف يجب ألا يتزحزح في أي نقطة.
    """
    lead_id = save_lead(user_id="900", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)

    def current_row():
        rows = [r for r in read_rows() if r["معرف العميل"] == "900"]
        assert len(rows) == 1
        return rows[0]

    assert current_row()[LEAD_ID_COLUMN] == lead_id

    assert mark_followup_sent(lead_id=lead_id, new_stage="1") is True
    assert current_row()[LEAD_ID_COLUMN] == lead_id

    assert mark_followup_sent(lead_id=lead_id, new_stage="2") is True
    assert current_row()[LEAD_ID_COLUMN] == lead_id

    assert mark_expired(lead_id=lead_id) is True
    assert current_row()[LEAD_ID_COLUMN] == lead_id
    assert current_row()["نتيجة المتابعة"] == OUTCOME_EXPIRED

    save_lead(user_id="999", service_name=OTHER_SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    assert current_row()[LEAD_ID_COLUMN] == lead_id

    # وقراءات متكررة من القرص لا تولّد شيئاً جديداً
    for _ in range(3):
        assert current_row()[LEAD_ID_COLUMN] == lead_id


CHILD_SCRIPT = """
import json, sys
sys.path.insert(0, sys.argv[1])
import leads_store
leads_store.LEADS_FILE = sys.argv[2]
leads_store.LOCK_FILE = sys.argv[2] + ".lock"
leads_store.BACKUP_FILE = sys.argv[2] + ".backup-pre-lead-id"
leads_store.BACKUP_FILE_PRICE_QUOTE = sys.argv[2] + ".backup-pre-price-quote-lead"
leads_store.BACKUP_FILE_STATUS_VOCABULARY = sys.argv[2] + ".backup-pre-status-vocabulary"
rows = leads_store._read_all_rows()
print("RESULT:" + json.dumps([r["lead_id"] for r in rows]))
"""


def _read_lead_ids_in_fresh_process(leads_file):
    """يقرأ الملف من عملية Python منفصلة تماماً - أقرب ما يكون لجلسة جديدة."""
    script = Path(leads_file).parent / "child_reader.py"
    script.write_text(CHILD_SCRIPT, encoding="utf-8")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script), str(PROJECT_ROOT), str(leads_file)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=True,
    )
    result_line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")][-1]
    return json.loads(result_line[len("RESULT:"):])


def test_lead_id_stable_across_separate_processes(isolated_leads_file):
    """
    ثبات عبر جلسات فعلية: عمليتان منفصلتان تقرآن نفس الملف بعد
    الكتابة، ولا واحدة منهما تولّد معرّفاً جديداً أو تغيّر الموجود.
    """
    first = save_lead(user_id="900", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    second = save_lead(user_id="901", service_name=SERVICE, channel="whatsapp", status=STATE_PRICE_QUOTED)

    assert _read_lead_ids_in_fresh_process(isolated_leads_file) == [first, second]
    assert _read_lead_ids_in_fresh_process(isolated_leads_file) == [first, second]
    assert [r[LEAD_ID_COLUMN] for r in read_rows()] == [first, second]


# --------------------------------- 2) استفساران في نفس الثانية لنفس العميل

def test_same_second_same_customer_same_service_no_collision(frozen_clock):
    """
    خمسون استفساراً في نفس الثانية من نفس العميل عن نفس الخدمة:
    خمسون صفاً بخمسين معرّفاً فريداً. هذه بالضبط الحالة التي كان
    المفتاح الثلاثي (عميل + خدمة + طابع زمني) يفشل فيها.
    """
    lead_ids = [
        save_lead(user_id="777", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
        for _ in range(50)
    ]

    assert len(set(lead_ids)) == 50

    rows = read_rows()
    assert len(rows) == 50
    assert len({r[LEAD_ID_COLUMN] for r in rows}) == 50

    stamps = {r["التاريخ والوقت"] for r in rows}
    assert stamps == {frozen_clock.strftime(leads_store.TIMESTAMP_FORMAT)}


def test_same_second_leads_are_updated_independently(frozen_clock):
    """
    الصفان متطابقان في كل شيء عدا lead_id. تعليم أحدهما بمتابعة
    وإنهاء الآخر يجب أن يصيب الصف المقصود وحده - لا الأول الذي
    تصادفه المطابقة.
    """
    first = save_lead(user_id="777", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    second = save_lead(user_id="777", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    assert first != second

    assert mark_followup_sent(lead_id=second, new_stage="1") is True
    assert mark_expired(lead_id=first) is True

    by_id = {r[LEAD_ID_COLUMN]: r for r in read_rows()}
    assert len(by_id) == 2

    assert by_id[second]["مرحلة المتابعة"] == "1"
    assert by_id[second]["نتيجة المتابعة"] == OUTCOME_PENDING

    assert by_id[first]["مرحلة المتابعة"] == "0"
    assert by_id[first]["نتيجة المتابعة"] == OUTCOME_EXPIRED


def test_mark_functions_reject_empty_lead_id():
    """معرّف فارغ يجب ألا يطابق الصفوف المهاجَرة أو أي صف آخر."""
    save_lead(user_id="900", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)

    assert mark_followup_sent(lead_id="", new_stage="1") is False
    assert mark_expired(lead_id="") is False
    assert mark_followup_sent(lead_id="ld_غير_موجود", new_stage="1") is False

    row = read_rows()[0]
    assert row["مرحلة المتابعة"] == "0"
    assert row["نتيجة المتابعة"] == OUTCOME_PENDING


# ------------------------------------------------------ 3) سلامة الهجرة

def test_migration_from_v1_preserves_data_and_assigns_ids(isolated_leads_file):
    write_csv(isolated_leads_file, V1_FIELDNAMES, [
        {
            "التاريخ والوقت": "2026-08-01 10:00:00", "معرف العميل": "111",
            "القناة": "telegram", "الخدمة المطلوبة": SERVICE,
            "الحالة": "not_ready", "بيانات التواصل": "", "تمت المتابعة": "نعم",
        },
        {
            "التاريخ والوقت": "2026-08-02 11:00:00", "معرف العميل": "222",
            "القناة": "telegram", "الخدمة المطلوبة": OTHER_SERVICE,
            "الحالة": "confirmed", "بيانات التواصل": "سارة 0770", "تمت المتابعة": "لا",
        },
    ])

    rows = read_rows()

    fieldnames, _ = read_csv(isolated_leads_file)
    assert fieldnames == FIELDNAMES
    assert len(rows) == 2

    assert all(r[LEAD_ID_COLUMN].startswith(leads_store.LEAD_ID_PREFIX) for r in rows)
    assert len({r[LEAD_ID_COLUMN] for r in rows}) == 2

    assert rows[0]["معرف العميل"] == "111"
    assert rows[0]["القناة"] == "telegram"
    assert rows[0]["الخدمة المطلوبة"] == SERVICE
    assert rows[0]["التاريخ والوقت"] == "2026-08-01 10:00:00"
    assert rows[0]["مرحلة المتابعة"] == "1"          # "تمت المتابعة" = نعم

    # V1 لا يحمل status_reason، فلا دليل على سبب `not_ready` -> لا يُخمَّن
    assert rows[0]["الحالة"] == STATE_LEGACY_UNKNOWN

    assert rows[1]["بيانات التواصل"] == "سارة 0770"
    assert rows[1]["الحالة"] == STATE_BOOKING_REQUESTED   # كانت "confirmed"
    assert rows[1]["مرحلة المتابعة"] == "0"          # "تمت المتابعة" = لا


def test_migration_from_v2_preserves_recovered_row_verbatim(isolated_leads_file):
    """
    صف V2 مسترجَع بسعر ونتيجة متابعة وتاريخ آخر متابعة: القيم الثلاث
    يجب أن تبقى حرفياً بعد الهجرة، لا مصفَّرة.

    النسخة السابقة من الهجرة كانت تكتب "" في السعر وتاريخ آخر متابعة
    ونتيجة المتابعة، و"0" في مرحلة المتابعة، لأنها تفترض أن أي ملف
    غير مطابق للترويسة هو V1. هذا الاختبار هو الذي يمسك ذلك السلوك.

    "الحالة" هو الحقل الوحيد الذي تغيّره مواءمة المفردات عمداً، فخرج
    من حلقة المطابقة الحرفية إلى تأكيد صريح على الترجمة. بقية حقول
    V2 تبقى تحت الشرط الصارم كما كانت.
    """
    recovered = {
        "التاريخ والوقت": "2026-08-10 09:00:00",
        "معرف العميل": "333",
        "القناة": "telegram",
        "الخدمة المطلوبة": SERVICE,
        "الحالة": "not_ready",
        "بيانات التواصل": "",
        "سعر الخدمة وقت الإنشاء": "150,000 دينار",
        "مرحلة المتابعة": "2",
        "تاريخ آخر متابعة": "2026-08-14 08:30:00",
        "نتيجة المتابعة": "مسترجَع",
    }
    untouched = {
        "التاريخ والوقت": "2026-08-11 09:00:00",
        "معرف العميل": "444",
        "القناة": "whatsapp",
        "الخدمة المطلوبة": OTHER_SERVICE,
        "الحالة": "not_ready",
        "بيانات التواصل": "",
        "سعر الخدمة وقت الإنشاء": "90,000 دينار",
        "مرحلة المتابعة": "1",
        "تاريخ آخر متابعة": "2026-08-12 10:00:00",
        "نتيجة المتابعة": "",
    }
    write_csv(isolated_leads_file, V2_FIELDNAMES, [recovered, untouched])

    rows = read_rows()
    assert len(rows) == 2

    migrated = rows[0]
    assert migrated["سعر الخدمة وقت الإنشاء"] == "150,000 دينار"
    assert migrated["نتيجة المتابعة"] == OUTCOME_RECOVERED
    assert migrated["تاريخ آخر متابعة"] == "2026-08-14 08:30:00"
    assert migrated["مرحلة المتابعة"] == "2"

    # "الحالة" وحدها تُترجَم: V2 بلا status_reason، فلا دليل على السبب
    assert all(r["الحالة"] == STATE_LEGACY_UNKNOWN for r in rows)

    # ولا حقل واحد آخر من V2 تغيّر في أي من الصفين
    preserved_fields = [f for f in V2_FIELDNAMES if f != "الحالة"]
    for original, after in zip([recovered, untouched], rows):
        for field in preserved_fields:
            assert after[field] == original[field], f"الحقل '{field}' تغيّر أثناء الهجرة"
        assert after[LEAD_ID_COLUMN].startswith(leads_store.LEAD_ID_PREFIX)

    assert len({r[LEAD_ID_COLUMN] for r in rows}) == 2

    # والمؤشرات المشتقة تبقى صحيحة بعد الهجرة
    metrics = leads_store.compute_funnel_metrics()
    assert metrics["recovered_leads"] == 1
    assert metrics["recovered_requested_revenue"] == 150000


def test_migration_is_idempotent(isolated_leads_file):
    """تشغيل الهجرة مرة أخرى لا يغيّر معرّفاً واحداً ولا يعيد كتابة الملف."""
    write_csv(isolated_leads_file, V2_FIELDNAMES, [{
        "التاريخ والوقت": "2026-08-10 09:00:00", "معرف العميل": "333",
        "القناة": "telegram", "الخدمة المطلوبة": SERVICE, "الحالة": "not_ready",
        "بيانات التواصل": "", "سعر الخدمة وقت الإنشاء": "150,000 دينار",
        "مرحلة المتابعة": "2", "تاريخ آخر متابعة": "2026-08-14 08:30:00",
        "نتيجة المتابعة": "مسترجَع",
    }])

    first_pass = read_rows()
    content_after_first = Path(isolated_leads_file).read_bytes()

    for _ in range(3):
        assert read_rows() == first_pass

    assert Path(isolated_leads_file).read_bytes() == content_after_first


def test_migration_backfills_only_missing_lead_ids(isolated_leads_file):
    """
    ملف بالبنية الحالية لكن أحد صفوفه بلا معرّف (كتابة يدوية أو هجرة
    مقطوعة): يُملأ الناقص فقط، والموجود يبقى كما هو حرفياً.
    """
    existing_id = "ld_معرف_قائم_لا_يتغير"
    write_csv(isolated_leads_file, FIELDNAMES, [
        {
            LEAD_ID_COLUMN: existing_id, "التاريخ والوقت": "2026-08-10 09:00:00",
            "معرف العميل": "333", "القناة": "telegram", "الخدمة المطلوبة": SERVICE,
            "الحالة": "not_ready", "بيانات التواصل": "", "سعر الخدمة وقت الإنشاء": "150,000 دينار",
            "مرحلة المتابعة": "1", "تاريخ آخر متابعة": "2026-08-14 08:30:00", "نتيجة المتابعة": "",
        },
        {
            LEAD_ID_COLUMN: "", "التاريخ والوقت": "2026-08-11 09:00:00",
            "معرف العميل": "444", "القناة": "telegram", "الخدمة المطلوبة": OTHER_SERVICE,
            "الحالة": "not_ready", "بيانات التواصل": "", "سعر الخدمة وقت الإنشاء": "90,000 دينار",
            "مرحلة المتابعة": "0", "تاريخ آخر متابعة": "", "نتيجة المتابعة": "",
        },
    ])

    rows = read_rows()

    assert rows[0][LEAD_ID_COLUMN] == existing_id
    assert rows[0]["سعر الخدمة وقت الإنشاء"] == "150,000 دينار"

    assert rows[1][LEAD_ID_COLUMN].startswith(leads_store.LEAD_ID_PREFIX)
    assert rows[1]["سعر الخدمة وقت الإنشاء"] == "90,000 دينار"

    # وثابت بعد ذلك
    assert [r[LEAD_ID_COLUMN] for r in read_rows()] == [r[LEAD_ID_COLUMN] for r in rows]


def test_backup_is_taken_once_before_first_write(isolated_leads_file):
    """
    النسخة الاحتياطية تلتقط حالة ما قبل lead_id بالضبط، ولا تُدهَس
    بأي كتابة لاحقة.
    """
    backup = Path(leads_store.BACKUP_FILE)
    original_row = {
        "التاريخ والوقت": "2026-08-10 09:00:00", "معرف العميل": "333",
        "القناة": "telegram", "الخدمة المطلوبة": SERVICE, "الحالة": "not_ready",
        "بيانات التواصل": "", "سعر الخدمة وقت الإنشاء": "150,000 دينار",
        "مرحلة المتابعة": "0", "تاريخ آخر متابعة": "", "نتيجة المتابعة": "",
    }
    write_csv(isolated_leads_file, V2_FIELDNAMES, [original_row])
    content_before = Path(isolated_leads_file).read_bytes()

    assert not backup.exists()

    read_rows()  # تُشغّل الهجرة، وهي أول كتابة

    assert backup.exists()
    assert backup.read_bytes() == content_before

    # كتابات لاحقة لا تلمس النسخة الاحتياطية
    save_lead(user_id="555", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    assert backup.read_bytes() == content_before


def test_no_backup_on_clean_start(isolated_leads_file):
    """لا ملف سابق = لا شيء يستحق النسخ."""
    save_lead(user_id="900", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    assert not Path(leads_store.BACKUP_FILE).exists()


# ----------------------------------- 4) مفتاح الهوية (channel, user_id)

def test_identity_key_separates_same_user_id_on_different_channels():
    """
    PRD D4: نفس المعرّف الرقمي على telegram وwhatsapp عميلان مختلفان.
    حجز عميل telegram يجب ألا يُعلّم صف عميل whatsapp كمُسترجَع.
    """
    telegram_lead = save_lead(user_id="555", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    whatsapp_lead = save_lead(user_id="555", service_name=SERVICE, channel="whatsapp", status=STATE_PRICE_QUOTED)

    mark_followup_sent(lead_id=telegram_lead, new_stage="1")
    mark_followup_sent(lead_id=whatsapp_lead, new_stage="1")

    save_lead(user_id="555", service_name=SERVICE, channel="telegram",
              status=STATE_BOOKING_REQUESTED, contact_info="سارة 0770")

    by_id = {r[LEAD_ID_COLUMN]: r for r in read_rows()}

    assert by_id[telegram_lead]["نتيجة المتابعة"] == OUTCOME_RECOVERED
    assert by_id[whatsapp_lead]["نتيجة المتابعة"] == OUTCOME_PENDING

    metrics = leads_store.compute_funnel_metrics()
    assert metrics["recovered_leads"] == 1


def test_identity_key_does_not_merge_channels_on_direct_booking():
    """الحجز المباشر بلا متابعة يُعلَّم "عضوي" (organic) - وعلى قناته وحدها."""
    telegram_lead = save_lead(user_id="555", service_name=SERVICE, channel="telegram", status=STATE_PRICE_QUOTED)
    whatsapp_lead = save_lead(user_id="555", service_name=SERVICE, channel="whatsapp", status=STATE_PRICE_QUOTED)

    save_lead(user_id="555", service_name=SERVICE, channel="whatsapp",
              status=STATE_BOOKING_REQUESTED, contact_info="سارة 0770")

    by_id = {r[LEAD_ID_COLUMN]: r for r in read_rows()}

    assert by_id[whatsapp_lead]["نتيجة المتابعة"] == OUTCOME_ORGANIC
    assert by_id[telegram_lead]["نتيجة المتابعة"] == OUTCOME_PENDING
