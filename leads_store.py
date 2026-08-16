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
import time
import threading
from datetime import datetime

LEADS_FILE = "leads.csv"
LOCK_FILE = LEADS_FILE + ".lock"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

FIELDNAMES = [
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

_LEGACY_FIELDNAMES = [
    "التاريخ والوقت", "معرف العميل", "القناة", "الخدمة المطلوبة",
    "الحالة", "بيانات التواصل", "تمت المتابعة",
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


def _write_all_rows_unlocked(rows: list[dict]) -> None:
    """كتابة ذرية (Atomic) بدون قفل - تُستخدم داخلياً فقط من دوال تُمسك القفل بنفسها بالفعل."""
    tmp_path = LEADS_FILE + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, LEADS_FILE)


def _migrate_legacy_file_if_needed_locked() -> None:
    if not os.path.isfile(LEADS_FILE):
        return

    try:
        with open(LEADS_FILE, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            existing_fieldnames = reader.fieldnames or []
            rows = list(reader)
    except (OSError, csv.Error):
        return

    if existing_fieldnames == FIELDNAMES:
        return

    migrated_rows = []
    for row in rows:
        migrated = {
            "التاريخ والوقت": row.get("التاريخ والوقت", ""),
            "معرف العميل": row.get("معرف العميل", ""),
            "القناة": row.get("القناة", ""),
            "الخدمة المطلوبة": row.get("الخدمة المطلوبة", ""),
            "الحالة": row.get("الحالة", ""),
            "بيانات التواصل": row.get("بيانات التواصل", ""),
            "سعر الخدمة وقت الإنشاء": "",
            "مرحلة المتابعة": "1" if row.get("تمت المتابعة") == "نعم" else "0",
            "تاريخ آخر متابعة": "",
            "نتيجة المتابعة": "",
        }
        migrated_rows.append(migrated)

    _write_all_rows_unlocked(migrated_rows)
    print(f"[leads_store] تمت هجرة {LEADS_FILE} إلى البنية الجديدة ({len(migrated_rows)} سجل).")


def _read_all_rows() -> list[dict]:
    """قراءة عامة (تُستخدم من الدوال القرائية فقط) - تشمل الهجرة عند الحاجة، بقفل كامل."""
    with _locked():
        _migrate_legacy_file_if_needed_locked()
        return _read_all_rows_unlocked()


def save_lead(user_id: str, service_name: str, channel: str, status: str, contact_info: str = "") -> None:
    with _locked():
        _migrate_legacy_file_if_needed_locked()
        rows = _read_all_rows_unlocked()

        if status == "confirmed":
            for row in rows:
                if (
                    row.get("معرف العميل") == user_id
                    and row.get("الخدمة المطلوبة") == service_name
                    and row.get("الحالة") == "not_ready"
                    and row.get("نتيجة المتابعة", "") == ""
                ):
                    stage = row.get("مرحلة المتابعة", "0")
                    row["نتيجة المتابعة"] = "مسترجَع" if stage in ("1", "2") else "أُغلق"

        new_row = {
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


def mark_followup_sent(user_id: str, service_name: str, timestamp: str, new_stage: str) -> bool:
    with _locked():
        rows = _read_all_rows_unlocked()
        updated = False
        for row in rows:
            if (
                row.get("التاريخ والوقت") == timestamp
                and row.get("معرف العميل") == user_id
                and row.get("الخدمة المطلوبة") == service_name
            ):
                row["مرحلة المتابعة"] = new_stage
                row["تاريخ آخر متابعة"] = datetime.now().strftime(TIMESTAMP_FORMAT)
                updated = True
                break
        if updated:
            _write_all_rows_unlocked(rows)
        return updated


def mark_expired(user_id: str, service_name: str, timestamp: str) -> bool:
    with _locked():
        rows = _read_all_rows_unlocked()
        updated = False
        for row in rows:
            if (
                row.get("التاريخ والوقت") == timestamp
                and row.get("معرف العميل") == user_id
                and row.get("الخدمة المطلوبة") == service_name
            ):
                row["نتيجة المتابعة"] = "منتهي"
                updated = True
                break
        if updated:
            _write_all_rows_unlocked(rows)
        return updated


def compute_recovery_metrics() -> dict:
    recovered_rows = [r for r in _read_all_rows() if r.get("نتيجة المتابعة") == "مسترجَع"]
    revenue = sum(_parse_price_to_number(r.get("سعر الخدمة وقت الإنشاء", "")) for r in recovered_rows)
    return {
        "leads_recovered": len(recovered_rows),
        "bookings_recovered": len(recovered_rows),
        "revenue_recovered": revenue,
    }