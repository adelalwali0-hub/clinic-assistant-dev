# مسرد المصطلحات — Clinic Assistant Dev

> آخر مراجعة: 2026-08-15
> المصدر: `AGENTS.md` + Audit مراجعة فقط

---

## A

### AI Override
تأثير نية GPT على قرار Business Logic. **مُفعَّل فقط** عند `USE_AI_INTENT=true` وداخل `awaiting_booking_confirmation` وللنوايا الثلاث الآمنة فقط. خارج هذا النطاق: Rule-Based.

### AI Understanding Layer
`ai_understanding.py` — طبقة تصنيف النية عبر GPT-4o-mini. تُرجع JSON: `intent` + `service_mentioned`. ليست Business Truth.

### `ask_more_info`
نية GPT: العميلة تسأل عن **تفاصيل الخدمة** (وليس السعر أو الحجز). **موجودة في classifier فقط** — **غير مُفعَّلة تجارياً** في Business Logic (مؤجلة بقرار صريح).

---

## B

### Business Logic / Business Truth
`business_logic.py` — مصدر الحقيقة للقرارات والأسعار (via `services.py`) والحجوزات وLeads وتغييرات session state.

---

## C

### Channel Layer
`channel_interface.py` + كونيكتors القنوات (حالياً `telegram_channel.py`). طبقة Channel-Agnostic بين APIs القنوات ومنطق الأعمال.

### `combined_handler`
دالة orchestration في `main.py`: تستدعي GPT → (اختياري) AI override → `handle_message` → طباعة `[COMPARE]` → إرجاع الرد.

### `confirm_booking`
نية: موافقة على الحجز. Rule-Based via `CONFIRM_WORDS`؛ قابلة لـ AI override في Phase 3B.

---

## D

### `decline`
نية: رفض الحجز. Rule-Based via `DECLINE_WORDS`؛ قابلة لـ AI override في Phase 3B. يُسجَّل Lead بحالة `not_ready`.

---

## H

### `hesitant`
نية: تردد/حاجة للوقت — ليس رفضاً نهائياً. Rule-Based via `HESITANT_WORDS` (Phase 3A.1)؛ قابلة لـ AI override في Phase 3B. لا تغيّر session state ولا تُسجّل Lead.

---

## I

### IncomingMessage / OutgoingMessage
Dataclasses في `channel_interface.py` — العقد الموحد للرسائل الواردة/الصادرة بين أي قناة والـRouter.

### Idempotency (في Router)
منع معالجة الرسالة مرتين عبر `(channel, message_id)` في ذاكرة `MessageRouter` — **In-Memory فقط** (تُفقد بعد restart).

---

## L

### Lead
سجل استفسار/حجز في `leads.csv` عبر `leads_store.py`.
**حالات مُؤكَّدة:** `confirmed` | `not_ready`

---

## M

### Message Router
`message_router.py` — يستقبل `IncomingMessage`، يستدعي handler، يرسل `OutgoingMessage`. Hook اختياري `ai_understand` موجود لكن **غير مربوط** في `main.py` الحالي.

---

## O

### `other`
نية/قرار fallback عندما لا تطابق الرسالة فرعاً معروفاً.

---

## P

### Phase 3A
Shadow Mode — مقارنة AI مع القواعد دون تأثير على الرد (عند `USE_AI_INTENT=false`). **مكتملة في الكود.**

### Phase 3A.1
دعم Rule-Based لـ `hesitant`. **مكتملة في الكود.**

### Phase 3B
AI Intent Override محدود عبر `USE_AI_INTENT`. **مكتملة في الكود.**

### Phase 4
Session persistence محلي عبر `session_store` → `data/sessions.json`. **مكتملة في الكود.**

### `price_inquiry`
نية: استفسار عن سعر خدمة. **Rule-Based دائماً** — خارج نطاق AI override.

---

## R

### Rule-Based / القواعد الثابتة
منطق `_decide` في `business_logic.py` — keyword matching وحالات session. مصدر القرار عند `USE_AI_INTENT=false` أو خارج نطاق override.

### `rule_decision`
آخر قرار سجّلته القواعد بمعزل عن AI (`_last_rule_decisions`) — In-Memory، للمقارنة في `[COMPARE]`.

---

## S

### Session State
حالة محادثة العميلة. **المالك:** `session_store.py`.
**القيم المُؤكَّدة:**
- `idle`
- `awaiting_booking_confirmation`
- `awaiting_contact_info`

### Shadow Mode
انظر Phase 3A. مقارنة `rule_decision` vs `ai_intent` في `[COMPARE]` دون تغيير الرد (عند `USE_AI_INTENT=false`).

### `services.py`
مصدر بيانات الخدمات والأسعار (`SERVICES`, `find_service`, `CENTER_NAME`).

---

## U

### `USE_AI_INTENT`
متغير بيئة (`false` افتراضياً).
- false → لا يسمح لـAI بالتأثير على القرار؛ حالياً يبقى GPT يُستدعى للمقارنة، وهذا سلوك تنفيذي مؤقت وليس الهدف النهائي للـflag.
- true → AI override للنوايا الثلاث داخل awaiting_booking_confirmation.

---

## اختصارات ملفات

| الملف | الدور |
|-------|-------|
| `main.py` | نقطة التشغيل + `combined_handler` |
| `business_logic.py` | Business Truth |
| `ai_understanding.py` | GPT classifier |
| `storage/session_store.py` | مالك session state |
| `leads_store.py` | Leads CSV |
| `services.py` | خدمات/أسعار |
| `telegram_channel.py` | كونيكتور Telegram |
| `message_router.py` | توجيه + idempotency |
| `channel_interface.py` | العقد الموحد |

---

## مصطلحات مؤجلة / غير مُفعَّلة

| المصطلح | الحالة |
|---------|--------|
| `ask_more_info` (تجارياً) | **مؤجل** — classifier فقط |
| Supabase / SQLite | **غير موجود** في التصميم الحالي |
| Deployment / n8n | **مؤجل** — لم يُبدأ |
| `architecture.md` / `notes.md` | **لم تُنشأ** بعد في `.memory/` |
