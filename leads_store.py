"""
تسجيل طلبات الحجز والاستفسارات (Leads) + محرك Lead Recovery
======================================================================
حفظ بسيط في ملف CSV. يسجل كل استفسار، مع سعر الخدمة كما كان وقت
إنشاء السجل (Snapshot)، ويدير دورة حياة المتابعة على مرحلتين:

  price_quoted --24h--> Follow-up 1 --72h--> Follow-up 2 --> Recovered/Expired

[مفردات الحالة - PRD §8/D2]
عمود "الحالة" يحمل حالة الـLead في دورة الحياة (§7) بمصطلحات §8:
  price_quoted      : سُعِّرت، لم تُجب بعد (Qualified Lead)
  declined          : رفضت صراحةً
  booking_requested : سلّمت بياناتها - Booking Request، *ليس* حجزاً مؤكداً
  legacy_unknown    : صف ما قبل هذه المواءمة، لا دليل على سبب حالته

القيمتان القديمتان `confirmed` و`not_ready` حُذفتا: كلتاهما كانت تقيس
شيئاً غير ما تسمّيه (F2). `confirmed` كان يعني "أرسلت رقمها" بينما
Confirmed Booking في §8 هو تأكيد الموظفة - حدث خارج النظام كلياً.

"نتيجة المتابعة" (الإسناد §9.1 + الانتهاء §7):
  ""        : لم يُحسم بعد
  "مسترجَع"  : حجزت بعد متابعة واحدة على الأقل (followup_assisted)
  "عضوي"    : حجزت مباشرة قبل أي متابعة (organic) - كانت "أُغلق"،
              وهو اسم يوحي بفرصة خاسرة بينما هي حجز ناجح بلا فضل لنا
  "منتهي"   : وصلت للمتابعة الثانية دون حجز

[لا إيراد قبل الحضور - PRD §8، القاعدة الحمراء]
compute_funnel_metrics() لا تُسمّي أي رقم "إيراداً". الطبقات الثلاث
العليا تحمل أسماءها الكاملة (Potential / Requested / Booked)، وطبقتا
Booked Revenue وRevenue تُرجَعان None لا صفراً: الصفر قياسٌ يقول
"قِسنا فوجدنا لا شيء"، وNone يقول "لا بيانات" - وهذا هو الصدق الوحيد
الممكن اليوم، إذ لا بيانات حضور في النظام ولا مسار للحصول عليها قبل
Clinic Feedback Loop (§11).

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
بـ`الحالة = price_quoted`، فيصير مؤهلاً لدورة المتابعة تلقائياً بعد
نافذة الصمت (SILENCE_WINDOW_HOURS، وهي نفسها عتبة أهلية المتابعة
الأولى - رقم واحد لا رقمان).

الردود اللاحقة تُحدِّث نفس الصف عبر lead_id ولا تُنشئ صفاً ثانياً:
  record_booking_request()  - وافقت وسلّمت بياناتها
  record_decline()          - رفضت صراحة (تنقل الحالة إلى declined)
  record_hesitation()       - ترددت (إشارة فقط، الحالة لا تتغير)

`save_lead()` تبقى كما هي حرفياً: تُلحق صفاً دون شرط. المنع من
التكرار يعيش في record_price_quote وحدها - وهذا مقصود، فاستفساران
في نفس الثانية عبر save_lead يبقيان Leadين منفصلين (PRD §6).

[النسخة الاحتياطية] قبل أول كتابة على leads.csv يُنسَخ الملف كما هو
إلى BACKUP_FILE وBACKUP_FILE_PRICE_QUOTE وBACKUP_FILE_STATUS_VOCABULARY،
مرة واحدة فقط لكل اسم، فيبقى لديك دائماً لقطة سليمة على القرص لكل
تغيير يمسّ دلالة الصفوف لا شكلها فقط.

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

import events

LEADS_FILE = "leads.csv"
LOCK_FILE = LEADS_FILE + ".lock"
BACKUP_FILE = LEADS_FILE + ".backup-pre-lead-id"
BACKUP_FILE_PRICE_QUOTE = LEADS_FILE + ".backup-pre-price-quote-lead"
BACKUP_FILE_STATUS_VOCABULARY = LEADS_FILE + ".backup-pre-status-vocabulary"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

LEAD_ID_COLUMN = "lead_id"
LEAD_ID_PREFIX = "ld_"

# ---------------------------------------------------------------- مفردات الحالة
# قيم عمود "الحالة" = حالة الـLead في دورة الحياة (PRD §7) بمصطلحات
# §8/D2 حرفياً. القيمتان السابقتان حُذفتا لأن كلتيهما كانت تكذب:
#   `confirmed` = "أرسلت رقم هاتفها"، بينما Confirmed Booking في §8
#                 محجوز لتأكيد الموظفة - وهو حدث لا يملكه النظام أصلاً.
#   `not_ready` = "قالت لا صراحةً" فقط، بينما Unbooked Lead في §8
#                 هو الصامت. الاسمان كانا يقيسان شيئاً غير ما يسمّيانه.
STATE_PRICE_QUOTED = "price_quoted"
STATE_DECLINED = "declined"
STATE_BOOKING_REQUESTED = "booking_requested"

# ليست حالة في §8 ولا تدّعي أنها كذلك: صف كُتب قبل مواءمة المفردات
# بقيمة `not_ready` وبلا status_reason، فلا دليل في الملف على أنه
# رفض صريح أم صمت بعد تسعير. تصنيفه تخميناً كان سيكتب ادّعاءً في
# بيانات سنقيس عليها لاحقاً. يبقى مؤهلاً للمتابعة كما كان بالضبط،
# ولا يدخل أي مقام في القياس (is_unbooked تستثنيه).
STATE_LEGACY_UNKNOWN = "legacy_unknown"

# الحالات التي ما زال الـLead فيها داخل قمع المتابعة. `declined` منها
# عمداً: الرافضة صراحةً ما زالت تتلقى متابعات آلية اليوم (D-015، تأجيل
# صريح يمسّ S7). هذا يحفظ سلوك المتابعة كما هو حرفياً بعد تغيير الأسماء.
OPEN_STATES = (STATE_PRICE_QUOTED, STATE_DECLINED, STATE_LEGACY_UNKNOWN)

# الحدث المقابل للحالة التي يكتبها مسار السقوط الآمن save_lead. حالة
# خارج هذا الجدول (legacy_unknown أو قيمة من مستدعٍ خارجي) تُنتج
# LEAD_CREATED وحده: صف أُنشئ فعلاً، وحالته لا تُترجم إلى انتقال في
# §6 - وتخمين انتقال لها يكتب ادّعاءً في السجل الذي سنقيس عليه.
_STATE_TO_EVENT = {
    STATE_PRICE_QUOTED: events.PRICE_QUOTED,
    STATE_DECLINED: events.DECLINED,
    STATE_BOOKING_REQUESTED: events.BOOKING_REQUESTED,
}

# قيم عمود "نتيجة المتابعة" = الإسناد (PRD §9.1) + الانتهاء (§7).
OUTCOME_PENDING = ""
OUTCOME_RECOVERED = "مسترجَع"      # followup_assisted - Recovered Lead (§8)
OUTCOME_ORGANIC = "عضوي"           # organic (§9.1) - حجزت بلا فضل للمتابعة
OUTCOME_EXPIRED = "منتهي"          # EXPIRED (§7)

# نافذة الصمت (PRD §8): بعدها يصير الـLead المُسعَّر الصامت Unbooked.
# نفس عتبة الأهلية للمتابعة الأولى - رقم واحد باسمه الصريح، لا رقمان.
SILENCE_WINDOW_HOURS = 24

# حقل تقني بقيم إنجليزية - كما lead_id تماماً. يسجّل *الإشارة الأخيرة*
# من العميلة، وهي سؤال مختلف عن سؤال عمود "الحالة": `hesitant` إشارة
# لا حالة (لا مقابل لها في §7)، والصف يبقى price_quoted بعدها.
# هذه القيم هي التي ستُحمَل لاحقاً في events.jsonl كما هي.
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

# ------------------------------------------------- مفردات ما قبل المواءمة
# `not_ready` وحدها لا تكفي لتحديد الحالة الجديدة: كانت تُكتب للرفض
# الصريح وللصمت بعد التسعير معاً. status_reason هو الدليل الوحيد في
# الملف، وحين يكون فارغاً لا دليل إطلاقاً -> STATE_LEGACY_UNKNOWN.
_LEGACY_STATE_NOT_READY = "not_ready"
_LEGACY_STATE_CONFIRMED = "confirmed"
_LEGACY_OUTCOME_ORGANIC = "أُغلق"

_LEGACY_NOT_READY_BY_REASON = {
    REASON_DECLINED: STATE_DECLINED,
    REASON_PRICE_QUOTED: STATE_PRICE_QUOTED,
    REASON_HESITANT: STATE_PRICE_QUOTED,
}

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
        (BACKUP_FILE_STATUS_VOCABULARY, "مواءمة مفردات الحالة مع PRD §8"),
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


def _remap_vocabulary(row: dict) -> dict:
    """
    يُرجع نسخة من الصف بمفردات §8، ولا يلمس أي حقل آخر.

    ثلاث قواعد فقط، كلها اشتقاق من دليل موجود في الملف - لا تخمين:
      confirmed         -> booking_requested   (الصف يعني: سلّمت بياناتها)
      أُغلق             -> عضوي                (نفس المعنى، اسم لا يكذب)
      not_ready         -> حسب status_reason، و legacy_unknown إن كان فارغاً

    أي قيمة أخرى تمرّ كما هي حرفياً: ملف كُتب بيد أو بنسخة لا نعرفها
    يبقى كما تركه صاحبه، ولا نخترع له تصنيفاً.
    """
    remapped = dict(row)

    state = remapped.get("الحالة", "")
    if state == _LEGACY_STATE_CONFIRMED:
        remapped["الحالة"] = STATE_BOOKING_REQUESTED
    elif state == _LEGACY_STATE_NOT_READY:
        reason = (remapped.get(STATUS_REASON_COLUMN) or "").strip()
        remapped["الحالة"] = _LEGACY_NOT_READY_BY_REASON.get(reason, STATE_LEGACY_UNKNOWN)

    if remapped.get("نتيجة المتابعة", "") == _LEGACY_OUTCOME_ORGANIC:
        remapped["نتيجة المتابعة"] = OUTCOME_ORGANIC

    return remapped


def _has_legacy_vocabulary(rows: list[dict]) -> bool:
    return any(_remap_vocabulary(row) != row for row in rows)


def _needs_migration(existing_fieldnames: list[str], rows: list[dict]) -> bool:
    """
    الملف بحاجة لهجرة إذا اختلفت ترويسته، أو وُجد فيه صف بلا lead_id،
    أو حمل صف واحد مفردات ما قبل §8.

    الترويسة وحدها لم تعد كافية دليلاً: هذه الهجرة تغيّر *قيماً* لا
    أعمدة، فملف بترويسة صحيحة تماماً قد يكون كله بمفردات قديمة.
    """
    if existing_fieldnames != FIELDNAMES:
        return True
    if any(not (row.get(LEAD_ID_COLUMN) or "").strip() for row in rows):
        return True
    return _has_legacy_vocabulary(rows)


def _migrate_file_if_needed_locked() -> None:
    """
    هجرة حافِظة للحقول من أي بنية سابقة إلى البنية الحالية:

      V1 (7 أعمدة، فيها "تمت المتابعة") -> الحالية
      V2 (10 أعمدة، بلا lead_id)        -> الحالية، بلا فقد أي حقل
      V3 (11 عموداً، بلا status_reason) -> الحالية، بلا فقد أي حقل
      V4 (نفس الأعمدة، مفردات ما قبل §8) -> الحالية، بإعادة تسمية القيم
      الحالية                            -> خروج فوري، بلا أي كتابة

    الأعمدة المستجدة تُملأ "" لكل صف قائم: صف كُتب قبل هذا التغيير
    لا يُعرَف سبب حالته، و"" تقول ذلك بصدق بدل تخمينه.

    مواءمة المفردات (V4) تغيّر *قيم* عمودين فقط - "الحالة" و"نتيجة
    المتابعة" - عبر _remap_vocabulary، وبما لا يخترع تصنيفاً لصف لا
    دليل عليه في الملف (يذهب إلى legacy_unknown).

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

        migrated_rows.append(_remap_vocabulary(migrated))

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


