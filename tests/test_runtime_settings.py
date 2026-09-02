"""
إعداد التشغيل - settings.py (PRD §19، §18)
==========================================================================
ثلاث عائلات من التأكيدات:

1) **الغياب يُنتج سلوك اليوم حرفياً.** كل مفتاح اختياري، وملف الإعداد
   كله اختياري. القيم الافتراضية هي الأرقام التي كانت مكتوبة في الكود
   قبل هذا الملف - وهذا ما يجعل نقل الثوابت إلى الإعداد بلا أثر سلوكي.

2) **الفساد يوقف الإقلاع ولا يسقط إلى الافتراضي.** مفتاح موجود بنوع
   خاطئ أو خارج المدى خطأ صريح. السقوط الصامت ينتج إعداداً يكذب على
   قارئه: «ضبطنا 6 وظل يرسل عند 24».

3) **الحدود المفروضة في الكود لا في الإعداد.** مدى أرقام التواصل
   [9، 15]، واشتراط TTL ≤ نافذة الصمت، ورفض الوضع المباشر بحواجز غير
   مبنية. هذه ليست تفضيلات: كل واحدة تحرس ثغرة موصوفة في PRD أو
   AUDIT-REPORT.

الاختبارات تستدعي `settings._load()` مباشرة بعد توجيه `CONFIG_PATH`
إلى ملف مؤقت، فلا تُعاد تهيئة أي وحدة ولا يُلمَس عزل conftest. الاستثناء
الوحيد اختبار التثبيت في الوسائط الافتراضية (آخر الملف) وهو يحتاج عملية
مستقلة - انظر ترويسته.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import contact_info
import leads_store
import settings
from storage import session_store

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load(tmp_path, monkeypatch, config) -> dict:
    """يكتب `config` في ملف مؤقت ويُرجع نتيجة تحميله."""
    path = tmp_path / "runtime_config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", str(path))
    return settings._load()


def load_expecting_error(tmp_path, monkeypatch, config) -> str:
    """نص رسالة الخطأ عند رفض الإعداد. يفشل الاختبار إن قُبل."""
    with pytest.raises(SystemExit) as excinfo:
        load(tmp_path, monkeypatch, config)
    return str(excinfo.value)


# ------------------------------------------------- 1) الغياب = سلوك اليوم

def test_missing_file_reproduces_todays_values(tmp_path, monkeypatch):
    """
    لا ملف إعداد إطلاقاً: النظام يعمل بالأرقام التي كانت في الكود.

    هذا شرط النقل: لولا ذلك لكان تحويل الثوابت إلى إعداد تغييراً سلوكياً
    متخفياً في هيئة إعادة تنظيم.
    """
    monkeypatch.setattr(settings, "CONFIG_PATH", str(tmp_path / "لا-يوجد.json"))
    loaded = settings._load()

    assert loaded["mode"] == settings.MODE_CONCIERGE
    assert loaded["silence_window_hours"] == 24
    assert loaded["second_followup_hours"] == 72
    assert loaded["expire_after_hours"] == 72
    assert loaded["session_ttl_hours"] == 24
    assert loaded["min_contact_digits"] == 9


def test_empty_config_reproduces_todays_values(tmp_path, monkeypatch):
    """ملف موجود وفارغ من المفاتيح: نفس النتيجة تماماً."""
    loaded = load(tmp_path, monkeypatch, {})
    assert loaded["silence_window_hours"] == 24
    assert loaded["session_ttl_hours"] == 24
    assert loaded["min_contact_digits"] == 9


def test_shipped_config_matches_module_constants(tmp_path, monkeypatch):
    """
    `config/runtime_config.json` المرفوع في المستودع صالح، وقيمه هي
    القيم التي تعمل بها الوحدات فعلاً. اختبار يمنع إعداداً مرفوعاً
    يخالف ما يقرأه الكود.
    """
    monkeypatch.chdir(PROJECT_ROOT)
    loaded = settings._load()

    assert loaded["silence_window_hours"] == leads_store.SILENCE_WINDOW_HOURS
    assert loaded["session_ttl_hours"] == session_store.SESSION_TTL_HOURS
    assert loaded["min_contact_digits"] == contact_info.MIN_CONTACT_DIGITS


def test_underscore_keys_are_documentation(tmp_path, monkeypatch):
    """
    JSON بلا تعليقات، ومن يبحث عن `first_followup_hours` المحذوف يجب أن
    يجد سببه في الملف. مفاتيح `_` توثيق يُتجاهَل في كل مستوى.
    """
    loaded = load(tmp_path, monkeypatch, {
        "_": "شرح عام",
        "_mode": "شرح للوضع",
        "channels": {"_": "شرح", "telegram": {"followup": {
            "_": "لا يوجد first_followup_hours - النافذة هي العتبة",
            "silence_window_hours": 12,
        }}},
        "session": {"_": "شرح", "ttl_hours": 6},
        "contact": {"_": "شرح", "min_digits": 10},
    })

    assert loaded["silence_window_hours"] == 12
    assert loaded["session_ttl_hours"] == 6
    assert loaded["min_contact_digits"] == 10


def test_unknown_key_is_rejected(tmp_path, monkeypatch):
    """
    مفتاح مكتوب خطأً يُقرأ اليوم كأنه غائب، فيعمل النظام بالافتراضي
    بينما يظن كاتبه أنه ضبطه. الرفض هو ما يكشف الخطأ المطبعي.
    """
    message = load_expecting_error(tmp_path, monkeypatch, {
        "channels": {"telegram": {"followup": {"silence_window_hour": 6}}},
    })
    assert "silence_window_hour" in message
    assert "غير معروف" in message


# ------------------------------------------------- 2) الفساد ≠ الغياب

@pytest.mark.parametrize("bad", ["24", True, None, [], {}])
def test_wrong_type_exits_instead_of_defaulting(tmp_path, monkeypatch, bad):
    """
    القيمة الفاسدة لا تسقط إلى الافتراضي بصمت. `true` مرفوض صراحةً رغم
    أنه عدد صحيح في بايثون: ساعةٌ قيمتها `true` خطأ مكتوب لا نية.
    """
    message = load_expecting_error(tmp_path, monkeypatch, {
        "channels": {"telegram": {"followup": {"silence_window_hours": bad}}},
    })
    assert "silence_window_hours" in message


@pytest.mark.parametrize("bad", [0, -6])
def test_non_positive_hours_rejected(tmp_path, monkeypatch, bad):
    message = load_expecting_error(tmp_path, monkeypatch, {
        "channels": {"telegram": {"followup": {"expire_after_hours": bad}}},
    })
    assert "أكبر من صفر" in message


def test_invalid_json_is_an_error_not_an_empty_config(tmp_path, monkeypatch):
    """ملف كتبه أحدهم ويظنه نافذاً - صمتُه أسوأ من رفضه."""
    path = tmp_path / "runtime_config.json"
    path.write_text("{ mode: concierge }", encoding="utf-8")
    monkeypatch.setattr(settings, "CONFIG_PATH", str(path))

    with pytest.raises(SystemExit) as excinfo:
        settings._load()
    assert "JSON غير صالح" in str(excinfo.value)


def test_all_errors_reported_at_once(tmp_path, monkeypatch):
    """
    الخروج عند أول خطأ يجعل ضبط عشرة مفاتيح عشر دورات تشغيل-فشل-تصحيح.
    """
    message = load_expecting_error(tmp_path, monkeypatch, {
        "mode": "نصف-آلي",
        "channels": {"telegram": {"followup": {
            "silence_window_hours": -1,
            "second_followup_hours": "72",
        }}},
        "contact": {"min_digits": 3},
    })

    assert "4 خطأ" in message
    for fragment in ("mode", "silence_window_hours", "second_followup_hours", "min_digits"):
        assert fragment in message


def test_unknown_mode_rejected(tmp_path, monkeypatch):
    message = load_expecting_error(tmp_path, monkeypatch, {"mode": "نصف-آلي"})
    assert "غير معروفة" in message


# ------------------------------------------------- 3) حدود مفروضة في الكود

def test_ttl_defaults_to_silence_window_not_to_24(tmp_path, monkeypatch):
    """
    المساواة كانت تعليقاً في ترويسة session_store يحرسه انضباط بشري.
    الافتراضي مشتقّ الآن: نافذة 6 ساعات تُنتج مهلة 6 لا 24 - وهي بالضبط
    الحالة التي كانت تُنتج التناقض حين تُنقل النافذة وتُنسى المهلة.
    """
    loaded = load(tmp_path, monkeypatch, {
        "channels": {"telegram": {"followup": {"silence_window_hours": 6}}},
    })
    assert loaded["silence_window_hours"] == 6
    assert loaded["session_ttl_hours"] == 6


def test_ttl_longer_than_silence_window_is_rejected(tmp_path, monkeypatch):
    """
    الاتجاه الخطر: بين النافذة والمهلة تكون العميلة في دورة المتابعة
    (وربما خرجت لها متابعة) بينما جلستها تدّعي محادثة جارية.
    """
    message = load_expecting_error(tmp_path, monkeypatch, {
        "channels": {"telegram": {"followup": {"silence_window_hours": 6}}},
        "session": {"ttl_hours": 24},
    })
    assert "ttl_hours" in message
    assert "دورة المتابعة" in message


def test_ttl_shorter_than_silence_window_is_allowed(tmp_path, monkeypatch):
    """
    الاتجاه الآمن مشروع: تُعامَل رسالتها معاملة رسالة جديدة قبل أن تصير
    مؤهلة للمتابعة - سلوك مرئي لا يناقض سجل الـLeads.
    """
    loaded = load(tmp_path, monkeypatch, {
        "channels": {"telegram": {"followup": {"silence_window_hours": 24}}},
        "session": {"ttl_hours": 6},
    })
    assert loaded["session_ttl_hours"] == 6


@pytest.mark.parametrize("digits", [1, 5, 8])
def test_min_contact_digits_below_floor_reopens_f9(tmp_path, monkeypatch, digits):
    """
    تحت التسعة لا يوجد رقم تواصل عراقي صالح (ترويسة contact_info). قيمة
    أقل تجعل رسالة عادية تُقرأ بيانات تواصل فتُكتب صفَّ حجز ملفّقاً في
    leads.csv - وهي F9 حرفياً، وAUDIT-REPORT يضعها فوق F1 لأنها تلفّق
    لا تفقد.
    """
    message = load_expecting_error(tmp_path, monkeypatch, {"contact": {"min_digits": digits}})
    assert "min_digits" in message
    assert "F9" in message


@pytest.mark.parametrize("digits", [16, 99])
def test_min_contact_digits_above_ceiling_rejected(tmp_path, monkeypatch, digits):
    """
    السقف لسبب مختلف عن الأرضية: ترويسة contact_info تقول إن رفض بيانات
    تواصل حقيقية أسوأ من الثغرة. خطأ طباعي (99) يرفض كل عميلة إلى الأبد.
    """
    message = load_expecting_error(tmp_path, monkeypatch, {"contact": {"min_digits": digits}})
    assert "min_digits" in message


@pytest.mark.parametrize("digits", [9, 11, 15])
def test_min_contact_digits_inside_range_accepted(tmp_path, monkeypatch, digits):
    loaded = load(tmp_path, monkeypatch, {"contact": {"min_digits": digits}})
    assert loaded["min_contact_digits"] == digits


# ------------------------------------------------- 4) حارس الوضع (§18)

def test_direct_mode_refuses_to_boot_while_rails_unbuilt(tmp_path, monkeypatch):
    """
    §18 مُنفَّذاً لا موصوفاً: «التأجيل مشروط بشكل الـPilot لا بالوقت».
    كان هذا الشرط جملة في وثيقة لا يعرفها الكود؛ صار رفض إقلاع.

    الحاجز الباقي اليوم S7 وحده - S8 بُني وS6 بُني بعده. قائمة المانعين
    تُفحَص مباشرةً لا عبر نص الرسالة: الرسالة تذكر S8 كذلك لكن بمعنى
    معاكس («S8 مبني، لكن غياب النافذة…»)، فالبحث عن الرمز في النص يخلط
    حاجزاً يمنع الإقلاع بحاجز يشترط قيمة.
    """
    blocking = settings._rails_blocking_direct()
    assert [code for code in ("S6", "S7", "S8") if any(code in item for item in blocking)] == ["S7"]

    message = load_expecting_error(tmp_path, monkeypatch, {"mode": "direct"})

    assert "S7" in message
    assert "Gate B" in message


def test_concierge_mode_boots_with_rails_unbuilt(tmp_path, monkeypatch):
    """الوجه الآخر للحارس: في Concierge يرسل إنسان، فالتأجيل مشروع."""
    loaded = load(tmp_path, monkeypatch, {"mode": "concierge"})
    assert loaded["mode"] == settings.MODE_CONCIERGE


def test_direct_mode_boots_once_every_rail_is_built(tmp_path, monkeypatch):
    """
    اليوم الذي تُبنى فيه الحواجز الثلاثة يمر الوضع المباشر بلا تعديل على
    الحارس - قلبُ علامة «مبني؟» وحده يكفي. يُثبَّت هنا حتى لا يكتشف من
    يبني S8 أن الحارس يرفض إلى الأبد.

    `send_window` في الإعداد منذ أن بُني S8: `_check_mode_rails` يشترط
    قيمتها صراحةً في الوضع المباشر - حاجز مبنيٌّ بلا قيمة مضبوطة لا
    يمنع شيئاً. الاشتراط الثاني وُلد مع الحاجز، وهذا الاختبار كان يسبقه.
    """
    monkeypatch.setattr(settings, "_RAILS_REQUIRED_FOR_DIRECT", tuple(
        (code, label, True) for code, label, _ in settings._RAILS_REQUIRED_FOR_DIRECT
    ))
    loaded = load(tmp_path, monkeypatch, {
        "mode": "direct",
        "channels": {"telegram": {"send_window": {"from": "09:00", "to": "21:00"}}},
    })
    assert loaded["mode"] == settings.MODE_DIRECT


# ------------------------------------------------- 5) المفاتيح المحجوزة (§19)

def test_reserved_key_is_validated_but_not_consumed(tmp_path, monkeypatch):
    """
    §19: «تُستوعب القيود في شكل المعطيات فقط». `max_messages_per_lead`
    يمرّ بلا أثر على أي قيمة يقرأها الكود - S7 غير مبني.

    و`send_window` بجواره هو الوجه الآخر: كان محجوزاً مثله حتى بُني S8،
    فصار **يُقرأ**. المفتاحان في اختبار واحد عمداً - الفرق بينهما هو
    كامل الفرق بين «شكل مثبَّت» و«حاجز نافذ»، بنفس المفتاح وبلا هجرة
    إعداد. من يبني S7 ينقل سطره من النفي إلى الإثبات.
    """
    loaded = load(tmp_path, monkeypatch, {
        "channels": {"telegram": {
            "followup": {"silence_window_hours": 24},
            "max_messages_per_lead": 3,
            "send_window": {"from": "09:00", "to": "21:00"},
        }},
    })
    assert loaded["silence_window_hours"] == 24
    assert "max_messages_per_lead" not in loaded
    assert loaded["send_window"] == (9 * 60, 21 * 60)


@pytest.mark.parametrize("window", [
    {"from": "9:00", "to": "21:00"},
    {"from": "09:00", "to": "25:00"},
    {"from": "09:00"},
    "09:00-21:00",
])
def test_malformed_reserved_send_window_rejected(tmp_path, monkeypatch, window):
    """
    التحقق رغم عدم القراءة مقصود: إعداد يمرّ اليوم صامتاً ثم ينكسر يوم
    يُقرأ فخٌّ مؤجَّل.
    """
    message = load_expecting_error(tmp_path, monkeypatch, {
        "channels": {"telegram": {"send_window": window}},
    })
    assert "send_window" in message


def test_second_channel_rejected_until_routing_exists(tmp_path, monkeypatch):
    """
    التعشيش شكل §19، والكود لا يوجّه بين قنوات بعد. قناتان حالة لا
    يستطيع تنفيذها - تُرفض بدل أن تُقرأ إحداهما وتُهمَل الأخرى بصمت.
    """
    message = load_expecting_error(tmp_path, monkeypatch, {
        "channels": {
            "telegram": {"followup": {"silence_window_hours": 24}},
            "instagram": {"followup": {"silence_window_hours": 6}},
        },
    })
    assert "لا يوجّه بين قنوات" in message


# ------------------------------------------------- 6) التثبيت في الوسائط الافتراضية

#: `get_leads_eligible_for_first_followup(hours_threshold=SILENCE_WINDOW_HOURS)`
#: يثبّت قيمته لحظة تعريف الدالة - أي لحظة استيراد leads_store. الإعداد
#: يُقرأ عند الاستيراد كذلك، فالترتيب سليم نظرياً. هذا الاختبار يثبته
#: عملياً بدل الاكتفاء بالتحليل.
#:
#: عملية مستقلة لا إعادة تحميل داخل الاختبار: conftest يوجّه مسارات
#: leads_store إلى tmp_path ويتحقق عند التفكيك أنها بقيت هناك، وإعادة
#: تحميل الوحدة تعيدها إلى "leads.csv" النسبي فتكسر العزل وتصيب الملف
#: الحقيقي. العملية المستقلة تختبر مسار الإقلاع الحقيقي كما هو.
_BINDING_PROBE = """
import json, sys
from datetime import datetime, timedelta

