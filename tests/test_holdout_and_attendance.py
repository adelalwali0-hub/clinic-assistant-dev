"""
اختبارات الـHoldout والحضور (PRD §10 وD7، §11، §9.2/§9.3 - D-024).

التغيير بلا أثر سلوكي في وضع اليوم (النسبة صفر، ولا مسار إدخال حضور)،
فما يُختبَر هنا خمس عائلات:

  1) المفردات: ما تكتبه المسارات الحيّة اليوم، وما لا تكتبه.
  2) الحتمية: نفس الـlead_id ينتج نفس المجموعة - **عبر عمليتين
     منفصلتين**، لا داخل عملية واحدة. الحتمية المُدّعاة في docstring
     ليست حتمية: `hash()` المدمجة تنجح في كل اختبار داخل العملية
     الواحدة وتفشل بين تشغيلين حقيقيين.
  3) لحظة الإسناد: عند UNBOOKED لا عند الإنشاء - وهي المسألة التي
     تحدد مقام كل تقرير لاحق.
  4) الاستثناء: الضابطة لا تُتابَع، والفرق بينها وبين الإيقاف (S6).
  5) الحضور: حدثٌ على رصد بشري وحده، ولا اشتقاق غياب من صمت.

والصفر يُختبَر بوصفه سلوكاً قائم الذات: لا كتابة، ولا حدث، ولا تغيّر
في الملف - وهو ما يجعل هذا التغيير آمناً اليوم.
"""

