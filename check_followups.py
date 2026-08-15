"""
فحص Lead Recovery (Dry Run - بدون إرسال فعلي)
=================================================================
سكربت تشخيصي يُشغَّل يدوياً لعرض حالة دورة Lead Recovery بأكملها في
الطرفية فقط - بدون إرسال أي رسالة حقيقية ولا تعديل أي بيانات.

يعرض ثلاث فئات:
- المؤهلون لـ Follow-up 1
- المؤهلون لـ Follow-up 2
- المرشحون للتعليم كـ "منتهي"
"""

from leads_store import (
    get_leads_eligible_for_first_followup,
    get_leads_eligible_for_second_followup,
    get_leads_to_expire,
)

FIRST_FOLLOWUP_HOURS = 24
SECOND_FOLLOWUP_HOURS = 72
EXPIRE_AFTER_HOURS = 72


def _print_group(title: str, rows: list[dict]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  لا يوجد.")
        return

    for row in rows:
        print(
            f"  - العميل: {row['معرف العميل']} | "
            f"الخدمة: {row['الخدمة المطلوبة']} | "
            f"بتاريخ: {row['التاريخ والوقت']} | "
            f"السعر: {row.get('سعر الخدمة وقت الإنشاء', '')}"
        )


if __name__ == "__main__":
    stage1_candidates = get_leads_eligible_for_first_followup(
        FIRST_FOLLOWUP_HOURS
    )
    stage2_candidates = get_leads_eligible_for_second_followup(
        SECOND_FOLLOWUP_HOURS
    )
    expire_candidates = get_leads_to_expire(EXPIRE_AFTER_HOURS)

    _print_group(
        f"مؤهلون لـFollow-up 1 (عدد: {len(stage1_candidates)}):",
        stage1_candidates,
    )

    _print_group(
        f"مؤهلون لـFollow-up 2 (عدد: {len(stage2_candidates)}):",
        stage2_candidates,
    )

    _print_group(
        f"مرشحون للتعليم كـ'منتهي' (عدد: {len(expire_candidates)}):",
        expire_candidates,
    )