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

[إنشاء الـLead لحظة عرض السعر - PRD D1]
`record_price_quote()` هي مسار الإنشاء الحقيقي: تُستدعى لحظة الرد
بالسعر، لا عند تسليم البيانات. الصمت حالة مشروعة - الصف يُكتب فوراً
بـ`الحالة = not_ready` و`status_reason = price_quoted`، فيصير مؤهلاً
لدورة المتابعة تلقائياً بعد نافذة الصمت (شرط الـ24 ساعة في
get_leads_eligible_for_first_followup هو نفسه نافذة الصمت في PRD §8).
لا قيمة جديدة في عمود "الحالة": مواءمة المفردات مع §8 عمل منفصل.

الردود اللاحقة تُحدِّث نفس الصف عبر lead_id ولا تُنشئ صفاً ثانياً:
  record_booking_request()  - وافقت وسلّمت بياناتها
  record_status_reason()    - رفضت صراحة أو ترددت

`save_lead()` تبقى كما هي حرفياً: تُلحق صفاً دون شرط. المنع من
التكرار يعيش في record_price_quote وحدها - وهذا مقصود، فاستفساران
في نفس الثانية عبر save_lead يبقيان Leadين منفصلين (PRD §6).

[النسخة الاحتياطية] قبل أول كتابة على leads.csv يُنسَخ الملف كما هو
إلى BACKUP_FILE وBACKUP_FILE_PRICE_QUOTE، مرة واحدة فقط لكل اسم،
فيبقى لديك دائماً لقطة سليمة على القرص لكل تغيير يمسّ دلالة الصفوف
لا شكلها فقط.

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
BACKUP_FILE_PRICE_QUOTE = LEADS_FILE + ".backup-pre-price-quote-lead"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

LEAD_ID_COLUMN = "lead_id"
LEAD_ID_PREFIX = "ld_"

# حقل تقني بقيم إنجليزية - كما lead_id تماماً. يسجّل *لماذا* الصف
# على حالته الحالية، دون المساس بمفردات عمود "الحالة" نفسه. هذه
# القيم هي التي ستُحمَل لاحقاً في events.jsonl كما هي.
STATUS_REASON_COLUMN = "status_reason"
REASON_PRICE_QUOTED = "price_quoted"
REASON_DECLINED = "declined"
REASON_HESITANT = "hesitant"
REASON_BOOKING_REQUESTED = "booking_requested"

