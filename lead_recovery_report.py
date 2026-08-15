"""
تقرير Lead Recovery - قراءة فقط
=====================================
سكربت بسيط يطبع مؤشرات Lead Recovery الثلاثة المطلوبة للقياس.
لا يعدّل أي بيانات - فقط يقرأ leads.csv عبر leads_store.py.
"""

from leads_store import compute_recovery_metrics


if __name__ == "__main__":
    metrics = compute_recovery_metrics()

    print("=== تقرير Lead Recovery ===")
    print(f"Leads Recovered:   {metrics['leads_recovered']}")
    print(f"Bookings Recovered: {metrics['bookings_recovered']}")
    print(f"Revenue Recovered:  {metrics['revenue_recovered']:,} دينار")