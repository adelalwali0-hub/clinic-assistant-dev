"""
إيقاف الأتمتة لعميلة واحدة - S6 (PRD §18)
==========================================================================
خمس عائلات من التأكيدات:

1) **الإيقاف يخصّ إنساناً لا استفساراً.** عميلة بثلاثة Leads مفتوحة
   تُوقَف مرة واحدة فتخرج الثلاثة من الأهلية، ويخرج معها أي Lead رابع
   تفتحه بعد الإيقاف. هذه هي العائلة التي تُثبت اختيار مكان التخزين.

2) **المحرك يحترمه مطلقاً - على مستويين.** الأهلية لا تُرجع الصف
   الموقوف، و`outbound.send` ترفض الإرسال إليه حتى لو استُدعيت مباشرة.

3) **الردود الحية غير متأثرة.** الموجّه يجيب العميلة الموقوفة.

4) **الأحداث.** AUTOMATION_PAUSED لكل Lead مفتوح، على الانتقال وحده،
   وAUTOMATION_RESUMED عند الرفع.

5) **لا عملية جماعية.** لا مسار في المنظومة يرفع الإيقاف عن أكثر من
   هوية واحدة معيَّنة بمعرّفها.
"""

import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import events
import leads_store
import outbound
import pause_automation
import settings
from channel_interface import IncomingMessage, OutgoingMessage, ReplyDecision
from message_router import MessageRouter
from storage import pause_store, session_store

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CHANNEL = "telegram"
USER = "u_pause_1"


class FakeChannel:
    """قناة تسجّل ما غادر فعلاً، ولا تلمس الشبكة."""

    def __init__(self, channel_name: str = CHANNEL):
        self.channel_name = channel_name
        self.sent: list[OutgoingMessage] = []

    def send_message(self, message: OutgoingMessage) -> bool:
        self.sent.append(message)
        return True

    def start_listening(self, on_message):  # pragma: no cover - لا يُستدعى هنا
        raise NotImplementedError


def _quote(user_id: str = USER, service: str = "حقن البوتوكس", channel: str = CHANNEL) -> str:
    return leads_store.record_price_quote(
        user_id=user_id, service_name=service, channel=channel
    )


def _age_all_leads(hours: float) -> None:
    """
    يُقدّم كل صفوف الملف إلى الوراء بـ`hours` ساعة، فتصير مؤهلة زمنياً.
    الكتابة المباشرة مقصودة: لا مسار إنتاجي يعدّل تاريخ الإنشاء.
    """
    rows = leads_store._read_all_rows()
    stamp = (datetime.now() - timedelta(hours=hours)).strftime(leads_store.TIMESTAMP_FORMAT)
    for row in rows:
        row["التاريخ والوقت"] = stamp
        if row.get("تاريخ آخر متابعة"):
            row["تاريخ آخر متابعة"] = stamp
    with leads_store._locked():
        leads_store._write_all_rows_unlocked(rows)


def _event_types() -> list[str]:
    return [e["event_type"] for e in events.read_all()]


# ------------------------------- 1) الإيقاف يخصّ إنساناً لا استفساراً

def test_pausing_once_skips_all_three_open_leads_of_the_same_customer():
    """
    **جوهر اختيار مكان التخزين.** عميلة سألت عن ثلاث خدمات فصار لها
    ثلاثة Leads مفتوحة (§6: خدمتان = Leadان). قالت «لا تراسلوني» مرة
    واحدة - وهي لم تقلها عن استفسار بعينه بل عن نفسها.

    لو سكن الإيقاف في صف الـLead لَخرج واحد من الثلاثة وبقي اثنان
    يراسلانها. الفشل كان سيبدو ناجحاً في أي اختبار بـLead واحد.
    """
    ids = [
        _quote(service="حقن البوتوكس"),
        _quote(service="تنظيف البشرة"),
        _quote(service="ازالة الشعر بالليزر"),
    ]
    assert len(set(ids)) == 3, "التهيئة نفسها فشلت: لم تُنشأ ثلاثة Leads متمايزة"

    _age_all_leads(settings.SILENCE_WINDOW_HOURS + 1)
    assert len(leads_store.get_leads_eligible_for_first_followup()) == 3

    assert leads_store.pause_automation(user_id=USER, channel=CHANNEL) is True

    assert leads_store.get_leads_eligible_for_first_followup() == []


