"""
اختبارات مسار الإرسال الموحّد ومكتبة الصياغات (PRD F5/D3، §16).

شرط خروج Gate A الذي تغطيه هذه الاختبارات حرفياً:
«كل رسالة صادرة عبر مسار واحد تنتج حدثاً بـvariant_id».

ست طبقات:
  1) المسار واحد: لا أحد يستدعي channel.send_message خارج outbound.
  2) الحدث: RESPONSE_SENT بـlead_id حقيقي وvariant_id، وعند النجاح وحده.
  3) المتابعة: FOLLOWUP_SENT وحده يحمل الصياغة - لا حدثان لرسالة واحدة.
  4) المكتبة: كل معرّف مسجَّل، وكل نص مطابق لما كان قبل النقل.
  5) حدود §16: حد الصياغتين لكل نية مفروض بالكود لا بالنية الحسنة.
  6) النسب: «أي صياغة سبقت طلب الحجز» مُجاب عنه من events.jsonl وحده.
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import events
import leads_store
import outbound
import variants
from business_logic import handle_message
from channel_interface import IncomingMessage, OutgoingMessage
from message_router import MessageRouter

# send_followups يرفض الاستيراد بلا توكن (SystemExit عند مستوى الوحدة).
# قيمة وهمية تكفي: القناة نفسها مُستبدَلة بمزيّفة في كل اختبار أدناه،
# ولا اتصال شبكي يقع.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-not-used")
import send_followups  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SERVICE_BOTOX = "حقن البوتوكس"
SERVICE_BOTOX_PRICE = "120,000 دينار"


# ----------------------------------------------------------------- أدوات

def make_message(user_id: str, text: str) -> IncomingMessage:
    return IncomingMessage(
        channel="telegram", user_id=user_id, text=text,
        timestamp=datetime.now(), message_id=None,
    )


class FakeChannel:
    """قناة مزيّفة: تسجّل ما أُرسل، ولا تلمس الشبكة إطلاقاً."""

    channel_name = "telegram"

    def __init__(self, succeed: bool = True, raises: bool = False):
        self.succeed = succeed
        self.raises = raises
        self.sent: list[OutgoingMessage] = []

    def send_message(self, message: OutgoingMessage) -> bool:
        self.sent.append(message)
        if self.raises:
            raise RuntimeError("انقطاع شبكة مُصطنَع")
        return self.succeed

    def start_listening(self, on_message):
        raise NotImplementedError

    def stop_listening(self):
        raise NotImplementedError


def of_type(event_type):
    return [e for e in events.read_all() if e["event_type"] == event_type]


def types_of():
    return [e["event_type"] for e in events.read_all()]


def quote_lead(user_id="u1", text="كم سعر البوتوكس؟"):
    """يمرّ بشجرة القرار فيُنشئ Lead حقيقياً ويعيد قراره."""
    return handle_message(make_message(user_id, text))


# =================================================== 1) المسار واحد

CHANNEL_CALL_ALLOWED = {"channel_interface.py", "telegram_channel.py", "outbound.py"}


def modules_calling_the_channel_directly(root: Path) -> list[str]:
    """
    يمسح شجرة `root` كاملة بحثاً عن استدعاء مباشر لـ`send_message`.

    rglob لا glob: المسح المسطّح على الجذر وحده كان يترك `storage/`
    وأي حزمة فرعية تُضاف لاحقاً بلا فحص - وهي بالضبط الأماكن التي
    يولد فيها مسار إرسال ثانٍ بلا أن يلاحظه أحد.

    الجذر معامل لا ثابت حتى يُمكن إثبات أن الماسح يمسك مخالفة مزروعة
    في مجلد فرعي، بلا كتابة أي شيء داخل المستودع نفسه.
    """
    offenders = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if path.name in CHANNEL_CALL_ALLOWED:
            continue
        # الاختبارات تبني قنوات مزيَّفة وتستدعيها عمداً؛ والمجلدات
        # المخفية وذاكرة بايت المؤقتة ليست مصدراً يُقرأ أصلاً.
        if any(part == "tests" or part == "__pycache__" or part.startswith(".")
               for part in relative.parts):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.send_message\s*\(", line):
                offenders.append(f"{relative.as_posix()}:{i}")
    return offenders


def test_no_module_calls_the_channel_outside_the_unified_path():
    """
    الحارس البنيوي لـF5: مسار إرسال ثانٍ يعود صامتاً ما لم يمنعه شيء.
    `send_message` مسموح تعريفه في العقد وتنفيذه في الكونيكتور، ومسموح
    استدعاؤه في outbound وحده.
    """
    offenders = modules_calling_the_channel_directly(PROJECT_ROOT)
    assert offenders == [], f"استدعاء مباشر للقناة خارج outbound: {offenders}"


def test_the_structural_guard_catches_a_violation_in_a_subdirectory(tmp_path):
    """
    الحارس نفسه مُختبَراً، لا مُفترَضاً.

    الماسح كان يمرّ على جذر المشروع مسطَّحاً، فكان `storage/` خارج
    نطاقه كلياً: مخالفة هناك تمرّ خضراء. الاختبار أعلاه لا يكشف هذا
    أبداً - نجاحه يعني «لا مخالفة» و«لا بحث» على السواء.

    هنا تُزرع مخالفة في مجلد فرعي داخل tmp_path ويُطلب من نفس الماسح
    أن يجدها. لا يُكتب شيء داخل المستودع.
    """
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    nested = tmp_path / "storage" / "deep"
    nested.mkdir(parents=True)
    (nested / "sneaky.py").write_text(
        "def go(channel, msg):\n    return channel.send_message(msg)\n", encoding="utf-8"
    )

    offenders = modules_calling_the_channel_directly(tmp_path)

    assert offenders == ["storage/deep/sneaky.py:2"], offenders


def test_the_structural_guard_still_honours_its_allowlist_and_exclusions(tmp_path):
    """
    والوجه الآخر: ماسح يبلّغ عن كل شيء ليس حارساً بل ضجيج يُسكَت.
    الملفات الثلاثة المسموح لها، وشجرة الاختبارات، تبقى خارج البلاغ
    حتى وهي تحمل الاستدعاء نفسه حرفياً.
    """
    call = "channel.send_message(m)\n"
    (tmp_path / "outbound.py").write_text(call, encoding="utf-8")
    (tmp_path / "telegram_channel.py").write_text(call, encoding="utf-8")
    (tmp_path / "channel_interface.py").write_text(call, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(call, encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text(call, encoding="utf-8")

    assert modules_calling_the_channel_directly(tmp_path) == []


def test_router_sends_through_outbound(monkeypatch):
    calls = []
    monkeypatch.setattr(outbound, "send", lambda *a, **k: calls.append((a, k)) or True)
    channel = FakeChannel()
    router = MessageRouter(channel=channel, handler=handle_message)

    router._on_message(make_message("u_router", "كم سعر البوتوكس؟"))

    assert len(calls) == 1
    assert channel.sent == []  # لم يُستدعَ إلا عبر outbound (وهو مُستبدَل)


def test_followups_send_through_outbound(monkeypatch):
    decision = quote_lead("u_fu_path")
    _age_lead(decision.lead_id, hours=30)

    calls = []
    monkeypatch.setattr(outbound, "send", lambda *a, **k: calls.append((a, k)) or True)
    send_followups.run_first_followups(FakeChannel())

    assert len(calls) == 1


def test_router_requires_a_handler():
    """
    stub_business_logic حُذف: كان المنتج الوحيد لنص صادر بلا صياغة.
    غيابُ افتراضٍ يعني أن نسيان الـhandler خطأ صريح لا صدى صامت.
    """
    with pytest.raises(TypeError):
        MessageRouter(channel=FakeChannel())

    import message_router
    assert not hasattr(message_router, "stub_business_logic")


# =================================================== 2) حدث الرد الحي

def test_response_sent_carries_real_lead_id_and_variant_id():
    channel = FakeChannel()
    router = MessageRouter(channel=channel, handler=handle_message)

    router._on_message(make_message("u_ev", "كم سعر البوتوكس؟"))

    sent = of_type(events.RESPONSE_SENT)
    assert len(sent) == 1
    assert sent[0]["variant_id"] == "price_quote.v1"
    assert sent[0]["lead_id"].startswith("ld_")
    # نفس الـLead الذي كتبه عرض السعر - لا معرّف ثانٍ
    assert sent[0]["lead_id"] == of_type(events.PRICE_QUOTED)[0]["lead_id"]
    assert sent[0]["channel"] == "telegram"


def test_response_sent_for_a_message_with_no_lead_has_empty_lead_id():
    """
    رسالة الترحيب تسبق وجود أي Lead. lead_id فارغ - أصدق من اختلاق
    معرّف - والحدث يبقى محسوباً ضمن الصادر.
    """
    router = MessageRouter(channel=FakeChannel(), handler=handle_message)
    router._on_message(make_message("u_greet", "شلونكم؟"))

    sent = of_type(events.RESPONSE_SENT)
    assert len(sent) == 1
    assert sent[0]["variant_id"] == "services_list.v1"
    assert sent[0]["lead_id"] == ""
    assert of_type(events.LEAD_CREATED) == []


@pytest.mark.parametrize("channel", [FakeChannel(succeed=False), FakeChannel(raises=True)])
def test_failed_send_emits_no_response_sent(channel):
    """حدث يقول «أُرسلت رسالة» بينما لم تُرسل كذبة لا يكشفها شيء لاحقاً."""
    router = MessageRouter(channel=channel, handler=handle_message)
    router._on_message(make_message("u_fail", "كم سعر البوتوكس؟"))

    assert of_type(events.RESPONSE_SENT) == []
    # والانتقال التجاري وقع فعلاً رغم فشل الإرسال - الصف كُتب
    assert of_type(events.PRICE_QUOTED) != []


def test_response_sent_payload_carries_intent_and_hash_but_not_the_text():
    router = MessageRouter(channel=FakeChannel(), handler=handle_message)
    router._on_message(make_message("u_payload", "كم سعر البوتوكس؟"))

    payload = of_type(events.RESPONSE_SENT)[0]["payload"]
    assert payload["intent"] == "price_quote"
    assert payload["variant_hash"] == variants.template_hash("price_quote.v1")
    assert payload["user_id"] == "u_payload"
    # النص لا يُنسَخ إلى سجل بالإلحاق فقط - المعرّف يقود إليه
    assert SERVICE_BOTOX_PRICE not in str(payload)


def test_handler_failure_sends_the_error_variant_and_logs_it():
    def broken_handler(message):
        raise RuntimeError("عطل مُصطنَع في شجرة القرار")

    channel = FakeChannel()
    router = MessageRouter(channel=channel, handler=broken_handler)
    router._on_message(make_message("u_err", "كم سعر البوتوكس؟"))

    assert channel.sent[0].text == variants.render("error_fallback.v1")
    sent = of_type(events.RESPONSE_SENT)
    assert len(sent) == 1
    assert sent[0]["variant_id"] == "error_fallback.v1"
    assert sent[0]["lead_id"] == ""


def test_message_without_a_variant_still_sends_and_warns(capsys):
    """
    إسقاط رسالة عميلة لثغرة محاسبية أسوأ من الثغرة نفسها: تُرسَل،
    لكن لا تمرّ بهدوء.
    """
    channel = FakeChannel()
    assert outbound.send(channel, OutgoingMessage(user_id="u_nv", text="نص بلا صياغة")) is True
    assert len(channel.sent) == 1
    assert "[VARIANT-MISSING]" in capsys.readouterr().err
    assert of_type(events.RESPONSE_SENT)[0]["variant_id"] is None


# =================================================== 3) مسار المتابعة

def _age_lead(lead_id: str, hours: float, followup_column: str = "التاريخ والوقت"):
    """يُقدّم طابع الصف الزمني ليصير مؤهلاً للمتابعة دون انتظار حقيقي."""
    rows = leads_store._read_all_rows_unlocked()
    stamp = datetime.now() - timedelta(hours=hours)
    for row in rows:
        if row[leads_store.LEAD_ID_COLUMN] == lead_id:
            row[followup_column] = stamp.strftime(leads_store.TIMESTAMP_FORMAT)
    leads_store._write_all_rows_unlocked(rows)


def test_followup_emits_followup_sent_alone_with_its_variant():
    """
    رسالة واحدة = حدث واحد. FOLLOWUP_SENT هو حدث الرسالة الصادرة لهذا
    المسار (§5)، ولا يُضاف RESPONSE_SENT فوقه لنفس الفعل.
    """
    decision = quote_lead("u_fu1")
    _age_lead(decision.lead_id, hours=30)

    channel = FakeChannel()
    send_followups.run_first_followups(channel)

    assert len(channel.sent) == 1
    assert channel.sent[0].variant_id == "followup_1.v1"

    sent = of_type(events.FOLLOWUP_SENT)
    assert len(sent) == 1
    assert sent[0]["variant_id"] == "followup_1.v1"
    assert sent[0]["lead_id"] == decision.lead_id
    assert sent[0]["payload"]["stage"] == "1"
    assert sent[0]["payload"]["variant_hash"] == variants.template_hash("followup_1.v1")
    assert of_type(events.RESPONSE_SENT) == []


def test_second_followup_carries_its_own_variant():
    decision = quote_lead("u_fu2")
    _age_lead(decision.lead_id, hours=200)
    send_followups.run_first_followups(FakeChannel())
    _age_lead(decision.lead_id, hours=100, followup_column="تاريخ آخر متابعة")

    channel = FakeChannel()
    send_followups.run_second_followups(channel)

    assert channel.sent[0].variant_id == "followup_2.v1"
    stages = {e["payload"]["stage"]: e["variant_id"] for e in of_type(events.FOLLOWUP_SENT)}
    assert stages == {"1": "followup_1.v1", "2": "followup_2.v1"}


def test_failed_followup_send_emits_nothing_and_leaves_the_row_unmarked():
    """السلوك السابق حرفياً: لا تعليم، فتُعاد المحاولة لاحقاً - وبلا حدث كاذب."""
    decision = quote_lead("u_fu_fail")
    _age_lead(decision.lead_id, hours=30)

    send_followups.run_first_followups(FakeChannel(succeed=False))

    assert of_type(events.FOLLOWUP_SENT) == []
    assert of_type(events.RESPONSE_SENT) == []
    row = [r for r in leads_store._read_all_rows_unlocked()
           if r[leads_store.LEAD_ID_COLUMN] == decision.lead_id][0]
    assert row["مرحلة المتابعة"] == "0"


def test_marking_outside_the_send_path_leaves_variant_empty():
    """استدعاء يدوي بلا صياغة: الحدث يُكتب صادقاً بـvariant_id فارغ، لا مختلَقاً."""
    decision = quote_lead("u_manual")
    assert leads_store.mark_followup_sent(lead_id=decision.lead_id, new_stage="1") is True
    assert of_type(events.FOLLOWUP_SENT)[0]["variant_id"] is None


# =================================================== 4) المكتبة والنصوص

@pytest.mark.parametrize("user_id,turns,expected", [
    ("v1", ["شلونكم؟"], "services_list.v1"),
    ("v2", ["كم سعر البوتوكس؟"], "price_quote.v1"),
    ("v3", ["بوتوكس", "نعم"], "ask_contact_info.v1"),
    ("v4", ["بوتوكس", "نعم", "سارة 07701234567"], "booking_request_ack.v1"),
    ("v5", ["بوتوكس", "لا"], "decline_ack.v1"),
    ("v6", ["بوتوكس", "خلي أفكر"], "hesitant_ack.v1"),
    ("v7", ["بوتوكس", "شنو يعني؟"], "booking_reply_reprompt.v1"),
])
def test_every_branch_returns_a_registered_variant(user_id, turns, expected):
    decision = None
    for text in turns:
        decision = handle_message(make_message(user_id, text))
    assert decision.variant_id == expected
    assert variants.get(expected).template  # مسجَّل فعلاً
    assert decision.text == variants.render(expected, **_params_for(expected))


def _params_for(variant_id: str) -> dict:
    from services import CENTER_NAME, services_list_text
    return {
        "price_quote.v1": {"service_name": SERVICE_BOTOX, "center_name": CENTER_NAME,
                           "price": SERVICE_BOTOX_PRICE},
        "services_list.v1": {"center_name": CENTER_NAME, "services_list": services_list_text()},
        "booking_request_ack.v1": {"service_name": SERVICE_BOTOX, "center_name": CENTER_NAME},
    }.get(variant_id, {})


EXPECTED_TEXTS = {
    "price_quote.v1": (
        "سعر حقن البوتوكس في مركز لمسة الجمال هو 120,000 دينار.\n"
        "هل ترغبين بحجز موعد؟"
    ),
    "ask_contact_info.v1": "ممتاز! الرجاء إرسال اسمك ورقم هاتفك في رسالة واحدة لتأكيد الحجز.",
    "decline_ack.v1": "تمام 🌸 إذا احتجتِ أي معلومة إضافية عن خدماتنا أنا موجودة.",
    "hesitant_ack.v1": (
        "تمام 🌸 خذي وقتك براحتك، وإذا حبيتي تعرفين أي تفاصيل إضافية "
        "أو تقررين الحجز، إحنا موجودين لخدمتج."
    ),
    "booking_reply_reprompt.v1": "هل ترغبين بتأكيد حجز موعد؟ (نعم / لا)",
    "booking_request_ack.v1": (
        "شكراً لك 🌸 تم تسجيل حجزك لخدمة حقن البوتوكس في مركز لمسة الجمال.\n"
        "سيتواصل معك فريقنا خلال وقت قصير لتأكيد الموعد المناسب."
    ),
    "error_fallback.v1": "عذراً، صار خطأ بسيط 🙏 حاولي ترسلين رسالتك مرة ثانية.",
    "followup_1.v1": (
        "مرحباً 🌸 لاحظنا أنك استفسرتِ سابقاً عن حقن البوتوكس (120,000 دينار) "
        "ولم يتم تأكيد الحجز بعد.\n"
        "هل ما زلتِ مهتمة؟ يسعدنا مساعدتك بحجز موعد مناسب في أي وقت."
    ),
    "followup_2.v1": (
        "🌸 آخر تذكير بخصوص حقن البوتوكس — إذا حابة تحجزين، "
        "احنا جاهزين نساعدج بأي وقت يناسبج."
    ),
}


@pytest.mark.parametrize("variant_id,expected", sorted(EXPECTED_TEXTS.items()))
def test_text_is_byte_for_byte_what_it_was_before_the_move(variant_id, expected):
    """
    النقل إلى المكتبة نقل، لا إعادة صياغة. تعديل أي نص هنا يجب أن
    يُسقط هذا الاختبار حتى لا تتغيّر صياغة يراها عميل بالسهو.
    """
    params = {
        "price_quote.v1": {"service_name": SERVICE_BOTOX, "center_name": "مركز لمسة الجمال",
                           "price": SERVICE_BOTOX_PRICE},
        "booking_request_ack.v1": {"service_name": SERVICE_BOTOX,
                                   "center_name": "مركز لمسة الجمال"},
        "followup_1.v1": {"service_name": SERVICE_BOTOX,
                          "price_suffix": f" ({SERVICE_BOTOX_PRICE})"},
        "followup_2.v1": {"service_name": SERVICE_BOTOX},
    }.get(variant_id, {})
    assert variants.render(variant_id, **params) == expected


def test_followup_1_without_a_price_keeps_the_old_shape():
    """السعر الفارغ كان يحذف القوس كاملاً - ولا يزال."""
    assert send_followups.build_followup_1_message(SERVICE_BOTOX, "") == (
        "مرحباً 🌸 لاحظنا أنك استفسرتِ سابقاً عن حقن البوتوكس "
        "ولم يتم تأكيد الحجز بعد.\n"
        "هل ما زلتِ مهتمة؟ يسعدنا مساعدتك بحجز موعد مناسب في أي وقت."
    )


def test_the_library_holds_every_outgoing_text_and_no_module_holds_another():
    """
    كل معرّف مذكور في الكود مسجَّل، وكل مسجَّل مذكور - لا صياغة يتيمة
    ولا معرّف يشير إلى فراغ.
    """
    referenced = set()
    for name in ("business_logic.py", "message_router.py", "send_followups.py"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        referenced |= set(re.findall(r'"([a-z0-9_]+\.v\d+)"', text))
    assert referenced == set(variants.all_variant_ids())


# =================================================== 5) حدود §16

def test_registry_rejects_a_third_variant_for_one_intent():
    """
    §16: ثلاث صياغات تقسّم عينة صغيرة أصلاً. الحد مفروض لحظة الاستيراد.
    """
    intent = "price_quote"
    with pytest.raises(ValueError, match="تجاوزت الحد"):
        variants._validate_registry([
            variants.Variant(f"{intent}.v{n}", intent, f"نص {n}") for n in (1, 2, 3)
        ])


def test_registry_rejects_duplicate_and_malformed_ids():
    with pytest.raises(ValueError, match="مكرر"):
        variants._validate_registry([
            variants.Variant("a.v1", "a", "س"), variants.Variant("a.v1", "a", "ص"),
        ])
    with pytest.raises(ValueError, match="لا يطابق نيّته"):
        variants._validate_registry([variants.Variant("b.v1", "a", "س")])
    with pytest.raises(ValueError, match="لا ينتهي برقم"):
        variants._validate_registry([variants.Variant("a.vx", "a", "س")])


def test_today_every_intent_has_exactly_one_variant():
    """
    هذا التغيير يجعل النسب ممكناً ولا يبدأ أي تجربة: صياغة واحدة لكل
    نية، ولا منطق اختيار في أي مكان.
    """
    per_intent = {}
    for variant_id in variants.all_variant_ids():
        per_intent.setdefault(variants.intent_of(variant_id), []).append(variant_id)
    assert all(len(ids) == 1 for ids in per_intent.values())
    # 10 + نيّتا كشف الغموض (التغيير #6) + إعادة سؤال بيانات التواصل (#7)
    assert len(per_intent) == 13


def test_template_hash_changes_when_the_template_changes():
    """البصمة تكشف تعديلاً صامتاً على صياغة مستعمَلة - بعد وقوعه."""
    original = variants.template_hash("price_quote.v1")
    assert original is not None and len(original) == 8
    assert variants.template_hash("لا_يوجد.v1") is None
    assert original != variants.template_hash("decline_ack.v1")


# =================================================== 6) النسب من الأحداث وحدها

def test_which_variant_preceded_the_booking_request_from_events_alone():
    """
    شرط خروج Gate A عملياً: بلا leads.csv وبلا الجلسة - من السجل وحده -
    نعرف أي صياغة أُرسلت لهذا الـLead وأيّها سبقت طلب الحجز.
    """
    channel = FakeChannel()
    router = MessageRouter(channel=channel, handler=handle_message)
    for text in ("كم سعر البوتوكس؟", "نعم", "سارة 0770 000 000"):
        router._on_message(make_message("u_attr", text))

    log = events.read_all()
    booking = [e for e in log if e["event_type"] == events.BOOKING_REQUESTED][0]
    lead_id = booking["lead_id"]

    outgoing_before = [
        e["variant_id"] for e in log
        if e["event_type"] == events.RESPONSE_SENT
        and e["lead_id"] == lead_id
        and e["timestamp"] < booking["timestamp"]
    ]
    assert outgoing_before == ["price_quote.v1", "ask_contact_info.v1"]

    # وكل صادر في المحادثة منسوب: ثلاث رسائل، ثلاثة أحداث، ثلاث صياغات
    assert len([e for e in log if e["event_type"] == events.RESPONSE_SENT]) == 3
    assert all(e["variant_id"] for e in log if e["event_type"] == events.RESPONSE_SENT)


def test_every_outgoing_message_produces_exactly_one_event():
    """
    الشرط الحرفي: «كل رسالة صادرة عبر مسار واحد تنتج حدثاً بـvariant_id».
    حي ومتابعة معاً في سيناريو واحد.
    """
    channel = FakeChannel()
    router = MessageRouter(channel=channel, handler=handle_message)
    router._on_message(make_message("u_all", "كم سعر البوتوكس؟"))
    decision = handle_message(make_message("u_all", "خلي أفكر"))
    _age_lead(decision.lead_id, hours=30)
    send_followups.run_first_followups(channel)

    message_events = [e for e in events.read_all()
                      if e["event_type"] in (events.RESPONSE_SENT, events.FOLLOWUP_SENT)]
    # رسالتان غادرتا عبر القناة: رد السعر ثم المتابعة
    # (رد التردد لم يمرّ بالموجّه في هذا السيناريو)
    assert len(channel.sent) == 2
    assert len(message_events) == 2
    assert all(e["variant_id"] for e in message_events)
    assert [e["variant_id"] for e in message_events] == ["price_quote.v1", "followup_1.v1"]