FIELDNAMES = [
    LEAD_ID_COLUMN,
    "التاريخ والوقت",
    "معرف العميل",
    "القناة",
    "الخدمة المطلوبة",
    "الحالة",
    STATUS_REASON_COLUMN,
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
    نسخ احتياطية تُنشأ مرة واحدة فقط لكل اسم، قبل أول كتابة على
    leads.csv بعد التغيير الذي يحمل ذلك الاسم.

    الشرط "مرة واحدة" مقصود: أول كتابة تحدث بعد تغيير ما هي كتابة
    هجرته، فيلتقط الملف الحالة السابقة له بالضبط. لو أُعيد النسخ عند
    كل كتابة لاحقة لضاعت تلك الحالة فوراً وصار الاسم كذباً.

    ولهذا السبب نفسه لكل تغيير اسمه: BACKUP_FILE موجود بالفعل من
    تغيير lead_id، فلو اكتفينا به لما التُقطت لقطة ما قبل إضافة
    status_reason إطلاقاً.

    لا يُنسَخ شيء إذا لم يكن هناك ملف أصلاً (تشغيل نظيف)، وفشل النسخ
    لا يُوقف الكتابة - يُطبع تحذير فقط، فالبيانات الحية أهم.
    """
    if not os.path.isfile(LEADS_FILE):
        return
    for backup_path, label in (
        (BACKUP_FILE, "lead_id"),
        (BACKUP_FILE_PRICE_QUOTE, "إنشاء الـLead لحظة عرض السعر"),
    ):
        if os.path.exists(backup_path):
            continue
        try:
            shutil.copy2(LEADS_FILE, backup_path)
            print(f"[leads_store] نسخة احتياطية لما قبل {label} -> {backup_path}")
        except OSError as e:
            print(f"[leads_store] تحذير: تعذّر إنشاء النسخة الاحتياطية {backup_path}: {e}")


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

      V1 (7 أعمدة، فيها "تمت المتابعة") -> الحالية
      V2 (10 أعمدة، بلا lead_id)        -> الحالية، بلا فقد أي حقل
      V3 (11 عموداً، بلا status_reason) -> الحالية، بلا فقد أي حقل
      الحالية                            -> خروج فوري، بلا أي كتابة

    الأعمدة المستجدة تُملأ "" لكل صف قائم: صف كُتب قبل هذا التغيير
    لا يُعرَف سبب حالته، و"" تقول ذلك بصدق بدل تخمينه.

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
    يكتب صف Lead جديداً - دون شرط - ويُرجع lead_id المستقر الخاص به.

    لم تعد مسار الإنشاء الأساسي: `record_price_quote()` هي التي تُنشئ
    الـLead لحظة عرض السعر (PRD D1). تبقى هذه الدالة كما هي حرفياً
    لمسارين: السقوط الآمن في business_logic.py حين لا تحمل الجلسة
    lead_id (جلسة بدأت قبل هذا التغيير)، وأي استدعاء خارجي قائم.

    لا منع تكرار هنا بقصد: استفساران متتاليان عبر هذه الدالة يبقيان
    صفّين منفصلين. المنع من التكرار يعيش في record_price_quote وحدها.

    status_reason يُترك "" - صف كتبته هذه الدالة لا يحمل سبباً مسجّلاً،
    وهذا أصدق من تخمين سبب من قيمة `status`.

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
            STATUS_REASON_COLUMN: "",
            "بيانات التواصل": contact_info,
            "سعر الخدمة وقت الإنشاء": _lookup_current_price(service_name),
            "مرحلة المتابعة": "0",
            "تاريخ آخر متابعة": "",
            "نتيجة المتابعة": "",
        }
        rows.append(new_row)
        _write_all_rows_unlocked(rows)
        return lead_id


def _is_open_lead(row: dict) -> bool:
    """
    Lead "مفتوح" = نيّة تجارية لم تُحسم بعد: لا نتيجة متابعة (لا
    مسترجَع ولا أُغلق ولا منتهي) ولم يُطلب حجزها.

    Lead محسوم لا يُعاد استخدامه: عميلة حجزت ثم عادت تسأل عن نفس
    الخدمة بعد شهر نيّة تجارية جديدة، لا استكمال للأولى (PRD §6).
    """
    return row.get("نتيجة المتابعة", "") == "" and row.get("الحالة") != "confirmed"


def record_price_quote(user_id: str, service_name: str, channel: str) -> str:
    """
    ينشئ الـLead لحظة الرد بالسعر (PRD D1) ويُرجع lead_id المستقر.

    هذه هي اللحظة التي يصبح فيها الـLead مؤهلاً (Qualified Lead في
    PRD §8): وصل PRICE_QUOTED. الصمت بعدها حالة مشروعة - الصف موجود
    ويدخل دورة المتابعة وحده بعد نافذة الصمت، بلا أي فعل من العميلة.

    الحالة المكتوبة `not_ready` بلا قيمة جديدة في عمود "الحالة":
    شرط الـ24 ساعة في get_leads_eligible_for_first_followup هو نفسه
    نافذة الصمت، فالصف الجديد (مرحلة 0، بلا نتيجة) يصير مؤهلاً بعد
    24 ساعة صمت بالضبط، ويخرج من الأهلية فور تحديثه إن ردّت قبلها.
    السبب الحقيقي مسجَّل في status_reason = price_quoted.

    idempotent لكل نيّة تجارية مفتوحة: إن كان للعميلة نفسها Lead
    مفتوح لنفس الخدمة على نفس القناة، يُرجَع معرّفه بلا كتابة - نفس
    العميلة تسأل عن نفس الخدمة مرتين لا تُنتج صفين. لا يُحدَّث الطابع
    الزمني عند إعادة الاستخدام: تحديثه يدفع ساعة المتابعة للأمام كلما
    سألت، فلا يُتابَع الـLead أبداً.

    البحث من الأحدث للأقدم: الصف الأحدث هو النيّة الجارية فعلاً.
    """
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()

        for row in reversed(rows):
            existing_id = (row.get(LEAD_ID_COLUMN) or "").strip()
            if (
                existing_id
                and _same_identity(row, channel, user_id)
                and row.get("الخدمة المطلوبة") == service_name
                and _is_open_lead(row)
            ):
                return existing_id

        lead_id = _new_lead_id()
        rows.append({
            LEAD_ID_COLUMN: lead_id,
            "التاريخ والوقت": datetime.now().strftime(TIMESTAMP_FORMAT),
            "معرف العميل": user_id,
            "القناة": channel,
            "الخدمة المطلوبة": service_name,
            "الحالة": "not_ready",
            STATUS_REASON_COLUMN: REASON_PRICE_QUOTED,
            "بيانات التواصل": "",
            # لقطة السعر لحظة *عرضه* على العميلة فعلاً، لا لحظة كتابة
            # صف بعدها بيوم - وهو ما يفترضه اسم العمود أصلاً.
            "سعر الخدمة وقت الإنشاء": _lookup_current_price(service_name),
            "مرحلة المتابعة": "0",
            "تاريخ آخر متابعة": "",
            "نتيجة المتابعة": "",
        })
        _write_all_rows_unlocked(rows)
        return lead_id


def record_booking_request(lead_id: str, contact_info: str) -> bool:
    """
    العميلة وافقت وسلّمت بياناتها: يُحدَّث **نفس صف** عرض السعر عبر
    lead_id، فلا يُنتج مسار "نعم ثم بيانات" صفين.

    نتيجة المتابعة تُحسب بنفس قاعدة save_lead حرفياً: "مسترجَع" إن
    سبقتها متابعة (مرحلة 1 أو 2)، وإلا "أُغلق" - فلا يتغير أي رقم
    تُخرجه compute_recovery_metrics عمّا كان يُخرجه المساران السابقان
    (صف not_ready مُعلَّم + صف confirmed جديد).

    نتيجة متابعة محسومة مسبقاً لا تُدهَس: Lead بلغ "منتهي" ثم حجز
    يُسجَّل حجزه ولا يُحتسب استرجاعاً - نفس تحفّظ save_lead، ولا يُضخَّم
    رقم الاسترجاع.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                if row.get("نتيجة المتابعة", "") == "":
                    stage = row.get("مرحلة المتابعة", "0")
                    row["نتيجة المتابعة"] = "مسترجَع" if stage in ("1", "2") else "أُغلق"
                row["الحالة"] = "confirmed"
                row[STATUS_REASON_COLUMN] = REASON_BOOKING_REQUESTED
                row["بيانات التواصل"] = contact_info
                _write_all_rows_unlocked(rows)
                return True
        return False


def record_status_reason(lead_id: str, reason: str) -> bool:
    """
    يسجّل *سبب* حالة الـLead دون المساس بحالته: رفضت صراحة، أو ترددت.

    عمود "الحالة" ومرحلة المتابعة ونتيجتها لا تتغير، فيبقى الصف مؤهلاً
    للمتابعة كما هو. هذا سلوك مقصود ومُسجَّل (D-015): الرافضة صراحةً
    ما زالت تتلقى متابعات آلية اليوم، تماماً كما كانت قبل هذا التغيير
    حين كان الرفض يُنشئ صف not_ready جديداً. كتم المتابعة عنها قرار
    سياسة مؤجَّل للتغيير رقم 3، ويمسّ S7.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                row[STATUS_REASON_COLUMN] = reason
                _write_all_rows_unlocked(rows)
                return True
        return False


def get_leads_eligible_for_first_followup(hours_threshold: float = 24) -> list[dict]:
    """
    الشروط لم تتغير بحرف واحد. الذي تغيّر هو *من* يستوفيها: منذ
    record_price_quote صار الـLead الصامت يُكتب لحظة عرض السعر، فيمرّ
    من هنا وحده بعد hours_threshold من الصمت. هذا العتبة الزمنية هي
    "نافذة الصمت" في PRD §8 - لا حاجة لتمثيلها بحالة منفصلة.
    """
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
