"""
Telegram Connector
===================
أول "كونيكتور" فعلي في النظام. يطبّق عقد MessagingChannel لقناة
Telegram فقط، ولا يحتوي على أي منطق أعمال - فقط يترجم بين Telegram
API والعقد الموحّد (IncomingMessage / OutgoingMessage).

يملأ message_id بمعرّف update_id الخاص بتيليجرام - يُستخدم من قبل
Message Router لمنع معالجة أي رسالة مرتين، مهما كان سبب التكرار
(إعادة إرسال من الشبكة، إعادة تشغيل، أو غيره).
"""

import time
from datetime import datetime
from typing import Callable, Optional

import requests

import privacy
from channel_interface import (
    MessagingChannel,
    IncomingMessage,
    OutgoingMessage,
)


class TelegramChannel(MessagingChannel):
    channel_name = "telegram"

    def __init__(self, bot_token: str, poll_interval: float = 1.0):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.poll_interval = poll_interval
        self._last_update_id: Optional[int] = None
        self._running = False

    def start_listening(self, on_message: Callable[[IncomingMessage], None]) -> None:
        self._running = True
        self._poll_loop(on_message)  # يبقي العملية حية (سكربت تطويري)

    def stop_listening(self) -> None:
        self._running = False

    def send_message(self, message: OutgoingMessage) -> bool:
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={"chat_id": message.user_id, "text": message.text},
                timeout=10,
            )
            return resp.status_code == 200
        except requests.RequestException as e:
            # `base_url` يحمل التوكن، و`requests` يضع الـURL كاملاً في نص
            # الاستثناء. التنقية غير مشروطة بأي راية - انظر privacy.py.
            print(f"[TelegramChannel] فشل الإرسال: {privacy.describe_error(e)}")
            return False

    def _poll_loop(self, on_message: Callable[[IncomingMessage], None]) -> None:
        print("[TelegramChannel] بدء الاستماع (Long Polling)...")
        while self._running:
            try:
                params = {"timeout": 30}
                if self._last_update_id is not None:
                    params["offset"] = self._last_update_id + 1

                resp = requests.get(
                    f"{self.base_url}/getUpdates",
                    params=params,
                    timeout=(10, 40),
                )
                data = resp.json()

                if not data.get("ok"):
                    # الجسم الخام لا يُطبع: `getUpdates` الفاشل يُرجع وصفاً
                    # اليوم، لكنه مخرَج API غير محدود لا نملك شكله.
                    # `description` وحده هو ما يُشخِّص، وهو من تلغرام لا
                    # من العميلة.
                    print(
                        "[TelegramChannel] خطأ من Telegram: "
                        f"{privacy.scrub_secrets(str(data.get('description', 'بلا وصف')))}"
                    )
                    time.sleep(self.poll_interval)
                    continue

                for update in data.get("result", []):
                    self._last_update_id = update["update_id"]
                    msg = update.get("message")
                    if not msg or "text" not in msg:
                        continue
                    incoming = IncomingMessage(
                        channel=self.channel_name,
                        user_id=str(msg["chat"]["id"]),
                        text=msg["text"],
                        timestamp=datetime.fromtimestamp(msg["date"]),
                        message_id=str(update["update_id"]),
                        raw=update,
                    )
                    on_message(incoming)

            except requests.exceptions.ReadTimeout:
                continue
            except requests.RequestException as e:
                print(f"[TelegramChannel] خطأ في الاستطلاع: {privacy.describe_error(e)}")
                time.sleep(self.poll_interval)