def test_pause_does_not_touch_a_different_customer():
    """الإيقاف يخصّ هوية واحدة: جارتها في نفس الملف تبقى مؤهلة."""
    _quote(user_id="u_paused")
    _quote(user_id="u_untouched")
    _age_all_leads(settings.SILENCE_WINDOW_HOURS + 1)

    leads_store.pause_automation(user_id="u_paused", channel=CHANNEL)

    eligible = leads_store.get_leads_eligible_for_first_followup()
    assert [row["معرف العميل"] for row in eligible] == ["u_untouched"]


def test_same_user_id_on_another_channel_is_a_different_identity():
    """§6: لا دمج بين القنوات. الإيقاف على قناة لا يعبر إلى أخرى."""
    _quote(user_id=USER, channel="telegram")
    _quote(user_id=USER, channel="instagram")
    _age_all_leads(settings.SILENCE_WINDOW_HOURS + 1)

    leads_store.pause_automation(user_id=USER, channel="telegram")

    eligible = leads_store.get_leads_eligible_for_first_followup()
    assert [row["القناة"] for row in eligible] == ["instagram"]


def test_a_lead_opened_after_the_pause_is_also_skipped():
    """
    الإيقاف يسري على المستقبل بلا وراثة ولا توزيع قيمة: الصف الجديد لا
    يحمل شيئاً، والقراءة على الهوية تشمله بحكم أنه صفّها.
    """
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    _quote(service="تنظيف البشرة")
    _age_all_leads(settings.SILENCE_WINDOW_HOURS + 1)

    assert leads_store.get_leads_eligible_for_first_followup() == []


# ------------------------------- 2) المحرك يحترمه - على مستويين

@pytest.mark.parametrize("stage,eligibility", [
    ("0", "get_leads_eligible_for_first_followup"),
    ("1", "get_leads_eligible_for_second_followup"),
    ("2", "get_leads_to_expire"),
])
def test_every_eligibility_pass_skips_a_paused_lead(stage, eligibility):
    """
    الثلاث جميعاً، وفيها مسار الانتهاء: «منتهي» تعني في §7 أنها صمتت
    خلال متابعتين، وهو ادّعاء كاذب عن عميلة لم تصلها متابعة قط.
    """
    lead_id = _quote()
    if stage != "0":
        leads_store.mark_followup_sent(lead_id=lead_id, new_stage=stage)
    _age_all_leads(max(settings.SILENCE_WINDOW_HOURS, settings.EXPIRE_AFTER_HOURS) + 1)

    assert len(getattr(leads_store, eligibility)()) == 1

    leads_store.pause_automation(user_id=USER, channel=CHANNEL)

    assert getattr(leads_store, eligibility)() == []


def test_the_set_of_eligibility_passes_is_exactly_three():
    """
    حارس ضد الثغرة التي وقعت في S8: قاعدة مبنية ومنسيّة عند الوصل.

    دالة أهلية رابعة تُضاف غداً بلا فلتر الإيقاف ترسل إلى عميلة موقوفة
    بصمت. هذا السطر يجعل إضافتها قراراً مرئياً في الـdiff: من يضيفها
    يقرأ هذا الاختبار قبل أن يُخضِرّه.
    """
    passes = {
        name for name in dir(leads_store)
        if name.startswith("get_leads_eligible_") or name == "get_leads_to_expire"
    }
    assert passes == {
        "get_leads_eligible_for_first_followup",
        "get_leads_eligible_for_second_followup",
        "get_leads_to_expire",
    }


def test_outbound_send_refuses_a_paused_identity_even_when_called_directly():
    """
    المستوى الثاني: مسار آلي غد يتجاوز دوال الأهلية ويستدعي `send`
    مباشرة. الحارس عند المصبّ يمنعه - ولا حدث ولا رسالة.
    """
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    channel = FakeChannel()

    sent = outbound.send(
        channel,
        OutgoingMessage(user_id=USER, text="متابعة", variant_id="followup_1.v1"),
        lead_id="ld_x",
    )

    assert sent is False
    assert channel.sent == []
    assert events.FOLLOWUP_SENT not in _event_types()
    assert events.RESPONSE_SENT not in _event_types()