def _outcome_for_stage(row: dict) -> str:
    """
    الإسناد (PRD §9.1) من عدّاد المتابعات وقت الحجز: حجزت بعد متابعة
    واحدة على الأقل = مسترجَع (followup_assisted)، وإلا = عضوي
    (organic). القاعدة نفسها التي كانت مكرّرة حرفياً في save_lead
    وrecord_booking_request - نسخة واحدة تمنع انحرافهما عن بعضهما.
    """
    return OUTCOME_RECOVERED if row.get("مرحلة المتابعة", "0") in ("1", "2") else OUTCOME_ORGANIC


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

        if status == STATE_BOOKING_REQUESTED:
            for row in rows:
                if (
                    _same_identity(row, channel, user_id)
                    and row.get("الخدمة المطلوبة") == service_name
                    and row.get("الحالة") in OPEN_STATES
                    and row.get("نتيجة المتابعة", "") == OUTCOME_PENDING
                ):
                    row["نتيجة المتابعة"] = _outcome_for_stage(row)

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

        # الإصدار بعد نجاح كتابة الصف وحده: كتابة فاشلة ترمي قبل هنا
        # فلا يُسجَّل انتقال لم يقع. مسار السقوط الآمن هذا يُنتج نفس
        # أحداث المسار الأساسي، فلا يختفي حجزٌ من القمع لأنه مرّ من هنا.
        base_payload = {
            "user_id": user_id,
            "service_name": service_name,
            "price": new_row["سعر الخدمة وقت الإنشاء"],
        }
        events.emit(events.LEAD_CREATED, lead_id=lead_id, channel=channel,
                    payload={**base_payload, "source": "save_lead"})
        state_event = _STATE_TO_EVENT.get(status)
        if state_event:
            events.emit(state_event, lead_id=lead_id, channel=channel,
                        payload={
                            **base_payload,
                            "followup_stage": new_row["مرحلة المتابعة"],
                            "outcome": new_row["نتيجة المتابعة"],
                            "contact_info_present": bool(contact_info),
                            "source": "save_lead",
                        })
        return lead_id


