"""
تسجيل طلبات الحجز والاستفسارات (Leads) بشكل دائم
======================================================
حفظ بسيط في ملف CSV - يسجل كل استفسار وصل لمرحلة عرض سعر، مع بيانات
التواصل الفعلية عند تأكيد الحجز، وعمود لتتبع هل تمت متابعة الاستفسارات
غير المحسومة (not_ready) أم لا - لتفعيل حلقة "استعادة الإيراد الضائع".

لاحقاً سيُستبدل بقاعدة بيانات حقيقية ولوحة تحكم، دون أي تغيير في
منطق العمل الذي يستدعي هذه الدالة.
"""

import csv
import os
from datetime import datetime

LEADS_FILE = "leads.csv"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
FIELDNAMES = [
    "التاريخ والوقت",
    "معرف العميل",
    "القناة",
    "الخدمة المطلوبة",
    "الحالة",
    "بيانات التواصل",
    "تمت المتابعة",
]


def save_lead(user_id: str, service_name: str, channel: str, status: str, contact_info: str = "") -> None:
    """
    status: "confirmed" (حجز مؤكد) أو "not_ready" (استفسرت ولم تحجز الآن)
    contact_info: نص الاسم ورقم الهاتف كما أرسلته العميلة (فارغ إذا لم تُطلب بعد)
    """
    file_exists = os.path.isfile(LEADS_FILE)
    with open(LEADS_FILE, mode="a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(FIELDNAMES)
        writer.writerow([
            datetime.now().strftime(TIMESTAMP_FORMAT),
            user_id,
            channel,
            service_name,
            status,
            contact_info,
            "لا",  # لم تتم المتابعة بعد
        ])


def _read_all_rows() -> list[dict]:
    if not os.path.isfile(LEADS_FILE):
        return []
    with open(LEADS_FILE, mode="r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_all_rows(rows: list[dict]) -> None:
    with open(LEADS_FILE, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def get_eligible_followups(hours_threshold: float) -> list[dict]:
    """
    يرجع كل الاستفسارات التي حالتها not_ready، لم تتم متابعتها بعد،
    ومرّ عليها أكثر من hours_threshold ساعة.
    """
    eligible = []
    now = datetime.now()
    for row in _read_all_rows():
        if row.get("الحالة") != "not_ready":
            continue
        if row.get("تمت المتابعة") == "نعم":
            continue
        try:
            lead_time = datetime.strptime(row["التاريخ والوقت"], TIMESTAMP_FORMAT)
        except (ValueError, KeyError):
            continue
        hours_passed = (now - lead_time).total_seconds() / 3600
        if hours_passed >= hours_threshold:
            eligible.append(row)
    return eligible


def mark_followed_up(user_id: str, service_name: str, timestamp: str) -> bool:
    """
    يحدّث صفاً محدداً (بالمطابقة الدقيقة على التاريخ+العميل+الخدمة)
    ليصبح "تمت المتابعة" = نعم، حتى لا تُرسل نفس الرسالة مرتين.
    يرجع True إذا تم العثور على الصف وتحديثه.
    """
    rows = _read_all_rows()
    updated = False
    for row in rows:
        if (
            row.get("التاريخ والوقت") == timestamp
            and row.get("معرف العميل") == user_id
            and row.get("الخدمة المطلوبة") == service_name
        ):
            row["تمت المتابعة"] = "نعم"
            updated = True
            break
    if updated:
        _write_all_rows(rows)
    return updated