"""
S12 - لا نص عميلة يبلغ الطرفية (PRD §18)
========================================================================
يقود محادثة كاملة عبر `MessageRouter` الحقيقي و`business_logic` الحقيقي،
ثم يفتّش **كل** ما كُتب على stdout وstderr بحثاً عن نص العميلة.

يُستعمل `capfd` لا `capsys` عن قصد: `capsys` يلتقط ما يمرّ بـ
`sys.stdout` في بايثون، و`capfd` يلتقط واصفَي الملف 1 و2 - فيرى كذلك ما
تكتبه مكتبة C مباشرة دون المرور بطبقة بايثون.

الاختبار الأخير في الملف (`..._control_...`) اختبار مضاد مقصود: يُشعل
الصدى ويؤكد أن النص **يظهر**. بدونه كان يمكن لكل ما فوقه أن ينجح لأن
المحادثة لم تُنفَّذ أصلاً - إثبات غياب لا يساوي شيئاً ما لم يُثبَت أنه
كان قادراً على رؤية الحضور.

======================================================================
ما لا يغطيه هذا الملف - يُقرأ مع النتيجة الخضراء لا بعدها
======================================================================
النجاح هنا يعني: «المسارات المقودة أدناه لم تطبع السنتينلات المختارة
أدناه». وهو **أضيق** من «لا بيانات شخصية تُطبع أبداً». تحديداً:

1. **المسارات المقودة وحدها.** `ai_understanding` مع خطأ OpenAI حقيقي،
   و`telegram_channel` مع شبكة حقيقية، ممثَّلان هنا بمزيّفات. الشكل
   الذي تأخذه رسالة استثناء من تلك المكتبات غير مُختبَر ضد المكتبة
   نفسها؛ المُختبَر هو أن `privacy.describe_error` يحجبها.

2. **السنتينلات المختارة وحدها.** تسريب جزئي (آخر أربعة أرقام مثلاً)
   ينجو من فحص السلسلة الكاملة. لذلك يُضاف فحص «أي تتابع من 9 أرقام
   فأكثر» - وهو يغطي الهاتف لا الاسم.

3. **هذا الملف لا بقية الحزمة.** لا حارس `autouse` يفحص مخرَجات كل
   اختبار: كان سيحتاج كل اختبار أن يسجّل سنتينلاته، وكان سيتصادم مع
   الاختبارات التي تستنزف الـbuffer بنفسها (`capsys.readouterr`). فما
   يقوله هذا الملف عن الاختبارات الأخرى: لا شيء.

4. **المحتوى لا الوجهة.** يُختبَر ما يُكتب، لا أين يُعاد توجيهه: مشغّل
   يوجّه stdout إلى ملف يصنع أثراً دائماً من أسطر محجوبة - وهذا مقبول
   لأنها محجوبة، لكن الملف نفسه خارج سلطة هذا الاختبار.

5. **`logging` غير مُهيّأ ولا مُختبَر.** `logging.basicConfig(DEBUG)` من
   أي مكان يجعل `httpx`/`openai` تسجّل جسم الطلب - وفيه نص العميلة -
   دون المرور بأي سطر يحجبه هذا الملف. مسجَّل في D-022.

6. **الطرفية لا القرص.** `data/sessions.json` يحمل `provisional_name`
   نصاً صريحاً، و`leads.csv` يحمل «بيانات التواصل». S12 هنا يغطي
   مخارج الإخراج وحدها - انظر D-022.
"""

import os
import re
from datetime import datetime

import pytest

import privacy
from business_logic import handle_message
from channel_interface import IncomingMessage, OutgoingMessage, ReplyDecision
from message_router import MessageRouter

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token-not-used")

# ------------------------------------------------------------ السنتينلات
# سلاسل لا تنتجها أي صياغة في variants.py ولا أي حقل في الإعداد، فظهور
# أيٍّ منها في المخرَج لا يحتمل تفسيراً غير التسريب.
NAME = "زهراء العبيدي"
PHONE = "07719998877"
USER_ID = "555001"  # ستة أرقام - لا يُشعل فحص «9 أرقام فأكثر»

TRACE_PRICE = "TRACEPRICE"
TRACE_YES = "TRACEYES"
TRACE_NAME = "TRACENAME"
TRACE_PHONE = "TRACEPHONE"
TRACE_AMBIGUOUS = "TRACEAMBIG"
TRACE_CHATTER = "TRACECHATTER"

ALL_TRACES = (
    TRACE_PRICE, TRACE_YES, TRACE_NAME,
    TRACE_PHONE, TRACE_AMBIGUOUS, TRACE_CHATTER,
)

