"""
Channel Layer - الطبقة المستقلة عن قناة التواصل
====================================================
هذه الطبقة تعرّف "العقد" (Interface) الذي يجب على أي قناة تواصل
(Telegram, WhatsApp, Instagram, Facebook...) الالتزام به.

منطق العمل (Business Logic, AI, Workflows, Dashboard) لن يتعامل أبداً
مع تفاصيل القناة مباشرة - فقط مع هذا العقد الموحّد. هذا هو ما يجعل
النظام Channel-Agnostic: استبدال Telegram بـ WhatsApp لاحقاً يعني
كتابة كونيكتور جديد فقط، دون لمس أي منطق أعمال.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Any, List

import privacy

# [S12] لماذا `repr=False` و`__repr__` مكتوبة بدل المولَّدة
# ----------------------------------------------------------------------
# حجب مواضع الطباعة يغلق ما نطبعه اليوم. و`__repr__` المولَّدة تُبقي
# الباب مفتوحاً لكل ما قد يُطبع غداً: `print(message)`، سلسلة f، `%r`،
# استثناء يُبنى بالكائن (`raise ValueError(f"...{message}")`)، أو
# `pytest --showlocals`. أيٌّ منها كان سيُخرج النص كاملاً دون المرور
# بأي سطر عالجناه.
#
# `raw` كان `repr=False` أصلاً؛ و`text` لم يكن. الآن النص محجوب في
# التمثيل نفسه، فالتسريب يصير مستحيلاً بالبنية لا ممنوعاً بالانضباط.
# هذا هو جواب «ماذا عن الـtraceback»: أثر بايثون لا يطبع القيم
# المحلية أصلاً، والمنفذ الحقيقي كان التمثيل - وقد أُغلق.


@dataclass(repr=False)
class IncomingMessage:
    """رسالة موحّدة قادمة من أي قناة - بغض النظر عن مصدرها"""
    channel: str            # "telegram" | "whatsapp" | "instagram" | "facebook"
    user_id: str            # معرف المستخدم داخل تلك القناة
    text: str                # نص الرسالة
    timestamp: datetime
    message_id: Optional[str] = None  # معرف فريد للرسالة داخل قناتها - يُستخدم لمنع المعالجة المكررة
    raw: Optional[Any] = field(default=None, repr=False)  # الحمولة الأصلية الخام (للتصحيح فقط)

    def __repr__(self) -> str:
        return (
            f"IncomingMessage(channel={self.channel!r}, user_id={self.user_id!r}, "
            f"text={privacy.redact(self.text)}, timestamp={self.timestamp!r}, "
            f"message_id={self.message_id!r})"
        )


@dataclass(repr=False)
class OutgoingMessage:
    """رسالة موحّدة صادرة إلى أي قناة"""
    user_id: str
    text: str
    quick_replies: Optional[List[str]] = None  # أزرار سريعة اختيارية (تدعمها أغلب القنوات)
    # [PRD D3] معرّف الصياغة المعتمدة التي أنتجت `text` (variants.py).
    # القناة لا تقرأه ولا ترسله - هو للسجل وحده، ليُربط كل صادر بنتيجته.
    # None ممكن تقنياً (رسالة بلا صياغة مسجّلة) ولا يمنع الإرسال، لكنه
    # يُنتج تحذيراً مرئياً في outbound.py - انظر [VARIANT-MISSING] هناك.
    variant_id: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"OutgoingMessage(user_id={self.user_id!r}, text={privacy.redact(self.text)}, "
            f"quick_replies={self.quick_replies!r}, variant_id={self.variant_id!r})"
        )


@dataclass(frozen=True, repr=False)
class ReplyDecision:
    """
    جواب طبقة منطق العمل على رسالة واردة: العقد بين `handler`
    وMessageRouter.

    يعيش هنا بجوار IncomingMessage/OutgoingMessage لأنه ثالث عقود
    الرسائل، وليجد Router نوعه دون أن يستورد business_logic - فيبقى
    الموجّه مستقلاً عن أي منطق أعمال بعينه كما كان.

    `lead_id` حاضر لأن مسار الإرسال يحتاجه لنسب الرسالة الصادرة إلى
    الـLead الذي أنتجها. لا يعبر إلى القناة إطلاقاً: الذي يُسلَّم
    للقناة هو OutgoingMessage وحده، وهو لا يحمله. None حين لا Lead
    بعد (رسالة ترحيب قبل أي استفسار سعر، أو رد خطأ).
    """
    text: str
    variant_id: Optional[str]
    lead_id: Optional[str]
    rule_decision: str

    def __repr__(self) -> str:
        return (
            f"ReplyDecision(text={privacy.redact(self.text)}, "
            f"variant_id={self.variant_id!r}, lead_id={self.lead_id!r}, "
            f"rule_decision={self.rule_decision!r})"
        )


class MessagingChannel(ABC):
    """
    العقد الذي يجب أن تلتزم به أي قناة تواصل جديدة.
    أي كونيكتور جديد (WhatsApp, Instagram, Facebook) يرث من هذا
    الصنف فقط، ويطبّق هذه الدوال الثلاث - دون تغيير أي شيء آخر
    في النظام.
    """

    channel_name: str = "generic"

    @abstractmethod
    def start_listening(self, on_message: Callable[[IncomingMessage], None]) -> None:
        """يبدأ الاستماع للرسائل الواردة، ولكل رسالة يستدعي on_message"""
        raise NotImplementedError

    @abstractmethod
    def stop_listening(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_message(self, message: OutgoingMessage) -> bool:
        """يرسل رسالة صادرة عبر هذه القناة. يرجع True عند النجاح"""
        raise NotImplementedError