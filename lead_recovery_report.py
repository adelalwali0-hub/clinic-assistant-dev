"""
تقرير قمع الـLeads - قراءة فقط
=====================================
سكربت بسيط يطبع مؤشرات القمع بمفردات PRD §8 حرفياً.
لا يعدّل أي بيانات - فقط يقرأ leads.csv عبر leads_store.py.

[لا إيراد قبل الحضور - PRD §8]
الطبقات الأربع تُطبع كلها بأسمائها الكاملة، بما فيها الطبقتان
اللتان لا نملك بياناتهما. طباعتهما "غير متاح" مع السبب مقصودة:
الغياب الصامت هو ما يجعل قارئ التقرير يعامل أعلى رقم أمامه كإيراد.
الطبقة المفقودة تُقال بصوت عالٍ، لا تُحذف.
"""

from leads_store import SILENCE_WINDOW_HOURS, compute_funnel_metrics

UNAVAILABLE_BOOKED = "غير متاح - يتطلب تأكيد الموظفة، والنظام لا يملك هذا الحدث"
UNAVAILABLE_REVENUE = "غير متاح - يتطلب بيانات حضور، وهي غير موجودة في النظام"

LABEL_WIDTH = 34


def _money_line(label: str, value: int | None, unavailable_reason: str = "", note: str = "") -> str:
    if value is None:
        return f"{label:<{LABEL_WIDTH}}{unavailable_reason}"
    suffix = f"   ← {note}" if note else ""
    return f"{label:<{LABEL_WIDTH}}{value:,} دينار{suffix}"


def _count_line(label: str, value: int | None, unavailable_reason: str = "", note: str = "") -> str:
    if value is None:
        return f"{label:<{LABEL_WIDTH}}{unavailable_reason}"
    suffix = f"   ← {note}" if note else ""
    return f"{label:<{LABEL_WIDTH}}{value}{suffix}"


def render_report(metrics: dict) -> str:
    lines = [
        "=== تقرير قمع الـLeads (PRD §8) ===",
        "",
        "الأعداد:",
        _count_line("Qualified Leads:", metrics["qualified_leads"], note="وصلت PRICE_QUOTED"),
        _count_line("Unbooked Leads:", metrics["unbooked_leads"],
                    note=f"صامتة بعد نافذة {SILENCE_WINDOW_HOURS:g} ساعة"),
        _count_line("Booking Requests:", metrics["booking_requests"], note="سلّمت بياناتها - ليست حجزاً مؤكداً"),
        _count_line("Recovered Leads:", metrics["recovered_leads"], note="حجزت بعد متابعة"),
        _count_line("Recovered Completed Bookings:", metrics["recovered_completed_bookings"],
                    unavailable_reason=UNAVAILABLE_REVENUE),
        "",
        "طبقات الإيراد:",
        _money_line("Potential Revenue:", metrics["potential_revenue"], note="حجم الفرصة، ليس وعداً"),
        _money_line("Requested Revenue:", metrics["requested_revenue"], note="مؤشر مبكر"),
        _money_line("  منها Recovered:", metrics["recovered_requested_revenue"], note="منسوب للمتابعة"),
        _money_line("Booked Revenue:", metrics["booked_revenue"], unavailable_reason=UNAVAILABLE_BOOKED),
        _money_line("Revenue:", metrics["revenue"], unavailable_reason=UNAVAILABLE_REVENUE),
        "",
        "🔴 قاعدة ثابتة: لا يُسمّى رقم «إيراداً» إلا عند الحضور.",
        "   الطبقات الثلاث العليا تُسمّى بأسمائها الكاملة دائماً - في الكود،",
        "   في التقارير، وفي أي عرض بيع.",
        "",
        "   وحدة الفوترة الوحيدة هي Recovered Completed Booking (§9.3)،",
        "   وهي غير متاحة اليوم. لا يُفوتَر على أي رقم أعلاه.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    # التقرير عربي بالكامل، وطرفية ويندوز الافتراضية cp1252. بلا هذا
    # السطر يموت السكربت بـUnicodeEncodeError لحظة توجيه مخرجاته إلى
    # ملف أو أنبوب - أي في كل استعمال غير تفاعلي، وهو استعمال تقرير.
    sys.stdout.reconfigure(encoding="utf-8")

    print(render_report(compute_funnel_metrics()))
