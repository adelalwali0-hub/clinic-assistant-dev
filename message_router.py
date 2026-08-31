"""
Message Router - الموجّه المركزي
====================================
يستقبل رسائل من أي قناة عبر العقد الموحّد (IncomingMessage)، يمنع
معالجة أي رسالة مرتين (Idempotency)، يمرّرها لمنطق العمل (مصدر
الحقيقة)، **يحدد الرد الفعلي أولاً**، ثم (اختيارياً) يستدعي طبقة
مقارنة جانبية (Shadow) لأغراض الاختبار فقط، ثم يرسل الرد.

[Phase 3A] ترتيب التنفيذ الصارم:
Incoming Message -> Rule-Based Business Logic -> تحديد ReplyDecision
    -> (Shadow) AI Understanding + Compare -> إرسال الرد

ai_understand (اختياري) يُستدعى بعد حساب الرد وقبل الإرسال، لكنه لا
يملك أي وسيلة للتأثير على الرد أو حالة الجلسة أو leads.csv - فقط
طباعة مقارنة في الطرفية. أي فشل فيه لا يوقف إرسال الرد الفعلي.

[التغيير #5] الموجّه لم يعد يستدعي `channel.send_message` بنفسه:
الإرسال والتسجيل معاً في outbound.send، وهو نفسه المسار الذي تمر
منه المتابعات. قاعدة تخص الصادر تُكتب مرة واحدة الآن.

`handler` صار معاملاً إلزامياً: كان له افتراضي `stub_business_logic`
يردّ صدى الرسالة، وهو كود ميت (main.py يمرّر combined_handler دائماً)
ومصدر الالتباس الأصلي في v2. حُذف - كان المنتج الوحيد الباقي لنص
صادر بلا صياغة مسجّلة، ولا موضع في مكتبة نصوص معتمدة بشرياً (§16)
لردّ صدى تشخيصي.
"""

from typing import Callable, Optional

import outbound
import privacy
import variants
from channel_interface import (
    MessagingChannel,
    IncomingMessage,
    OutgoingMessage,
    ReplyDecision,
)

MessageHandler = Callable[[IncomingMessage], ReplyDecision]

FALLBACK_ERROR_VARIANT = "error_fallback.v1"


class MessageRouter:
    def __init__(
        self,
        channel: MessagingChannel,
        handler: MessageHandler,
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

        # [S12] نص الرسالة لا يُطبع. حين تكون الجلسة awaiting_contact_info
        # يكون هذا النص هو الاسم والرقم - انظر ترويسة privacy.py.
        print(f"[IN]  ({message.channel}) {message.user_id}: {privacy.redact(message.text)}")

        # 1) القواعد الثابتة تحدد الرد الفعلي أولاً وحصرياً
        try:
            decision = self.handler(message)
        except Exception as e:
            # `handler` يمرّ بمنطق العمل كله وقد يستدعي طبقة الفهم، ونص
            # العميلة يعبره - فرسالة استثنائه تُعامَل كأنها قد تحمله.
            print(
                f"[ERROR] فشل معالجة الرسالة من {message.user_id}: "
                f"{privacy.describe_error(e, may_carry_text=True)}"
            )
            # رد الخطأ صادر كأي صادر آخر: يحمل صياغته ويُسجَّل. lead_id
            # غير معروف هنا - العطل وقع قبل أن يُحدَّد أي Lead.
            decision = ReplyDecision(
                text=variants.render(FALLBACK_ERROR_VARIANT),
                variant_id=FALLBACK_ERROR_VARIANT,
                lead_id=None,
                rule_decision="error",
            )

        # 2) طبقة المقارنة الجانبية (Shadow) - لا تؤثر على الرد إطلاقاً
        if self.ai_understand is not None:
            try:
                self.ai_understand(message)
            except Exception as e:
                print(
                    "[AI Understanding] فشل غير متوقع في الطبقة الجانبية: "
                    f"{privacy.describe_error(e, may_carry_text=True)}"
                )

        # 3) إرسال الرد الفعلي عبر مسار الإرسال الموحّد (outbound.send):
        # هو الذي يستدعي القناة، يعزل فشلها، يطبع سطر [OUT]، ويُصدر
        # RESPONSE_SENT عند النجاح وحده. لا استدعاء مباشر للقناة هنا.
        outbound.send(
            self.channel,
            OutgoingMessage(
                user_id=message.user_id,
                text=decision.text,
                variant_id=decision.variant_id,
            ),
            lead_id=decision.lead_id,
        )

    def run(self) -> None:
        print(f"Message Router جاهز ويستمع على قناة: {self.channel.channel_name}")
        self.channel.start_listening(self._on_message)