#: كل ما يجب ألا يظهر: السنتينلات، والاسم كاملاً **وكل كلمة منه على
#: حدة** (تسريب «زهراء» وحدها تسريب)، والرقم.
FORBIDDEN = ALL_TRACES + (NAME, PHONE) + tuple(NAME.split())

#: تتابع تسعة أرقام فأكثر = رقم تواصل بتعريف `contact_info.py` نفسه.
NINE_DIGIT_RUN = re.compile(r"\d{9,}")

BOT_TOKEN_URL = "https://api.telegram.org/bot9988776655:AAHsecretTOKENvalue123456789/sendMessage"


# ----------------------------------------------------------------- أدوات

def make_message(text: str, message_id: str | None = None) -> IncomingMessage:
    return IncomingMessage(
        channel="telegram", user_id=USER_ID, text=text,
        timestamp=datetime.now(), message_id=message_id,
    )


class FakeChannel:
    """قناة مزيّفة - لا شبكة إطلاقاً. تُقلّد النجاح والفشل والاستثناء."""

    channel_name = "telegram"

    def __init__(self, succeed: bool = True, raises: BaseException | None = None):
        self.succeed = succeed
        self.raises = raises
        self.sent: list[OutgoingMessage] = []

    def send_message(self, message: OutgoingMessage) -> bool:
        self.sent.append(message)
        if self.raises is not None:
            raise self.raises
        return self.succeed

    def start_listening(self, on_message):
        raise NotImplementedError

    def stop_listening(self):
        raise NotImplementedError


def drive_full_conversation(router: MessageRouter) -> None:
    """
    محادثة كاملة بالمسار القاعدي: استفسار سعر ← موافقة ← اسم وحده ←
    اسم ورقم. مضافاً إليها فرع الغموض وفرع الكلام العام.

    الترتيب هو ترتيب الإنتاج: الرسالة الرابعة تصل والجلسة
    `awaiting_contact_info`، وهي اللحظة التي يكون فيها نص الرسالة
    الوارد **هو** بيانات التواصل - سبب S12 الأول.
    """
    router._on_message(make_message(f"شكد سعر البوتوكس؟ {TRACE_PRICE}", "m1"))
    router._on_message(make_message(f"نعم {TRACE_YES}", "m2"))
    router._on_message(make_message(f"{NAME} {TRACE_NAME}", "m3"))
    router._on_message(make_message(f"{NAME} {PHONE} {TRACE_PHONE}", "m4"))
    router._on_message(make_message(f"بوتوكس وفيلر {TRACE_AMBIGUOUS}", "m5"))
    router._on_message(make_message(f"هلا شلونكم {TRACE_CHATTER}", "m6"))


def assert_nothing_leaked(captured) -> None:
    """كل مخرَج الاختبار - stdout وstderr معاً - خالٍ من كل ممنوع."""
    output = captured.out + captured.err

    leaked = [needle for needle in FORBIDDEN if needle in output]
    assert not leaked, (
        "تسرّب نص عميلة إلى الطرفية: " + "، ".join(leaked) + f"\n--- المخرَج ---\n{output}"
    )

    digit_run = NINE_DIGIT_RUN.search(output)
    assert digit_run is None, (
        f"تتابع {len(digit_run.group())} رقماً في المخرَج ({digit_run.group()}) - "
        f"رقم تواصل بتعريف contact_info.\n--- المخرَج ---\n{output}"
    )


# ------------------------------------------------- 1) المحادثة الكاملة

def test_full_conversation_prints_no_customer_text(capfd):
    """
    الادعاء المركزي لـS12: محادثة كاملة تمر بلحظة `awaiting_contact_info`
    ولا يبلغ حرفٌ واحد من كلام العميلة الطرفية.
    """
    router = MessageRouter(channel=FakeChannel(), handler=handle_message)
    drive_full_conversation(router)
    assert_nothing_leaked(capfd.readouterr())


def test_the_redacted_line_is_still_a_useful_diagnostic(capfd):
    """
    الحجب ليس إسكاتاً. السطر يبقى يحمل القناة والمعرّف وعدّ الأحرف
    والأرقام - وعدّ الأرقام هو ما يخبر المطوّر أن الرسالة «بدت بيانات
    تواصل» دون أن يرى الرقم.
    """
    router = MessageRouter(channel=FakeChannel(), handler=handle_message)
    router._on_message(make_message(f"{NAME} {PHONE}", "m1"))
    out = capfd.readouterr().out

    assert "[IN]" in out
    assert USER_ID in out          # المشغّل يحتاج معرفة أي محادثة
    assert "telegram" in out
    assert "محجوب" in out
    assert "11 رقم" in out          # طول الهاتف، لا الهاتف
    assert "[OUT]" in out
    assert ".v" in out              # الصادر يحمل معرّف صياغته