def _is_open_lead(row: dict) -> bool:
    """
    Lead "مفتوح" = نيّة تجارية لم تُحسم بعد: لا نتيجة متابعة (لا
    مسترجَع ولا عضوي ولا منتهي) ولم يُطلب حجزها.

    Lead محسوم لا يُعاد استخدامه: عميلة حجزت ثم عادت تسأل عن نفس
    الخدمة بعد شهر نيّة تجارية جديدة، لا استكمال للأولى (PRD §6).
    """
    return (
        row.get("نتيجة المتابعة", "") == OUTCOME_PENDING
        and row.get("الحالة") != STATE_BOOKING_REQUESTED
    )


def record_price_quote(user_id: str, service_name: str, channel: str) -> str:
    """
    ينشئ الـLead لحظة الرد بالسعر (PRD D1) ويُرجع lead_id المستقر.

    هذه هي اللحظة التي يصبح فيها الـLead مؤهلاً (Qualified Lead في
    PRD §8): وصل PRICE_QUOTED. الصمت بعدها حالة مشروعة - الصف موجود
    ويدخل دورة المتابعة وحده بعد نافذة الصمت، بلا أي فعل من العميلة.

    الحالة المكتوبة `price_quoted` هي حرفياً ما تعنيه (§7/§8): سُعِّرت
    ولم تُجب بعد. الصف الجديد (مرحلة 0، بلا نتيجة) يصير مؤهلاً للمتابعة
    بعد SILENCE_WINDOW_HOURS من الصمت بالضبط، ويخرج من الأهلية فور
    تحديثه إن ردّت قبلها.

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
                # سُعِّرت مرة أخرى على نفس الـLead: PRICE_QUOTED يقع
                # فعلاً (الرد يحمل السعر)، وLEAD_CREATED لا يقع - لم
                # يُنشأ صف. هذا هو الموضع الوحيد في النظام الذي يعرف
                # الفرق، ولهذا يعيش الإصدار هنا لا في business_logic.
                events.emit(events.PRICE_QUOTED, lead_id=existing_id, channel=channel,
                            payload={
                                "user_id": user_id,
                                "service_name": service_name,
                                "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                                "lead_created": False,
                            })
                return existing_id

        lead_id = _new_lead_id()
        new_row = {
            LEAD_ID_COLUMN: lead_id,
            "التاريخ والوقت": datetime.now().strftime(TIMESTAMP_FORMAT),
            "معرف العميل": user_id,
            "القناة": channel,
            "الخدمة المطلوبة": service_name,
            "الحالة": STATE_PRICE_QUOTED,
            STATUS_REASON_COLUMN: REASON_PRICE_QUOTED,
            "بيانات التواصل": "",
            # لقطة السعر لحظة *عرضه* على العميلة فعلاً، لا لحظة كتابة
            # صف بعدها بيوم - وهو ما يفترضه اسم العمود أصلاً.
            "سعر الخدمة وقت الإنشاء": _lookup_current_price(service_name),
            "مرحلة المتابعة": "0",
            "تاريخ آخر متابعة": "",
            "نتيجة المتابعة": "",
        }
        rows.append(new_row)
        _write_all_rows_unlocked(rows)

        # حدثان في نفس اللحظة، وهما مختلفان قصداً (§6): D1 يجعل
        # الإنشاء والتسعير متزامنين اليوم، وهما ينفصلان في التغيير #6
        # حين يُنشأ Lead عند سؤال الاستيضاح قبل أي سعر.
        quote_payload = {
            "user_id": user_id,
            "service_name": service_name,
            "price": new_row["سعر الخدمة وقت الإنشاء"],
        }
        events.emit(events.LEAD_CREATED, lead_id=lead_id, channel=channel,
                    payload={**quote_payload, "source": "price_quote"})
        events.emit(events.PRICE_QUOTED, lead_id=lead_id, channel=channel,
                    payload={**quote_payload, "lead_created": True})
        return lead_id


def record_booking_request(lead_id: str, contact_info: str) -> bool:
    """
    العميلة وافقت وسلّمت بياناتها: يُحدَّث **نفس صف** عرض السعر عبر
    lead_id، فلا يُنتج مسار "نعم ثم بيانات" صفين.

    الحالة تصير `booking_requested` - وهي حرفياً ما حدث (§8: Booking
    Request = سلّمت بياناتها، *قبل* تأكيد الموظفة). لا يُكتب هنا شيء
    اسمه حجز مؤكَّد: التأكيد والحضور حدثان تملكهما العيادة وحدها،
    ولا يملك النظام أي مسار لكتابتهما (§5، §7).

    نتيجة المتابعة تُحسب بنفس قاعدة save_lead حرفياً عبر
    _outcome_for_stage - فلا يتغير أي رقم تُخرجه compute_funnel_metrics
    عمّا كان يُخرجه المساران السابقان.

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
                if row.get("نتيجة المتابعة", "") == OUTCOME_PENDING:
                    row["نتيجة المتابعة"] = _outcome_for_stage(row)
                row["الحالة"] = STATE_BOOKING_REQUESTED
                row[STATUS_REASON_COLUMN] = REASON_BOOKING_REQUESTED
                row["بيانات التواصل"] = contact_info
                _write_all_rows_unlocked(rows)
                # بيانات التواصل نفسها لا تدخل الحدث - وجودها فقط.
                events.emit(events.BOOKING_REQUESTED, lead_id=lead_id,
                            channel=row.get("القناة", ""),
                            payload={
                                "user_id": row.get("معرف العميل", ""),
                                "service_name": row.get("الخدمة المطلوبة", ""),
                                "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                                "followup_stage": row.get("مرحلة المتابعة", "0"),
                                "outcome": row.get("نتيجة المتابعة", ""),
                                "contact_info_present": bool(contact_info),
                                "source": "record_booking_request",
                            })
                return True
        return False