import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import events
import events_funnel
import leads_store
import settings
from leads_store import (
    ATTENDANCE_ATTENDED,
    ATTENDANCE_COLUMN,
    ATTENDANCE_NONE,
    ATTENDANCE_NO_SHOW,
    FIELDNAMES,
    HOLDOUT_COLUMN,
    HOLDOUT_CONTROL,
    HOLDOUT_TREATMENT,
    HOLDOUT_UNASSIGNED,
    LEAD_ID_COLUMN,
    OUTCOME_PENDING,
    STATE_BOOKING_REQUESTED,
    STATE_PRICE_QUOTED,
    STATUS_REASON_COLUMN,
    TIMESTAMP_FORMAT,
    assign_holdout_groups,
    compute_funnel_metrics,
    get_leads_eligible_for_first_followup,
    get_leads_eligible_for_second_followup,
    get_leads_to_expire,
    mark_followup_sent,
    record_attendance,
    record_booking_request,
    record_price_quote,
    save_lead,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SERVICE = "حقن البوتوكس"
PRICE = "120,000 دينار"
CHANNEL = "telegram"

# بنية ما قبل هذا التغيير: الأعمدة الحالية كلها ما عدا العمودين الجديدين.
V6_FIELDNAMES = [f for f in FIELDNAMES
                 if f not in (HOLDOUT_COLUMN, ATTENDANCE_COLUMN)]


# ----------------------------------------------------------------- أدوات

def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_rows():
    return leads_store._read_all_rows()


def read_header(path):
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        return csv.DictReader(f).fieldnames


def emitted(event_type: str) -> list[dict]:
    return [e for e in events.read_all() if e["event_type"] == event_type]


def hours_ago(hours: float) -> str:
    return (datetime.now() - timedelta(hours=hours)).strftime(TIMESTAMP_FORMAT)


def v6_row(lead_id: str, **overrides) -> dict:
    """صف بالبنية السابقة لهذا التغيير - بلا عمودَي §10/§11."""
    row = {
        LEAD_ID_COLUMN: lead_id,
        "التاريخ والوقت": hours_ago(48),
        "معرف العميل": "111",
        "القناة": CHANNEL,
        "الخدمة المطلوبة": SERVICE,
        "الحالة": STATE_PRICE_QUOTED,
        STATUS_REASON_COLUMN: "price_quoted",
        "بيانات التواصل": "",
        "سعر الخدمة وقت الإنشاء": PRICE,
        "مرحلة المتابعة": "0",
        "تاريخ آخر متابعة": "",
        "نتيجة المتابعة": OUTCOME_PENDING,
        leads_store.CONSENT_COLUMN: leads_store.CONSENT_NONE,
        leads_store.CONTACT_WINDOW_COLUMN: "",
    }
    row.update(overrides)
    return row


def current_row(lead_id: str, **overrides) -> dict:
    """صف بالبنية الحالية، غير مُسنَد وغير مرصود ما لم يُطلب غير ذلك."""
    row = v6_row(lead_id, **overrides)
    row.setdefault(HOLDOUT_COLUMN, HOLDOUT_UNASSIGNED)
    row.setdefault(ATTENDANCE_COLUMN, ATTENDANCE_NONE)
    return row


def unbooked_lead(user_id: str = "500") -> str:
    """Lead مُسعَّر صامت منذ 48 ساعة - أي UNBOOKED فعلاً (§8)."""
    lead_id = record_price_quote(user_id, SERVICE, CHANNEL)
    leads_store._update_lead_row(lead_id, {"التاريخ والوقت": hours_ago(48)})
    return lead_id


# ============================================ 1) مفردات ما يُكتب اليوم

def test_creation_paths_write_no_group_and_no_attendance(isolated_leads_file):
    """
    الـLead يُولد خارج التجربة وبلا رصد حضور. `record_price_quote` هي
    مسار الإنشاء (D1)، و`save_lead` مسار السقوط الآمن - ولا امتياز
    لأحدهما.
    """
    record_price_quote("601", SERVICE, CHANNEL)
    save_lead("602", SERVICE, CHANNEL, STATE_PRICE_QUOTED)

    for row in read_rows():
        assert row[HOLDOUT_COLUMN] == HOLDOUT_UNASSIGNED
        assert row[ATTENDANCE_COLUMN] == ATTENDANCE_NONE


def test_unassigned_is_not_the_same_value_as_treatment(isolated_leads_file):
    """
    منطق D-016: «لم يُسنَد» و«أُسنِد إلى المعالَجة» واقعتان مختلفتان
    ولا تتقاسمان قيمة. لو كان الحقل ثنائياً (`false` للاثنتين) لصار
    سؤال «هل دخل هذا الصف التجربة؟» غير قابل للإجابة يوم نقيس.
    """
    assert HOLDOUT_UNASSIGNED != HOLDOUT_TREATMENT
    assert HOLDOUT_UNASSIGNED != HOLDOUT_CONTROL
    assert HOLDOUT_UNASSIGNED == ""


def test_empty_attendance_is_not_a_no_show(isolated_leads_file):
    """
    «لا رصد» ليست «لم تحضر». من لم تحضر لا تترك أثراً اليوم (§11)،
    فالفراغ هنا غياب معلومة لا معلومة غياب - و`no_show` رصدٌ موجب
    يكتبه إنسان رأى.
    """
    assert ATTENDANCE_NONE != ATTENDANCE_NO_SHOW
    assert ATTENDANCE_NONE == ""

    lead_id = record_price_quote("603", SERVICE, CHANNEL)
    record_booking_request(lead_id, "سارة 07701234567")

    assert read_rows()[0][ATTENDANCE_COLUMN] == ATTENDANCE_NONE
    assert emitted(events.NO_SHOW) == []
    assert emitted(events.BOOKING_COMPLETED) == []


def test_lapsed_is_named_in_the_prd_but_defined_nowhere_yet(isolated_leads_file):
    """
    §11 يسمّي `LAPSED` حالةً صريحة لعدم الرد. لا يُعرَّف اليوم لأن لا
    شيء يكتبه - نفس سابقة granted/withdrawn في D-021. رمزٌ بلا منتج
    يُقرأ لاحقاً كأن أحداً رصده.
    """
    values = {name for name in dir(leads_store) if name.startswith("ATTENDANCE_")}
    assert values == {"ATTENDANCE_COLUMN", "ATTENDANCE_ATTENDED",
                      "ATTENDANCE_NO_SHOW", "ATTENDANCE_NONE"}


# ================================================ 2) الحتمية والتدقيق

HOLDOUT_PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import leads_store
lead_ids = json.loads(sys.argv[2])
percentage = float(sys.argv[3])
print("RESULT:" + json.dumps({
    lid: leads_store._holdout_group_for(lid, percentage) for lid in lead_ids
}))
"""


def _groups_in_fresh_process(tmp_path, lead_ids, percentage):
    """
    يحسب المجموعات في عملية Python منفصلة تماماً.

    هذه هي كامل قيمة الاختبار: بايثون يعشوِش تجزئة النصوص لكل عملية
    (PYTHONHASHSEED)، فـ`hash()` المدمجة تعطي نتيجة ثابتة داخل العملية
    الواحدة ومختلفة بين تشغيلين. اختبارٌ داخل عملية واحدة كان سيخضرّ
    على تنفيذ يخالف §10 مخالفةً كاملة.
    """
    script = tmp_path / "holdout_probe.py"
    script.write_text(HOLDOUT_PROBE, encoding="utf-8")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script), str(PROJECT_ROOT),
         json.dumps(lead_ids), str(percentage)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=True,
    )
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")][-1]
    return json.loads(line[len("RESULT:"):])


def test_same_lead_id_gets_the_same_group_in_two_separate_processes(tmp_path):
    """
    عمليتان منفصلتان، نفس المعرّفات، نفس النسبة -> نفس المجموعات
    حرفياً. وهذا ما يجعل الإسناد «قابلاً لإعادة الحساب والتدقيق»
    (§10) بدل أن يكون ادّعاءً في تعليق.

    الاختبار يُشغَّل مرتين لا مرة: تشغيل واحد يقارن العملية الابنة
    بالأم، وتشغيلان يثبتان أن الابنتين متطابقتان كذلك.
    """
    lead_ids = [f"ld_{i:032x}" for i in range(60)]

    first = _groups_in_fresh_process(tmp_path, lead_ids, 50)
    second = _groups_in_fresh_process(tmp_path, lead_ids, 50)
    in_process = {lid: leads_store._holdout_group_for(lid, 50) for lid in lead_ids}

    assert first == second
    assert first == in_process
    # وليست كلها مجموعة واحدة - وإلا لمرّ الاختبار على دالة ثابتة
    assert set(first.values()) == {HOLDOUT_CONTROL, HOLDOUT_TREATMENT}


def test_group_does_not_depend_on_pythonhashseed(tmp_path):
    """
    الحارس المباشر على `hash()` المدمجة: نفس الحساب ببذرتَي تجزئة
    مختلفتين صراحةً. لو استُعملت `hash()` لاختلفت النتيجتان هنا
    وحدهما، وبقي كل اختبار آخر أخضر.
    """
    lead_ids = [f"ld_{i:032x}" for i in range(40)]
    script = tmp_path / "holdout_probe.py"
    script.write_text(HOLDOUT_PROBE, encoding="utf-8")

    results = []
    for seed in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONHASHSEED=seed)
        proc = subprocess.run(
            [sys.executable, str(script), str(PROJECT_ROOT), json.dumps(lead_ids), "50"],
            capture_output=True, text=True, encoding="utf-8", env=env, check=True,
        )
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")][-1]
        results.append(json.loads(line[len("RESULT:"):]))

    assert results[0] == results[1] == results[2], (
        "الإسناد يتغيّر بتغيّر PYTHONHASHSEED - أي أن العميلة تنتقل بين "
        "المجموعتين بين تشغيلين. هذا ما يمنعه §10 حرفياً."
    )


def test_raising_the_percentage_never_moves_a_lead_out_of_control(isolated_leads_file):
    """
    شكل العتبة (`bucket < النسبة`) يجعل الزيادة **تضيف** إلى الضابطة
    ولا تُخرج منها أحداً. لو كانت قسمةً إلى فئات لأعادت كل زيادة توزيع
    الجميع، فيخالف الصفُّ المكتوب أمس إعادةَ حسابه اليوم بلا سبب.
    """
    lead_ids = [f"ld_{i:032x}" for i in range(300)]

    control_at_20 = {lid for lid in lead_ids
                     if leads_store._holdout_group_for(lid, 20) == HOLDOUT_CONTROL}
    control_at_50 = {lid for lid in lead_ids
                     if leads_store._holdout_group_for(lid, 50) == HOLDOUT_CONTROL}

    assert control_at_20 < control_at_50


def test_distribution_tracks_the_configured_percentage(isolated_leads_file):
    """
    التوزيع يقارب النسبة المضبوطة. ليس اختبار جودة تجزئة - هو حارس
    على خطأ حسابي في العتبة (خلط النسبة المئوية بالكسر مثلاً).
    """
    lead_ids = [f"ld_{i:032x}" for i in range(4000)]
    share = sum(leads_store._holdout_group_for(lid, 25) == HOLDOUT_CONTROL
                for lid in lead_ids) / len(lead_ids)

    assert 0.22 < share < 0.28


def test_assignment_event_carries_enough_to_recompute_the_decision(isolated_leads_file):
    """
    §6: كل تقرير يُشتق من `events.jsonl`. فالحدث يحمل المجموعة والسلّة
    و**النسبة التي كانت نافذة لحظة وقوعه** - لا نسبة اليوم. بدونها
    يصير التدقيق مشروطاً بألا يتغيّر الإعداد أبداً بعد أول إسناد.
    """
    lead_id = unbooked_lead("604")

    assert assign_holdout_groups(percentage=50) == 1

    (event,) = emitted(events.HOLDOUT_ASSIGNED)
    assert event["lead_id"] == lead_id
    assert event["payload"]["group"] in (HOLDOUT_CONTROL, HOLDOUT_TREATMENT)
    assert event["payload"]["holdout_percentage"] == 50
    assert event["payload"]["bucket"] == leads_store._holdout_bucket_for(lead_id)
    assert event["payload"]["group"] == read_rows()[0][HOLDOUT_COLUMN]


# ============================================== 3) لحظة الإسناد ومقامه

def test_no_group_is_written_before_the_silence_window_elapses(isolated_leads_file):
    """
    §10: الإسناد عند UNBOOKED. الـLead المُسعَّر قبل ساعة لم يبلغه بعد،
    فلا يُسنَد - ولو أُسنِد لدخل التجربة صفٌّ ما زال يُتوقَّع منه أن يردّ.
    """
    record_price_quote("605", SERVICE, CHANNEL)

    assert assign_holdout_groups(percentage=50) == 0
    assert read_rows()[0][HOLDOUT_COLUMN] == HOLDOUT_UNASSIGNED
    assert emitted(events.HOLDOUT_ASSIGNED) == []


def test_a_lead_that_booked_immediately_never_enters_either_arm(isolated_leads_file):
    """
    **جوهر اختيار لحظة الإسناد.** من حجزت فوراً لم تدخل دورة المتابعة
    قط. الإسناد عند الإنشاء كان سيضعها في إحدى المجموعتين، فيصير مقام
    التقرير «كل من سُعِّرت» بدل «كل من صمتت» - ويقارن مجموعتين
    مختلفتي التركيب ويسمّي الفارق أثراً.
    """
    booked = record_price_quote("606", SERVICE, CHANNEL)
    record_booking_request(booked, "هدى 07701112233")
    silent = unbooked_lead("607")

    assign_holdout_groups(percentage=50)

    rows = {row[LEAD_ID_COLUMN]: row for row in read_rows()}
    assert rows[booked][HOLDOUT_COLUMN] == HOLDOUT_UNASSIGNED
    assert rows[silent][HOLDOUT_COLUMN] in (HOLDOUT_CONTROL, HOLDOUT_TREATMENT)


def test_treatment_is_written_explicitly_not_inferred_by_subtraction(isolated_leads_file):
    """
    الصف يحمل `treatment` مكتوبةً. لو وُسمت الضابطة وحدها لصارت
    المعالَجة في أي تقرير «كل ما عداها» - فتمتلئ بمن حجزن فوراً وبمن
    هويتها موقوفة وبصفوف ما قبل التغيير، ويعود المقامان مختلفَي
    التركيب من الباب الخلفي.
    """
    lead_ids = [unbooked_lead(str(700 + i)) for i in range(12)]

    assign_holdout_groups(percentage=50)

    written = {row[HOLDOUT_COLUMN] for row in read_rows()}
    assert HOLDOUT_TREATMENT in written, (
        "لم تُكتب المعالَجة صراحةً - فهي تُستنتج بالطرح، وهذا ما يفسد المقام"
    )
    assert written <= {HOLDOUT_CONTROL, HOLDOUT_TREATMENT}
    assert len(lead_ids) == len(read_rows())


def test_assignment_happens_once_and_never_changes(isolated_leads_file):
    """
    §10: «يُسنَد مرة واحدة ولا يتغيّر أبداً». تشغيلٌ ثانٍ لا يُعيد
    إسناد صفٍّ موسوم ولا يُصدر حدثاً ثانياً - حتى بنسبة مختلفة تماماً.
    """
    unbooked_lead("608")
    assert assign_holdout_groups(percentage=50) == 1
    first = read_rows()[0][HOLDOUT_COLUMN]

    assert assign_holdout_groups(percentage=50) == 0
    assert assign_holdout_groups(percentage=99) == 0

    assert read_rows()[0][HOLDOUT_COLUMN] == first
    assert len(emitted(events.HOLDOUT_ASSIGNED)) == 1


def test_a_paused_identity_is_in_neither_arm(isolated_leads_file):
    """
    من طلبت التوقف خارج التجربة بمجموعتيها. عدّها في المعالَجة يلوّثها
    بمن لن تصلها متابعة أبداً؛ وعدّها في الضابطة يحسب قرارها الشخصي
    تصميماً منّا. الإيقاف قرار عميلة، والـholdout تصميم قياس.
    """
    lead_id = unbooked_lead("609")
    leads_store.pause_automation(user_id="609", channel=CHANNEL)

    assert assign_holdout_groups(percentage=50) == 0
    assert read_rows()[0][HOLDOUT_COLUMN] == HOLDOUT_UNASSIGNED
    assert lead_id not in [e["lead_id"] for e in emitted(events.HOLDOUT_ASSIGNED)]


def test_a_lead_that_already_got_a_followup_is_never_assigned(isolated_leads_file):
    """
    الإسناد لا يقع بأثر رجعي على صفٍّ تلقّى متابعته أصلاً: وسمُه
    ضابطةً يزعم أنه لم يُتابَع، ووسمُه معالَجةً يزعم أنه دخل تجربة لم
    تكن تعمل حين تُوبع.
    """
    lead_id = unbooked_lead("610")
    mark_followup_sent(lead_id=lead_id, new_stage="1")

    assert assign_holdout_groups(percentage=50) == 0
    assert read_rows()[0][HOLDOUT_COLUMN] == HOLDOUT_UNASSIGNED


# ================================================ 4) الاستثناء من المتابعة

def test_control_is_not_eligible_and_treatment_is(isolated_leads_file):
    """
    شرط الاستثناء الوحيد في محرك المتابعة (§10: «حقل واحد + شرط استثناء
    واحد»). صفّان متطابقان في كل شيء إلا المجموعة يتصرّفان تصرفين.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [
        current_row("ld_ctrl", **{"معرف العميل": "801", HOLDOUT_COLUMN: HOLDOUT_CONTROL}),
        current_row("ld_treat", **{"معرف العميل": "802", HOLDOUT_COLUMN: HOLDOUT_TREATMENT}),
        current_row("ld_plain", **{"معرف العميل": "803"}),
    ])

    eligible = {row[LEAD_ID_COLUMN] for row in get_leads_eligible_for_first_followup()}
    assert eligible == {"ld_treat", "ld_plain"}


