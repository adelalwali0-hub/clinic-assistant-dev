"""
حدود الـAI الأربع القابلة للاختبار (PRD §17) - وGate A موطنها المُعلن.

§17 ينص: «تأكيدات قابلة للاختبار (تُكتب كاختبارات في Gate A)». هذه هي.
قبل هذا الملف كان `ai_understanding.py` و`main.py` بلا أي تغطية -
وهما بالضبط الملفان اللذان تعيش فيهما الحدود الأربع كلها.

  §17.1  مع USE_AI_INTENT=false لا يقع أي اتصال شبكي، والقمع يعمل كاملاً
  §17.2  لا مسار تنفيذ يجعل مخرَج AI مصدراً لسعر
  §17.3  مخرَج خارج القائمة المغلقة → fallback آمن، ولا يوقف الرد
  §17.4  تبديل مزود AI لا يتطلب أي تعديل في business_logic.py

[كيف يُثبَت الصمت الشبكي]
لا بقراءة الراية. الراية تقول ما نُوي، لا ما وقع. يُثبَت بحارس على
`socket` نفسه: أي محاولة اتصال - من أي مكتبة، بما فيها استعلام DNS -
تُفجّر الاختبار. وفوقه جاسوس على understand_message يفشل إن نودي.

ومعهما شاهد سالب (test_the_network_guard_is_not_vacuous): يثبت أن
الحارس يمسك اتصالاً حقيقياً حين يقع. اختبار صمت ينجح لسببين لا يفرّق
بينهما فحص بالعين - إما أن النظام صامت، وإما أن الحارس مكسور. بلا
الشاهد السالب لا يفصل بينهما شيء، ويبقى أقوى اختبار في الملف بلا معنى.
"""

import importlib
import re
import socket
from pathlib import Path

import pytest

import ai_understanding
import business_logic
import events
import leads_store
from business_logic import handle_message
from channel_interface import IncomingMessage
from storage import session_store

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SERVICE_BOTOX_PRICE = "120,000 دينار"
SERVICE_FILLER_PRICE = "150,000 دينار"


# ----------------------------------------------------------------- أدوات

def make_message(user_id: str, text: str, channel: str = "telegram") -> IncomingMessage:
    from datetime import datetime
    return IncomingMessage(channel=channel, user_id=user_id, text=text, timestamp=datetime.now())


class NetworkAttempted(Exception):
    """يُميَّز عن AssertionError حتى لا يبتلعه `except Exception` في الكود المُختبَر."""


@pytest.fixture
def no_network(monkeypatch):
    """
    يمنع كل خروج شبكي على مستوى المقبس - لا على مستوى مكتبة بعينها.
    ترقيع `openai` وحده كان سيترك أي مسار HTTP آخر مفتوحاً وغير مرئي.

    يُرجع سجلّ المحاولات. وجوده هو ما يجعل الشاهد السالب ذا معنى:
    «رجع fallback» وحده لا يثبت أن الحارس عمل - العميل قد يفشل قبل
    المقبس بأي سبب آخر. السجل يقول أي محاولة وقعت بالضبط، فيُثبَت
    الصمت بقائمة فارغة، ويُثبَت أن الحارس حيٌّ بقائمة غير فارغة.
    """
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(args[0] if args else "?")
        raise NetworkAttempted("محاولة اتصال شبكي أثناء اختبار يفترض الصمت")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    return attempts


@pytest.fixture
def load_main(monkeypatch):
    """
    main.py يقرأ USE_AI_INTENT **لحظة الاستيراد** إلى ثابت وحدة، ويرفض
    الاستيراد بلا TELEGRAM_BOT_TOKEN. لاختبار الوضعين لا مفرّ من إعادة
    تحميل الوحدة تحت بيئة مضبوطة.

    وتُعاد إلى الوضع المرجعي (false) بعد كل اختبار: وحدة مُعاد تحميلها
    تبقى معادة في `sys.modules` لبقية الجلسة، فترك main على true كان
    سيسرّب حالة إلى ملفات اختبار أخرى.
    """
    loaded = []

    def _load(use_ai: bool):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-not-used")
        monkeypatch.setenv("USE_AI_INTENT", "true" if use_ai else "false")
        import main
        module = importlib.reload(main)
        loaded.append(module)
        return module

    yield _load

    if loaded:
        monkeypatch.setenv("USE_AI_INTENT", "false")
        importlib.reload(loaded[-1])


class FakeCompletions:
    """عميل OpenAI مزيَّف: يُرجع ما يُملى عليه، أو يرمي ما يُملى عليه."""

    def __init__(self, content=None, raises=None):
        self.content = content
        self.raises = raises
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        message = type("M", (), {"content": self.content})
        choice = type("C", (), {"message": message})
        return type("R", (), {"choices": [choice]})