def test_send_followups_sends_nothing_for_a_paused_customer():
    """المحرك الحقيقي، لا محاكاته: صفر رسالة تغادر."""
    _quote()
    _age_all_leads(settings.SILENCE_WINDOW_HOURS + 1)
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)

    channel = FakeChannel()
    for row in leads_store.get_leads_eligible_for_first_followup():
        outbound.send(
            channel,
            OutgoingMessage(user_id=row["معرف العميل"], text="x", variant_id="followup_1.v1"),
            lead_id=row["lead_id"],
        )

    assert channel.sent == []


# ------------------------------- 3) الردود الحية غير متأثرة

def test_live_reply_still_reaches_a_paused_customer():
    """
    الإيقاف يمنع ما نبدؤه، لا جوابها. من كتبت إلينا تنتظر رداً، ومن
    طلبت التوقف عن المتابعات لم تطلب أن نتجاهلها حين تسأل.
    """
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    channel = FakeChannel()
    router = MessageRouter(
        channel=channel,
        handler=lambda m: ReplyDecision(
            text="أهلاً", variant_id="services_list.v1", lead_id=None, rule_decision="other"
        ),
    )

    router._on_message(
        IncomingMessage(
            channel=CHANNEL, user_id=USER, text="شكد سعر البوتوكس؟",
            timestamp=datetime.now(), message_id="m1",
        )
    )

    assert len(channel.sent) == 1
    assert events.RESPONSE_SENT in _event_types()


def test_is_reply_is_the_only_exemption():
    """
    الافتراضي يحرس. مسار جديد ينسى الراية يُمنع، لا يمرّ - نفس اتجاه
    الخطأ الذي اختاره S8.
    """
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    channel = FakeChannel()
    message = OutgoingMessage(user_id=USER, text="x", variant_id="services_list.v1")

    assert outbound.send(channel, message) is False
    assert outbound.send(channel, message, is_reply=True) is True


# ------------------------------- 4) الأحداث

def test_pause_emits_one_event_per_open_lead():
    """
    حدث لكل Lead مفتوح: بقية أحداث النظام معلّقة على lead_id، وحدثٌ
    واحد يحمل معرّفاً من ثلاثة يجعل الاثنين الآخرين غير قابلين لإعادة
    البناء من السجل وحده (§6: كل تقرير يُشتق من events.jsonl).
    """
    _quote(service="حقن البوتوكس")
    _quote(service="تنظيف البشرة")

    leads_store.pause_automation(user_id=USER, channel=CHANNEL)

    paused = [e for e in events.read_all() if e["event_type"] == events.AUTOMATION_PAUSED]
    assert len(paused) == 2
    assert {e["lead_id"] for e in paused} == {
        row["lead_id"] for row in leads_store._read_all_rows()
    }
    for event in paused:
        assert event["payload"]["user_id"] == USER
        assert event["payload"]["leads_affected"] == 2
        assert event["payload"]["source"] == pause_store.SOURCE_OPERATOR
        assert event["channel"] == CHANNEL


def test_pause_with_no_open_lead_still_records_the_fact():
    """
    لا Lead تُعلَّق عليه الواقعة - وهي وقعت. حدث واحد بـlead_id فارغ،
    نفس سابقة AMBIGUITY_ASKED.
    """
    leads_store.pause_automation(user_id="u_no_leads", channel=CHANNEL)

    paused = [e for e in events.read_all() if e["event_type"] == events.AUTOMATION_PAUSED]
    assert len(paused) == 1
    assert paused[0]["lead_id"] == ""
    assert paused[0]["payload"]["leads_affected"] == 0


def test_pausing_twice_records_one_pause():
    """الحدث على الانتقال وحده: طلب ثانٍ لا يضيف إيقافاً لم يقع."""
    _quote()
    assert leads_store.pause_automation(user_id=USER, channel=CHANNEL) is True
    assert leads_store.pause_automation(user_id=USER, channel=CHANNEL) is False

    assert _event_types().count(events.AUTOMATION_PAUSED) == 1