def test_control_can_never_reach_the_other_two_passes(isolated_leads_file):
    """
    توثيق **لماذا لا فلتر holdout** في المتابعة الثانية والإنهاء: آلة
    الحالات تكفي. الصف الضابط لا يتلقى متابعة أولى، فمرحلته تبقى "0"
    ولا يبلغ "1" ولا "2" أبداً. حاجزٌ هناك كودٌ ميت يدّعي حراسة.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [
        current_row("ld_c1", **{HOLDOUT_COLUMN: HOLDOUT_CONTROL}),
    ])

    assert get_leads_eligible_for_first_followup() == []
    assert get_leads_eligible_for_second_followup() == []
    assert get_leads_to_expire() == []
    assert read_rows()[0]["مرحلة المتابعة"] == "0"


def test_control_still_receives_a_full_live_reply(isolated_leads_file):
    """
    §10 حرفياً: «لا يُحجب أي شيء طلبته العميلة. أي رسالة منها تتلقى
    رداً كاملاً فورياً». ولهذا **لا حارس holdout في outbound.send**
    بإزاء حارس الإيقاف هناك: `send` لا يميّز متابعةً من رد حيّ، فحارسٌ
    فيه كان سيُسكِت جوابها هي - وهو الشيء الوحيد الذي يمنعه §10.
    """
    import outbound
    from channel_interface import OutgoingMessage

    class FakeChannel:
        channel_name = CHANNEL

        def __init__(self):
            self.sent = []

        def send_message(self, message):
            self.sent.append(message)
            return True

    write_csv(isolated_leads_file, FIELDNAMES, [
        current_row("ld_c2", **{"معرف العميل": "804", HOLDOUT_COLUMN: HOLDOUT_CONTROL}),
    ])
    channel = FakeChannel()

    sent = outbound.send(
        channel,
        OutgoingMessage(user_id="804", text="السعر 120,000 دينار", variant_id="price_quote.v1"),
        lead_id="ld_c2",
    )

    assert sent is True
    assert len(channel.sent) == 1


def test_holdout_lives_on_the_lead_and_pause_on_the_identity(isolated_leads_file):
    """
    الفرق البنيوي الذي يمنع دمج الحاجزين: الإيقاف يشمل كل Leadات
    الهوية، والـholdout يُسنَد لكل Lead على حدة. عميلة لها استفساران
    قد يقع أحدهما في كل مجموعة - وهذا سليم، ورفعُه إلى الهوية يكسر
    التوزيع الحتمي ويُدخل حالةً على مستوى الهوية يمنعها §21.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [
        current_row("ld_x", **{"معرف العميل": "805", "الخدمة المطلوبة": SERVICE,
                               HOLDOUT_COLUMN: HOLDOUT_CONTROL}),
        current_row("ld_y", **{"معرف العميل": "805", "الخدمة المطلوبة": "ليزر",
                               HOLDOUT_COLUMN: HOLDOUT_TREATMENT}),
    ])

    eligible = {row[LEAD_ID_COLUMN] for row in get_leads_eligible_for_first_followup()}
    assert eligible == {"ld_y"}

    # بينما الإيقاف على نفس الهوية يُسقط الاثنين معاً
    leads_store.pause_automation(user_id="805", channel=CHANNEL)
    assert get_leads_eligible_for_first_followup() == []