def fake_client(content=None, raises=None):
    completions = FakeCompletions(content=content, raises=raises)
    chat = type("Chat", (), {"completions": completions})
    return type("Client", (), {"chat": chat})(), completions


def run_full_funnel(handler, user_id: str) -> list:
    """
    القمع كاملاً عبر `handler` المُمرَّر: استفسار سعر → عرض حجز →
    موافقة → بيانات تواصل → صف مكتوب.
    """
    return [
        handler(make_message(user_id, "كم سعر البوتوكس؟")),
        handler(make_message(user_id, "نعم")),
        handler(make_message(user_id, "سارة 0770 000 000")),
    ]


def assert_funnel_completed(user_id: str, decisions: list):
    """القمع لم يعمل «تقريباً»: سعر حقيقي، صف مكتوب، جلسة مغلقة."""
    assert SERVICE_BOTOX_PRICE in decisions[0].text

    rows = [r for r in leads_store._read_all_rows() if r["معرف العميل"] == user_id]
    assert len(rows) == 1
    assert rows[0]["الحالة"] == leads_store.STATE_BOOKING_REQUESTED
    assert rows[0]["بيانات التواصل"] == "سارة 0770 000 000"

    assert session_store.get_session(user_id)["state"] == session_store.STATE_IDLE

    types = [e["event_type"] for e in events.read_all()]
    assert events.LEAD_CREATED in types
    assert events.BOOKING_REQUESTED in types


# ============================================ §17.1 الصمت الشبكي والقمع

def test_no_network_call_at_all_and_the_full_funnel_still_works(load_main, no_network, monkeypatch):
    """
    §17.1 حرفياً. الحارس على المقبس يمسك أي خروج مهما كانت المكتبة،
    والجاسوس يمسك أي نداء لطبقة الفهم حتى لو لم تصل إلى الشبكة.

    ولا يكفي أن شيئاً لم يقع: القمع كله يجب أن يكتمل تحت الحارسين -
    وإلا كان الصمت صمت نظام معطّل.
    """
    main = load_main(use_ai=False)

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("understand_message نوديت مع USE_AI_INTENT=false")

    monkeypatch.setattr(main, "understand_message", must_not_be_called)

    assert main.USE_AI_INTENT is False
    decisions = run_full_funnel(main.combined_handler, "ai_off_1")
    assert_funnel_completed("ai_off_1", decisions)

    # ولا محاولة اتصال واحدة - ولا حتى استعلام DNS
    assert no_network == [], f"وقع خروج شبكي: {no_network}"


