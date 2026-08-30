"""
مطابقة الكلمات المفتاحية بحدود الكلمة - Bounded Keyword Matching
========================================================================
قبل هذا الملف كانت كل مطابقة في النظام مطابقة سلسلة فرعية (`in`):
`"لا" in "يلا اشوف"` صحيحة، و`"ليزر" in "ليزرات"` صحيحة كذلك. النتيجة
أن رسالة ودودة تُقرأ رفضاً، وجمع كلمة يُقرأ استفساراً عن الخدمة. الخطأ
صامت تماماً: لا استثناء ولا سطر سجل - رد خاطئ على عميلة حقيقية فقط.

البديل هنا مطابقة **بحدود الكلمة**: يُقسَّم النص إلى وحدات (tokens)
على كل ما ليس حرفاً أو رقماً، وتُطابَق الكلمة المفتاحية على وحدة كاملة
لا على جزء منها.

[لماذا لا تكفي المساواة وحدها]
العربية تُلصق سوابقها بالكلمة: «بالليزر» و«لليزر» و«والليزر» كلها تعني
الليزر، وكلها وحدة واحدة بعد التقسيم. اشتراط المساواة التامة كان
سيُسقط أكثر مما يُصلح. لذلك تُقبل الوحدة إذا **انتهت** بالكلمة
المفتاحية وكان كل حرف قبلها داخل الوحدة من مجموعة السوابق
{و ا ل ب ك ف} - وهي حروف المعاني الملتصقة في العربية، لا حروف عامة.

ثلاثة قيود تمنع هذا التساهل من أن يصير سلسلة فرعية من جديد:
  1. المطابقة تنتهي عند نهاية الوحدة - فـ«ليزرات» لا تطابق «ليزر».
  2. السوابق لا تُسمح إلا لكلمة مفتاحية طولها 3 أحرف فأكثر - فـ«لا»
     (حرفان) تُطابَق بالمساواة وحدها، و«يلا» لا تطابقها أبداً. الكلمة
     القصيرة تقع داخل كلمات كثيرة بالمصادفة؛ الطويلة لا تكاد.
  3. كل حرف من السابقة يجب أن يكون من المجموعة - حرف واحد خارجها
     يُبطل المطابقة كلها.

الكلمة المفتاحية متعددة الكلمات («تنظيف بشرة») تُطابَق على وحدات
**متتالية**، كل وحدة بنفس القاعدة أعلاه.

`normalize_arabic` انتقلت إلى هنا من services.py بجسمها حرفاً بحرف:
التوحيد والمطابقة قاعدة واحدة، وفصلهما كان سيجعل هذا الملف يستورد
services بينما services يستورده. services.py يعيد تصديرها فيبقى
`from services import normalize_arabic` عاملاً كما كان.
"""

import re

# حروف المعاني الملتصقة بأول الكلمة في العربية. ليست مجموعة عامة:
# كل حرف هنا يظهر سابقةً حقيقية (بـ، لـ، كـ، فـ، و، ال).
CLITIC_PREFIX_CHARS = frozenset("والبكف")

# أقصر كلمة مفتاحية يُسمح لها بسابقة. أقصر منها يُطابَق بالمساواة وحدها -
# انظر القيد (2) في الترويسة.
MIN_LENGTH_FOR_CLITIC_PREFIX = 3

_TOKEN_SEPARATOR = re.compile(r"\W+", re.UNICODE)


def normalize_arabic(text: str) -> str:
    """
    توحيد النص العربي قبل المطابقة:
    - إزالة "أل" التعريف من بداية كل كلمة (البشرة -> بشرة)
    - توحيد أشكال الألف والتاء المربوطة/الهاء الشائعة
    - إزالة المسافات الزائدة
    """
    normalized = text.strip().lower()
    normalized = re.sub(r"[إأآا]", "ا", normalized)
    normalized = re.sub(r"ة", "ه", normalized)
    words = normalized.split()
    words = [w[2:] if w.startswith("ال") and len(w) > 2 else w for w in words]
    return " ".join(words)


def tokenize(text: str) -> list[str]:
    """
    يوحّد النص ثم يقسّمه إلى وحدات على كل ما ليس حرفاً أو رقماً.
    علامات الترقيم والرموز فواصل لا محتوى: «البوتوكس؟» تصير وحدة
    واحدة «بوتوكس» لا وحدة تحمل علامة استفهام.
    """
    return [token for token in _TOKEN_SEPARATOR.split(normalize_arabic(text)) if token]


def token_matches_word(token: str, word: str) -> bool:
    """
    هل تطابق الوحدة `token` كلمةَ المفتاح `word`؟ القاعدة كاملة في
    ترويسة الملف. كلاهما مفترَض أنه مُوحَّد مسبقاً (tokenize).
    """
    if not word:
        return False
    if token == word:
        return True
    if len(word) < MIN_LENGTH_FOR_CLITIC_PREFIX:
        return False
    if len(token) <= len(word) or not token.endswith(word):
        return False
    prefix = token[: len(token) - len(word)]
    return all(char in CLITIC_PREFIX_CHARS for char in prefix)


def matches(text: str, keyword: str) -> bool:
    """
    هل تُذكَر الكلمة المفتاحية `keyword` في `text` بحدود الكلمة؟
    الكلمة المفتاحية متعددة الكلمات تحتاج وحدات متتالية بنفس الترتيب.
    """
    keyword_words = tokenize(keyword)
    if not keyword_words:
        return False

    tokens = tokenize(text)
    span = len(keyword_words)
    for start in range(len(tokens) - span + 1):
        if all(
            token_matches_word(tokens[start + offset], keyword_words[offset])
            for offset in range(span)
        ):
            return True
    return False


def matches_any(text: str, keywords) -> bool:
    """هل يطابق النص أياً من الكلمات المفتاحية المعطاة؟"""
    return any(matches(text, keyword) for keyword in keywords)
