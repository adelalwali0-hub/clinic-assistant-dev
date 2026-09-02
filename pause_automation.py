"""
إيقاف الأتمتة لعميلة واحدة - أداة المشغّل (PRD §18 حاجز S6)
=======================================================================
الطريقة الوحيدة لضبط الإيقاف ورفعه. لا تحرير يدوي لأي ملف: القرار
يمرّ من هنا فيُكتب في المتجر ويُسجَّل في events.jsonl معاً، ولا يقع
أحدهما بلا الآخر.

    py -3 pause_automation.py --list
    py -3 pause_automation.py --status --user 123456
    py -3 pause_automation.py --status --lead ld_ab12…
    py -3 pause_automation.py --pause  --user 123456
    py -3 pause_automation.py --resume --user 123456

[لماذا لا كشف آلي عن «لا تراسلوني»]
كان مطروحاً أن يُشعل الإيقاف تلقائياً حين تطابق رسالتها عبارة توقّف.
رُفض، والسبب في اتجاهَي الخطأ لا في دقّة المطابقة:

  إيجابية كاذبة  - نُسكت عميلة مهتمّة **إلى الأبد**، ولا يعلم أحد أن
                   ذلك حدث: لا رسالة تخرج فلا شيء يلفت النظر إليها.
  سلبية كاذبة    - في وضع Concierge تكلّف رسالة واحدة كان إنسان
                   يقرؤها أصلاً قبل إرسالها.

الأولى صامتة ودائمة، والثانية مرئية ومحدودة. وحيث يقرأ إنسان كل رسالة
أصلاً، التخمين الآلي يضيف خطأً بلا أن يضيف قدرة. الأداة هنا لأن
المشغّل يقرأ، لا لأن الآلة عاجزة عن المطابقة.

⚠️ هذا الاستدلال مربوط بوضع Concierge. §18 يعيد S6 إلى 🔴 لحظة إرسال
النظام رسالة واحدة تلقائياً - وفي الوضع المباشر لا إنسان يقرأ، فتصير
السلبية الكاذبة رسالةً لا يراها أحد قبل مغادرتها. عندها يُعاد فتح
سؤال الكشف بأرقام ذلك الوضع لا بأرقام هذا. مسجَّل في D-023.

[S12] لا نص عميلة في أي مخرَج هنا: المعروض معرّفات وحالات وطوابع زمنية
وأسماء خدمات - لا رسائل ولا بيانات تواصل.
"""

import argparse
import sys

import leads_store
import settings
from storage import pause_store

#: تأكيد مكتوب لرفع الإيقاف. لا راية تتجاوزه (لا `--yes` ولا غيرها):
#: الرفع يستأنف مراسلة إنسانة طلبت التوقف، ومشقّةُ كتابة كلمة هي كل ما
#: يفصل بين قرار وبين ضغطة سهم لأعلى في الطرفية.
RESUME_CONFIRMATION = "RESUME"


def _resolve_identity(args) -> tuple[str, str] | None:
    """
    الهوية `(channel, user_id)` من المعرّف المعطى.

    `--lead` يُترجَم إلى هوية صاحبته: الإيقاف يخصّ إنساناً لا استفساراً،
    ومن يقرأ leads.csv يرى lead_id فيُقبل منه. الترجمة تُطبَع صراحةً
    قبل أي فعل، فلا يظن أحد أنه أوقف استفساراً واحداً من ثلاثة.
    """
    if args.user:
        return settings.CHANNEL_NAME, args.user

    for row in leads_store._read_all_rows():
        if row.get(leads_store.LEAD_ID_COLUMN) == args.lead:
            channel = row.get("القناة", "")
            user_id = row.get("معرف العميل", "")
            print(f"الـLead {args.lead} يخصّ ({channel}) {user_id}")
            return channel, user_id

    print(f"لا يوجد Lead بالمعرّف {args.lead}.", file=sys.stderr)
    return None


def _leads_of(channel: str, user_id: str) -> list[dict]:
    return [
        row for row in leads_store._read_all_rows()
        if row.get("القناة", "") == channel and row.get("معرف العميل", "") == user_id
    ]