import leads_store

assert leads_store.SILENCE_WINDOW_HOURS == 6, leads_store.SILENCE_WINDOW_HOURS

lead_id = leads_store.record_price_quote(
    user_id="u1", service_name="حقن البوتوكس", channel="telegram"
)

class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.now() + timedelta(hours=7)

leads_store.datetime = FrozenDatetime

# بلا وسيط: الأهلية من الوسيط الافتراضي المثبَّت لحظة الاستيراد.
eligible = leads_store.get_leads_eligible_for_first_followup()
print(json.dumps({
    "silence_window": leads_store.SILENCE_WINDOW_HOURS,
    "eligible": len(eligible),
}))
"""


def test_configured_window_reaches_bound_default_arguments(tmp_path):
    """
    نافذة 6 ساعات في الإعداد، وLead عمره 7 ساعات، واستدعاء **بلا وسيط**.

    لو بقي الوسيط الافتراضي مثبَّتاً على 24 لخرجت النتيجة صفراً: العتبة
    لم تتحقق بعد. خروجها 1 يثبت أن القيمة المضبوطة وصلت إلى الوسيط
    الافتراضي فعلاً، لا إلى ثابت الوحدة وحده.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "clinic_config.json").write_text(
        (PROJECT_ROOT / "config" / "clinic_config.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "config" / "runtime_config.json").write_text(
        json.dumps({"channels": {"telegram": {"followup": {"silence_window_hours": 6}}}}),
        encoding="utf-8",
    )
    (tmp_path / "probe.py").write_text(textwrap.dedent(_BINDING_PROBE), encoding="utf-8")

    env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "probe.py"],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["silence_window"] == 6
    assert payload["eligible"] == 1, (
        "الوسيط الافتراضي لم يلتقط النافذة المضبوطة - بقي مثبَّتاً على قيمة الاستيراد القديمة"
    )