def test_resume_emits_its_own_event_and_restores_eligibility():
    """
    AUTOMATION_RESUMED اسم مضاف إلى §6 بقرار موثّق: بلاه يقول السجل إن
    عميلة أوقِفت ولا يقول إنها استُؤنفت، فيعرض كل تقرير مشتق منه
    إيقافاً ما زال قائماً بينما الحقيقة غيره.
    """
    _quote()
    _age_all_leads(settings.SILENCE_WINDOW_HOURS + 1)
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    assert leads_store.get_leads_eligible_for_first_followup() == []

    assert leads_store.resume_automation(user_id=USER, channel=CHANNEL) is True

    assert len(leads_store.get_leads_eligible_for_first_followup()) == 1
    assert _event_types().count(events.AUTOMATION_RESUMED) == 1


def test_resuming_what_is_not_paused_changes_nothing():
    _quote()
    assert leads_store.resume_automation(user_id=USER, channel=CHANNEL) is False
    assert events.AUTOMATION_RESUMED not in _event_types()


def test_automation_is_never_resumed_by_time_passing():
    """S6 حرفياً: «لا يُستأنف الموقوف تلقائياً أبداً»."""
    _quote()
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)

    _age_all_leads(settings.SESSION_TTL_HOURS * 10)

    assert pause_store.is_paused(CHANNEL, USER) is True
    assert leads_store.get_leads_eligible_for_first_followup() == []


def test_session_pruning_does_not_erase_the_pause():
    """
    **الاختبار الذي يبرّر فصل الملفين، بتشغيل الكانِس فعلاً لا بوصفه.**

    `_prune_expired` تُسقط كل جلسة تجاوزت مهلتها عند أول كتابة لاحقة.
    لو سكن الإيقاف في sessions.json لمحاه هذا الكانِس بعد يوم، فتعود
    المتابعات إلى من طلبت التوقف بلا سطر سجل ولا استثناء.

    هنا: جلسة العميلة الموقوفة أقدم من المهلة، ثم تكتب عميلة أخرى
    فيدور الكانِس فعلاً. الجلسة تسقط - وهذا صحيح - والإيقاف يبقى.
    """
    _quote()
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)

    stale = (datetime.now() - timedelta(hours=settings.SESSION_TTL_HOURS * 2)).isoformat(
        timespec="microseconds"
    )
    session_store.update_session(USER, state=session_store.STATE_AWAITING_BOOKING_REPLY)
    sessions = json.loads(Path(session_store.SESSIONS_FILE).read_text(encoding="utf-8"))
    sessions[USER]["updated_at"] = stale
    Path(session_store.SESSIONS_FILE).write_text(
        json.dumps(sessions, ensure_ascii=False), encoding="utf-8"
    )

    # كتابة عميلة أخرى: هنا يدور الكانِس على الجلسات كلها.
    session_store.update_session("u_someone_else", state=session_store.STATE_AWAITING_BOOKING_REPLY)

    survivors = json.loads(Path(session_store.SESSIONS_FILE).read_text(encoding="utf-8"))
    assert USER not in survivors, "التهيئة فشلت: الكانِس لم يدر، فالاختبار لا يثبت شيئاً"

    assert pause_store.is_paused(CHANNEL, USER) is True
    assert leads_store.get_leads_eligible_for_first_followup() == []


# ------------------------------- 5) لا عملية جماعية

def test_no_bulk_resume_exists_anywhere():
    """
    رفع الإيقاف يستأنف مراسلة إنسانة طلبت التوقف. لا شكل جماعي له - ولا
    حتى للاختبار. هذا الاختبار يمنع إضافة واحد بحسن نيّة.
    """
    for module in (pause_store, leads_store):
        for name in dir(module):
            lowered = name.lower()
            if "resume" in lowered or "unpause" in lowered:
                assert not any(word in lowered for word in ("all", "bulk", "every", "clear")), (
                    f"{module.__name__}.{name} يبدو عملية رفع جماعية - انظر ترويسة pause_store"
                )


@pytest.mark.parametrize("channel,user_id", [("", USER), (CHANNEL, ""), ("", "")])
def test_incomplete_identity_never_pauses_or_resumes(channel, user_id):
    """هوية ناقصة ليست «كل الهويات» بل خطأ - ولا تُنفَّذ بأي اتجاه."""
    assert pause_store.pause(channel=channel, user_id=user_id) is False
    assert pause_store.resume(channel=channel, user_id=user_id) is False


def test_resume_requires_an_identifier_on_the_command_line():
    """الأداة ترفض فعلاً بلا معرّف صريح بدل أن تفترض «الجميع»."""
    with pytest.raises(SystemExit) as excinfo:
        pause_automation.main(["--resume"])
    assert excinfo.value.code != 0


