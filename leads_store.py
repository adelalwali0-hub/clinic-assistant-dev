"""
تسجيل طلبات الحجز والاستفسارات (Leads) + محرك Lead Recovery
======================================================================
حفظ بسيط في ملف CSV. يسجل كل استفسار (confirmed/not_ready)، مع سعر
الخدمة كما كان وقت إنشاء السجل (Snapshot)، ويدير دورة حياة المتابعة
على مرحلتين:

  not_ready --24h--> Follow-up 1 --72h--> Follow-up 2 --> Recovered/Expired

"نتيجة المتابعة":
  ""        : لم يُحسم بعد
  "مسترجَع"  : حجزت بعد متابعة واحدة على الأقل
  "أُغلق"    : حجزت مباشرة قبل أي متابعة (لا تُحتسب كاسترجاع)
  "منتهي"   : وصلت للمتابعة الثانية دون حجز

[الهوية والمعرّف - PRD D3/D4]
كل صف يحمل `lead_id` مستقراً يُولَّد مرة واحدة فقط ولا يتغير أبداً
بعدها. كل دوال التعديل (mark_followup_sent, mark_expired) تُخاطب
الصف بـ`lead_id` وحده، لا بمفتاح مركّب من قيم قابلة للتكرار.

هوية العميل مفتاح مركّب (channel, external_user_id) = عمودا "القناة"
و"معرف العميل" معاً. لا Identity Resolution: نفس المعرّف على قناتين
مختلفتين عميلان مختلفان.

[النسخة الاحتياطية] قبل أول كتابة على leads.csv يُنسَخ الملف كما هو
إلى BACKUP_FILE مرة واحدة فقط، فيبقى لديك دائماً الحالة السابقة
لإضافة lead_id سليمة على القرص.

[حماية التزامن] كل عملية تُعدِّل الملف (save_lead, mark_followup_sent,
mark_expired, الهجرة التلقائية) تُنفَّذ بالكامل (قراءة+تعديل+كتابة)
داخل قفل مزدوج:
  1) threading.Lock  - يحمي من تصادم خيوط متعددة داخل نفس العملية.
  2) قفل ملفي بسيط (lock file عبر إنشاء حصري) - يحمي من تصادم عمليتين
     منفصلتين تعملان بالتوازي على نفس leads.csv (مثل main.py مع
     send_followups.py يعمل يدوياً أو لاحقاً عبر جدولة خارجية مثل n8n).
هذا يمنع Lost Update: لا يعود ممكناً أن تُبنى عملية تعديل على قراءة
قديمة تجاوزها تعديل آخر حدث بالتوازي.

الكتابة نفسها Atomic (ملف مؤقت ثم استبدال ذري) لمنع تلف الملف عند
انقطاع مفاجئ أثناء الكتابة.
"""

import csv
import os
import re
import shutil
import time
import threading
import uuid
from datetime import datetime

LEADS_FILE = "leads.csv"
LOCK_FILE = LEADS_FILE + ".lock"
BACKUP_FILE = LEADS_FILE + ".backup-pre-lead-id"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

LEAD_ID_COLUMN = "lead_id"
LEAD_ID_PREFIX = "ld_"

FIELDNAMES = [
    LEAD_ID_COLUMN,
    "التاريخ والوقت",
    "معرف العميل",
    "القناة",
    "الخدمة المطلوبة",
    "الحالة",
    "بيانات التواصل",
    "سعر الخدمة وقت الإنشاء",
    "مرحلة المتابعة",
    "تاريخ آخر متابعة",
    "نتيجة المتابعة",
]

# بنية V1 القديمة (7 أعمدة). وجود عمودها المميز في ترويسة الملف هو
# الدليل الوحيد على أن الهجرة تجري من V1 وليس من بنية أحدث.
_V1_LEGACY_COLUMN = "تمت المتابعة"

_LEGACY_FIELDNAMES = [
    "التاريخ والوقت", "معرف العميل", "القناة", "الخدمة المطلوبة",
    "الحالة", "بيانات التواصل", _V1_LEGACY_COLUMN,
]

_thread_lock = threading.Lock()