# ================================================ 5) الصفر: لا شيء يقع

def test_zero_percent_writes_nothing_and_emits_nothing(isolated_leads_file):
    """
    وضع اليوم المعتمد. لا صفٌّ يُسنَد، ولا حدث، ولا حتى كتابة على
    القرص - وهذا ما يجعل هذا التغيير بلا أثر سلوكي إطلاقاً.
    """
    unbooked_lead("611")
    mtime_before = Path(isolated_leads_file).stat().st_mtime_ns

    assert assign_holdout_groups(percentage=0) == 0

    assert Path(isolated_leads_file).stat().st_mtime_ns == mtime_before
    assert read_rows()[0][HOLDOUT_COLUMN] == HOLDOUT_UNASSIGNED
    assert emitted(events.HOLDOUT_ASSIGNED) == []


def test_zero_is_the_default_and_the_shipped_configuration(isolated_leads_file):
    """
    الافتراضي صفر في الكود **وفي الملف المرفوع**. §10 يشترط نسبة
    «محسوبة لا مفترضة» من Baseline وMDE (شرط خروج Gate C)، وكلاهما
    غير موجود اليوم.
    """
    assert settings.HOLDOUT_PERCENTAGE == 0
    assert leads_store.HOLDOUT_PERCENTAGE == 0
    assert assign_holdout_groups() == 0