def test_resume_command_aborts_without_the_typed_confirmation(monkeypatch, capsys):
    """التأكيد المكتوب ليس تزييناً: بدونه لا يُرفع الإيقاف."""
    _quote()
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    monkeypatch.setattr(settings, "CHANNEL_NAME", CHANNEL)
    monkeypatch.setattr("builtins.input", lambda _: "نعم")

    assert pause_automation.main(["--resume", "--user", USER]) == 1
    assert pause_store.is_paused(CHANNEL, USER) is True


def test_resume_command_lifts_the_pause_with_the_confirmation(monkeypatch):
    _quote()
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    monkeypatch.setattr(settings, "CHANNEL_NAME", CHANNEL)
    monkeypatch.setattr("builtins.input", lambda _: pause_automation.RESUME_CONFIRMATION)

    assert pause_automation.main(["--resume", "--user", USER]) == 0
    assert pause_store.is_paused(CHANNEL, USER) is False


def test_pause_command_accepts_a_lead_id_and_pauses_the_whole_identity(monkeypatch, capsys):
    """
    المشغّل يقرأ leads.csv فيرى lead_id. يُقبل منه، ويُترجَم إلى هوية
    صاحبته - ويُطبع من هي قبل الفعل، فلا يظن أنه أوقف استفساراً واحداً.
    """
    lead_id = _quote(service="حقن البوتوكس")
    _quote(service="تنظيف البشرة")
    monkeypatch.setattr(settings, "CHANNEL_NAME", CHANNEL)

    assert pause_automation.main(["--pause", "--lead", lead_id]) == 0

    assert USER in capsys.readouterr().out
    assert pause_store.is_paused(CHANNEL, USER) is True


def test_operator_tool_prints_no_customer_text(monkeypatch, capsys):
    """S12: مخرَج الأداة معرّفات وحالات - لا نص عميلة ولا بيانات تواصل."""
    lead_id = _quote()
    leads_store.record_booking_request(lead_id=lead_id, contact_info="زينب 07701234567")
    monkeypatch.setattr(settings, "CHANNEL_NAME", CHANNEL)

    pause_automation.main(["--pause", "--user", USER])
    pause_automation.main(["--status", "--user", USER])
    pause_automation.main(["--list"])

    out = capsys.readouterr().out
    assert "07701234567" not in out
    assert "زينب" not in out


# ------------------------------- عزل الملف نفسه

def test_pauses_live_in_their_own_file_not_in_sessions():
    """
    ملفان لأن لهما سياستَي احتفاظ متناقضتين. دمجهما يجعل كانِس الجلسات
    يمحو إيقافاً بعد يوم، فتعود المتابعات لمن طلبت التوقف بلا أثر.
    """
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)

    assert Path(pause_store.PAUSES_FILE).is_file()
    assert Path(pause_store.PAUSES_FILE).name == "pauses.json"

    assert Path(session_store.SESSIONS_FILE) != Path(pause_store.PAUSES_FILE)


def test_missing_pause_file_reads_as_no_pauses():
    """غياب الملف حالة صحيحة - لا إيقافات، بلا صراخ ولا استثناء."""
    assert not Path(pause_store.PAUSES_FILE).exists()
    assert pause_store.is_paused(CHANNEL, USER) is False
    assert pause_store.get_pause(CHANNEL, USER) is None
    assert pause_store.paused_identity_set() == set()
    assert pause_store.paused_identities() == []


def test_resume_keeps_the_record_and_stamps_resumed_at():
    """
    الصف لا يُحذف عند الاستئناف. الحذف كان سيجعل «لم تطلب التوقف قط»
    و«طلبت ثم استُؤنفت» متطابقتين أمام المشغّل، وهما مختلفتان تماماً.
    """
    assert pause_store.get_pause(CHANNEL, USER) is None, "قبل أي طلب: لا صف إطلاقاً"

    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    paused_record = pause_store.get_pause(CHANNEL, USER)
    assert paused_record["paused"] is True
    assert paused_record["paused_at"]
    assert paused_record["resumed_at"] is None

    leads_store.resume_automation(user_id=USER, channel=CHANNEL)

    record = pause_store.get_pause(CHANNEL, USER)
    assert record is not None, "الصف حُذف عند الاستئناف - وهو ما تمنعه الترويسة صراحةً"
    assert record["paused"] is False
    assert record["resumed_at"], "resumed_at لم يُختم"
    assert record["paused_at"] == paused_record["paused_at"], "طابع الإيقاف الأصلي ضاع"