class _CrossProcessLock:
    """
    قفل بسيط عابر للعمليات، بدون أي مكتبة خارجية: يحاول إنشاء ملف
    LOCK_FILE حصرياً (يفشل إن كان موجوداً بالفعل من عملية أخرى تعمل
    حالياً)، مع إعادة محاولة قصيرة حتى مهلة زمنية معقولة. يُحذف الملف
    عند الخروج من الـwith دائماً (حتى عند حدوث خطأ).
    """

    def __init__(self, path: str, timeout: float = 10.0, poll_interval: float = 0.05):
        self.path = path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd = None

    def __enter__(self):
        start = time.monotonic()
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.monotonic() - start > self.timeout:
                    raise TimeoutError(
                        f"تعذر الحصول على قفل {self.path} خلال {self.timeout} ثانية - "
                        f"قد تكون هناك عملية أخرى عالقة تستخدم leads.csv."
                    )
                time.sleep(self.poll_interval)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is not None:
            os.close(self._fd)
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


def _locked():
    """
    يُستخدم كـ with _locked(): حول أي عملية قراءة+تعديل+كتابة كاملة.
    يضمن الترتيب: قفل الخيط أولاً (سريع، محلي)، ثم قفل الملف العابر
    للعمليات (الأبطأ نسبياً، يحمي من عمليات أخرى).
    """
    class _Combined:
        def __enter__(self):
            _thread_lock.acquire()
            self._cross = _CrossProcessLock(LOCK_FILE)
            self._cross.__enter__()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            try:
                self._cross.__exit__(exc_type, exc_val, exc_tb)
            finally:
                _thread_lock.release()
            return False

    return _Combined()


def _new_lead_id() -> str:
    """
    معرّف Lead مستقر. يُولَّد مرة واحدة فقط - عند كتابة صف جديد، أو
    عند هجرة صف قديم لا يحمل معرّفاً - ولا تكتبه أي دالة تعديل بعدها.

    عشوائي (uuid4) وليس مشتقاً من (القناة، العميل، الخدمة، الوقت)
    قصداً: الاشتقاق الحتمي يتصادم عند استفسارين في نفس الثانية من
    نفس العميل عن نفس الخدمة، وهما Leadان منفصلان حسب PRD §6.
    """
    return LEAD_ID_PREFIX + uuid.uuid4().hex


def _same_identity(row: dict, channel: str, user_id: str) -> bool:
    """
    مفتاح الهوية المركّب (channel, external_user_id) - PRD D4.
    لا Identity Resolution: نفس المعرّف الرقمي على قناتين مختلفتين
    عميلان مختلفان، ما لم يوجد دليل على العكس - ولا يوجد اليوم.
    """
    return row.get("القناة") == channel and row.get("معرف العميل") == user_id


def _lookup_current_price(service_name: str) -> str:
    try:
        from services import SERVICES
    except Exception:
        return ""
    for s in SERVICES:
        if s.get("name") == service_name:
            return s.get("price", "")
    return ""