def test_eligibility_is_identical_with_the_experiment_off(isolated_leads_file):
    """
    بنسبة صفر تُنتج دورة المتابعة نفس المجموعة التي كانت تنتجها قبل
    وجود العمود أصلاً.
    """
    for i in range(5):
        unbooked_lead(str(900 + i))
    before = {row[LEAD_ID_COLUMN] for row in get_leads_eligible_for_first_followup()}

    assign_holdout_groups()

    assert {row[LEAD_ID_COLUMN] for row in get_leads_eligible_for_first_followup()} == before
    assert len(before) == 5


# ======================================================= 6) الحضور (§11)

@pytest.mark.parametrize("attendance,expected_event", [
    (ATTENDANCE_ATTENDED, events.BOOKING_COMPLETED),
    (ATTENDANCE_NO_SHOW, events.NO_SHOW),
])
def test_recording_attendance_emits_exactly_its_event(isolated_leads_file,
                                                      attendance, expected_event):
    """
    موضع الإصدار الوحيد لاسمَي §6 اللذين لم يكن لهما موضع قط. رصدٌ
    واحد -> حدث واحد باسمه، ولا شيء غيره.
    """
    lead_id = record_price_quote("612", SERVICE, CHANNEL)
    record_booking_request(lead_id, "سارة 07701234567")

    assert record_attendance(lead_id, attendance) is True

    assert read_rows()[0][ATTENDANCE_COLUMN] == attendance
    (event,) = emitted(expected_event)
    assert event["lead_id"] == lead_id
    other = {events.BOOKING_COMPLETED, events.NO_SHOW} - {expected_event}
    assert emitted(other.pop()) == []