def _update_lead_row(lead_id: str, changes: dict) -> bool:
    """تعديل حقول محددة في صف واحد بـlead_id. مسار مشترك، لا سلوك خاص به."""
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                row.update(changes)
                _write_all_rows_unlocked(rows)
                return True
        return False


def record_decline(lead_id: str) -> bool:
    """
    رفضت صراحةً: الحالة تصير `declined` - وهي حالة أولى الدرجة في
    دورة حياة §7، لا مجرد سبب مسجَّل في حقل جانبي.

    مرحلة المتابعة ونتيجتها لا تتغيران، و`declined` داخل OPEN_STATES،
    فيبقى الصف مؤهلاً للمتابعة كما هو تماماً. هذا سلوك مقصود ومُسجَّل
    (D-015): الرافضة صراحةً ما زالت تتلقى متابعات آلية اليوم. كتم
    المتابعة عنها قرار سياسة لا قرار تسمية، ويمسّ S7 - وهذا التغيير
    يمسّ الأسماء وحدها فلا يحسمه.

    صف بلغ booking_requested لا تُنزَع منه حالته: الجلسة تُمسح عند
    الحجز فلا يمرّ هذا المسار عملياً، والحارس يمنع أن يمحو خطأ لاحق
    حجزاً قائماً.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                became_declined = row.get("الحالة") != STATE_BOOKING_REQUESTED
                if became_declined:
                    row["الحالة"] = STATE_DECLINED
                row[STATUS_REASON_COLUMN] = REASON_DECLINED
                _write_all_rows_unlocked(rows)
                # الحارس أعلاه يحمي صفاً بلغ booking_requested من فقد
                # حالته؛ عندها لم يقع انتقال في دورة الحياة - تغيّر
                # status_reason وحده - فلا يُصدَر DECLINED عن لا شيء.
                if became_declined:
                    events.emit(events.DECLINED, lead_id=lead_id,
                                channel=row.get("القناة", ""),
                                payload={
                                    "user_id": row.get("معرف العميل", ""),
                                    "service_name": row.get("الخدمة المطلوبة", ""),
                                    "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                                    "followup_stage": row.get("مرحلة المتابعة", "0"),
                                    "source": "record_decline",
                                })
                return True
        return False


def record_hesitation(lead_id: str) -> bool:
    """
    ترددت: إشارة تُسجَّل، ولا حالة تتغير.

    `hesitant` ليست حالة في §7 ولا مصطلحاً في §8 - هي نيّة في شجرة
    القرار (D-006/D-007). الـLead يبقى `price_quoted`: لم تُجب بعد،
    وهذا بالضبط ما تقوله الحالة.
    """
    return _update_lead_row(lead_id, {STATUS_REASON_COLUMN: REASON_HESITANT})


def get_leads_eligible_for_first_followup(hours_threshold: float = SILENCE_WINDOW_HOURS) -> list[dict]:
    """
    الشروط لم تتغير بحرف واحد. الذي تغيّر هو *من* يستوفيها: منذ
    record_price_quote صار الـLead الصامت يُكتب لحظة عرض السعر، فيمرّ
    من هنا وحده بعد hours_threshold من الصمت. هذه العتبة الزمنية هي
    "نافذة الصمت" في PRD §8 - لا حاجة لتمثيلها بحالة مخزَّنة.

    الفلتر صار `in OPEN_STATES` بدل `== "not_ready"`: القيمة الواحدة
    القديمة انقسمت إلى ثلاث (price_quoted / declined / legacy_unknown)،
    وكلها كانت not_ready وكلها تبقى مؤهلة - نفس المجموعة بالضبط.
    """
    eligible = []
    now = datetime.now()
    for row in _read_all_rows():
        if row.get("الحالة") not in OPEN_STATES:
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
        if row.get("الحالة") not in OPEN_STATES:
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
        if row.get("الحالة") not in OPEN_STATES:
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
                # send_followups.py لا يستدعي هذه الدالة إلا بعد نجاح
                # channel.send_message، فالحدث يعني "رسالة غادرت فعلاً"
                # لا "حاولنا". محاولة فاشلة ثم إعادة محاولة ناجحة تُنتج
                # حدثاً واحداً بالضبط، لا حدثين ولا صفراً.
                events.emit(events.FOLLOWUP_SENT, lead_id=lead_id,
                            channel=row.get("القناة", ""),
                            payload={
                                "user_id": row.get("معرف العميل", ""),
                                "service_name": row.get("الخدمة المطلوبة", ""),
                                "stage": new_stage,
                            })
                return True
        return False


def mark_expired(lead_id: str) -> bool:
    """
    يُعلّم صف Lead واحداً كـ"منتهي". المخاطبة بـlead_id وحده - كما في
    mark_followup_sent.

    ما يُكتب لم يتغيّر بحرف واحد عن _update_lead_row: نفس الحقل بنفس
    القيمة بلا شرط. الحلقة مكتوبة صراحةً هنا لأن الحدث يحتاج القناة
    والخدمة من الصف نفسه، و_update_lead_row لا تُرجع الصف.

    LEAD_EXPIRED اسم خارج قائمة §6 - إضافة موثّقة بقرار صريح: §7
    يجعل EXPIRED حالة حقيقية في دورة الحياة و§6 لا يحمل اسماً لها،
    وبلا الحدث لا يستطيع تقرير مشتق من الأحداث وحدها التمييز بين
    Lead منتهٍ وLead ما زال مفتوحاً.

    الإصدار مشروط بأن القيمة السابقة لم تكن "منتهي" أصلاً: استدعاء
    ثانٍ على نفس الصف يكتب نفس القيمة كما كان يفعل تماماً، ولا يضيف
    انتهاءً ثانياً لم يقع.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                already_expired = row.get("نتيجة المتابعة", "") == OUTCOME_EXPIRED
                row["نتيجة المتابعة"] = OUTCOME_EXPIRED
                _write_all_rows_unlocked(rows)
                if not already_expired:
                    events.emit(events.LEAD_EXPIRED, lead_id=lead_id,
                                channel=row.get("القناة", ""),
                                payload={
                                    "user_id": row.get("معرف العميل", ""),
                                    "service_name": row.get("الخدمة المطلوبة", ""),
                                    "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                                    "followup_stage": row.get("مرحلة المتابعة", "0"),
                                })
                return True
        return False