def test_outgoing_line_keeps_the_variant_id_that_names_the_text(capfd):
    """
    ما يعوّض حجب النص الصادر: `variant_id` يقود إلى النص الكامل في
    `variants.py`. لا شيء تشخيصي فُقد - فقط لم يُنسَخ.
    """
    router = MessageRouter(channel=FakeChannel(), handler=handle_message)
    router._on_message(make_message(f"شكد سعر البوتوكس؟ {TRACE_PRICE}", "m1"))
    out = capfd.readouterr().out

    assert "price_quote" in out, "سطر [OUT] فقد variant_id فصار الحجب إسكاتاً"
    assert TRACE_PRICE not in out


# ---------------------------------------------------- 2) مسارات الفشل

def test_handler_exception_carrying_the_text_does_not_print_it(capfd):
    """
    استثناء من `handler` رسالته **هي** نص العميلة. هذا هو الشكل الذي
    يجعل «انهيار وسط المعالجة» تسريباً: أثر بايثون لا يطبع القيم
    المحلية، لكن رسالة الاستثناء تُطبع.
    """
    def exploding_handler(message):
        raise RuntimeError(f"فشل على النص: {message.text}")

    router = MessageRouter(channel=FakeChannel(), handler=exploding_handler)
    router._on_message(make_message(f"{NAME} {PHONE} {TRACE_PHONE}", "m1"))

    captured = capfd.readouterr()
    assert "[ERROR]" in captured.out
    assert "RuntimeError" in captured.out, "اسم النوع يجب أن يبقى - وإلا فلا تشخيص"
    assert_nothing_leaked(captured)


def test_failed_send_does_not_print_the_message_text(capfd):
    """
    `[OUT-FAIL]` - الموضع الذي لا تذكره §18 وقد كان يطبع النص كاملاً
    مثل `[OUT]` تماماً.
    """
    router = MessageRouter(channel=FakeChannel(succeed=False), handler=handle_message)
    drive_full_conversation(router)

    captured = capfd.readouterr()
    assert "[OUT-FAIL]" in captured.out
    assert_nothing_leaked(captured)


def test_channel_exception_does_not_print_the_bot_token(capfd):
    """
    ليس بياناً شخصياً بل مفتاح تحكّم كامل: `requests` يضع الـURL كاملاً
    في نص استثناء الشبكة، وURL تلغرام يحمل التوكن بين مقطعين.
    """
    boom = RuntimeError(f"HTTPSConnectionPool: Max retries exceeded with url: {BOT_TOKEN_URL}")
    router = MessageRouter(channel=FakeChannel(raises=boom), handler=handle_message)
    router._on_message(make_message(f"شكد سعر البوتوكس؟ {TRACE_PRICE}", "m1"))

    captured = capfd.readouterr()
    out = captured.out + captured.err
    assert "[OUT-FAIL]" in out
    assert "9988776655:AAHsecretTOKENvalue123456789" not in out
    assert "توكن محجوب" in out
    assert "api.telegram.org" in out, "التنقية تحجب التوكن لا الـURL كله - وإلا فلا تشخيص"


def test_duplicate_message_skip_line_prints_no_text(capfd):
    """مسار Idempotency (S9) يطبع سطره الخاص - ومعرّف الرسالة وحده."""
    router = MessageRouter(channel=FakeChannel(), handler=handle_message)
    message = make_message(f"{NAME} {PHONE} {TRACE_PHONE}", "same-id")
    router._on_message(message)
    router._on_message(message)

    captured = capfd.readouterr()
    assert "[SKIP]" in captured.out
    assert_nothing_leaked(captured)


# -------------------------------------------------- 3) الضمانة البنيوية

@pytest.mark.parametrize("build", [
    lambda: IncomingMessage(
        channel="telegram", user_id=USER_ID, text=f"{NAME} {PHONE}",
        timestamp=datetime.now(), message_id="m1", raw={"text": f"{NAME} {PHONE}"},
    ),
    lambda: OutgoingMessage(user_id=USER_ID, text=f"{NAME} {PHONE}", variant_id="v.1"),
    lambda: ReplyDecision(
        text=f"{NAME} {PHONE}", variant_id="v.1", lead_id="lead_x", rule_decision="other",
    ),
])
def test_repr_of_message_objects_never_carries_the_text(build):
    """
    `__repr__` المولَّدة كانت تُخرج النص كاملاً عند أي `print(message)`
    أو `%r` أو استثناء يُبنى بالكائن أو `pytest --showlocals`. حجب
    مواضع الطباعة وحدها كان سيترك هذا الباب مفتوحاً لكل سطر يُكتب غداً.
    """
    obj = build()
    text = repr(obj)
    assert NAME not in text
    assert PHONE not in text
    # الحجب على حقل النص وحده - لا إسكات للتمثيل كله: بقية الحقول تبقى
    # مقروءة، والاسم الصنفي يبقى في مقدّمته كأي dataclass.
    assert "text=<محجوب:" in text
    assert text.startswith(type(obj).__name__ + "(")