def test_attendance_does_not_move_the_lifecycle_state(isolated_leads_file):
    """
    لا حالة `completed` في دورة الحياة. إضافتها تحرّك OPEN_STATES
    وis_unbooked ومقام كل نسبة في compute_funnel_metrics - و§9.3 لا
    تحتاجها: وحدة الفوترة تُشتق من `events.jsonl`.
    """
    lead_id = record_price_quote("613", SERVICE, CHANNEL)
    record_booking_request(lead_id, "سارة 07701234567")

    record_attendance(lead_id, ATTENDANCE_ATTENDED)

    assert read_rows()[0]["الحالة"] == STATE_BOOKING_REQUESTED


def test_attendance_is_refused_for_a_lead_that_never_requested_a_booking(isolated_leads_file):
    """
    من لم تطلب حجزاً لا تملك موعداً، فتسجيل حضورها ادّعاء عن لقاء لم
    يُطلب قط. §7 يجعل كل انتقال بعد REQUESTED فرعاً منه.
    """
    lead_id = record_price_quote("614", SERVICE, CHANNEL)

    assert record_attendance(lead_id, ATTENDANCE_ATTENDED) is False

    assert read_rows()[0][ATTENDANCE_COLUMN] == ATTENDANCE_NONE
    assert emitted(events.BOOKING_COMPLETED) == []


@pytest.mark.parametrize("bad", ["", "lapsed", "غير مثبت", "true", "completed", None])
def test_unknown_attendance_values_are_refused_with_no_event(isolated_leads_file, bad):
    """
    مفردات مغلقة. `lapsed` من بينها اليوم صراحةً: §11 يسمّيه لكن لا
    مسار يكتبه، وقبولُه هنا كان سيسمح بكتابة رمز لا ينتجه شيء.
    """
    lead_id = record_price_quote("615", SERVICE, CHANNEL)
    record_booking_request(lead_id, "سارة 07701234567")

    assert record_attendance(lead_id, bad) is False

    assert read_rows()[0][ATTENDANCE_COLUMN] == ATTENDANCE_NONE
    assert emitted(events.BOOKING_COMPLETED) == []
    assert emitted(events.NO_SHOW) == []


def test_re_recording_the_same_value_emits_no_second_event(isolated_leads_file):
    """
    الحدث على الانتقال وحده - نفس تحفّظ mark_expired وpause_automation.
    ضغطتان على نفس الزر لا تُنتجان حضورين.
    """
    lead_id = record_price_quote("616", SERVICE, CHANNEL)
    record_booking_request(lead_id, "سارة 07701234567")

    record_attendance(lead_id, ATTENDANCE_ATTENDED)
    record_attendance(lead_id, ATTENDANCE_ATTENDED)

    assert len(emitted(events.BOOKING_COMPLETED)) == 1


