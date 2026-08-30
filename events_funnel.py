"""
تقرير القمع من `events.jsonl` وحده - قراءة فقط (PRD §20 Gate A، §8)
========================================================================
دليل Gate A ينص حرفياً على «تقرير قمع مشتق من `events.jsonl` وحده».
`lead_recovery_report.py` يشتق نفس الأرقام من `leads.csv`. الاثنان
يتعايشان عمداً ولا يحلّ أحدهما محل الآخر: تطابق رقمين مشتقين من
مخزنين مستقلين دليلٌ أقوى من رقم واحد من مخزن واحد. اختلافهما يوماً
ما هو الإنذار الوحيد الذي يكشف انحراف السجل عن الملف.

[ما لا يفعله هذا الملف]
لا يقرأ `leads.csv`، ولا يستورد أي دالة حساب من `leads_store`. الاستيراد
الوحيد منه `_parse_price_to_number` - محلّل نصي محض (نص السعر → عدد)
لا يفتح ملفاً ولا يلمس قرصاً. نسخُه هنا كان سيصنع مُحلّل أسعار ثانياً،
أي مصدر حقيقة ثانياً للمال - وهو أسوأ من ارتباط واحد باسم خاص.
`test_events.py` يثبت الاستقلال سلوكياً: التقرير يعمل كاملاً وملف
leads.csv غائب.

[لماذا مؤشرات لا نص]
`funnel_from_events` تُرجع نفس القاموس الذي تُرجعه
`compute_funnel_metrics`، مفتاحاً بمفتاح. لذلك يتشارك التقريران
`render_report` نفسها: أي فرق في المخرجات يكون فرقاً في الأرقام، لا
فرقاً في التنسيق.
"""

from datetime import datetime

import events
from leads_store import SILENCE_WINDOW_HOURS, _parse_price_to_number


def funnel_from_events(evts: list[dict], now: datetime,
                       hours_threshold: float = SILENCE_WINDOW_HOURS) -> dict:
    """
    يشتق مؤشرات §8 من events.jsonl **وحده**.

    القواعد، وكلٌّ منها ترجمة مباشرة لتعريف §8:
      - Qualified Lead = LEAD_CREATED (لكل lead_id مرة واحدة).
      - الحالة الجارية = آخر حدث حامل لحالة في ترتيب الإلحاق.
        FOLLOWUP_SENT وLEAD_EXPIRED لا يغيّران الحالة - وهذا مطابق
        لـleads.csv حيث لا يلمسان عمود "الحالة".
      - Unbooked = حالته price_quoted ومضت نافذة الصمت على إنشائه.
        لا حدث MARKED_UNBOOKED: التعريف زمني ويُحسب عند القراءة.
      - Recovered = FOLLOWUP_SENT قبل BOOKING_REQUESTED، وبلا
        LEAD_EXPIRED قبله (نتيجة محسومة لا تُدهَس - نفس تحفّظ
        record_booking_request). بلا LEAD_EXPIRED كان هذا الشرط
        الأخير غير قابل للتعبير من الأحداث إطلاقاً.
      - الطبقات التي لا مصدر لها تبقى None، لا صفراً.
    """
    created_at: dict[str, datetime] = {}
    price_of: dict[str, str] = {}
    state_of: dict[str, str] = {}
    followed_up: set[str] = set()
    expired: set[str] = set()
    requested: list[str] = []
    recovered: list[str] = []

    state_events = {
        events.PRICE_QUOTED: "price_quoted",
        events.DECLINED: "declined",
        events.BOOKING_REQUESTED: "booking_requested",
    }

    for e in evts:  # ترتيب الإلحاق هو ترتيب الوقوع
        lead_id, etype, payload = e["lead_id"], e["event_type"], e["payload"]

        if etype == events.LEAD_CREATED:
            created_at[lead_id] = datetime.fromisoformat(e["timestamp"])
            price_of[lead_id] = payload.get("price", "")
        elif etype == events.FOLLOWUP_SENT:
            followed_up.add(lead_id)
        elif etype == events.LEAD_EXPIRED:
            expired.add(lead_id)

        if etype in state_events:
            state_of[lead_id] = state_events[etype]

        if etype == events.BOOKING_REQUESTED:
            requested.append(lead_id)
            if lead_id in followed_up and lead_id not in expired:
                recovered.append(lead_id)

    def total(lead_ids) -> int:
        return sum(_parse_price_to_number(price_of.get(i, "")) for i in lead_ids)

    unbooked = [
        lead_id for lead_id in created_at
        if state_of.get(lead_id) == "price_quoted"
        and (now - created_at[lead_id]).total_seconds() / 3600 >= hours_threshold
    ]

    return {
        "qualified_leads": len(created_at),
        "unbooked_leads": len(unbooked),
        "booking_requests": len(requested),
        "recovered_leads": len(recovered),
        "recovered_completed_bookings": None,
        "potential_revenue": total(created_at),
        "requested_revenue": total(requested),
        "recovered_requested_revenue": total(recovered),
        "booked_revenue": None,
        "revenue": None,
    }


def compute_funnel_metrics_from_events(now: datetime | None = None) -> dict:
    """يقرأ السجل كاملاً ويشتق المؤشرات. نقطة الدخول للسكربت."""
    return funnel_from_events(events.read_all(), now=now or datetime.now())


if __name__ == "__main__":
    import sys

    # الاستيراد هنا لا في الأعلى: الوحدة نفسها يجب أن تبقى قابلة
    # للاستيراد بلا أي علاقة بمشتق leads.csv.
    from lead_recovery_report import render_report

    # التقرير عربي بالكامل، وطرفية ويندوز الافتراضية cp1252. بلا هذا
    # السطر يموت السكربت بـUnicodeEncodeError لحظة توجيه مخرجاته إلى
    # ملف أو أنبوب - أي في كل استعمال غير تفاعلي، وهو استعمال تقرير.
    sys.stdout.reconfigure(encoding="utf-8")

    print(f"[المصدر: {events.EVENTS_FILE} وحده - لا leads.csv]")
    print(render_report(compute_funnel_metrics_from_events()))