def test_echo_is_off_unless_someone_turns_it_on():
    """
    الافتراضي عند الاستيراد المجرَّد. لا مفتاح في `runtime_config.json`
    ولا متغيّر بيئة يستطيع قلب هذا - المسار الوحيد راية سطر أوامر
    تُقرأ في `main.__main__` وحدها.
    """
    assert privacy.echo_enabled() is False
    assert "محجوب" in privacy.redact("أي نص")


def test_no_module_reads_the_echo_flag_from_config_or_environment():
    """
    حارس نصّي على القرار لا على السلوك: أول محاولة لجعل الصدى مفتاحاً
    يبقى على القرص (إعداد أو بيئة) تصير تعديلاً مرئياً في الـdiff.
    """
    source = (privacy.__file__,)
    for path in source:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read().split('"""', 2)[-1]  # بعد الترويسة
    assert "environ" not in body, "الصدى صار يُقرأ من متغيّر بيئة - يبقى على الجهاز"
    assert "runtime_config" not in body, "الصدى صار مفتاح إعداد - يُنسَخ مع الملف"


def test_the_flag_is_the_only_way_in_and_a_typo_stops_the_boot(monkeypatch, capfd):
    """
    مسار الإشعال الوحيد، وسلوكه عند الخطأ المطبعي.

    الرفض لا التجاهل - نفس منطق `settings.py` مع المفاتيح المجهولة:
    راية مكتوبة خطأً تُتجاهَل بصمت فيظن كاتبها أن الصدى مشتعل ويقرأ
    `<محجوب: …>` تعطّلاً لا حجباً.
    """
    import main

    monkeypatch.setattr(privacy, "_echo_enabled", False)

    main._apply_cli_flags([])
    assert privacy.echo_enabled() is False

    with pytest.raises(SystemExit):
        main._apply_cli_flags(["--echo-customer-texts"])
    assert privacy.echo_enabled() is False

    main._apply_cli_flags([main.ECHO_FLAG])
    assert privacy.echo_enabled() is True
    assert "صدى نصوص العميلات مُشتعل" in capfd.readouterr().err, (
        "الإشعال بلا بانر - وضع كاشف للبيانات يبدأ بصمت"
    )


@pytest.mark.parametrize("raw,masked", [
    (BOT_TOKEN_URL, "9988776655:AAHsecretTOKENvalue123456789"),
    ("token=112233445:ABCdefGHIjklMNOpqrsTUVwxyz012345", "112233445:ABCdefGHIjklMNOpqrsTUVwxyz012345"),
])
def test_secret_scrubbing_is_unconditional(raw, masked, monkeypatch):
    """التوكن يُنقّى في الوضعين: الصدى يكشف نصوص العميلات، لا الأسرار."""
    for echo in (False, True):
        monkeypatch.setattr(privacy, "_echo_enabled", echo)
        assert masked not in privacy.scrub_secrets(raw)
        assert masked not in privacy.describe_error(RuntimeError(raw))


# --------------------------------------------- 4) الاختبار المضاد للجدوى

def test_echo_control_proves_the_absence_assertions_can_see_text(capfd, monkeypatch):
    """
    الاختبار المضاد. يُشعل الصدى ويؤكد أن `assert_nothing_leaked` **يفشل**
    على نفس المحادثة.

    بدونه لا يفصل شيء بين «لم يتسرّب نص» و«لم تُنفَّذ المحادثة أصلاً»:
    إثبات غياب بلا إثبات القدرة على رؤية الحضور نتيجة خضراء فارغة.
    """
    monkeypatch.setattr(privacy, "_echo_enabled", True)
    router = MessageRouter(channel=FakeChannel(), handler=handle_message)
    drive_full_conversation(router)

    captured = capfd.readouterr()
    assert PHONE in captured.out, "الصدى مشتعل ولم يظهر النص - المحادثة لم تُنفَّذ"
    assert "<ECHO!>" in captured.out, "النص المكشوف بلا علامة تُميّزه عن سطر افتراضي"

    with pytest.raises(AssertionError):
        assert_nothing_leaked(captured)