def test_attendance_event_carries_the_holdout_group(isolated_leads_file):
    """
    §9.3: وحدة الفوترة تشترط أن يكون الـLead **في المجموعة المعالَجة**
    وأن تكون العيادة أكّدت الحضور. الشرطان يلتقيان في هذا الحدث، فيُقرأ
    الاستحقاق من السجل وحده بلا العودة إلى leads.csv (§6).
    """
    lead_id = unbooked_lead("617")
    assign_holdout_groups(percentage=50)
    group = read_rows()[0][HOLDOUT_COLUMN]
    record_booking_request(lead_id, "سارة 07701234567")

    record_attendance(lead_id, ATTENDANCE_ATTENDED)

    (event,) = emitted(events.BOOKING_COMPLETED)
    assert event["payload"]["holdout_group"] == group


def test_no_module_calls_record_attendance_yet(isolated_leads_file):
    """
    حارس نصّي: نقطتا لمس §11 غير مبنيتين، ولا مسار إنتاجي يسجّل حضوراً.
    أول مستدعٍ يجب أن يكون قراراً مرئياً في الـdiff - لا تسلّلاً.
    """
    consumers = ["send_followups.py", "check_followups.py", "business_logic.py",
                 "message_router.py", "lead_recovery_report.py", "events_funnel.py",
                 "outbound.py", "main.py", "pause_automation.py"]

    for name in consumers:
        source = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "record_attendance" not in source, f"{name} صار يسجّل حضوراً"


# ======================================================= 7) الهجرة

def test_migration_adds_both_columns_and_leaves_them_empty(isolated_leads_file):
    """
    الصف القديم لم يكن في أي تجربة ولم يُرصد حضوره. "" تقول ذلك بصدق،
    ولا يُحسب له holdout بأثر رجعي رغم أن حسابه ممكن تماماً: إسنادٌ
    يُكتب اليوم لصفٍّ تلقّى متابعاته قبل شهر ادّعاءٌ عن الماضي.
    """
    write_csv(isolated_leads_file, V6_FIELDNAMES, [v6_row("ld_m1")])

    row = read_rows()[0]
    assert read_header(isolated_leads_file) == FIELDNAMES
    assert row[HOLDOUT_COLUMN] == HOLDOUT_UNASSIGNED
    assert row[ATTENDANCE_COLUMN] == ATTENDANCE_NONE


def test_migration_preserves_every_pre_change_value(isolated_leads_file):
    """هجرة حافِظة للحقول: لا حقل من البنية السابقة يتغيّر بحرف."""
    original = v6_row("ld_m2", **{
        "معرف العميل": "222", "القناة": "instagram",
        "سعر الخدمة وقت الإنشاء": "150,000 دينار",
        "مرحلة المتابعة": "2", "تاريخ آخر متابعة": "2026-08-24 08:30:00",
        "بيانات التواصل": "هدى 07709876543",
        leads_store.CONTACT_WINDOW_COLUMN: "2026-08-20 09:00:00",
    })
    write_csv(isolated_leads_file, V6_FIELDNAMES, [original])

    after = read_rows()[0]
    for field in V6_FIELDNAMES:
        assert after[field] == original[field], f"الحقل '{field}' تغيّر أثناء الهجرة"


def test_migration_is_idempotent(isolated_leads_file):
    write_csv(isolated_leads_file, V6_FIELDNAMES, [v6_row("ld_m3")])

    first = read_rows()[0]
    second = read_rows()[0]

    assert first == second
    assert second[LEAD_ID_COLUMN] == "ld_m3"


def test_empty_new_columns_never_rewrite_the_file(isolated_leads_file):
    """
    "" قيمة نهائية مشروعة في العمودين - وهي قيمة **كل صف اليوم**. لو
    أشعلت الهجرة لأُعيدت كتابة الملف عند كل قراءة بلا نهاية. هذا هو
    سلوك contact_window_opened_at لا سلوك consent_status.
    """
    write_csv(isolated_leads_file, FIELDNAMES, [current_row("ld_m4")])
    mtime_before = Path(isolated_leads_file).stat().st_mtime_ns

    read_rows()
    read_rows()

    assert Path(isolated_leads_file).stat().st_mtime_ns == mtime_before


def test_migration_takes_its_own_backup_once(isolated_leads_file):
    """لقطة ما قبل هذا التغيير، ولا تُدهَس بأي كتابة لاحقة."""
    backup = Path(leads_store.BACKUP_FILE_HOLDOUT_ATTENDANCE)
    write_csv(isolated_leads_file, V6_FIELDNAMES, [v6_row("ld_m5")])

    read_rows()
    assert backup.is_file()
    snapshot = backup.read_text(encoding="utf-8-sig")
    assert HOLDOUT_COLUMN not in snapshot      # لقطة ما *قبل* العمودين فعلاً
    assert ATTENDANCE_COLUMN not in snapshot

    record_price_quote("618", SERVICE, CHANNEL)
    assert backup.read_text(encoding="utf-8-sig") == snapshot


# ======================================================= 8) الإعداد

def load_config(tmp_path, monkeypatch, config) -> dict:
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", str(path))
    return settings._load()