def is_unbooked(row: dict, now: datetime | None = None,
                hours_threshold: float = SILENCE_WINDOW_HOURS) -> bool:
    """
    Unbooked Lead (PRD §8): Qualified Lead لم يصل BOOKING_REQUESTED
    خلال نافذة الصمت.

    مشتقّة لا مخزَّنة: لا شيء في النظام يعمل بجدولة ليكتب هذه الحالة
    لحظة انقضاء النافذة، ولو خُزِّنت لصارت قديمة بين تشغيل وآخر.
    الشرط الزمني نفسه هو التعريف، فتُحسب عند القراءة.

    الرفض الصريح **مستثنى** من هذا المقام: §7 يجعل DECLINED وUNBOOKED
    فرعين شقيقين لا متداخلين، ونصّ §8 وحده يقرأ كأنه يشملهما. اعتُمد
    §7 - وهو قراءة تصحيحية موثّقة للـPRD، لا تعديل عليه. الأثر: مقام
    Recovery Rate يبقى "الصامتات" وحدهن، فلا يُخفَّض المعدل بمن رفضن
    صراحةً - وهو مقلوب الخطأ الذي رصده الـAudit (مقام منقوص يضخّم
    المعدل). legacy_unknown مستثنى كذلك: لا دليل يضعه في أي مقام.

    صف "منتهي" يبقى Unbooked: كان صامتاً ولم يحجز أبداً - وهو بالضبط
    ما يقيسه المقام.
    """
    if row.get("الحالة") != STATE_PRICE_QUOTED:
        return False
    try:
        created = datetime.strptime(row["التاريخ والوقت"], TIMESTAMP_FORMAT)
    except (ValueError, KeyError, TypeError):
        return False
    reference = now or datetime.now()
    return (reference - created).total_seconds() / 3600 >= hours_threshold