def _parse_price_to_number(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", price_str or "")
    return int(digits) if digits else 0


def _read_all_rows_unlocked() -> list[dict]:
    """قراءة بدون قفل - تُستخدم داخلياً فقط من دوال تُمسك القفل بنفسها بالفعل."""
    if not os.path.isfile(LEADS_FILE):
        return []
    try:
        with open(LEADS_FILE, "r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def _backup_once_unlocked() -> None:
    """
    نسخة احتياطية تُنشأ مرة واحدة فقط، قبل أول كتابة على leads.csv.

    الشرط "مرة واحدة" مقصود: أول كتابة تحدث بعد هذا التغيير هي كتابة
    الهجرة، فيلتقط الملف حالة ما قبل lead_id بالضبط. لو أُعيد النسخ
    عند كل كتابة لاحقة لضاعت تلك الحالة فوراً وصار الاسم كذباً.

    لا يُنسَخ شيء إذا لم يكن هناك ملف أصلاً (تشغيل نظيف)، وفشل النسخ
    لا يُوقف الكتابة - يُطبع تحذير فقط، فالبيانات الحية أهم.
    """
    if not os.path.isfile(LEADS_FILE):
        return
    if os.path.exists(BACKUP_FILE):
        return
    try:
        shutil.copy2(LEADS_FILE, BACKUP_FILE)
        print(f"[leads_store] نسخة احتياطية لما قبل lead_id -> {BACKUP_FILE}")
    except OSError as e:
        print(f"[leads_store] تحذير: تعذّر إنشاء النسخة الاحتياطية {BACKUP_FILE}: {e}")


def _write_all_rows_unlocked(rows: list[dict]) -> None:
    """
    كتابة ذرية (Atomic) بدون قفل - تُستخدم داخلياً فقط من دوال تُمسك
    القفل بنفسها بالفعل. هذه هي مسار الكتابة الوحيد على leads.csv،
    ولذلك تُستدعى منها النسخة الاحتياطية.
    """
    _backup_once_unlocked()
    tmp_path = LEADS_FILE + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, LEADS_FILE)


def _needs_migration(existing_fieldnames: list[str], rows: list[dict]) -> bool:
    """الملف بحاجة لهجرة إذا اختلفت ترويسته، أو إذا وُجد فيه صف بلا lead_id."""
    if existing_fieldnames != FIELDNAMES:
        return True
    return any(not (row.get(LEAD_ID_COLUMN) or "").strip() for row in rows)


def _migrate_file_if_needed_locked() -> None:
    """
    هجرة حافِظة للحقول من أي بنية سابقة إلى البنية الحالية:

      V1 (7 أعمدة، فيها "تمت المتابعة") -> V3
      V2 (10 أعمدة، بلا lead_id)        -> V3، بلا فقد أي حقل
      V3                                 -> خروج فوري، بلا أي كتابة

    كل حقل يُنقَل كما هو عبر row.get(field). النسخة السابقة من هذه
    الدالة كانت تصفّر السعر ومرحلة المتابعة وتاريخها ونتيجتها لأنها
    تفترض أن أي ملف غير مطابق للترويسة هو V1 - وهذا يفقد بيانات V2
    بالكامل لحظة إضافة أي عمود جديد.

    idempotent: أي lead_id موجود لا يُعاد توليده أبداً، فتشغيل الهجرة
    مرتين لا يغيّر معرّفاً واحداً.
    """
    if not os.path.isfile(LEADS_FILE):
        return

    try:
        with open(LEADS_FILE, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            existing_fieldnames = reader.fieldnames or []
            rows = list(reader)
    except (OSError, csv.Error):
        return

    if not _needs_migration(existing_fieldnames, rows):
        return

    is_v1 = _V1_LEGACY_COLUMN in existing_fieldnames

    migrated_rows = []
    for row in rows:
        migrated = {field: (row.get(field) or "") for field in FIELDNAMES}

        migrated[LEAD_ID_COLUMN] = migrated[LEAD_ID_COLUMN].strip() or _new_lead_id()

        if is_v1:
            migrated["مرحلة المتابعة"] = "1" if row.get(_V1_LEGACY_COLUMN) == "نعم" else "0"

        migrated_rows.append(migrated)

    _write_all_rows_unlocked(migrated_rows)
    print(
        f"[leads_store] تمت هجرة {LEADS_FILE} إلى البنية الحالية "
        f"({len(migrated_rows)} سجل، مع عمود {LEAD_ID_COLUMN})."
    )


def _read_all_rows() -> list[dict]:
    """قراءة عامة (تُستخدم من الدوال القرائية فقط) - تشمل الهجرة عند الحاجة، بقفل كامل."""
    with _locked():
        _migrate_file_if_needed_locked()
        return _read_all_rows_unlocked()


def save_lead(user_id: str, service_name: str, channel: str, status: str, contact_info: str = "") -> str:
    """
    يكتب صف Lead جديداً ويُرجع lead_id المستقر الخاص به.

    القيمة المُرجَعة إضافة متوافقة رجعياً (كانت None): مواقع الاستدعاء
    الحالية في business_logic.py تتجاهلها دون أي تغيير، وهي المَعبر
    الذي ستستهلكه طبقة الأحداث لاحقاً.
    """
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()

        if status == "confirmed":
            for row in rows:
                if (
                    _same_identity(row, channel, user_id)
                    and row.get("الخدمة المطلوبة") == service_name
                    and row.get("الحالة") == "not_ready"
                    and row.get("نتيجة المتابعة", "") == ""
                ):
                    stage = row.get("مرحلة المتابعة", "0")
                    row["نتيجة المتابعة"] = "مسترجَع" if stage in ("1", "2") else "أُغلق"

        lead_id = _new_lead_id()
        new_row = {
            LEAD_ID_COLUMN: lead_id,
            "التاريخ والوقت": datetime.now().strftime(TIMESTAMP_FORMAT),
            "معرف العميل": user_id,
            "القناة": channel,
            "الخدمة المطلوبة": service_name,
            "الحالة": status,
            "بيانات التواصل": contact_info,
            "سعر الخدمة وقت الإنشاء": _lookup_current_price(service_name),
            "مرحلة المتابعة": "0",
            "تاريخ آخر متابعة": "",
            "نتيجة المتابعة": "",
        }
        rows.append(new_row)
        _write_all_rows_unlocked(rows)
        return lead_id


def get_leads_eligible_for_first_followup(hours_threshold: float = 24) -> list[dict]:
    eligible = []
    now = datetime.now()
    for row in _read_all_rows():
        if row.get("الحالة") != "not_ready":
            continue
        if row.get("مرحلة المتابعة", "0") != "0":
            continue
        if row.get("نتيجة المتابعة", "") != "":
            continue
        try:
            created = datetime.strptime(row["التاريخ والوقت"], TIMESTAMP_FORMAT)
        except (ValueError, KeyError):
            continue
        if (now - created).total_seconds() / 3600 >= hours_threshold:
            eligible.append(row)
    return eligible


def get_leads_eligible_for_second_followup(hours_threshold: float = 72) -> list[dict]:
    eligible = []
    now = datetime.now()
    for row in _read_all_rows():
        if row.get("الحالة") != "not_ready":
            continue
        if row.get("مرحلة المتابعة", "0") != "1":
            continue
        if row.get("نتيجة المتابعة", "") != "":
            continue
        last_followup = row.get("تاريخ آخر متابعة", "")
        if not last_followup:
            continue
        try:
            last_dt = datetime.strptime(last_followup, TIMESTAMP_FORMAT)
        except ValueError:
            continue
        if (now - last_dt).total_seconds() / 3600 >= hours_threshold:
            eligible.append(row)
    return eligible


def get_leads_to_expire(hours_after_second_followup: float = 72) -> list[dict]:
    candidates = []
    now = datetime.now()
    for row in _read_all_rows():
        if row.get("الحالة") != "not_ready":
            continue
        if row.get("مرحلة المتابعة", "0") != "2":
            continue
        if row.get("نتيجة المتابعة", "") != "":
            continue
        last_followup = row.get("تاريخ آخر متابعة", "")
        if not last_followup:
            continue
        try:
            last_dt = datetime.strptime(last_followup, TIMESTAMP_FORMAT)
        except ValueError:
            continue
        if (now - last_dt).total_seconds() / 3600 >= hours_after_second_followup:
            candidates.append(row)
    return candidates


def mark_followup_sent(lead_id: str, new_stage: str) -> bool:
    """
    يُعلّم صف Lead واحداً بأن متابعة أُرسلت له. المخاطبة بـlead_id
    وحده: المفتاح الثلاثي السابق (عميل + خدمة + طابع زمني بدقة الثانية)
    كان قادراً على مطابقة أكثر من صف عند استفسارين في نفس الثانية.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                row["مرحلة المتابعة"] = new_stage
                row["تاريخ آخر متابعة"] = datetime.now().strftime(TIMESTAMP_FORMAT)
                _write_all_rows_unlocked(rows)
                return True
        return False


def mark_expired(lead_id: str) -> bool:
    """يُعلّم صف Lead واحداً كـ"منتهي". المخاطبة بـlead_id وحده - كما في mark_followup_sent."""
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                row["نتيجة المتابعة"] = "منتهي"
                _write_all_rows_unlocked(rows)
                return True
        return False


def compute_recovery_metrics() -> dict:
    recovered_rows = [r for r in _read_all_rows() if r.get("نتيجة المتابعة") == "مسترجَع"]
    revenue = sum(_parse_price_to_number(r.get("سعر الخدمة وقت الإنشاء", "")) for r in recovered_rows)
    return {
        "leads_recovered": len(recovered_rows),
        "bookings_recovered": len(recovered_rows),
        "revenue_recovered": revenue,
    }
