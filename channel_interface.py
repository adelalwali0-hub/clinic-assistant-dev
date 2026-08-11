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


@dataclass
class IncomingMessage:
    """رسالة موحّدة قادمة من أي قناة - بغض النظر عن مصدرها"""
    channel: str            # "telegram" | "whatsapp" | "instagram" | "facebook"
    user_id: str            # معرف المستخدم داخل تلك القناة
    text: str                # نص الرسالة
    timestamp: datetime
    message_id: Optional[str] = None  # معرف فريد للرسالة داخل قناتها - يُستخدم لمنع المعالجة المكررة
    raw: Optional[Any] = field(default=None, repr=False)  # الحمولة الأصلية الخام (للتصحيح فقط)


@dataclass
class OutgoingMessage:
    """رسالة موحّدة صادرة إلى أي قناة"""
    user_id: str
    text: str
    quick_replies: Optional[List[str]] = None  # أزرار سريعة اختيارية (تدعمها أغلب القنوات)


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