def test_the_network_guard_is_not_vacuous(no_network, monkeypatch):
    """
    الشاهد السالب - وسبب وجوده أنه يقيس الحارس لا النظام.

    اختبار الصمت أعلاه ينجح في حالتين لا يفرّق بينهما فحص بالعين:
    إما أن النظام صامت فعلاً، وإما أن حارس المقبس لا يمسك شيئاً
    أصلاً. هنا يُشغَّل المسار الذي **يجب** أن يتصل (USE_AI_INTENT=true
    مع عميل حقيقي)، ويُثبَت أن الحارس اعترضه.

    وبما أن understand_message يبتلع كل استثناء إلى fallback آمن،
    لا يكفي أن النتيجة fallback: العميل قد يفشل قبل بلوغ المقبس بأي
    سبب آخر، فيمرّ الشاهد بلا أن يمسّ الحارس. لذلك يُقرأ سجلّ الحارس
    نفسه - محاولة مسجَّلة واحدة على الأقل هي الدليل.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(ai_understanding, "_client", None)  # عميل حقيقي، لا مزيَّف

    result = ai_understanding.understand_message("كم سعر البوتوكس؟")

    # الحارس اعترض فعلاً - وهذا ما يجعل اختبار الصمت أعلاه ذا معنى
    assert no_network != [], "الحارس لم يعترض شيئاً: اختبار الصمت أعلاه بلا قيمة"
    # والطبقة ردّت بالـfallback بدل أن تنهار
    assert result == ai_understanding.FALLBACK_RESULT


def test_with_ai_on_the_provider_is_actually_consulted(load_main, monkeypatch):
    """
    الوجه الثاني للشاهد السالب: الجاسوس في §17.1 يثبت «لم يُنادَ» -
    وهذا يثبت أن نفس الجاسوس كان سيرصد النداء لو وقع.
    """
    main = load_main(use_ai=True)
    calls = []

    def spy(text, recent_history=None, session_state="idle"):
        calls.append(text)
        return {"intent": "other", "service_mentioned": None}

    monkeypatch.setattr(main, "understand_message", spy)

    main.combined_handler(make_message("ai_on_1", "كم سعر البوتوكس؟"))
    assert calls == ["كم سعر البوتوكس؟"]


# ====================================== §17.2 لا سعر من مخرَج AI أبداً

@pytest.mark.parametrize("ai_intent", sorted(ai_understanding.ALLOWED_INTENTS) + [
    "confirm_booking", "price_inquiry", "لا-قيمة", "", None, "DROP TABLE",
])
def test_no_ai_intent_can_change_the_quoted_price(ai_intent):
    """
    §17.2: السعر يُشتق من نص العميلة و`clinic_config` وحدهما. أي قيمة
    لـai_intent - مسموحة أو مخترَعة - تُنتج نفس السعر حرفياً.
    """
    baseline = handle_message(make_message("p_base", "كم سعر البوتوكس؟"), ai_intent=None).text

    user_id = f"p_{abs(hash(str(ai_intent)))}"
    reply = handle_message(make_message(user_id, "كم سعر البوتوكس؟"), ai_intent=ai_intent).text

    assert reply == baseline
    assert SERVICE_BOTOX_PRICE in reply
    assert SERVICE_FILLER_PRICE not in reply


def test_a_provider_naming_a_different_service_cannot_move_the_price(load_main, monkeypatch):
    """
    الهجوم المباشر على §17.2: مزوّد يدّعي أن العميلة تقصد خدمة أغلى.
    `service_mentioned` لا يعبر إلى business_logic إطلاقاً، والسعر
    يبقى سعر ما كتبته العميلة هي.
    """
    main = load_main(use_ai=True)

    def lying_provider(text, recent_history=None, session_state="idle"):
        return {"intent": "price_inquiry", "service_mentioned": "حقن الفيلر"}

    monkeypatch.setattr(main, "understand_message", lying_provider)

    reply = main.combined_handler(make_message("p_lie", "كم سعر البوتوكس؟")).text

    assert SERVICE_BOTOX_PRICE in reply
    assert SERVICE_FILLER_PRICE not in reply


def test_the_override_list_holds_no_price_bearing_intent():
    """
    الحد البنيوي تحت §17.2: النوايا الثلاث المسموح لها بالتأثير لا
    تلمس أي فرع تسعير. `price_inquiry` خارجها عمداً.
    """
    for allowed in (business_logic.AI_OVERRIDE_ALLOWED,):
        assert allowed == {"confirm_booking", "decline", "hesitant"}
        assert "price_inquiry" not in allowed
        assert "ask_more_info" not in allowed


def test_business_logic_imports_no_ai_provider_at_all():
    """
    §17.2 و§17.4 معاً، بنيوياً: لو استورد business_logic مزوّداً، لصار
    السعر على بعد سطر واحد من مخرَج نموذج - ولصار تبديل المزود تعديلاً
    في منطق العمل.
    """
    source = (PROJECT_ROOT / "business_logic.py").read_text(encoding="utf-8")
    code_lines = [
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    ]
    joined = "\n".join(code_lines)

    for forbidden in ("import openai", "from openai", "import ai_understanding",
                      "from ai_understanding", "OpenAI("):
        assert forbidden not in joined, f"business_logic.py يستورد مزوّداً: {forbidden}"

    assert not re.search(r"^\s*import\s+ai_understanding", joined, re.M)


# ================================ §17.3 خارج القائمة المغلقة → fallback

@pytest.mark.parametrize("parsed", [
    {"intent": "buy_a_car", "service_mentioned": None},
    {"intent": None, "service_mentioned": None},
    {"intent": "PRICE_INQUIRY", "service_mentioned": None},   # حساسية حالة الأحرف
    {"service_mentioned": "بوتوكس"},                          # بلا intent إطلاقاً
    {},
    "نص لا قاموس",
    None,
    42,
])
def test_anything_outside_the_closed_list_normalizes_to_the_safe_fallback(parsed):
    """§17.3 على مستوى الوحدة: القائمة مغلقة فعلاً، لا بالنية."""
    assert ai_understanding._validate_and_normalize(parsed) == ai_understanding.FALLBACK_RESULT


def test_a_valid_intent_with_a_junk_service_keeps_the_intent_and_drops_the_service():
    """التطبيع لا يهدم ما هو صالح: النية تبقى، والحقل الفاسد وحده يسقط."""
    out = ai_understanding._validate_and_normalize({"intent": "hesitant", "service_mentioned": 99})
    assert out == {"intent": "hesitant", "service_mentioned": None}


@pytest.mark.parametrize("content,raises", [
    ('{"intent": "buy_a_car", "service_mentioned": null}', None),   # نية خارج القائمة
    ("ليس JSON إطلاقاً", None),                                     # رد غير قابل للتحليل
    ('{"intent": "hesitant"', None),                                # JSON مقطوع
    ("", None),
    (None, ConnectionError("الشبكة سقطت")),                         # فشل شبكي
    (None, ValueError("خطأ من المزوّد")),
    (None, TimeoutError("مهلة")),
])
def test_understand_message_never_raises_and_always_returns_a_shape(monkeypatch, content, raises):
    """
    §17.3 من طرف إلى طرف: أي خلل - نية مرفوضة، JSON فاسد، شبكة ساقطة -
    يُرجع FALLBACK_RESULT ولا يرمي. الرد لا يتوقف.
    """
    client, _ = fake_client(content=content, raises=raises)
    monkeypatch.setattr(ai_understanding, "_client", client)

    result = ai_understanding.understand_message("أي نص")

    assert result == ai_understanding.FALLBACK_RESULT
    assert set(result) == {"intent", "service_mentioned"}


def test_a_missing_api_key_stops_the_program_and_is_not_hidden_as_fallback(monkeypatch):
    """
    الاستثناء المقصود الوحيد: خطأ ضبط يجب أن يُوقف البرنامج بوضوح، لا
    أن يتنكّر في هيئة «العميلة قالت other» إلى الأبد.
    """
    monkeypatch.setattr(ai_understanding, "_client", None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(SystemExit):
        ai_understanding.understand_message("أي نص")


def test_a_garbage_provider_does_not_block_the_reply(load_main, monkeypatch):
    """
    §17.3 عند مستوى النظام: مزوّد يُرجع هراءً لا يمنع العميلة من
    تلقّي ردّها، والرد يحمل صياغة مسجَّلة كأي رد آخر.
    """
    main = load_main(use_ai=True)

    def garbage_provider(text, recent_history=None, session_state="idle"):
        return {"intent": "buy_a_car", "service_mentioned": ["ليست", "نصاً"]}

    monkeypatch.setattr(main, "understand_message", garbage_provider)

    decisions = run_full_funnel(main.combined_handler, "garbage_1")

    assert_funnel_completed("garbage_1", decisions)
    for decision in decisions:
        assert decision.variant_id is not None


# ================================== §17.4 تبديل المزود بلا تعديل منطق

def test_three_providers_produce_one_identical_funnel(load_main, monkeypatch):
    """
    §17.4 سلوكياً: نفس `business_logic.handle_message` يخدم ثلاثة
    مزوّدين مختلفي الشكل - بلا AI، ومزوّد على شكل OpenAI، ومزوّد
    بشكل داخلي مختلف كلياً - والقمع يخرج متطابقاً حرفياً.

    القياس على variant_id وrule_decision لا على النص: هما ما يقرره
    منطق العمل، والنص مجرد عرض لهما.
    """
    def openai_shaped(text, recent_history=None, session_state="idle"):
        return {"intent": "other", "service_mentioned": None}

    def differently_shaped(text, recent_history=None, session_state="idle"):
        # مزوّد آخر: يبني نتيجته من بنية داخلية لا تشبه الأولى إطلاقاً
        verdict = type("Verdict", (), {"label": "other", "service": None})()
        return {"intent": verdict.label, "service_mentioned": verdict.service}

    def fingerprint(decisions):
        return [(d.variant_id, d.rule_decision) for d in decisions]

    main_off = load_main(use_ai=False)
    without_ai = fingerprint(run_full_funnel(main_off.combined_handler, "sw_none"))

    main_on = load_main(use_ai=True)
    monkeypatch.setattr(main_on, "understand_message", openai_shaped)
    with_provider_a = fingerprint(run_full_funnel(main_on.combined_handler, "sw_a"))

    monkeypatch.setattr(main_on, "understand_message", differently_shaped)
    with_provider_b = fingerprint(run_full_funnel(main_on.combined_handler, "sw_b"))

    assert without_ai == with_provider_a == with_provider_b
    # ونفس دالة منطق العمل خدمت الثلاثة - لا نسخة لكل مزوّد
    assert main_off.handle_message is main_on.handle_message is handle_message


def test_the_provider_module_is_reachable_only_through_main(load_main):
    """
    §17.4 بنيوياً: `ai_understanding` يُستورد في main.py وحده. أي وحدة
    أخرى تستورده تصنع نقطة تبديل ثانية يجب تذكّرها عند تغيير المزود -
    وما يُنسى تذكّره هو ما ينكسر.
    """
    offenders = []
    for path in PROJECT_ROOT.rglob("*.py"):
        if path.name == "main.py" or "tests" in path.parts or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for i, line in enumerate(source.splitlines(), 1):
            if re.match(r"\s*(from|import)\s+ai_understanding", line):
                offenders.append(f"{path.name}:{i}")

    assert offenders == [], f"ai_understanding مستورَد خارج main.py: {offenders}"