def test_resumed_identity_leaves_the_paused_views():
    """
    الصف يبقى مخزَّناً، ولا يظهر في أي عرض لـ«الموقوفات الآن»: البقاء
    للسجل لا للأثر.
    """
    leads_store.pause_automation(user_id=USER, channel=CHANNEL)
    assert pause_store.paused_identity_set() == {(CHANNEL, USER)}
    assert [row[1] for row in pause_store.paused_identities()] == [USER]

    leads_store.resume_automation(user_id=USER, channel=CHANNEL)

    assert pause_store.paused_identity_set() == set()
    assert pause_store.paused_identities() == []
    assert pause_store.get_pause(CHANNEL, USER) is not None


def test_paused_identity_set_spans_channels_and_users():
    """
    المجموعة هي ما تقرأه دوال الأهلية مرة واحدة لكل مرور. تحمل الهوية
    كاملة `(channel, user_id)` - لا user_id وحده، وإلا لعبر الإيقاف
    بين القنوات وهو ما يمنعه §6.
    """
    pause_store.pause("telegram", "u1")
    pause_store.pause("instagram", "u1")
    pause_store.pause("telegram", "u2")
    pause_store.resume("telegram", "u2")

    assert pause_store.paused_identity_set() == {("telegram", "u1"), ("instagram", "u1")}


def test_pause_survives_a_fresh_process():
    """
    الإيقاف على القرص لا في ذاكرة عملية. عملية مستقلة تقرأ نفس الملف
    وتراه موقوفاً - وهي الحالة الحقيقية: `pause_automation.py` عملية
    و`send_followups.py` عملية أخرى.
    """
    pause_store.pause(CHANNEL, USER)

    probe = (
        "import json,sys;"
        "sys.path.insert(0, r'%s');"
        "from storage import pause_store;"
        "pause_store.PAUSES_FILE = r'%s';"
        "print(json.dumps(pause_store.is_paused(%r, %r)))"
        % (str(PROJECT_ROOT), pause_store.PAUSES_FILE, CHANNEL, USER)
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) is True


def test_write_leaves_no_temp_file_behind():
    """
    الكتابة ذرّية: ملف مؤقت ثم استبدال. بقاء `.tmp` يعني أن الاستبدال
    لم يقع، وأن قارئاً قد يرى ملفاً نصف مكتوب.
    """
    pause_store.pause(CHANNEL, USER)
    pause_store.resume(CHANNEL, USER)

    assert not Path(pause_store.PAUSES_FILE + ".tmp").exists()
    json.loads(Path(pause_store.PAUSES_FILE).read_text(encoding="utf-8"))


def test_concurrent_pauses_do_not_lose_any_identity():
    """
    القفل يحمي دورة (قراءة، تعديل، كتابة). بلاه تقرأ خيوط متزامنة نفس
    القاموس فتكتب كلٌّ منها فوق الأخرى، ويضيع إيقاف طُلب فعلاً - وهو
    أخطر ما يمكن أن يضيع في هذا الملف.
    """
    identities = [f"u_concurrent_{i}" for i in range(24)]
    errors: list[Exception] = []

    def worker(user_id: str) -> None:
        try:
            pause_store.pause(CHANNEL, user_id)
        except Exception as e:  # pragma: no cover - يُبلَّغ عنه أدناه
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(user_id,)) for user_id in identities]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert pause_store.paused_identity_set() == {(CHANNEL, user_id) for user_id in identities}


def test_corrupt_pause_file_is_loud_not_silent(capsys):
    """
    ملف تالف يُقرأ «لا إيقافات» - وهو الاتجاه الخطر الوحيد في هذا
    الملف، ولا مفرّ منه. فيُصرَخ به: التلف هنا حادثة تستدعي إنساناً.
    """
    Path(pause_store.PAUSES_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(pause_store.PAUSES_FILE).write_text("{ليس JSON", encoding="utf-8")

    assert pause_store.is_paused(CHANNEL, USER) is False
    assert "!!" in capsys.readouterr().out
