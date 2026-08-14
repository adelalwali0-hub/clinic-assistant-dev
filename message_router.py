"""
Message Router - الموجّه المركزي
====================================
يستقبل رسائل من أي قناة عبر العقد الموحّد (IncomingMessage)، يمنع
معالجة أي رسالة مرتين (Idempotency)، يمرّرها لمنطق العمل (مصدر
الحقيقة)، **يحدد الرد الفعلي أولاً**، ثم (اختيارياً) يستدعي طبقة
مقارنة جانبية (Shadow) لأغراض الاختبار فقط، ثم يرسل الرد.

[Phase 3A] ترتيب التنفيذ الصارم:
Incoming Message -> Rule-Based Business Logic -> تحديد reply_text
    -> (Shadow) AI Understanding + Compare -> إرسال الرد

ai_understand (اختياري) يُستدعى بعد حساب reply_text وقبل الإرسال،
لكنه لا يملك أي وسيلة للتأثير على reply_text أو حالة الجلسة أو
leads.csv - فقط طباعة مقارنة في الطرفية. أي فشل فيه لا يوقف إرسال
الرد الفعلي.
"""

from typing import Callable, Optional
from channel_interface import (
    MessagingChannel,
    IncomingMessage,
    OutgoingMessage,
)

MessageHandler = Callable[[IncomingMessage], str]


def stub_business_logic(message: IncomingMessage) -> str:
    return (
        f'وصلتني رسالتك: "{message.text}"\n'
        f"(رد تجريبي من طبقة القنوات - القناة: {message.channel})"
    )


FALLBACK_ERROR_REPLY = "عذراً، صار خطأ بسيط 🙏 حاولي ترسلين رسالتك مرة ثانية."


class MessageRouter:
    def __init__(
        self,
        channel: MessagingChannel,
        handler: MessageHandler = stub_business_logic,
        ai_understand: Optional[Callable[[IncomingMessage], None]] = None,
    ):
        self.channel = channel
        self.handler = handler
        self.ai_understand = ai_understand
        # نتتبع (channel, message_id) لكل رسالة عولجت فعلاً - لمنع التكرار
        self._processed_message_ids: set[tuple[str, str]] = set()

    def _on_message(self, message: IncomingMessage) -> None:
        dedup_key = (message.channel, message.message_id)

        if message.message_id is not None:
            if dedup_key in self._processed_message_ids:
                print(f"[SKIP] رسالة مكررة تم تجاهلها - message_id={message.message_id}")
                return
            self._processed_message_ids.add(dedup_key)

        print(f"[IN]  ({message.channel}) {message.user_id}: {message.text}")

        # 1) القواعد الثابتة تحدد الرد الفعلي أولاً وحصرياً
        try:
            reply_text = self.handler(message)
        except Exception as e:
            print(f"[ERROR] فشل معالجة الرسالة من {message.user_id}: {e}")
            reply_text = FALLBACK_ERROR_REPLY

        # 2) طبقة المقارنة الجانبية (Shadow) - لا تؤثر على reply_text إطلاقاً
        if self.ai_understand is not None:
            try:
                self.ai_understand(message)
            except Exception as e:
                print(f"[AI Understanding] فشل غير متوقع في الطبقة الجانبية: {e}")

        # 3) إرسال الرد الفعلي (من القواعد الثابتة فقط)
        try:
            outgoing = OutgoingMessage(user_id=message.user_id, text=reply_text)
            success = self.channel.send_message(outgoing)
            status = "ok" if success else "FAILED"
            print(f"[OUT] -> {message.user_id}: {reply_text} ({status})")
        except Exception as e:
            print(f"[ERROR] فشل إرسال الرد إلى {message.user_id}: {e}")

    def run(self) -> None:
        print(f"Message Router جاهز ويستمع على قناة: {self.channel.channel_name}")
        self.channel.start_listening(self._on_message)