def cmd_status(channel: str, user_id: str) -> int:
    record = pause_store.get_pause(channel, user_id)
    rows = _leads_of(channel, user_id)
    open_rows = [r for r in rows if leads_store._is_open_lead(r)]

    print(f"\nالهوية: ({channel}) {user_id}")
    if record is None:
        print("  الأتمتة: تعمل - لم يُطلب إيقافها قط.")
    elif record.get("paused"):
        print(f"  الأتمتة: **موقوفة** منذ {record.get('paused_at')} (المصدر: {record.get('source')})")
    else:
        print(
            f"  الأتمتة: تعمل - أوقِفت في {record.get('paused_at')} "
            f"ورُفع الإيقاف في {record.get('resumed_at')}."
        )

    print(f"  الـLeads: {len(rows)} إجمالاً، {len(open_rows)} مفتوحاً.")
    for row in open_rows:
        print(
            f"    - {row.get(leads_store.LEAD_ID_COLUMN)} | "
            f"{row.get('الخدمة المطلوبة')} | {row.get('الحالة')} | "
            f"مرحلة المتابعة {row.get('مرحلة المتابعة', '0')}"
        )
    return 0


def cmd_list() -> int:
    rows = pause_store.paused_identities()
    print(f"\nالهويات الموقوفة (عدد: {len(rows)}):")
    if not rows:
        print("  لا يوجد.")
        return 0
    for channel, user_id, record in rows:
        open_count = len([r for r in _leads_of(channel, user_id) if leads_store._is_open_lead(r)])
        print(
            f"  - ({channel}) {user_id} | منذ {record.get('paused_at')} | "
            f"المصدر: {record.get('source')} | {open_count} Lead مفتوح"
        )
    return 0


def cmd_pause(channel: str, user_id: str) -> int:
    if leads_store.pause_automation(user_id=user_id, channel=channel):
        print(f"\nتم إيقاف الأتمتة لـ({channel}) {user_id}.")
        print("  لن تُرسَل أي متابعة آلية لأي من استفساراتها - الحالية والقادمة.")
        print("  ردود المحادثة الحية غير متأثرة: إذا راسلتنا، نجيبها.")
        return 0
    print(f"\nالأتمتة موقوفة أصلاً لـ({channel}) {user_id} - لم يتغيّر شيء.")
    return 0


def cmd_resume(channel: str, user_id: str) -> int:
    if not pause_store.is_paused(channel, user_id):
        print(f"\nالأتمتة ليست موقوفة لـ({channel}) {user_id} - لا شيء يُرفع.")
        return 0

    print(f"\nرفع الإيقاف عن ({channel}) {user_id} يعني استئناف مراسلتها آلياً.")
    print("هذه العميلة طلبت التوقف، أو طُلب التوقف نيابةً عنها.")
    try:
        answer = input(f"اكتب {RESUME_CONFIRMATION} للتأكيد، أو أي شيء آخر للإلغاء: ")
    except EOFError:
        answer = ""

    if answer.strip() != RESUME_CONFIRMATION:
        print("أُلغي. الإيقاف كما هو.")
        return 1

    if leads_store.resume_automation(user_id=user_id, channel=channel):
        print(f"استُؤنفت الأتمتة لـ({channel}) {user_id}.")
        return 0
    print("لم يقع تغيير.", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="إيقاف الأتمتة لعميلة واحدة ورفعه (S6).",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="عرض كل الهويات الموقوفة")
    action.add_argument("--status", action="store_true", help="حالة هوية واحدة")
    action.add_argument("--pause", action="store_true", help="إيقاف الأتمتة لهوية واحدة")
    action.add_argument("--resume", action="store_true", help="رفع الإيقاف عن هوية واحدة")

    # المعرّف ليس اختيارياً لأي فعل غير --list، ولا يوجد شكل جماعي له:
    # لا "--all" ولا حرف بدل. انظر ترويسة storage/pause_store.py.
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--user", metavar="USER_ID", help="معرّف العميلة على القناة")
    identity.add_argument("--lead", metavar="LEAD_ID", help="معرّف Lead - يُترجَم إلى هوية صاحبته")
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list()

    if not (args.user or args.lead):
        parser.error(
            "هذا الفعل يحتاج معرّفاً صريحاً: --user أو --lead. "
            "لا يوجد شكل جماعي - الإيقاف قرار عميلة واحدة، ورفعه كذلك."
        )

    identity = _resolve_identity(args)
    if identity is None:
        return 1
    channel, user_id = identity

    if args.status:
        return cmd_status(channel, user_id)
    if args.pause:
        return cmd_pause(channel, user_id)
    return cmd_resume(channel, user_id)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