def config_error(tmp_path, monkeypatch, config) -> str:
    with pytest.raises(SystemExit) as excinfo:
        load_config(tmp_path, monkeypatch, config)
    return str(excinfo.value)


def test_absent_holdout_key_means_zero(tmp_path, monkeypatch):
    """الغياب يأخذ قيمة اليوم - نفس عقد بقية الملف."""
    assert load_config(tmp_path, monkeypatch, {})["holdout_percentage"] == 0


@pytest.mark.parametrize("value", [0, 10, 20, 50, 12.5, 99.9])
def test_valid_percentages_are_accepted(tmp_path, monkeypatch, value):
    """الصفر قيمة مشروعة لا غياب - ولهذا لا تصلح `_positive_number` هنا."""
    loaded = load_config(tmp_path, monkeypatch, {"holdout": {"percentage": value}})
    assert loaded["holdout_percentage"] == value


@pytest.mark.parametrize("value", ["20", True, None, [20]])
def test_wrong_type_halts_boot(tmp_path, monkeypatch, value):
    """لا سقوط صامت إلى الافتراضي: إعدادٌ يكذب على قارئه أسوأ من توقف."""
    assert "holdout.percentage" in config_error(
        tmp_path, monkeypatch, {"holdout": {"percentage": value}})


@pytest.mark.parametrize("value", [-1, -0.5])
def test_negative_percentage_halts_boot(tmp_path, monkeypatch, value):
    assert "holdout.percentage" in config_error(
        tmp_path, monkeypatch, {"holdout": {"percentage": value}})


@pytest.mark.parametrize("value", [100, 100.5, 250])
def test_hundred_percent_is_refused_as_a_switch_off_not_an_experiment(
        tmp_path, monkeypatch, value):
    """
    بمئة بالمئة لا يتلقى أي Lead متابعة، والمجموعة المعالَجة تصير
    فارغة - فلا Recovery Rate يُطرح منه شيء (§9.2). الشكل نفسه يكتب
    «أطفئ النظام» و«شغّل تجربة»، ولا يملك الكود ما يميّز. نفس منطق
    `send_window` حين يتساوى حدّاها.
    """
    message = config_error(tmp_path, monkeypatch, {"holdout": {"percentage": value}})
    assert "holdout.percentage" in message


def test_unknown_key_inside_holdout_halts_boot(tmp_path, monkeypatch):
    """مفتاح مكتوب خطأً يُقرأ اليوم كأنه غير موجود - وهذا فخ صامت."""
    assert "percentag" in config_error(
        tmp_path, monkeypatch, {"holdout": {"percentag": 20}})


# ============================================== 9) لا تغيير في التقارير

def test_funnel_metrics_are_untouched_by_either_column(isolated_leads_file):
    """
    وجود الحقلين لا يعني وجود بيانات. `compute_funnel_metrics` تُرجع
    None لما لا تستطيع قياسه - ولو تحوّلت إلى صفر لصارت تقول «قِسنا
    فوجدنا لا شيء»، وهو عطب F3 الذي أغلقه D-016.
    """
    lead_id = unbooked_lead("619")
    assign_holdout_groups(percentage=50)
    record_booking_request(lead_id, "سارة 07701234567")
    record_attendance(lead_id, ATTENDANCE_ATTENDED)

    metrics = compute_funnel_metrics()

    assert metrics["booked_revenue"] is None
    assert metrics["revenue"] is None
    assert metrics["recovered_completed_bookings"] is None


def test_reports_do_not_read_either_column_yet(isolated_leads_file):
    """
    حارس نصّي على القرار: لا تقرير يقرأ العمودين. أول قارئ لهما تعديل
    مرئي في الـdiff - نفس حارس D-021.
    """
    consumers = ["lead_recovery_report.py", "events_funnel.py", "check_followups.py",
                 "business_logic.py", "outbound.py"]

    for name in consumers:
        source = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert HOLDOUT_COLUMN not in source, f"{name} صار يقرأ عمود الـholdout"
        assert ATTENDANCE_COLUMN not in source, f"{name} صار يقرأ عمود الحضور"


def test_events_funnel_ignores_the_three_new_event_types(isolated_leads_file):
    """
    تقرير القمع مشتقّ من الأحداث (§20)، والأنواع الثلاثة الجديدة لا
    تدخل أي مقام فيه اليوم. §9.3 يشترط الحضور للفوترة، لا للقمع.
    """
    lead_id = unbooked_lead("620")
    now = datetime.now()
    before = events_funnel.funnel_from_events(events.read_all(), now=now)

    assign_holdout_groups(percentage=50)
    record_booking_request(lead_id, "سارة 07701234567")
    record_attendance(lead_id, ATTENDANCE_ATTENDED)

    after = events_funnel.funnel_from_events(events.read_all(), now=now)
    assert after["booked_revenue"] == before["booked_revenue"] is None
    assert after["revenue"] == before["revenue"] is None
