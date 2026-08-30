"""
اختبارات سجل الأحداث بالإلحاق فقط - events.jsonl (PRD F4/D3، §6).

تغطي خمس طبقات:
  1) بنية السطر والملف: الحقول السبعة، JSON صالح لكل سطر، عربية غير
     مهروبة، الإلحاق لا الاستبدال.
  2) مواضع الإصدار: كل انتقال قائم اليوم يُنتج حدثه، والانتقال الذي
     لم يقع لا يُنتج شيئاً.
  3) الصمت المقصود: التردد بلا حدث، ومعرّف مجهول بلا حدث.
  4) الخصوصية: بيانات التواصل لا تظهر في الملف إطلاقاً.
  5) عزل الفشل: فشل كتابة الحدث لا يُسقط الفعل التجاري، وفشل كتابة
     الصف لا يُنتج حدثاً.

وفوقها الاختبار الحاسم (الطبقة 6): أرقام القمع المشتقة من
events.jsonl **وحده** تطابق compute_funnel_metrics() من leads.csv
لنفس السيناريو. هذه هي أدلة Gate A نفسها، اختباراً لا ادّعاءً.

الاشتقاق نفسه يعيش في events_funnel.py - وحدة شحن لا دالة اختبار.
كان هنا سابقاً، فكان الدليل يثبت تكافؤ نسخة لا يشغّلها أحد.
"""

import json
from datetime import datetime, timedelta

import pytest

import events
import events_funnel
import lead_recovery_report
import leads_store
from business_logic import handle_message
from channel_interface import IncomingMessage
from events_funnel import funnel_from_events
from leads_store import (
    OUTCOME_EXPIRED,
    OUTCOME_RECOVERED,
    SILENCE_WINDOW_HOURS,
    STATE_BOOKING_REQUESTED,
    STATE_DECLINED,
    compute_funnel_metrics,
    mark_expired,
    mark_followup_sent,
    record_booking_request,
    record_decline,
    record_hesitation,
    record_price_quote,
    save_lead,
)

SERVICE_BOTOX = "حقن البوتوكس"
SERVICE_BOTOX_PRICE = "120,000 دينار"
SERVICE_LASER = "إزالة الشعر بالليزر (جلسة واحدة)"


# ----------------------------------------------------------------- أدوات

def read_events():
    return events.read_all()


def types_of(evts=None):
    return [e["event_type"] for e in (evts if evts is not None else read_events())]


def only(event_type, evts=None):
    return [e for e in (evts if evts is not None else read_events())
            if e["event_type"] == event_type]