def _sum_prices(rows: list[dict]) -> int:
    return sum(_parse_price_to_number(r.get("سعر الخدمة وقت الإنشاء", "")) for r in rows)


def compute_funnel_metrics() -> dict:
    """
    مؤشرات القمع بمفردات PRD §8 حرفياً - وبلا رقم واحد اسمه "إيراد".

    كل صف في الملف هو Qualified Lead بحكم وجوده: لا يُكتب صف إلا بعد
    عرض سعر (record_price_quote) أو حجز (save_lead)، وكلاهما يعني أن
    السعر عُرِض فعلاً.

    طبقات الإيراد الأربع (§8) تُرجَع كلها بأسمائها الكاملة:

      potential_revenue  - Σ سعر كل Qualified Lead      (حجم الفرصة)
      requested_revenue  - Σ سعر كل Booking Request     (مؤشر مبكر)
      booked_revenue     - None: يتطلب تأكيد الموظفة، والنظام لا يملكه
      revenue            - None: يتطلب الحضور، ولا بيانات حضور إطلاقاً

    None وليس صفراً - والفرق ليس شكلياً: الصفر قياسٌ ("قِسنا فوجدنا
    لا شيء")، وNone غياب قياس ("لا بيانات"). صفرٌ في تقرير أمام عيادة
    يُقرأ رقماً حقيقياً، وهذا بالضبط نوع الكذب الذي يعالجه F3.

    recovered_completed_bookings هي وحدة الفوترة الوحيدة (§9.3)، وهي
    None لنفس السبب. الدالة السابقة كانت تُرجع `bookings_recovered`
    مساوياً لـ`leads_recovered` تماماً - نفس القائمة تُعدّ مرتين،
    وأحد الاسمين يوحي بحجوزات مكتملة. الاسم الموحي حُذف، ومكانه رقم
    صادق واحد: لا نعرف.

    وتُرجع `recovered_requested_revenue` بدل `revenue_recovered`:
    نفس الحساب حرفياً، باسم يقول ما يقيسه فعلاً - مجموع أسعار مقتبَسة
    لحظة تسليم الهاتف، لا مالاً وصل العيادة.
    """
    rows = _read_all_rows()
    now = datetime.now()

    booking_requests = [r for r in rows if r.get("الحالة") == STATE_BOOKING_REQUESTED]
    unbooked = [r for r in rows if is_unbooked(r, now)]
    recovered = [r for r in rows if r.get("نتيجة المتابعة") == OUTCOME_RECOVERED]

    return {
        # الأعداد
        "qualified_leads": len(rows),
        "unbooked_leads": len(unbooked),
        "booking_requests": len(booking_requests),
        "recovered_leads": len(recovered),
        "recovered_completed_bookings": None,
        # طبقات الإيراد (§8)
        "potential_revenue": _sum_prices(rows),
        "requested_revenue": _sum_prices(booking_requests),
        "recovered_requested_revenue": _sum_prices(recovered),
        "booked_revenue": None,
        "revenue": None,
    }
