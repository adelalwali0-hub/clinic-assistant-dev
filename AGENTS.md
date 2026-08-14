# Project: Clinic Assistant Dev

نظام مساعد محادثة للعيادات يركز حاليا على Telegram ويستخدم Business Logic
كـBusiness Truth مع طبقة AI Understanding محدودة التأثير.

## Memory map

| File | Contains | Load |
|---|---|---|
| `.memory/decisions.md` | القرارات المعمارية المهمة وسبب اتخاذها | always |
| `.memory/glossary.md` | المصطلحات والاختصارات الخاصة بالمشروع | always |
| `.memory/architecture.md` | خريطة المعمارية وتدفق البيانات | on demand |
| `.memory/notes.md` | ملاحظات ومعلومات لا تنتمي لملف آخر | on demand |

لا تنشئ `.memory/` حتى توجد أول كتابة فعلية للذاكرة.

## Active tasks

| Task | Status | Owner | Next step | Updated |
|---|---|---|---|---|
| Phase 3A  Shadow Mode | Completed | Team | لا يوجد | 2026-08-14 |
| Phase 3A.1  hesitant Rule-Based support | Completed | Team | لا يوجد | 2026-08-14 |
| Phase 3B  limited AI intent override | Completed | Team | لا يوجد | 2026-08-14 |
| Phase 4  Local Session Persistence | Completed | Team | لا يوجد | 2026-08-14 |
| Cursor + Agent Memory setup | In progress | Team | إكمال إعداد ذاكرة المشروع ثم Audit فقط | 2026-08-15 |

Current stable commit:
`229a8f1`  `Complete Phase 4 with persistent session storage`

## Update rules

- لا تعدل كود المشروع أو المعمارية أو إعدادات التشغيل اعتمادا على الذاكرة وحدها دون طلب صريح أو موافقة واضحة.
- أي قرار يغير طريقة العمل المستقبلية للمشروع يسجل في `.memory/decisions.md`.
- أي مصطلح أو اختصار جديد خاص بالمشروع يسجل في `.memory/glossary.md`.
- أي تغيير فعلي في المعمارية يسجل في `.memory/architecture.md`.
- الملاحظات الأخرى تذهب إلى `.memory/notes.md`.
- عند تغيير حالة مهمة في Active tasks حدث الجدول في نفس جلسة العمل.
- قبل حفظ أي تعديل على ملفات الذاكرة اعرض الـdiff أولا.
- لا تسجل أي تخمين على أنه حقيقة ميز بين الحقيقة والافتراض والسؤال المفتوح.
- لا تكتب أي API keys أو tokens أو passwords أو credentials في ملفات الذاكرة.

## Project constraints

- Business Logic هو مصدر الحقيقة للقرارات والأسعار والحجوزات والـLeads.
- `services.py` هو مصدر بيانات الخدمات والأسعار الحالية.
- GPT هو Conversation Intelligence وليس Business Truth.
- في Phase 3B تأثير AI محصور فقط في:
  - `confirm_booking`
  - `decline`
  - `hesitant`
- `price_inquiry` يبقى Rule-Based في هذه المرحلة.
- `ask_more_info` غير مفعل تجاريا حاليا ومؤجل بقرار صريح.
- GPT لا يملك صلاحية مباشرة لتغيير session state أو الأسعار أو تسجيل الـLeads.
- `storage/session_store.py` هو المالك الوحيد لـsession state.
- Session persistence حاليا محلي عبر `data/sessions.json`.
- لا Supabase ولا SQLite في التصميم الحالي.
- `data/` و`leads.csv` بيانات تشغيلية محلية ولا يجب رفعها إلى GitHub.
- لا تغير `Channel Layer` أو `Message Router` إلا عند وجود سبب معماري واضح وموافقة صريحة.
- قبل تنفيذ أي Phase جديدة افهم الحالة الحالية والاختبارات والـconstraints أولا.
- لا تبدأ `ask_more_info` أو Deployment أو n8n أو إعادة هيكلة كبيرة تلقائيا.

## Safe working mode

عند فتح المشروع لأول مرة في جلسة جديدة:
1. اقرأ `AGENTS.md`.
2. اقرأ ملفات `.memory/` الموسومة `always` عندما تصبح موجودة.
3. افهم الـcurrent phase والـstable commit.
4. لا تنفذ تعديلات مباشرة إذا كانت المهمة غير واضحة.
5. عند طلب Audit قدم التقرير أولا ولا تعدل الكود.