class FrozenDatetime(datetime):
    """
    يجمّد datetime.now() على لحظة واحدة قابلة للتقديم - نفس أداة
    test_price_quote_lead، مطبَّقة هنا على leads_store وevents معاً
    حتى لا ينحرف زمن الصف عن زمن حدثه.
    """
    frozen_at = datetime(2026, 8, 28, 9, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.frozen_at


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(leads_store, "datetime", FrozenDatetime)
    monkeypatch.setattr(events, "datetime", FrozenDatetime)
    FrozenDatetime.frozen_at = datetime(2026, 8, 28, 9, 0, 0)
    return FrozenDatetime


def advance(clock, **kwargs):
    clock.frozen_at = clock.frozen_at + timedelta(**kwargs)


def make_message(user_id: str, text: str, channel: str = "telegram") -> IncomingMessage:
    return IncomingMessage(channel=channel, user_id=user_id, text=text, timestamp=datetime.now())


# ------------------------------------------------ 1) بنية السطر والملف

def test_event_carries_the_seven_required_fields():
    lead_id = record_price_quote(user_id="900", service_name=SERVICE_BOTOX, channel="telegram")

    event = only(events.LEAD_CREATED)[0]
    assert list(event.keys()) == list(events.EVENT_KEYS)
    assert event["event_id"].startswith(events.EVENT_ID_PREFIX)
    assert event["lead_id"] == lead_id
    assert event["channel"] == "telegram"
    assert isinstance(event["payload"], dict)
    # الحقل موجود دائماً وقيمته None في هذا التغيير - التغيير #5 يملؤه.
    assert "variant_id" in event and event["variant_id"] is None
    datetime.fromisoformat(event["timestamp"])  # طابع زمني صالح


def test_event_ids_are_unique_per_event():
    record_price_quote(user_id="901", service_name=SERVICE_BOTOX, channel="telegram")
    record_price_quote(user_id="902", service_name=SERVICE_LASER, channel="telegram")

    ids = [e["event_id"] for e in read_events()]
    assert len(ids) == len(set(ids))


def test_file_is_one_valid_json_object_per_line(isolated_events_file):
    record_price_quote(user_id="903", service_name=SERVICE_BOTOX, channel="telegram")
    record_price_quote(user_id="904", service_name=SERVICE_LASER, channel="whatsapp")

    lines = isolated_events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    for line in lines:
        assert json.loads(line)["event_id"]


def test_arabic_is_written_unescaped(isolated_events_file):
    record_price_quote(user_id="905", service_name=SERVICE_BOTOX, channel="telegram")

    raw = isolated_events_file.read_text(encoding="utf-8")
    assert SERVICE_BOTOX in raw
    assert "\\u" not in raw


def test_writes_append_and_never_truncate(isolated_events_file):
    record_price_quote(user_id="906", service_name=SERVICE_BOTOX, channel="telegram")
    first_batch = isolated_events_file.read_text(encoding="utf-8")

    record_price_quote(user_id="907", service_name=SERVICE_LASER, channel="telegram")
    second_batch = isolated_events_file.read_text(encoding="utf-8")

    assert second_batch.startswith(first_batch)
    assert len(second_batch) > len(first_batch)


# ------------------------------------------------------ 2) مواضع الإصدار

def test_price_quote_emits_lead_created_then_price_quoted():
    lead_id = record_price_quote(user_id="910", service_name=SERVICE_BOTOX, channel="telegram")

    evts = read_events()
    assert types_of(evts) == [events.LEAD_CREATED, events.PRICE_QUOTED]
    assert {e["lead_id"] for e in evts} == {lead_id}
    assert only(events.LEAD_CREATED, evts)[0]["payload"]["price"] == SERVICE_BOTOX_PRICE
    assert only(events.PRICE_QUOTED, evts)[0]["payload"]["lead_created"] is True


def test_requote_emits_price_quoted_without_a_second_lead_created():
    first = record_price_quote(user_id="911", service_name=SERVICE_BOTOX, channel="telegram")
    second = record_price_quote(user_id="911", service_name=SERVICE_BOTOX, channel="telegram")

    assert first == second
    assert types_of() == [events.LEAD_CREATED, events.PRICE_QUOTED, events.PRICE_QUOTED]
    assert only(events.PRICE_QUOTED)[1]["payload"]["lead_created"] is False


def test_booking_request_emits_booking_requested():
    lead_id = record_price_quote(user_id="912", service_name=SERVICE_BOTOX, channel="telegram")
    record_booking_request(lead_id=lead_id, contact_info="سارة 07701234567")

    event = only(events.BOOKING_REQUESTED)[0]
    assert event["lead_id"] == lead_id
    assert event["channel"] == "telegram"
    assert event["payload"]["contact_info_present"] is True
    assert event["payload"]["service_name"] == SERVICE_BOTOX


def test_decline_emits_declined():
    lead_id = record_price_quote(user_id="913", service_name=SERVICE_BOTOX, channel="telegram")
    record_decline(lead_id)

    assert types_of() == [events.LEAD_CREATED, events.PRICE_QUOTED, events.DECLINED]
    assert only(events.DECLINED)[0]["lead_id"] == lead_id


def test_followup_sent_emits_with_its_stage():
    lead_id = record_price_quote(user_id="914", service_name=SERVICE_BOTOX, channel="telegram")
    mark_followup_sent(lead_id=lead_id, new_stage="1")
    mark_followup_sent(lead_id=lead_id, new_stage="2")

    stages = [e["payload"]["stage"] for e in only(events.FOLLOWUP_SENT)]
    assert stages == ["1", "2"]


def test_expiry_emits_lead_expired_once():
    lead_id = record_price_quote(user_id="915", service_name=SERVICE_BOTOX, channel="telegram")
    mark_expired(lead_id)
    mark_expired(lead_id)  # استدعاء ثانٍ: نفس الكتابة، بلا انتهاء ثانٍ

    assert len(only(events.LEAD_EXPIRED)) == 1
    assert only(events.LEAD_EXPIRED)[0]["lead_id"] == lead_id


def test_save_lead_fallback_emits_creation_and_its_state():
    lead_id = save_lead(user_id="916", service_name=SERVICE_BOTOX, channel="telegram",
                        status=STATE_BOOKING_REQUESTED, contact_info="ليلى 07709998888")

    assert types_of() == [events.LEAD_CREATED, events.BOOKING_REQUESTED]
    assert {e["lead_id"] for e in read_events()} == {lead_id}
    assert only(events.LEAD_CREATED)[0]["payload"]["source"] == "save_lead"


def test_save_lead_fallback_decline_emits_declined():
    save_lead(user_id="917", service_name=SERVICE_BOTOX, channel="telegram",
              status=STATE_DECLINED)

    assert types_of() == [events.LEAD_CREATED, events.DECLINED]


# ------------------------------ 2ب) المسار الحقيقي عبر business_logic

def test_full_conversation_through_handle_message_emits_the_funnel():
    """
    المسار الذي تسلكه حركة المرور فعلاً - لا استدعاء مباشر لـleads_store.
    business_logic لم يتغيّر بحرف واحد، ومع ذلك صار كل انتقال مرئياً.
    """
    handle_message(make_message("950", "كم سعر البوتوكس؟"))
    handle_message(make_message("950", "نعم"))
    handle_message(make_message("950", "سارة 0770 000 000"))

    assert types_of() == [events.LEAD_CREATED, events.PRICE_QUOTED, events.BOOKING_REQUESTED]
    # معرّف واحد يربط المحادثة كلها من السعر حتى الطلب
    assert len({e["lead_id"] for e in read_events()}) == 1


def test_decline_conversation_through_handle_message_emits_declined():
    handle_message(make_message("951", "بوتوكس"))
    handle_message(make_message("951", "لا"))

    assert types_of() == [events.LEAD_CREATED, events.PRICE_QUOTED, events.DECLINED]


def test_silent_lead_through_handle_message_leaves_a_trace():
    """
    الفجوة F1 مقلوبةً: العميلة التي سألت عن السعر ثم صمتت لم تكن
    تترك أي أثر. الآن تترك حدثين، وهي الأغلبية الساحقة.
    """
    handle_message(make_message("952", "بوتوكس"))

    assert types_of() == [events.LEAD_CREATED, events.PRICE_QUOTED]


def test_unknown_service_message_emits_nothing():
    """رسالة لا تطابق أي خدمة: لا Lead، فلا حدث. لا ضجيج في السجل."""
    handle_message(make_message("953", "شلونكم؟"))

    assert read_events() == []


# ------------------------------------------------------ 3) الصمت المقصود

def test_hesitation_emits_nothing():
    lead_id = record_price_quote(user_id="920", service_name=SERVICE_BOTOX, channel="telegram")
    before = len(read_events())

    record_hesitation(lead_id)

    # `hesitant` إشارة لا حالة: لا مقابل لها في §7 ولا اسم في §6.
    assert len(read_events()) == before


def test_decline_on_a_booking_requested_row_emits_nothing():
    lead_id = record_price_quote(user_id="921", service_name=SERVICE_BOTOX, channel="telegram")
    record_booking_request(lead_id=lead_id, contact_info="نور 07705554444")
    before = len(read_events())

    assert record_decline(lead_id) is True
    # الحارس يمنع فقد الحالة، فلم يقع انتقال - تغيّر status_reason وحده.
    assert len(read_events()) == before


def test_unknown_lead_id_emits_nothing():
    assert record_booking_request(lead_id="ld_ghost", contact_info="x") is False
    assert record_decline("ld_ghost") is False
    assert mark_followup_sent(lead_id="ld_ghost", new_stage="1") is False
    assert mark_expired("ld_ghost") is False

    assert read_events() == []


# --------------------------------------------------------- 4) الخصوصية

def test_contact_info_never_reaches_the_event_log(isolated_events_file):
    phone = "07701234567"
    lead_id = record_price_quote(user_id="930", service_name=SERVICE_BOTOX, channel="telegram")
    record_booking_request(lead_id=lead_id, contact_info=f"سارة {phone}")
    save_lead(user_id="931", service_name=SERVICE_BOTOX, channel="telegram",
              status=STATE_BOOKING_REQUESTED, contact_info=f"هدى {phone}")

    raw = isolated_events_file.read_text(encoding="utf-8")
    assert phone not in raw
    assert "سارة" not in raw and "هدى" not in raw


# ------------------------------------------------------- 5) عزل الفشل

def test_event_failure_does_not_break_the_business_action(tmp_path, monkeypatch, capsys):
    # مسار غير قابل للفتح للكتابة (مجلد) - يحاكي قرصاً ممتلئاً أو
    # صلاحية مرفوضة بلا ترقيع للدوال الداخلية.
    broken = tmp_path / "events_dir"
    broken.mkdir()
    monkeypatch.setattr(events, "EVENTS_FILE", str(broken))

    lead_id = record_price_quote(user_id="940", service_name=SERVICE_BOTOX, channel="telegram")

    # الفعل التجاري اكتمل رغم فشل السجل
    assert lead_id
    rows = leads_store._read_all_rows()
    assert len(rows) == 1 and rows[0][leads_store.LEAD_ID_COLUMN] == lead_id
    # والفشل مرئي على stderr، لا صامت
    assert "[EVENT-FAIL]" in capsys.readouterr().err


def test_emit_returns_false_instead_of_raising(tmp_path, monkeypatch):
    broken = tmp_path / "events_dir"
    broken.mkdir()
    monkeypatch.setattr(events, "EVENTS_FILE", str(broken))

    assert events.emit(events.LEAD_CREATED, lead_id="ld_x", channel="telegram") is False


def test_failed_row_write_emits_no_event(monkeypatch):
    def boom(rows):
        raise OSError("القرص ممتلئ")

    monkeypatch.setattr(leads_store, "_write_all_rows_unlocked", boom)

    with pytest.raises(OSError):
        record_price_quote(user_id="941", service_name=SERVICE_BOTOX, channel="telegram")

    # السجل لا يسبق leads.csv أبداً
    assert read_events() == []


def test_corrupt_line_does_not_sink_the_whole_log(isolated_events_file):
    record_price_quote(user_id="942", service_name=SERVICE_BOTOX, channel="telegram")
    with open(isolated_events_file, "a", encoding="utf-8") as f:
        f.write('{"event_id": "ev_truncated"\n')
    record_price_quote(user_id="943", service_name=SERVICE_LASER, channel="telegram")

    assert types_of() == [events.LEAD_CREATED, events.PRICE_QUOTED,
                          events.LEAD_CREATED, events.PRICE_QUOTED]


# ---------------------------------- 6) القمع من الأحداث وحدها == من leads.csv

def test_funnel_derived_from_events_alone_matches_leads_csv(frozen_clock):
    """
    دليل Gate A: "تقرير قمع مشتق من events.jsonl وحده".

    سبعة Leads تغطي كل فرع حي في دورة الحياة اليوم: حجز عضوي، حجز
    مسترجَع بعد متابعة، صامتة، رافضة، منتهية بعد متابعتين، مُسعَّرة
    مرتين، ومنتهية ثم حجزت. ثم تُقارَن كل قيمة في القمع، مشتقةً من
    مخزنين مستقلين.

    الفرع الأخير هو الذي يجعل LEAD_EXPIRED ضرورياً لا تجميلياً:
    record_booking_request لا يدهس نتيجة محسومة، فالمنتهية التي حجزت
    **ليست** مسترجَعة في leads.csv. بلا حدث الانتهاء لا يملك السجل
    ما يميّزها عن مسترجَعة حقيقية، ويُضخَّم رقم الاسترجاع.

    خارج نطاق التكافؤ عمداً: مسار save_lead للحجز يغلق **صفاً آخر**
    (يكتب "نتيجة المتابعة" لصف Lead مختلف) وهو كتابة إسناد بلا اسم
    في §6 ولا حدث يمثّلها. السيناريو يستخدم المسار الأساسي - وهو
    مسار حركة المرور الحقيقية منذ التغييرين #1 و#2.
    """
    # A: مُسعَّرة ثم حجزت بلا متابعة -> عضوي
    lead_a = record_price_quote(user_id="A", service_name=SERVICE_BOTOX, channel="telegram")
    # B: مُسعَّرة، ستُتابَع ثم تحجز -> مسترجَع
    lead_b = record_price_quote(user_id="B", service_name=SERVICE_LASER, channel="telegram")
    # C: مُسعَّرة ثم صمتت -> Unbooked
    record_price_quote(user_id="C", service_name=SERVICE_BOTOX, channel="whatsapp")
    # D: مُسعَّرة ثم رفضت صراحةً -> خارج مقام Unbooked
    lead_d = record_price_quote(user_id="D", service_name=SERVICE_LASER, channel="telegram")
    # E: مُسعَّرة، متابعتان ثم انتهت -> Unbooked ومنتهية
    lead_e = record_price_quote(user_id="E", service_name=SERVICE_BOTOX, channel="telegram")
    # F: مُسعَّرة مرتين على نفس النيّة -> Lead واحد لا اثنان
    record_price_quote(user_id="F", service_name=SERVICE_LASER, channel="whatsapp")
    # G: ستنتهي بعد متابعتين ثم تحجز -> حجز، وليس استرجاعاً
    lead_g = record_price_quote(user_id="G", service_name=SERVICE_BOTOX, channel="telegram")

    advance(frozen_clock, hours=2)
    record_price_quote(user_id="F", service_name=SERVICE_LASER, channel="whatsapp")
    record_decline(lead_d)
    record_booking_request(lead_id=lead_a, contact_info="أ 0770")

    advance(frozen_clock, hours=25)
    mark_followup_sent(lead_id=lead_b, new_stage="1")
    mark_followup_sent(lead_id=lead_e, new_stage="1")
    mark_followup_sent(lead_id=lead_g, new_stage="1")

    advance(frozen_clock, hours=1)
    record_booking_request(lead_id=lead_b, contact_info="ب 0771")

    advance(frozen_clock, hours=73)
    mark_followup_sent(lead_id=lead_e, new_stage="2")
    mark_followup_sent(lead_id=lead_g, new_stage="2")
    advance(frozen_clock, hours=73)
    mark_expired(lead_e)
    mark_expired(lead_g)

    advance(frozen_clock, hours=5)
    record_booking_request(lead_id=lead_g, contact_info="ز 0772")

    # H: سُعِّرت الآن وصمتت - داخل نافذة الصمت بعد، فليست Unbooked في
    # أي من المخزنين. بلا صف حديث كهذا يمرّ شرط النافذة الزمنية بلا
    # أن يُختبر: كل الصامتات الأخرى تجاوزت العتبة أصلاً.
    record_price_quote(user_id="H", service_name=SERVICE_LASER, channel="telegram")

    from_csv = compute_funnel_metrics()
    from_events = funnel_from_events(read_events(), now=FrozenDatetime.frozen_at)

    assert from_events == from_csv

    # وأن التكافؤ ليس تطابق أصفار: السيناريو فعلاً ملأ كل خانة.
    assert from_csv["qualified_leads"] == 8
    assert from_csv["booking_requests"] == 3    # A, B, G
    assert from_csv["recovered_leads"] == 1     # B وحدها - G انتهت قبل حجزها
    assert from_csv["unbooked_leads"] == 3      # C, E, F - وH ما زالت داخل النافذة
    assert from_csv["potential_revenue"] > 0
    assert from_csv["recovered_requested_revenue"] < from_csv["requested_revenue"]


def test_events_alone_separate_expired_from_open(frozen_clock):
    """
    السبب المباشر لوجود LEAD_EXPIRED: بدونه الصفّان أدناه يبدوان
    متطابقين تماماً في السجل، وكلاهما price_quoted صامت.
    """
    still_open = record_price_quote(user_id="G", service_name=SERVICE_BOTOX, channel="telegram")
    gone = record_price_quote(user_id="H", service_name=SERVICE_BOTOX, channel="whatsapp")
    mark_expired(gone)

    expired_ids = {e["lead_id"] for e in only(events.LEAD_EXPIRED)}
    assert expired_ids == {gone}
    assert still_open not in expired_ids

    rows = {r[leads_store.LEAD_ID_COLUMN]: r for r in leads_store._read_all_rows()}
    assert rows[gone]["نتيجة المتابعة"] == OUTCOME_EXPIRED
    assert rows[still_open]["نتيجة المتابعة"] == ""


def test_recovered_attribution_is_derivable_from_event_order(frozen_clock):
    """
    مسترجَع مقابل عضوي (§9.1) من ترتيب الأحداث وحده - وهو نفس ما
    يكتبه _outcome_for_stage في العمود.
    """
    organic = record_price_quote(user_id="I", service_name=SERVICE_BOTOX, channel="telegram")
    assisted = record_price_quote(user_id="J", service_name=SERVICE_BOTOX, channel="telegram")

    record_booking_request(lead_id=organic, contact_info="ي 0770")
    advance(frozen_clock, hours=25)
    mark_followup_sent(lead_id=assisted, new_stage="1")
    record_booking_request(lead_id=assisted, contact_info="ك 0771")

    rows = {r[leads_store.LEAD_ID_COLUMN]: r for r in leads_store._read_all_rows()}
    assert rows[assisted]["نتيجة المتابعة"] == OUTCOME_RECOVERED

    derived = funnel_from_events(read_events(), now=FrozenDatetime.frozen_at)
    assert derived["recovered_leads"] == 1
    assert derived["booking_requests"] == 2


# ------------------------------- 7) التقريران المتعايشان (دليل Gate A)

def _two_leads_one_recovered(clock):
    """سيناريو صغير يملأ كل خانة يملكها المخزنان معاً."""
    organic = record_price_quote(user_id="R1", service_name=SERVICE_BOTOX, channel="telegram")
    assisted = record_price_quote(user_id="R2", service_name=SERVICE_LASER, channel="telegram")
    record_booking_request(lead_id=organic, contact_info="ر 0770")
    advance(clock, hours=25)
    mark_followup_sent(lead_id=assisted, new_stage="1")
    record_booking_request(lead_id=assisted, contact_info="ز 0771")


def test_the_events_report_reads_no_leads_csv_at_all(frozen_clock, tmp_path, monkeypatch):
    """
    ادّعاء «من events.jsonl وحده» مُثبَتاً لا موصوفاً: يُسحب leads.csv
    من تحت التقرير كلياً - يُوجَّه المسار إلى ملف غير موجود - ثم
    يُطلب التقرير كاملاً.

    بلا هذا الاختبار كان الاستقلال جملةً في ترويسة الوحدة: استيراد
    واحد كسول من leads_store يكفي لكسره بلا أن يحمرّ شيء، ما دام
    الملف الحقيقي موجوداً دائماً أثناء الاختبارات.
    """
    _two_leads_one_recovered(frozen_clock)
    metrics_before = events_funnel.funnel_from_events(read_events(), now=FrozenDatetime.frozen_at)

    # leads.csv لم يعد موجوداً بأي معنى. المسار يبقى داخل tmp_path -
    # حارس العزل في conftest يفرض ذلك، والاختبار لا يستثني نفسه منه.
    missing = tmp_path / "لا-يوجد" / "leads.csv"
    monkeypatch.setattr(leads_store, "LEADS_FILE", str(missing))
    assert not missing.exists()

    metrics_after = events_funnel.funnel_from_events(read_events(), now=FrozenDatetime.frozen_at)
    rendered = lead_recovery_report.render_report(metrics_after)

    assert metrics_after == metrics_before
    assert metrics_after["qualified_leads"] == 2
    assert metrics_after["recovered_leads"] == 1
    assert metrics_after["potential_revenue"] > 0
    # والتقرير يُرسم كاملاً، لا يسقط عند أول رقم
    assert "Qualified Leads:" in rendered
    assert "Recovered Leads:" in rendered


def test_both_reports_render_identically_from_their_two_stores(frozen_clock):
    """
    التقريران يتعايشان ولا يحلّ أحدهما محل الآخر. تطابقهما هو الدليل؛
    واختلافهما يوماً ما هو الإنذار الوحيد الذي يكشف انحراف السجل عن
    الملف. يُقارَن النص المرسوم لا القاموس وحده: التقريران يمرّان
    بنفس render_report، فأي فرق في المخرجات فرقٌ في الأرقام.
    """
    _two_leads_one_recovered(frozen_clock)

    from_csv = lead_recovery_report.render_report(compute_funnel_metrics())
    from_events = lead_recovery_report.render_report(
        events_funnel.funnel_from_events(read_events(), now=FrozenDatetime.frozen_at)
    )

    assert from_events == from_csv
    assert "لا يُسمّى رقم «إيراداً» إلا عند الحضور" in from_events


def test_the_script_entry_point_reads_the_log_and_needs_no_argument(frozen_clock):
    """
    نقطة دخول السكربت نفسها - لا الدالة الداخلية وحدها. سكربت لا
    يُستدعى في أي اختبار يتعفّن بصمت.
    """
    _two_leads_one_recovered(frozen_clock)

    metrics = events_funnel.compute_funnel_metrics_from_events(now=FrozenDatetime.frozen_at)
    assert metrics == events_funnel.funnel_from_events(
        read_events(), now=FrozenDatetime.frozen_at
    )
    assert metrics["qualified_leads"] == 2


def test_an_empty_log_reports_zeroes_not_a_crash():
    """سجل غير موجود حالة صحيحة: تقرير أصفار، لا استثناء."""
    metrics = events_funnel.compute_funnel_metrics_from_events(now=datetime.now())

    assert metrics["qualified_leads"] == 0
    assert metrics["potential_revenue"] == 0
    # والطبقات التي لا مصدر لها تبقى None لا صفراً - الفرق مقصود (§8)
    assert metrics["revenue"] is None
    assert metrics["booked_revenue"] is None
    assert "غير متاح" in lead_recovery_report.render_report(metrics)
