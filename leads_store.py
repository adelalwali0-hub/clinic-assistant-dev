"""
تسجيل طلبات الحجز والاستفسارات (Leads) + محرك Lead Recovery
======================================================================
حفظ بسيط في ملف CSV. يسجل كل استفسار، مع سعر الخدمة كما كان وقت
إنشاء السجل (Snapshot)، ويدير دورة حياة المتابعة على مرحلتين:

  price_quoted --24h--> Follow-up 1 --72h--> Follow-up 2 --> Recovered/Expired

[مفردات الحالة - PRD §8/D2]
عمود "الحالة" يحمل حالة الـLead في دورة الحياة (§7) بمصطلحات §8:
  price_quoted      : سُعِّرت، لم تُجب بعد (Qualified Lead)
  declined          : رفضت صراحةً
  booking_requested : سلّمت بياناتها - Booking Request، *ليس* حجزاً مؤكداً
  legacy_unknown    : صف ما قبل هذه المواءمة، لا دليل على سبب حالته

القيمتان القديمتان `confirmed` و`not_ready` حُذفتا: كلتاهما كانت تقيس
شيئاً غير ما تسمّيه (F2). `confirmed` كان يعني "أرسلت رقمها" بينما
Confirmed Booking في §8 هو تأكيد الموظفة - حدث خارج النظام كلياً.

"نتيجة المتابعة" (الإسناد §9.1 + الانتهاء §7):
  ""        : لم يُحسم بعد
  "مسترجَع"  : حجزت بعد متابعة واحدة على الأقل (followup_assisted)
  "عضوي"    : حجزت مباشرة قبل أي متابعة (organic) - كانت "أُغلق"،
              وهو اسم يوحي بفرصة خاسرة بينما هي حجز ناجح بلا فضل لنا
  "منتهي"   : وصلت للمتابعة الثانية دون حجز

[لا إيراد قبل الحضور - PRD §8، القاعدة الحمراء]
compute_funnel_metrics() لا تُسمّي أي رقم "إيراداً". الطبقات الثلاث
العليا تحمل أسماءها الكاملة (Potential / Requested / Booked)، وطبقتا
Booked Revenue وRevenue تُرجَعان None لا صفراً: الصفر قياسٌ يقول
"قِسنا فوجدنا لا شيء"، وNone يقول "لا بيانات" - وهذا هو الصدق الوحيد
الممكن اليوم، إذ لا بيانات حضور في النظام ولا مسار للحصول عليها قبل
Clinic Feedback Loop (§11).

[الموافقة التسويقية ونافذة التواصل - PRD §19]
عمودان منفصلان قصداً، لأنهما شيئان مختلفان لا يجوز خلطهما:

  consent_status            : موافقة تسويقية - تُمنَح صراحةً وتبقى حتى
                              تُسحَب. قيمها اليوم:
                                none           : لم تُطلب ولم تُمنَح
                                legacy_unknown : صف كُتب قبل وجود العمود
  contact_window_opened_at  : طابع زمني للحظة التي فتحت فيها رسالتها
                              نافذة الخدمة. ليست حالة: النافذة زمن،
                              وحقل نصي يقول "مفتوحة" يصير كذباً بعد ساعة.

الصف المكتوب اليوم يحمل `none` دائماً: عميلاتنا يبدأن التواصل ليسألن
عن خدمة أو ليحجزن، ولم يوافقن على شيء وراء ذلك التبادل. لا مسار في
النظام كله يطلب موافقة تسويقية (§19: لا يُبنى تدفّق موافقة الآن)،
فأي قيمة أخرى ادّعاء. و«راسلتنا أولاً» ليست قيمة في هذا العمود
إطلاقاً - هي بالضبط ما يحمله العمود الثاني بطابع زمني.

`granted` و`withdrawn` هما الاسمان المقصودان حين يوجد مسار opt-in
حقيقي؛ لا يُعرَّفان اليوم لأن لا شيء يكتبهما.

**لا شيء يقرأ العمودين اليوم.** لا أهلية متابعة ولا توقيت ولا إرسال
يتغيّر بسببهما. وُجِدا لأن كتابتهما بأثر رجعي مستحيلة (§19: رخيص
الآن، مستحيل بأثر رجعي): كل يوم تشغيل يضيف صفوفاً لا سبيل لمنحها
قيمة صادقة لاحقاً.

الطابع الزمني **حدٌّ أدنى** للحظة الفتح الحقيقية: يُكتب عند إنشاء
الصف، ويُحدَّث في المسارات التي تكتب الصف أصلاً بفعل رسالة منها
(record_booking_request / record_decline / record_hesitation). رسالة
واردة لا تكتب صفاً (تحية، سؤال استيضاح، إعادة سؤال البيانات، سؤال
سعر مكرر على Lead قائم) لا تُحرّكه، فالنافذة المحسوبة منه تُغلق
مبكراً لا متأخراً. اتجاه الخطأ مقصود وهو عكس اختيار D-019 (عمر
الجلسة من mtime = حدّ أعلى) للسبب نفسه: يُختار الاتجاه الذي لا يسمح
خطؤه بإرسال رسالة لا نملك حق إرسالها.

[الـHoldout والحضور - PRD §10 وD7، §11]
عمودان جديدان، ولا شيء يقرأ أحدهما في وضع اليوم:

  holdout_flag      : "" | control | treatment. الإسناد حتمي من
                      hash(lead_id)، ويقع **مرة واحدة عند UNBOOKED**
                      ولا يتغيّر أبداً (§10). النسبة معطى إعداد
                      (`holdout.percentage`) افتراضه صفر - أي لا تجربة
                      تعمل، فكل صف في الملف اليوم يحمل "".
  attendance_status : "" | attended | no_show. رصدٌ بشري من العيادة،
                      ولا مسار إدخال له اليوم (§11 لم تُبنَ نقطتا
                      لمسها). "" ليست "لم تحضر" أبداً.

القيم الثلاث في كل عمود لا قيمتان، وهو منطق D-016 حرفياً: "لم يُسنَد"
ليست "أُسنِد إلى المعالَجة"، و"لا رصد" ليست "لم تحضر". دمج أيّ زوج
منها يجعل سؤال «هل رُصد أم افتُرض؟» غير قابل للإجابة يوم نقيس.

الإسناد **عند UNBOOKED لا عند الإنشاء**: الإسناد عند الإنشاء يضع في
المجموعتين نساءً حجزن فوراً أو رفضن صراحةً ولم يدخلن دورة المتابعة
قط، فيقارن التقرير مجموعتين مختلفتي التركيب ويسمّي الفارق أثراً. انظر
ترويسة `assign_holdout_groups`.

الاستثناء من المتابعة سطر واحد في `get_leads_eligible_for_first_followup`
بجوار حاجز الإيقاف مباشرة - وهناك مكتوبٌ بالتفصيل لماذا يبقيان اثنين
ولا يُدمجان (قرار عميلة ≠ تصميم تجربة).

[إيقاف الأتمتة - PRD §18 حاجز S6]
الإيقاف **لا يسكن هذا الملف ولا أي عمود فيه**، بل
`storage/pause_store.py` مفتاحه `(channel, user_id)`. السبب أن الإيقاف
يخصّ إنساناً لا نيّة تجارية: عميلة لها ثلاثة استفسارات مفتوحة قالت
«لا تراسلوني» مرة واحدة تعني الثلاثة وأي رابع تفتحه غداً. عمودٌ في
هذا الملف كان سيجعل ذلك قاعدةً تُطبَّق بتوزيع القيمة على كل صفوفها
وبوراثتها في كل مسار إنشاء - أي انضباطاً بشرياً في أربعة مواضع. على
الهوية يصير بنيةً: لا صف يحمل القيمة، فلا صف يستطيع أن يفوته.

هذا الملف **يقرأ** الإيقاف ولا يكتبه إلا عبر الدالتين الوحيدتين
(`pause_automation` / `resume_automation`)، وهما هنا لا في المتجر لأن
حدثَي §6 معلّقان على lead_id ولا يعرفهما متجرٌ لا يقرأ leads.csv.

القراءة في دوال الأهلية الثلاث: الصف الموقوف ليس مؤهلاً - لا لمتابعة
أولى ولا ثانية ولا لانتهاء. الثالثة ليست سهواً: `mark_expired` لا
ترسل شيئاً، لكن «منتهي» تعني في §7 أنها صمتت خلال متابعتين، وهو
ادّعاء كاذب عن عميلة لم تصلها متابعة قط. الصف الموقوف يبقى مفتوحاً -
وهذا أثر مقصود ومعروف على مقام القمع، مسجَّل في D-023.

[الهوية والمعرّف - PRD D3/D4]
كل صف يحمل `lead_id` مستقراً يُولَّد مرة واحدة فقط ولا يتغير أبداً
بعدها. كل دوال التعديل (mark_followup_sent, mark_expired) تُخاطب
الصف بـ`lead_id` وحده، لا بمفتاح مركّب من قيم قابلة للتكرار.

هوية العميل مفتاح مركّب (channel, external_user_id) = عمودا "القناة"
و"معرف العميل" معاً. لا Identity Resolution: نفس المعرّف على قناتين
مختلفتين عميلان مختلفان.

[إنشاء الـLead لحظة عرض السعر - PRD D1]
`record_price_quote()` هي مسار الإنشاء الحقيقي: تُستدعى لحظة الرد
بالسعر، لا عند تسليم البيانات. الصمت حالة مشروعة - الصف يُكتب فوراً
بـ`الحالة = price_quoted`، فيصير مؤهلاً لدورة المتابعة تلقائياً بعد
نافذة الصمت (SILENCE_WINDOW_HOURS، وهي نفسها عتبة أهلية المتابعة
الأولى - رقم واحد لا رقمان).

الردود اللاحقة تُحدِّث نفس الصف عبر lead_id ولا تُنشئ صفاً ثانياً:
  record_booking_request()  - وافقت وسلّمت بياناتها
  record_decline()          - رفضت صراحة (تنقل الحالة إلى declined)
  record_hesitation()       - ترددت (إشارة فقط، الحالة لا تتغير)

`save_lead()` تبقى كما هي حرفياً: تُلحق صفاً دون شرط. المنع من
التكرار يعيش في record_price_quote وحدها - وهذا مقصود، فاستفساران
في نفس الثانية عبر save_lead يبقيان Leadين منفصلين (PRD §6).

[النسخة الاحتياطية] قبل أول كتابة على leads.csv يُنسَخ الملف كما هو
إلى BACKUP_FILE وBACKUP_FILE_PRICE_QUOTE وBACKUP_FILE_STATUS_VOCABULARY
وBACKUP_FILE_CONSENT وBACKUP_FILE_HOLDOUT_ATTENDANCE، مرة واحدة فقط لكل
اسم، فيبقى لديك دائماً لقطة سليمة على القرص لكل تغيير يمسّ دلالة
الصفوف لا شكلها فقط.

[حماية التزامن] كل عملية تُعدِّل الملف (save_lead, mark_followup_sent,
mark_expired, الهجرة التلقائية) تُنفَّذ بالكامل (قراءة+تعديل+كتابة)
داخل قفل مزدوج:
  1) threading.Lock  - يحمي من تصادم خيوط متعددة داخل نفس العملية.
  2) قفل ملفي بسيط (lock file عبر إنشاء حصري) - يحمي من تصادم عمليتين
     منفصلتين تعملان بالتوازي على نفس leads.csv (مثل main.py مع
     send_followups.py يعمل يدوياً أو لاحقاً عبر جدولة خارجية مثل n8n).
هذا يمنع Lost Update: لا يعود ممكناً أن تُبنى عملية تعديل على قراءة
قديمة تجاوزها تعديل آخر حدث بالتوازي.

الكتابة نفسها Atomic (ملف مؤقت ثم استبدال ذري) لمنع تلف الملف عند
انقطاع مفاجئ أثناء الكتابة.
"""

import csv
import hashlib
import os
import re
import shutil
import time
import threading
import uuid
from datetime import datetime

import events
import settings
import variants  # لبصمة القالب في حدث المتابعة - وحدة أوراق بلا اعتماديات
from storage import pause_store

LEADS_FILE = "leads.csv"
LOCK_FILE = LEADS_FILE + ".lock"
BACKUP_FILE = LEADS_FILE + ".backup-pre-lead-id"
BACKUP_FILE_PRICE_QUOTE = LEADS_FILE + ".backup-pre-price-quote-lead"
BACKUP_FILE_STATUS_VOCABULARY = LEADS_FILE + ".backup-pre-status-vocabulary"
BACKUP_FILE_CONSENT = LEADS_FILE + ".backup-pre-consent"
BACKUP_FILE_HOLDOUT_ATTENDANCE = LEADS_FILE + ".backup-pre-holdout-attendance"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

LEAD_ID_COLUMN = "lead_id"
LEAD_ID_PREFIX = "ld_"

# ---------------------------------------------------------------- مفردات الحالة
# قيم عمود "الحالة" = حالة الـLead في دورة الحياة (PRD §7) بمصطلحات
# §8/D2 حرفياً. القيمتان السابقتان حُذفتا لأن كلتيهما كانت تكذب:
#   `confirmed` = "أرسلت رقم هاتفها"، بينما Confirmed Booking في §8
#                 محجوز لتأكيد الموظفة - وهو حدث لا يملكه النظام أصلاً.
#   `not_ready` = "قالت لا صراحةً" فقط، بينما Unbooked Lead في §8
#                 هو الصامت. الاسمان كانا يقيسان شيئاً غير ما يسمّيانه.
STATE_PRICE_QUOTED = "price_quoted"
STATE_DECLINED = "declined"
STATE_BOOKING_REQUESTED = "booking_requested"

# ليست حالة في §8 ولا تدّعي أنها كذلك: صف كُتب قبل مواءمة المفردات
# بقيمة `not_ready` وبلا status_reason، فلا دليل في الملف على أنه
# رفض صريح أم صمت بعد تسعير. تصنيفه تخميناً كان سيكتب ادّعاءً في
# بيانات سنقيس عليها لاحقاً. يبقى مؤهلاً للمتابعة كما كان بالضبط،
# ولا يدخل أي مقام في القياس (is_unbooked تستثنيه).
STATE_LEGACY_UNKNOWN = "legacy_unknown"

# الحالات التي ما زال الـLead فيها داخل قمع المتابعة. `declined` منها
# عمداً: الرافضة صراحةً ما زالت تتلقى متابعات آلية اليوم (D-015، تأجيل
# صريح يمسّ S7). هذا يحفظ سلوك المتابعة كما هو حرفياً بعد تغيير الأسماء.
OPEN_STATES = (STATE_PRICE_QUOTED, STATE_DECLINED, STATE_LEGACY_UNKNOWN)

# الحدث المقابل للحالة التي يكتبها مسار السقوط الآمن save_lead. حالة
# خارج هذا الجدول (legacy_unknown أو قيمة من مستدعٍ خارجي) تُنتج
# LEAD_CREATED وحده: صف أُنشئ فعلاً، وحالته لا تُترجم إلى انتقال في
# §6 - وتخمين انتقال لها يكتب ادّعاءً في السجل الذي سنقيس عليه.
_STATE_TO_EVENT = {
    STATE_PRICE_QUOTED: events.PRICE_QUOTED,
    STATE_DECLINED: events.DECLINED,
    STATE_BOOKING_REQUESTED: events.BOOKING_REQUESTED,
}

# قيم عمود "نتيجة المتابعة" = الإسناد (PRD §9.1) + الانتهاء (§7).
OUTCOME_PENDING = ""
OUTCOME_RECOVERED = "مسترجَع"      # followup_assisted - Recovered Lead (§8)
OUTCOME_ORGANIC = "عضوي"           # organic (§9.1) - حجزت بلا فضل للمتابعة
OUTCOME_EXPIRED = "منتهي"          # EXPIRED (§7)

# نافذة الصمت (PRD §8): بعدها يصير الـLead المُسعَّر الصامت Unbooked.
# نفس عتبة الأهلية للمتابعة الأولى - رقم واحد باسمه الصريح، لا رقمان.
#
# صار معطى إعداد لا ثابتاً (§19): تُضبط في
# config/runtime_config.json تحت channels.<القناة>.followup. القيمة
# الافتراضية عند غياب المفتاح هي 24 نفسها، فسلوك أي تركيب لا يضبطها لم
# يتغيّر. تُقرأ مرة عند الاستيراد، وهي القيمة التي تُثبَّت في الوسائط
# الافتراضية لدوال الأهلية أدناه.
SILENCE_WINDOW_HOURS = settings.SILENCE_WINDOW_HOURS

# نسبة المجموعة الضابطة (§10، D7) - معطى إعداد، افتراضه صفر.
# صفر = لا تجربة تعمل: `assign_holdout_groups` تخرج قبل أي قراءة، فلا
# صفٌّ يُسنَد ولا حدث يُصدَر ولا صفٌّ يُستثنى من المتابعة. هذه قيمة
# اليوم، وهي ما يجعل هذا التغيير بلا أثر سلوكي إطلاقاً.
HOLDOUT_PERCENTAGE = settings.HOLDOUT_PERCENTAGE

# حقل تقني بقيم إنجليزية - كما lead_id تماماً. يسجّل *الإشارة الأخيرة*
# من العميلة، وهي سؤال مختلف عن سؤال عمود "الحالة": `hesitant` إشارة
# لا حالة (لا مقابل لها في §7)، والصف يبقى price_quoted بعدها.
# هذه القيم هي التي ستُحمَل لاحقاً في events.jsonl كما هي.
STATUS_REASON_COLUMN = "status_reason"
REASON_PRICE_QUOTED = "price_quoted"
REASON_DECLINED = "declined"
REASON_HESITANT = "hesitant"
REASON_BOOKING_REQUESTED = "booking_requested"

# ------------------------------------------- الموافقة التسويقية (PRD §19)
# حقلان تقنيان بقيم إنجليزية - كما lead_id وstatus_reason. الفصل بينهما
# هو كامل الفكرة: الأول إذنٌ يدوم حتى يُسحَب، والثاني زمنٌ ينقضي وحده.
CONSENT_COLUMN = "consent_status"

# القيمة الوحيدة التي يكتبها أي مسار حيّ اليوم: لم نطلب موافقة تسويقية
# قط، فلم تُمنَح. متابعتنا (رسالة لمن لم تحجز، بعد النافذة، لم تطلبها)
# تسويقية بتصنيف Meta - و§10 يسمّيها بذلك حرفياً: «المتابعة التسويقية
# غير المطلوبة». `none` تقول هذا بلا تجميل.
CONSENT_NONE = "none"

# صف كُتب قبل وجود العمود. ليس `none`: القيمة التي يكتبها مسار حيّ
# تؤكّد أن الصف مرّ على كود لا يطلب موافقة لحظة كتابته، أما الصف
# القديم فلم يُلاحَظ على هذا المحور إطلاقاً. دمجهما في رمز واحد يجعل
# سؤال «هل رُصد هذا الصف أم افتُرض؟» غير قابل للإجابة لاحقاً - وهو
# نفس ضرر D-016. أثرهما التشغيلي واحد: **لا إرسال تسويقي**؛
# `legacy_unknown` لا تُقرأ «ربما وافقت» أبداً.
CONSENT_LEGACY_UNKNOWN = "legacy_unknown"

# لحظة فتح نافذة الخدمة برسالة منها - طابع زمني بصيغة TIMESTAMP_FORMAT،
# لا حالة. "" = لا سجل (صف ما قبل العمود). ولا تُشتق من "التاريخ
# والوقت" لصف قديم: وقت الإنشاء ≥ وقت رسالتها، فاشتقاقه يدفع انتهاء
# النافذة إلى الأمام - أي يزعم نافذة مفتوحة بعد إغلاقها الحقيقي.
CONTACT_WINDOW_COLUMN = "contact_window_opened_at"

# ------------------------------------------------- الـHoldout (PRD §10، D7)
# اسم العمود من §6 حرفياً. القيم **ثلاث لا اثنتان**، وهذا تصحيح موثّق
# على §6 بلا تعديل على PRD.md: "flag" بقيمتين لا يفرّق بين «لم يُسنَد»
# و«أُسنِد إلى المعالَجة»، وهما واقعتان مختلفتان تماماً. الفرق هو منطق
# D-016 حرفياً - لا تتقاسم «لم يُرصد» و«رُصد فوجدناه كذا» قيمة واحدة.
HOLDOUT_COLUMN = "holdout_flag"

# اسما المجموعتين من §9.2 نفسه (ضابطة / معالَجة).
HOLDOUT_CONTROL = "control"        # لا تتلقى أي متابعة آلية أبداً
HOLDOUT_TREATMENT = "treatment"    # تتلقى دورة المتابعة كاملة

# "" = لم يُسنَد. ثلاثة أسباب مشروعة تماماً لها، ولا رابع:
#   1) النسبة صفر - لا تجربة تعمل أصلاً (وضع اليوم: كل الصفوف هكذا)
#   2) الـLead لم يبلغ UNBOOKED قط (حجزت فوراً، أو ما زالت داخل النافذة)
#   3) هويتها موقوفة (S6) - فهي خارج التجربة بمجموعتيها، انظر أدناه
HOLDOUT_UNASSIGNED = ""

# ------------------------------------------- الحضور (PRD §11، §9.3)
# عمود واحد بقيم إنجليزية - كما consent_status. `الحالة` **لا تتغيّر**
# عند تسجيل الحضور: لا حالة `completed` في دورة الحياة اليوم. إضافتها
# تحرّك OPEN_STATES وis_unbooked ومقام كل نسبة في compute_funnel_metrics،
# وهذا التغيير لا يمسّ أي مقام (انظر ترويسة الملف).
ATTENDANCE_COLUMN = "attendance_status"

ATTENDANCE_ATTENDED = "attended"   # العيادة أكّدت الحضور -> BOOKING_COMPLETED
ATTENDANCE_NO_SHOW = "no_show"     # العيادة قالت لم تحضر  -> NO_SHOW

# "" = لا رصد. الموعد لم يقع بعد، أو لم يُسأل أحد، أو سُئل ولم يُجب.
# **لا تُقرأ «لم تحضر» أبداً**: من لم تحضر لا تترك أثراً اليوم (§11)،
# فالغياب في هذا العمود غياب معلومة لا معلومة غياب. `no_show` رصدٌ
# موجب يكتبه إنسان رأى، وهو الطلب الحقيقي الوحيد في §11.
ATTENDANCE_NONE = ""

# لا `legacy_unknown` هنا - وهذا فرق مقصود عن consent_status (D-021).
# هناك كان `none` ادّعاءً يستطيع مسار حيّ أن يقوله («مرّ هذا الصف على
# كود لا يطلب موافقة»)، فاختلف عن الصف الذي لم يُلاحَظ أصلاً. هنا لا
# مسار حيّ يكتب شيئاً إطلاقاً: صف اليوم وصف الأمس كلاهما غير مرصود
# بالتساوي، ورمزان لواقعة واحدة طقسٌ لا صدق.
#
# `lapsed` (§11: «عدم الرد حالة صريحة: LAPSED أو غير مثبت - وليس
# افتراض نجاح») **مقصود ولا يُعرَّف اليوم** - نفس سابقة granted/withdrawn
# في D-021: لا شيء يكتبه. يوجد يوم توجد نقطة لمس سألت ولم تتلقَّ جواباً،
# وعندها يكون الفرق بينه وبين "" فرقاً حقيقياً: سُئلنا فصمتنا، لا لم نسأل.

FIELDNAMES = [
    LEAD_ID_COLUMN,
    "التاريخ والوقت",
    "معرف العميل",
    "القناة",
    "الخدمة المطلوبة",
    "الحالة",
    STATUS_REASON_COLUMN,
    "بيانات التواصل",
    "سعر الخدمة وقت الإنشاء",
    "مرحلة المتابعة",
    "تاريخ آخر متابعة",
    "نتيجة المتابعة",
    CONSENT_COLUMN,
    CONTACT_WINDOW_COLUMN,
    HOLDOUT_COLUMN,
    ATTENDANCE_COLUMN,
]

# بنية V1 القديمة (7 أعمدة). وجود عمودها المميز في ترويسة الملف هو
# الدليل الوحيد على أن الهجرة تجري من V1 وليس من بنية أحدث.
_V1_LEGACY_COLUMN = "تمت المتابعة"

_LEGACY_FIELDNAMES = [
    "التاريخ والوقت", "معرف العميل", "القناة", "الخدمة المطلوبة",
    "الحالة", "بيانات التواصل", _V1_LEGACY_COLUMN,
]

# ------------------------------------------------- مفردات ما قبل المواءمة
# `not_ready` وحدها لا تكفي لتحديد الحالة الجديدة: كانت تُكتب للرفض
# الصريح وللصمت بعد التسعير معاً. status_reason هو الدليل الوحيد في
# الملف، وحين يكون فارغاً لا دليل إطلاقاً -> STATE_LEGACY_UNKNOWN.
_LEGACY_STATE_NOT_READY = "not_ready"
_LEGACY_STATE_CONFIRMED = "confirmed"
_LEGACY_OUTCOME_ORGANIC = "أُغلق"

_LEGACY_NOT_READY_BY_REASON = {
    REASON_DECLINED: STATE_DECLINED,
    REASON_PRICE_QUOTED: STATE_PRICE_QUOTED,
    REASON_HESITANT: STATE_PRICE_QUOTED,
}

_thread_lock = threading.Lock()


class _CrossProcessLock:
    """
    قفل بسيط عابر للعمليات، بدون أي مكتبة خارجية: يحاول إنشاء ملف
    LOCK_FILE حصرياً (يفشل إن كان موجوداً بالفعل من عملية أخرى تعمل
    حالياً)، مع إعادة محاولة قصيرة حتى مهلة زمنية معقولة. يُحذف الملف
    عند الخروج من الـwith دائماً (حتى عند حدوث خطأ).
    """

    def __init__(self, path: str, timeout: float = 10.0, poll_interval: float = 0.05):
        self.path = path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._fd = None

    def __enter__(self):
        start = time.monotonic()
        while True:
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.monotonic() - start > self.timeout:
                    raise TimeoutError(
                        f"تعذر الحصول على قفل {self.path} خلال {self.timeout} ثانية - "
                        f"قد تكون هناك عملية أخرى عالقة تستخدم leads.csv."
                    )
                time.sleep(self.poll_interval)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is not None:
            os.close(self._fd)
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


def _locked():
    """
    يُستخدم كـ with _locked(): حول أي عملية قراءة+تعديل+كتابة كاملة.
    يضمن الترتيب: قفل الخيط أولاً (سريع، محلي)، ثم قفل الملف العابر
    للعمليات (الأبطأ نسبياً، يحمي من عمليات أخرى).
    """
    class _Combined:
        def __enter__(self):
            _thread_lock.acquire()
            self._cross = _CrossProcessLock(LOCK_FILE)
            self._cross.__enter__()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            try:
                self._cross.__exit__(exc_type, exc_val, exc_tb)
            finally:
                _thread_lock.release()
            return False

    return _Combined()


def _new_lead_id() -> str:
    """
    معرّف Lead مستقر. يُولَّد مرة واحدة فقط - عند كتابة صف جديد، أو
    عند هجرة صف قديم لا يحمل معرّفاً - ولا تكتبه أي دالة تعديل بعدها.

    عشوائي (uuid4) وليس مشتقاً من (القناة، العميل، الخدمة، الوقت)
    قصداً: الاشتقاق الحتمي يتصادم عند استفسارين في نفس الثانية من
    نفس العميل عن نفس الخدمة، وهما Leadان منفصلان حسب PRD §6.
    """
    return LEAD_ID_PREFIX + uuid.uuid4().hex


def _same_identity(row: dict, channel: str, user_id: str) -> bool:
    """
    مفتاح الهوية المركّب (channel, external_user_id) - PRD D4.
    لا Identity Resolution: نفس المعرّف الرقمي على قناتين مختلفتين
    عميلان مختلفان، ما لم يوجد دليل على العكس - ولا يوجد اليوم.
    """
    return row.get("القناة") == channel and row.get("معرف العميل") == user_id


def _lookup_current_price(service_name: str) -> str:
    try:
        from services import SERVICES
    except Exception:
        return ""
    for s in SERVICES:
        if s.get("name") == service_name:
            return s.get("price", "")
    return ""


def _parse_price_to_number(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", price_str or "")
    return int(digits) if digits else 0


def _read_all_rows_unlocked() -> list[dict]:
    """قراءة بدون قفل - تُستخدم داخلياً فقط من دوال تُمسك القفل بنفسها بالفعل."""
    if not os.path.isfile(LEADS_FILE):
        return []
    try:
        with open(LEADS_FILE, "r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def _backup_once_unlocked() -> None:
    """
    نسخ احتياطية تُنشأ مرة واحدة فقط لكل اسم، قبل أول كتابة على
    leads.csv بعد التغيير الذي يحمل ذلك الاسم.

    الشرط "مرة واحدة" مقصود: أول كتابة تحدث بعد تغيير ما هي كتابة
    هجرته، فيلتقط الملف الحالة السابقة له بالضبط. لو أُعيد النسخ عند
    كل كتابة لاحقة لضاعت تلك الحالة فوراً وصار الاسم كذباً.

    ولهذا السبب نفسه لكل تغيير اسمه: BACKUP_FILE موجود بالفعل من
    تغيير lead_id، فلو اكتفينا به لما التُقطت لقطة ما قبل إضافة
    status_reason إطلاقاً.

    لا يُنسَخ شيء إذا لم يكن هناك ملف أصلاً (تشغيل نظيف)، وفشل النسخ
    لا يُوقف الكتابة - يُطبع تحذير فقط، فالبيانات الحية أهم.
    """
    if not os.path.isfile(LEADS_FILE):
        return
    for backup_path, label in (
        (BACKUP_FILE, "lead_id"),
        (BACKUP_FILE_PRICE_QUOTE, "إنشاء الـLead لحظة عرض السعر"),
        (BACKUP_FILE_STATUS_VOCABULARY, "مواءمة مفردات الحالة مع PRD §8"),
        (BACKUP_FILE_CONSENT, "حقل الموافقة التسويقية ونافذة التواصل (§19)"),
        (BACKUP_FILE_HOLDOUT_ATTENDANCE, "حقلَي الـHoldout والحضور (§10، §11)"),
    ):
        if os.path.exists(backup_path):
            continue
        try:
            shutil.copy2(LEADS_FILE, backup_path)
            print(f"[leads_store] نسخة احتياطية لما قبل {label} -> {backup_path}")
        except OSError as e:
            print(f"[leads_store] تحذير: تعذّر إنشاء النسخة الاحتياطية {backup_path}: {e}")


def _write_all_rows_unlocked(rows: list[dict]) -> None:
    """
    كتابة ذرية (Atomic) بدون قفل - تُستخدم داخلياً فقط من دوال تُمسك
    القفل بنفسها بالفعل. هذه هي مسار الكتابة الوحيد على leads.csv،
    ولذلك تُستدعى منها النسخة الاحتياطية.
    """
    _backup_once_unlocked()
    tmp_path = LEADS_FILE + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp_path, LEADS_FILE)


def _remap_vocabulary(row: dict) -> dict:
    """
    يُرجع نسخة من الصف بمفردات §8، ولا يلمس أي حقل آخر.

    ثلاث قواعد فقط، كلها اشتقاق من دليل موجود في الملف - لا تخمين:
      confirmed         -> booking_requested   (الصف يعني: سلّمت بياناتها)
      أُغلق             -> عضوي                (نفس المعنى، اسم لا يكذب)
      not_ready         -> حسب status_reason، و legacy_unknown إن كان فارغاً

    أي قيمة أخرى تمرّ كما هي حرفياً: ملف كُتب بيد أو بنسخة لا نعرفها
    يبقى كما تركه صاحبه، ولا نخترع له تصنيفاً.
    """
    remapped = dict(row)

    state = remapped.get("الحالة", "")
    if state == _LEGACY_STATE_CONFIRMED:
        remapped["الحالة"] = STATE_BOOKING_REQUESTED
    elif state == _LEGACY_STATE_NOT_READY:
        reason = (remapped.get(STATUS_REASON_COLUMN) or "").strip()
        remapped["الحالة"] = _LEGACY_NOT_READY_BY_REASON.get(reason, STATE_LEGACY_UNKNOWN)

    if remapped.get("نتيجة المتابعة", "") == _LEGACY_OUTCOME_ORGANIC:
        remapped["نتيجة المتابعة"] = OUTCOME_ORGANIC

    return remapped


def _has_legacy_vocabulary(rows: list[dict]) -> bool:
    return any(_remap_vocabulary(row) != row for row in rows)


def _needs_migration(existing_fieldnames: list[str], rows: list[dict]) -> bool:
    """
    الملف بحاجة لهجرة إذا اختلفت ترويسته، أو وُجد فيه صف بلا lead_id،
    أو صف بـconsent_status فارغ، أو حمل صف واحد مفردات ما قبل §8.

    الترويسة وحدها لم تعد كافية دليلاً: هذه الهجرة تغيّر *قيماً* لا
    أعمدة، فملف بترويسة صحيحة تماماً قد يكون كله بمفردات قديمة.

    consent_status الفارغ يُشعل الهجرة لأن "" ليست قيمة في مفرداته
    إطلاقاً - هي عمود موجود لم يُملأ. أما contact_window_opened_at
    الفارغ فقيمة نهائية مشروعة ("لا سجل للحظة الفتح")، فلا يُشعل شيئاً:
    لو أشعلها لأُعيدت كتابة الملف عند كل قراءة بلا نهاية.

    holdout_flag وattendance_status الفارغان يتبعان النافذة لا الموافقة:
    كلاهما قيمة نهائية مشروعة تماماً - "لم يُسنَد" و"لا رصد" - وهي قيمة
    **كل صف في الملف اليوم** (النسبة صفر، ولا مسار حضور). إشعالهما كان
    سيعيد كتابة الملف عند كل قراءة إلى الأبد.
    """
    if existing_fieldnames != FIELDNAMES:
        return True
    if any(not (row.get(LEAD_ID_COLUMN) or "").strip() for row in rows):
        return True
    if any(not (row.get(CONSENT_COLUMN) or "").strip() for row in rows):
        return True
    return _has_legacy_vocabulary(rows)


def _migrate_file_if_needed_locked() -> None:
    """
    هجرة حافِظة للحقول من أي بنية سابقة إلى البنية الحالية:

      V1 (7 أعمدة، فيها "تمت المتابعة") -> الحالية
      V2 (10 أعمدة، بلا lead_id)        -> الحالية، بلا فقد أي حقل
      V3 (11 عموداً، بلا status_reason) -> الحالية، بلا فقد أي حقل
      V4 (نفس الأعمدة، مفردات ما قبل §8) -> الحالية، بإعادة تسمية القيم
      V5 (بلا عمودي §19)                 -> الحالية، بلا فقد أي حقل
      V6 (بلا عمودي §10/§11)             -> الحالية، بلا فقد أي حقل
      الحالية                            -> خروج فوري، بلا أي كتابة

    الأعمدة المستجدة تُملأ "" لكل صف قائم: صف كُتب قبل هذا التغيير
    لا يُعرَف سبب حالته، و"" تقول ذلك بصدق بدل تخمينه.

    استثناء واحد (V5): consent_status الفارغ يصير legacy_unknown، لأن
    "" في هذا العمود تحديداً تُقرأ لاحقاً "لم يُملأ بعد" لا "لا نعرف"،
    والفرق يظهر يوم نقيس. ولا يصير `none`: انظر تعليق الثابت أعلاه.
    contact_window_opened_at يبقى "" - لا سجل، ولا يُشتق من وقت
    الإنشاء لأن الاشتقاق يزعم نافذة أطول من الحقيقية.

    وعمودا V6 يبقيان "" بلا استثناء ولا اشتقاق. الـholdout تحديداً
    **لا يُحسب بأثر رجعي** لصف قديم رغم أن حسابه ممكن تماماً
    (`_holdout_group_for` دالة نقية تعمل على أي lead_id): إسنادٌ يُكتب
    اليوم لصفٍّ تلقّى متابعاته قبل شهر يزعم أنه كان في مجموعة، وهو
    ادّعاء عن الماضي لا رصد له. الصف القديم لم يكن في أي تجربة، و""
    تقول ذلك بصدق.

    مواءمة المفردات (V4) تغيّر *قيم* عمودين فقط - "الحالة" و"نتيجة
    المتابعة" - عبر _remap_vocabulary، وبما لا يخترع تصنيفاً لصف لا
    دليل عليه في الملف (يذهب إلى legacy_unknown).

    كل حقل يُنقَل كما هو عبر row.get(field). النسخة السابقة من هذه
    الدالة كانت تصفّر السعر ومرحلة المتابعة وتاريخها ونتيجتها لأنها
    تفترض أن أي ملف غير مطابق للترويسة هو V1 - وهذا يفقد بيانات V2
    بالكامل لحظة إضافة أي عمود جديد.

    idempotent: أي lead_id موجود لا يُعاد توليده أبداً، فتشغيل الهجرة
    مرتين لا يغيّر معرّفاً واحداً.
    """
    if not os.path.isfile(LEADS_FILE):
        return

    try:
        with open(LEADS_FILE, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            existing_fieldnames = reader.fieldnames or []
            rows = list(reader)
    except (OSError, csv.Error):
        return

    if not _needs_migration(existing_fieldnames, rows):
        return

    is_v1 = _V1_LEGACY_COLUMN in existing_fieldnames

    migrated_rows = []
    for row in rows:
        migrated = {field: (row.get(field) or "") for field in FIELDNAMES}

        migrated[LEAD_ID_COLUMN] = migrated[LEAD_ID_COLUMN].strip() or _new_lead_id()
        migrated[CONSENT_COLUMN] = migrated[CONSENT_COLUMN].strip() or CONSENT_LEGACY_UNKNOWN

        if is_v1:
            migrated["مرحلة المتابعة"] = "1" if row.get(_V1_LEGACY_COLUMN) == "نعم" else "0"

        migrated_rows.append(_remap_vocabulary(migrated))

    _write_all_rows_unlocked(migrated_rows)
    print(
        f"[leads_store] تمت هجرة {LEADS_FILE} إلى البنية الحالية "
        f"({len(migrated_rows)} سجل، مع عمود {LEAD_ID_COLUMN})."
    )


def _read_all_rows() -> list[dict]:
    """قراءة عامة (تُستخدم من الدوال القرائية فقط) - تشمل الهجرة عند الحاجة، بقفل كامل."""
    with _locked():
        _migrate_file_if_needed_locked()
        return _read_all_rows_unlocked()


def _outcome_for_stage(row: dict) -> str:
    """
    الإسناد (PRD §9.1) من عدّاد المتابعات وقت الحجز: حجزت بعد متابعة
    واحدة على الأقل = مسترجَع (followup_assisted)، وإلا = عضوي
    (organic). القاعدة نفسها التي كانت مكرّرة حرفياً في save_lead
    وrecord_booking_request - نسخة واحدة تمنع انحرافهما عن بعضهما.
    """
    return OUTCOME_RECOVERED if row.get("مرحلة المتابعة", "0") in ("1", "2") else OUTCOME_ORGANIC


def save_lead(user_id: str, service_name: str, channel: str, status: str, contact_info: str = "") -> str:
    """
    يكتب صف Lead جديداً - دون شرط - ويُرجع lead_id المستقر الخاص به.

    لم تعد مسار الإنشاء الأساسي: `record_price_quote()` هي التي تُنشئ
    الـLead لحظة عرض السعر (PRD D1). تبقى هذه الدالة كما هي حرفياً
    لمسارين: السقوط الآمن في business_logic.py حين لا تحمل الجلسة
    lead_id (جلسة بدأت قبل هذا التغيير)، وأي استدعاء خارجي قائم.

    لا منع تكرار هنا بقصد: استفساران متتاليان عبر هذه الدالة يبقيان
    صفّين منفصلين. المنع من التكرار يعيش في record_price_quote وحدها.

    status_reason يُترك "" - صف كتبته هذه الدالة لا يحمل سبباً مسجّلاً،
    وهذا أصدق من تخمين سبب من قيمة `status`.

    القيمة المُرجَعة إضافة متوافقة رجعياً (كانت None): مواقع الاستدعاء
    الحالية في business_logic.py تتجاهلها دون أي تغيير، وهي المَعبر
    الذي ستستهلكه طبقة الأحداث لاحقاً.
    """
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()

        if status == STATE_BOOKING_REQUESTED:
            for row in rows:
                if (
                    _same_identity(row, channel, user_id)
                    and row.get("الخدمة المطلوبة") == service_name
                    and row.get("الحالة") in OPEN_STATES
                    and row.get("نتيجة المتابعة", "") == OUTCOME_PENDING
                ):
                    row["نتيجة المتابعة"] = _outcome_for_stage(row)

        lead_id = _new_lead_id()
        # طابع واحد للعمودين: الصف يُكتب رداً على رسالة منها، فلحظة
        # الإنشاء هي أقرب ما نملك للحظة فتح النافذة (حدّ أدنى لها).
        created_at = datetime.now().strftime(TIMESTAMP_FORMAT)
        new_row = {
            LEAD_ID_COLUMN: lead_id,
            "التاريخ والوقت": created_at,
            "معرف العميل": user_id,
            "القناة": channel,
            "الخدمة المطلوبة": service_name,
            "الحالة": status,
            STATUS_REASON_COLUMN: "",
            "بيانات التواصل": contact_info,
            "سعر الخدمة وقت الإنشاء": _lookup_current_price(service_name),
            "مرحلة المتابعة": "0",
            "تاريخ آخر متابعة": "",
            "نتيجة المتابعة": "",
            CONSENT_COLUMN: CONSENT_NONE,
            CONTACT_WINDOW_COLUMN: created_at,
            # لا إسناد ولا رصد عند الإنشاء - انظر record_price_quote.
            HOLDOUT_COLUMN: HOLDOUT_UNASSIGNED,
            ATTENDANCE_COLUMN: ATTENDANCE_NONE,
        }
        rows.append(new_row)
        _write_all_rows_unlocked(rows)

        # الإصدار بعد نجاح كتابة الصف وحده: كتابة فاشلة ترمي قبل هنا
        # فلا يُسجَّل انتقال لم يقع. مسار السقوط الآمن هذا يُنتج نفس
        # أحداث المسار الأساسي، فلا يختفي حجزٌ من القمع لأنه مرّ من هنا.
        base_payload = {
            "user_id": user_id,
            "service_name": service_name,
            "price": new_row["سعر الخدمة وقت الإنشاء"],
        }
        events.emit(events.LEAD_CREATED, lead_id=lead_id, channel=channel,
                    payload={**base_payload, "source": "save_lead"})
        state_event = _STATE_TO_EVENT.get(status)
        if state_event:
            events.emit(state_event, lead_id=lead_id, channel=channel,
                        payload={
                            **base_payload,
                            "followup_stage": new_row["مرحلة المتابعة"],
                            "outcome": new_row["نتيجة المتابعة"],
                            "contact_info_present": bool(contact_info),
                            "source": "save_lead",
                        })
        return lead_id


def _is_open_lead(row: dict) -> bool:
    """
    Lead "مفتوح" = نيّة تجارية لم تُحسم بعد: لا نتيجة متابعة (لا
    مسترجَع ولا عضوي ولا منتهي) ولم يُطلب حجزها.

    Lead محسوم لا يُعاد استخدامه: عميلة حجزت ثم عادت تسأل عن نفس
    الخدمة بعد شهر نيّة تجارية جديدة، لا استكمال للأولى (PRD §6).
    """
    return (
        row.get("نتيجة المتابعة", "") == OUTCOME_PENDING
        and row.get("الحالة") != STATE_BOOKING_REQUESTED
    )


def record_price_quote(user_id: str, service_name: str, channel: str) -> str:
    """
    ينشئ الـLead لحظة الرد بالسعر (PRD D1) ويُرجع lead_id المستقر.

    هذه هي اللحظة التي يصبح فيها الـLead مؤهلاً (Qualified Lead في
    PRD §8): وصل PRICE_QUOTED. الصمت بعدها حالة مشروعة - الصف موجود
    ويدخل دورة المتابعة وحده بعد نافذة الصمت، بلا أي فعل من العميلة.

    الحالة المكتوبة `price_quoted` هي حرفياً ما تعنيه (§7/§8): سُعِّرت
    ولم تُجب بعد. الصف الجديد (مرحلة 0، بلا نتيجة) يصير مؤهلاً للمتابعة
    بعد SILENCE_WINDOW_HOURS من الصمت بالضبط، ويخرج من الأهلية فور
    تحديثه إن ردّت قبلها.

    idempotent لكل نيّة تجارية مفتوحة: إن كان للعميلة نفسها Lead
    مفتوح لنفس الخدمة على نفس القناة، يُرجَع معرّفه بلا كتابة - نفس
    العميلة تسأل عن نفس الخدمة مرتين لا تُنتج صفين. لا يُحدَّث الطابع
    الزمني عند إعادة الاستخدام: تحديثه يدفع ساعة المتابعة للأمام كلما
    سألت، فلا يُتابَع الـLead أبداً.

    البحث من الأحدث للأقدم: الصف الأحدث هو النيّة الجارية فعلاً.
    """
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()

        for row in reversed(rows):
            existing_id = (row.get(LEAD_ID_COLUMN) or "").strip()
            if (
                existing_id
                and _same_identity(row, channel, user_id)
                and row.get("الخدمة المطلوبة") == service_name
                and _is_open_lead(row)
            ):
                # سُعِّرت مرة أخرى على نفس الـLead: PRICE_QUOTED يقع
                # فعلاً (الرد يحمل السعر)، وLEAD_CREATED لا يقع - لم
                # يُنشأ صف. هذا هو الموضع الوحيد في النظام الذي يعرف
                # الفرق، ولهذا يعيش الإصدار هنا لا في business_logic.
                events.emit(events.PRICE_QUOTED, lead_id=existing_id, channel=channel,
                            payload={
                                "user_id": user_id,
                                "service_name": service_name,
                                "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                                "lead_created": False,
                            })
                return existing_id

        lead_id = _new_lead_id()
        created_at = datetime.now().strftime(TIMESTAMP_FORMAT)
        new_row = {
            LEAD_ID_COLUMN: lead_id,
            "التاريخ والوقت": created_at,
            "معرف العميل": user_id,
            "القناة": channel,
            "الخدمة المطلوبة": service_name,
            "الحالة": STATE_PRICE_QUOTED,
            STATUS_REASON_COLUMN: REASON_PRICE_QUOTED,
            "بيانات التواصل": "",
            # لقطة السعر لحظة *عرضه* على العميلة فعلاً، لا لحظة كتابة
            # صف بعدها بيوم - وهو ما يفترضه اسم العمود أصلاً.
            "سعر الخدمة وقت الإنشاء": _lookup_current_price(service_name),
            "مرحلة المتابعة": "0",
            "تاريخ آخر متابعة": "",
            "نتيجة المتابعة": "",
            # السعر يُعرض رداً على سؤالها، فنافذتها مفتوحة الآن. والموافقة
            # التسويقية لم تُطلب هنا ولا في أي موضع (§19: لا تدفّق موافقة).
            CONSENT_COLUMN: CONSENT_NONE,
            CONTACT_WINDOW_COLUMN: created_at,
            # الـLead يُولد **خارج** التجربة. §10: الإسناد يقع عند
            # UNBOOKED وحده - أي بعد أن تصمت نافذة الصمت كاملةً، لا
            # لحظة التسعير. الفرق ليس توقيتاً بل مقام التقرير كله:
            # انظر ترويسة assign_holdout_groups.
            #
            # والحضور واقعة بعد موعد لم يُطلب حجزه بعد.
            HOLDOUT_COLUMN: HOLDOUT_UNASSIGNED,
            ATTENDANCE_COLUMN: ATTENDANCE_NONE,
        }
        rows.append(new_row)
        _write_all_rows_unlocked(rows)

        # حدثان في نفس اللحظة، وهما مختلفان قصداً (§6): D1 يجعل
        # الإنشاء والتسعير متزامنين اليوم، وهما ينفصلان في التغيير #6
        # حين يُنشأ Lead عند سؤال الاستيضاح قبل أي سعر.
        quote_payload = {
            "user_id": user_id,
            "service_name": service_name,
            "price": new_row["سعر الخدمة وقت الإنشاء"],
        }
        events.emit(events.LEAD_CREATED, lead_id=lead_id, channel=channel,
                    payload={**quote_payload, "source": "price_quote"})
        events.emit(events.PRICE_QUOTED, lead_id=lead_id, channel=channel,
                    payload={**quote_payload, "lead_created": True})
        return lead_id


def record_booking_request(lead_id: str, contact_info: str) -> bool:
    """
    العميلة وافقت وسلّمت بياناتها: يُحدَّث **نفس صف** عرض السعر عبر
    lead_id، فلا يُنتج مسار "نعم ثم بيانات" صفين.

    الحالة تصير `booking_requested` - وهي حرفياً ما حدث (§8: Booking
    Request = سلّمت بياناتها، *قبل* تأكيد الموظفة). لا يُكتب هنا شيء
    اسمه حجز مؤكَّد: التأكيد والحضور حدثان تملكهما العيادة وحدها،
    ولا يملك النظام أي مسار لكتابتهما (§5، §7).

    نتيجة المتابعة تُحسب بنفس قاعدة save_lead حرفياً عبر
    _outcome_for_stage - فلا يتغير أي رقم تُخرجه compute_funnel_metrics
    عمّا كان يُخرجه المساران السابقان.

    نتيجة متابعة محسومة مسبقاً لا تُدهَس: Lead بلغ "منتهي" ثم حجز
    يُسجَّل حجزه ولا يُحتسب استرجاعاً - نفس تحفّظ save_lead، ولا يُضخَّم
    رقم الاسترجاع.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                if row.get("نتيجة المتابعة", "") == OUTCOME_PENDING:
                    row["نتيجة المتابعة"] = _outcome_for_stage(row)
                row["الحالة"] = STATE_BOOKING_REQUESTED
                row[STATUS_REASON_COLUMN] = REASON_BOOKING_REQUESTED
                row["بيانات التواصل"] = contact_info
                # رسالتها هي التي أوصلتنا إلى هنا، فنافذة الخدمة فُتحت
                # من جديد. الموافقة التسويقية لا تتغيّر: تسليم رقم للحجز
                # ليس إذناً برسائل تسويقية لاحقة.
                row[CONTACT_WINDOW_COLUMN] = datetime.now().strftime(TIMESTAMP_FORMAT)
                _write_all_rows_unlocked(rows)
                # بيانات التواصل نفسها لا تدخل الحدث - وجودها فقط.
                events.emit(events.BOOKING_REQUESTED, lead_id=lead_id,
                            channel=row.get("القناة", ""),
                            payload={
                                "user_id": row.get("معرف العميل", ""),
                                "service_name": row.get("الخدمة المطلوبة", ""),
                                "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                                "followup_stage": row.get("مرحلة المتابعة", "0"),
                                "outcome": row.get("نتيجة المتابعة", ""),
                                "contact_info_present": bool(contact_info),
                                "source": "record_booking_request",
                            })
                return True
        return False


def _update_lead_row(lead_id: str, changes: dict) -> bool:
    """تعديل حقول محددة في صف واحد بـlead_id. مسار مشترك، لا سلوك خاص به."""
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                row.update(changes)
                _write_all_rows_unlocked(rows)
                return True
        return False


def record_decline(lead_id: str) -> bool:
    """
    رفضت صراحةً: الحالة تصير `declined` - وهي حالة أولى الدرجة في
    دورة حياة §7، لا مجرد سبب مسجَّل في حقل جانبي.

    مرحلة المتابعة ونتيجتها لا تتغيران، و`declined` داخل OPEN_STATES،
    فيبقى الصف مؤهلاً للمتابعة كما هو تماماً. هذا سلوك مقصود ومُسجَّل
    (D-015): الرافضة صراحةً ما زالت تتلقى متابعات آلية اليوم. كتم
    المتابعة عنها قرار سياسة لا قرار تسمية، ويمسّ S7 - وهذا التغيير
    يمسّ الأسماء وحدها فلا يحسمه.

    صف بلغ booking_requested لا تُنزَع منه حالته: الجلسة تُمسح عند
    الحجز فلا يمرّ هذا المسار عملياً، والحارس يمنع أن يمحو خطأ لاحق
    حجزاً قائماً.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                became_declined = row.get("الحالة") != STATE_BOOKING_REQUESTED
                if became_declined:
                    row["الحالة"] = STATE_DECLINED
                row[STATUS_REASON_COLUMN] = REASON_DECLINED
                # رفضها رسالة منها كذلك: النافذة تُفتح برسالة، لا برضا.
                row[CONTACT_WINDOW_COLUMN] = datetime.now().strftime(TIMESTAMP_FORMAT)
                _write_all_rows_unlocked(rows)
                # الحارس أعلاه يحمي صفاً بلغ booking_requested من فقد
                # حالته؛ عندها لم يقع انتقال في دورة الحياة - تغيّر
                # status_reason وحده - فلا يُصدَر DECLINED عن لا شيء.
                if became_declined:
                    events.emit(events.DECLINED, lead_id=lead_id,
                                channel=row.get("القناة", ""),
                                payload={
                                    "user_id": row.get("معرف العميل", ""),
                                    "service_name": row.get("الخدمة المطلوبة", ""),
                                    "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                                    "followup_stage": row.get("مرحلة المتابعة", "0"),
                                    "source": "record_decline",
                                })
                return True
        return False


def record_hesitation(lead_id: str) -> bool:
    """
    ترددت: إشارة تُسجَّل، ولا حالة تتغير.

    `hesitant` ليست حالة في §7 ولا مصطلحاً في §8 - هي نيّة في شجرة
    القرار (D-006/D-007). الـLead يبقى `price_quoted`: لم تُجب بعد،
    وهذا بالضبط ما تقوله الحالة.

    ترددها رسالة منها، فطابع فتح النافذة يُحدَّث - وهو كل ما يُحدَّث
    من عمودي §19 هنا.
    """
    return _update_lead_row(lead_id, {
        STATUS_REASON_COLUMN: REASON_HESITANT,
        CONTACT_WINDOW_COLUMN: datetime.now().strftime(TIMESTAMP_FORMAT),
    })


# ------------------------------------------------- إيقاف الأتمتة (S6)

def _is_paused_row(row: dict, paused: set) -> bool:
    """
    هل ينتمي هذا الصف إلى هوية موقوفة؟ `paused` مجموعة تُقرأ مرة واحدة
    قبل المرور على الصفوف - انظر `pause_store.paused_identity_set`.
    """
    return (row.get("القناة", ""), row.get("معرف العميل", "")) in paused


def _open_leads_for_identity_unlocked(rows: list[dict], channel: str, user_id: str) -> list[dict]:
    """صفوف هذه الهوية التي ما زالت الأتمتة قادرة على لمسها."""
    return [
        row for row in rows
        if _same_identity(row, channel, user_id) and _is_open_lead(row)
    ]


def _emit_automation_event(event_type: str, channel: str, user_id: str,
                           rows: list[dict], source: str | None = None) -> int:
    """
    حدث لكل Lead مفتوح لهذه الهوية - أو حدث واحد بـlead_id فارغ إن لم
    يكن لها أي Lead مفتوح (سابقة AMBIGUITY_ASKED: الواقعة وقعت ولا Lead
    تُعلَّق عليه). يُرجع عدد الـLeads المتأثرة.

    `leads_affected` في كل حمولة هو **عدد الصفوف لا عدد النساء**: من
    يعدّ النساء يعدّ `user_id` متمايزة. القاعدة مكتوبة في events.py
    كذلك، حيث يقرؤها من يكتب تقريراً.
    """
    affected = _open_leads_for_identity_unlocked(rows, channel, user_id)
    payload_base = {
        "user_id": user_id,
        "leads_affected": len(affected),
    }
    if source is not None:
        payload_base["source"] = source

    if not affected:
        events.emit(event_type, lead_id="", channel=channel, payload=dict(payload_base))
        return 0

    for row in affected:
        events.emit(
            event_type,
            lead_id=row.get(LEAD_ID_COLUMN, ""),
            channel=channel,
            payload={**payload_base, "service_name": row.get("الخدمة المطلوبة", "")},
        )
    return len(affected)


def pause_automation(user_id: str, channel: str,
                     source: str = pause_store.SOURCE_OPERATOR) -> bool:
    """
    يوقف الأتمتة لعميلة واحدة (S6). يُرجع True إن وقع الإيقاف الآن،
    وFalse إن كانت موقوفة أصلاً أو كانت الهوية ناقصة.

    الإيقاف يخصّ الهوية فيسري على كل Leadاتها المفتوحة وعلى ما تفتحه
    لاحقاً - انظر ترويسة الملف. `AUTOMATION_PAUSED` يُصدَر على الانتقال
    وحده: طلب ثانٍ من نفس العميلة لا يضيف إيقافاً ثانياً لم يقع.

    الترتيب كترتيب بقية الملف: الحالة تُكتب أولاً، والحدث بعد نجاحها
    وحده. حدثٌ يقول «أوقفنا» بينما لم يُكتب الإيقاف يجعل المتابعات
    تغادر بينما السجل يشهد أنها لن تغادر - وهو أسوأ اتجاه ممكن هنا.
    """
    if not pause_store.pause(channel=channel, user_id=user_id, source=source):
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        _emit_automation_event(events.AUTOMATION_PAUSED, channel, user_id, rows, source)
    return True


def resume_automation(user_id: str, channel: str) -> bool:
    """
    يرفع الإيقاف عن **عميلة واحدة معيَّنة بمعرّفها** (S6). يُرجع True
    إن كانت موقوفة فاستُؤنفت الآن، وFalse إن لم تكن موقوفة أصلاً.

    لا نظير جماعي لهذه الدالة ولن يوجد - لا هنا ولا في المتجر ولا في
    مسار اختبار. رفع الإيقاف يستأنف مراسلة إنسانة طلبت التوقف، فيُتخذ
    لها وحدها وبمعرّفها صراحةً.

    `AUTOMATION_RESUMED` اسم مضاف إلى §6 بقرار موثّق - انظر events.py.
    """
    if not pause_store.resume(channel=channel, user_id=user_id):
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        _emit_automation_event(events.AUTOMATION_RESUMED, channel, user_id, rows)
    return True


# ------------------------------------------------- الـHoldout (PRD §10، D7)

def _is_unbooked_now(row: dict, now: datetime, hours_threshold: float) -> bool:
    """
    شروط UNBOOKED (§8) وحدها: الحالة والمرحلة والنتيجة والزمن. **بلا
    أي حاجز بشري** - لا إيقاف ولا holdout.

    استُخرجت من `get_leads_eligible_for_first_followup` حرفياً بلا
    تغيير شرط واحد، لأن `assign_holdout_groups` تحتاج نفس التعريف
    بالضبط: «اللحظة التي تصير فيها مؤهلة للمتابعة الأولى» هي «اللحظة
    التي تصير فيها UNBOOKED» (رقم واحد لا رقمان - انظر SILENCE_WINDOW_HOURS)،
    وهي لحظة الإسناد في §10. تعريفان لنفس اللحظة كانا سينفرطان،
    فيُسنَد الـholdout إلى مجموعة ليست هي التي تُتابَع.
    """
    if row.get("الحالة") not in OPEN_STATES:
        return False
    if row.get("مرحلة المتابعة", "0") != "0":
        return False
    if row.get("نتيجة المتابعة", "") != "":
        return False
    try:
        created = datetime.strptime(row["التاريخ والوقت"], TIMESTAMP_FORMAT)
    except (ValueError, KeyError):
        return False
    return (now - created).total_seconds() / 3600 >= hours_threshold


def _holdout_group_for(lead_id: str, percentage: float) -> str:
    """
    المجموعة من `hash(lead_id)` - دالة نقية، بلا حالة، بلا قرص، بلا وقت.

    [لماذا sha256 لا `hash()` المدمجة]
    بايثون يعشوِش تجزئة النصوص لكل عملية (PYTHONHASHSEED). أي أن
    `hash(lead_id)` يعطي رقماً مختلفاً في كل تشغيل لـsend_followups.py،
    فتنتقل نفس العميلة بين المجموعتين بين ليلة وأخرى. هذا بالضبط ما
    يمنعه §10 («لا عشوائية زمنية، قابل لإعادة الحساب والتدقيق»)،
    وكان سيمرّ من كل اختبار داخل عملية واحدة بلا أن يُكشف.

    [لماذا عتبة لا باقي قسمة على فئات]
    `bucket < النسبة` تجعل رفع النسبة لاحقاً **يضيف** إلى الضابطة ولا
    يقلب أحداً من الضابطة إلى المعالَجة. لو كانت القسمة إلى فئات
    لأعادت كل زيادة توزيع الجميع، فيصير الصف المكتوب أمس مخالفاً
    لإعادة حسابه اليوم بلا أن يتغيّر شيء في الـLead.

    [الدقة]
    عشرة آلاف سلّة تكفي لنسبة بخانة عشرية واحدة (12.5% = 1250)، وهي
    أدقّ مما يستطيع حجم عيادة واحدة أن يميّزه إحصائياً أصلاً.
    """
    digest = hashlib.sha256(lead_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 10000
    return HOLDOUT_CONTROL if bucket < percentage * 100 else HOLDOUT_TREATMENT


def _holdout_bucket_for(lead_id: str) -> int:
    """السلّة وحدها - تدخل حمولة الحدث ليُعاد حساب القرار من السجل."""
    return int(hashlib.sha256(lead_id.encode("utf-8")).hexdigest()[:8], 16) % 10000


def assign_holdout_groups(percentage: float = HOLDOUT_PERCENTAGE,
                          hours_threshold: float = SILENCE_WINDOW_HOURS) -> int:
    """
    يُسنِد المجموعة لكل Lead بلغ UNBOOKED ولم يُسنَد بعد. يُرجع عدد
    المُسنَدين الآن.

    [متى: عند UNBOOKED، لا عند إنشاء الـLead]
    §10 حرفياً: «يُسنَد مرة واحدة عند `UNBOOKED` ولا يتغير أبداً».
    والفرق ليس توقيتاً - هو **مقام التقرير**. الإسناد عند الإنشاء يضع
    في المجموعتين نساءً حجزن فوراً أو رفضن صراحةً، ولم يدخلن دورة
    المتابعة قط. عندها يقارن التقرير مجموعتين مختلفتي التركيب ويسمّي
    الفارق أثراً - وهو عطب F3 نفسه في موضع جديد: رقمٌ يقيس شيئاً غير
    الذي يسمّيه.

    وبالإسناد هنا يصير المقام هو مقام §9.2 بعينه: الـUnbooked وحدهم،
    في الطرفين، بنفس التعريف الزمني.

    [لماذا تُكتب المعالَجة صراحةً ولا تُستنتج بالطرح]
    الصف يحمل `treatment` مكتوبةً لا مستنتجة من «ليس control». لو
    اكتُفي بوسم الضابطة، لكانت المعالَجة في أي تقرير = كل ما عداها -
    فتمتلئ بمن حجزن فوراً وبمن هويتها موقوفة وبصفوف ما قبل التغيير،
    ويصير مقاما الطرفين مختلفَي التركيب مرة أخرى. القيمة المكتوبة هي
    الفرق بين «أُسنِدت إلى المعالَجة» و«لم نجد لها وسماً».

    [الموقوفة خارج التجربة بمجموعتيها]
    الشرط يمرّ عبر `_is_paused_row` كما تفعل دالة الأهلية بالضبط، فمن
    طلبت التوقف لا تُسنَد إلى شيء وتبقى "". وضعها في المعالَجة كان
    سيلوّثها بمن لن تصلها متابعة أبداً؛ ووضعها في الضابطة كان سيحسب
    قرارها الشخصي تصميماً تجريبياً. الإيقاف قرار عميلة، والـholdout
    تصميم قياس - ولا يُجمعان في مقام واحد.

    [الصفر: لا شيء يقع]
    نسبة صفر تخرج قبل أي قراءة أو كتابة أو حدث. هذا وضع اليوم المعتمد
    (Gate C لم يُحسب بعد)، وهو ما يجعل هذا التغيير بلا أثر سلوكي.

    idempotent: صفٌّ يحمل وسماً لا يُعاد إسناده أبداً - §10: «ولا
    يتغير أبداً». تشغيل الدالة عشر مرات يُنتج نفس الملف ونفس الأحداث.
    """
    if percentage <= 0:
        return 0

    now = datetime.now()
    paused = pause_store.paused_identity_set()
    assigned = []

    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if (row.get(HOLDOUT_COLUMN) or "") != HOLDOUT_UNASSIGNED:
                continue
            if not _is_unbooked_now(row, now, hours_threshold):
                continue
            if _is_paused_row(row, paused):
                continue
            lead_id = (row.get(LEAD_ID_COLUMN) or "").strip()
            if not lead_id:
                continue
            row[HOLDOUT_COLUMN] = _holdout_group_for(lead_id, percentage)
            assigned.append(row)

        if not assigned:
            return 0
        _write_all_rows_unlocked(rows)

        # الأحداث بعد نجاح الكتابة وحدها - ترتيب هذا الملف كله.
        for row in assigned:
            events.emit(
                events.HOLDOUT_ASSIGNED,
                lead_id=row[LEAD_ID_COLUMN],
                channel=row.get("القناة", ""),
                payload={
                    "user_id": row.get("معرف العميل", ""),
                    "service_name": row.get("الخدمة المطلوبة", ""),
                    "group": row[HOLDOUT_COLUMN],
                    # القرار قابل لإعادة الحساب من السجل وحده: السلّة
                    # والنسبة **التي كانت نافذة لحظة وقوعه** لا نسبة
                    # اليوم. بدونهما يصير التدقيق مشروطاً بألا يتغيّر
                    # الإعداد أبداً.
                    "bucket": _holdout_bucket_for(row[LEAD_ID_COLUMN]),
                    "holdout_percentage": percentage,
                },
            )
    return len(assigned)


def get_leads_eligible_for_first_followup(hours_threshold: float = SILENCE_WINDOW_HOURS) -> list[dict]:
    """
    الشروط لم تتغير بحرف واحد. الذي تغيّر هو *من* يستوفيها: منذ
    record_price_quote صار الـLead الصامت يُكتب لحظة عرض السعر، فيمرّ
    من هنا وحده بعد hours_threshold من الصمت. هذه العتبة الزمنية هي
    "نافذة الصمت" في PRD §8 - لا حاجة لتمثيلها بحالة مخزَّنة.

    الفلتر صار `in OPEN_STATES` بدل `== "not_ready"`: القيمة الواحدة
    القديمة انقسمت إلى ثلاث (price_quoted / declined / legacy_unknown)،
    وكلها كانت not_ready وكلها تبقى مؤهلة - نفس المجموعة بالضبط.

    شروط UNBOOKED نفسها انتقلت إلى `_is_unbooked_now` بلا تغيير حرف،
    ليقرأها هذا المسار و`assign_holdout_groups` من نسخة واحدة.
    """
    eligible = []
    now = datetime.now()
    paused = pause_store.paused_identity_set()
    for row in _read_all_rows():
        if not _is_unbooked_now(row, now, hours_threshold):
            continue

        # ------------------------------------------------------------
        # [حاجزان متجاوران - ولا يُدمجان أبداً]
        #
        # يتشابهان في الشكل تماماً: سطرٌ يمنع متابعة. ومن يمرّ هنا غداً
        # ليرتّب الكود سيُغريه توحيدهما في «هل هذا الصف مستثنى؟» واحدة،
        # أو نقل الـholdout إلى `pause_store` ليصيرا متجراً واحداً.
        # **لا تفعل.** الأول قرار عميلة على نفسها، والثاني تصميم تجربة
        # على القياس، ولا يجوز أن يُعدّا معاً في أي مقام:
        #
        #   - الموقوفة استعملت حقها في ألا نراسلها (S6/D-023). عدّها في
        #     الضابطة يحسب قرارها الشخصي تصميماً منّا، فيلوّث أثراً
        #     نزعم أننا سبّبناه. وهي تُوقَف بمفتاح الهوية فتشمل كل
        #     Leadاتها الحالية والقادمة.
        #   - الضابطة اختارها `hash(lead_id)` لنقيس ما كان سيحدث بلا
        #     تدخلنا (§10). وهي **لكل Lead** لا لكل هوية: عميلة لها
        #     استفساران قد يقع أحدهما في كل مجموعة، وهذا سليم - رفعُه
        #     إلى الهوية يكسر التوزيع الحتمي ويُدخل حالةً على مستوى
        #     الهوية يمنعها §21.
        #
        # ولذلك أيضاً لا حارس holdout في `outbound.send` بإزاء حارس
        # الإيقاف هناك: `send` لا يميّز متابعةً من رداً حياً، و§10 صريح
        # أن الضابطة تتلقى رداً كاملاً فورياً على كل ما تطلبه - الحجب
        # يقتصر على المتابعة التسويقية غير المطلوبة.
        # ------------------------------------------------------------
        if _is_paused_row(row, paused):
            continue
        if row.get(HOLDOUT_COLUMN) == HOLDOUT_CONTROL:
            continue

        eligible.append(row)
    return eligible


def get_leads_eligible_for_second_followup(hours_threshold: float = 72) -> list[dict]:
    """
    [لا فلتر holdout هنا ولا في `get_leads_to_expire` - وليس سهواً]
    الفلتر هناك سيكون كوداً ميتاً يدّعي حراسة. الصف الضابط لا يتلقى
    متابعة أولى أبداً، فمرحلته تبقى "0" ولا يستوفي شرط `!= "1"` هنا
    ولا `!= "2"` في الإنهاء. حاجزٌ لا يمكن أن يُطلَق يُقرأ في المراجعة
    حمايةً قائمة، ويخفي أن الحماية الحقيقية هي آلة الحالات.

    والفرق عن الإيقاف حقيقي: الموقوفة **تُفلتَر في الثلاث** لأن هويتها
    قد تكون أُوقفت بعد متابعة أولى وقعت فعلاً، فتوجد صفوف موقوفة في
    المرحلة "1" و"2" - ومنها ما لا يجوز أن يُسمّى "منتهي" (ترويسة
    الملف). الضابطة لا يمكن أن توجد في تينك المرحلتين أصلاً.
    """
    eligible = []
    now = datetime.now()
    paused = pause_store.paused_identity_set()
    for row in _read_all_rows():
        if row.get("الحالة") not in OPEN_STATES:
            continue
        if _is_paused_row(row, paused):
            continue
        if row.get("مرحلة المتابعة", "0") != "1":
            continue
        if row.get("نتيجة المتابعة", "") != "":
            continue
        last_followup = row.get("تاريخ آخر متابعة", "")
        if not last_followup:
            continue
        try:
            last_dt = datetime.strptime(last_followup, TIMESTAMP_FORMAT)
        except ValueError:
            continue
        if (now - last_dt).total_seconds() / 3600 >= hours_threshold:
            eligible.append(row)
    return eligible


def get_leads_to_expire(hours_after_second_followup: float = 72) -> list[dict]:
    candidates = []
    now = datetime.now()
    paused = pause_store.paused_identity_set()
    for row in _read_all_rows():
        if row.get("الحالة") not in OPEN_STATES:
            continue
        if _is_paused_row(row, paused):
            continue
        if row.get("مرحلة المتابعة", "0") != "2":
            continue
        if row.get("نتيجة المتابعة", "") != "":
            continue
        last_followup = row.get("تاريخ آخر متابعة", "")
        if not last_followup:
            continue
        try:
            last_dt = datetime.strptime(last_followup, TIMESTAMP_FORMAT)
        except ValueError:
            continue
        if (now - last_dt).total_seconds() / 3600 >= hours_after_second_followup:
            candidates.append(row)
    return candidates


def mark_followup_sent(lead_id: str, new_stage: str, variant_id: str | None = None) -> bool:
    """
    يُعلّم صف Lead واحداً بأن متابعة أُرسلت له. المخاطبة بـlead_id
    وحده: المفتاح الثلاثي السابق (عميل + خدمة + طابع زمني بدقة الثانية)
    كان قادراً على مطابقة أكثر من صف عند استفسارين في نفس الثانية.

    [التغيير #5] `variant_id` يصل من مسار الإرسال الموحّد: FOLLOWUP_SENT
    هو حدث الرسالة الصادرة لمسار المتابعة (§5)، فيحمل الصياغة التي
    أُرسلت فعلاً كما يحمل RESPONSE_SENT صياغته. الإصدار يبقى هنا لا في
    outbound.send لأنه يجب أن يقع تحت قفل leads.csv بعد نجاح كتابة
    الصف: حدث متابعة بلا صف مُعلَّم يعني إعادة إرسال أبدية لا يفسّرها
    السجل.

    None مسموح ويعني "عُلِّم خارج مسار الإرسال" (استدعاء يدوي أو
    اختبار)، ويُكتب الحدث بـvariant_id فارغ. لا مسار إنتاجي يفعل ذلك:
    send_followups.py يمرّر المعرّف دائماً.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                row["مرحلة المتابعة"] = new_stage
                row["تاريخ آخر متابعة"] = datetime.now().strftime(TIMESTAMP_FORMAT)
                _write_all_rows_unlocked(rows)
                # send_followups.py لا يستدعي هذه الدالة إلا بعد نجاح
                # الإرسال عبر outbound.send، فالحدث يعني "رسالة غادرت
                # فعلاً" لا "حاولنا" - وهو نفس معناه منذ أول سطر كُتب
                # به على القرص. محاولة فاشلة ثم إعادة محاولة ناجحة
                # تُنتج حدثاً واحداً بالضبط، لا حدثين ولا صفراً.
                events.emit(events.FOLLOWUP_SENT, lead_id=lead_id,
                            channel=row.get("القناة", ""),
                            variant_id=variant_id,
                            payload={
                                "user_id": row.get("معرف العميل", ""),
                                "service_name": row.get("الخدمة المطلوبة", ""),
                                "stage": new_stage,
                                "variant_hash": variants.template_hash(variant_id),
                            })
                return True
        return False


def mark_expired(lead_id: str) -> bool:
    """
    يُعلّم صف Lead واحداً كـ"منتهي". المخاطبة بـlead_id وحده - كما في
    mark_followup_sent.

    ما يُكتب لم يتغيّر بحرف واحد عن _update_lead_row: نفس الحقل بنفس
    القيمة بلا شرط. الحلقة مكتوبة صراحةً هنا لأن الحدث يحتاج القناة
    والخدمة من الصف نفسه، و_update_lead_row لا تُرجع الصف.

    LEAD_EXPIRED اسم خارج قائمة §6 - إضافة موثّقة بقرار صريح: §7
    يجعل EXPIRED حالة حقيقية في دورة الحياة و§6 لا يحمل اسماً لها،
    وبلا الحدث لا يستطيع تقرير مشتق من الأحداث وحدها التمييز بين
    Lead منتهٍ وLead ما زال مفتوحاً.

    الإصدار مشروط بأن القيمة السابقة لم تكن "منتهي" أصلاً: استدعاء
    ثانٍ على نفس الصف يكتب نفس القيمة كما كان يفعل تماماً، ولا يضيف
    انتهاءً ثانياً لم يقع.
    """
    if not lead_id:
        return False
    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) == lead_id:
                already_expired = row.get("نتيجة المتابعة", "") == OUTCOME_EXPIRED
                row["نتيجة المتابعة"] = OUTCOME_EXPIRED
                _write_all_rows_unlocked(rows)
                if not already_expired:
                    events.emit(events.LEAD_EXPIRED, lead_id=lead_id,
                                channel=row.get("القناة", ""),
                                payload={
                                    "user_id": row.get("معرف العميل", ""),
                                    "service_name": row.get("الخدمة المطلوبة", ""),
                                    "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                                    "followup_stage": row.get("مرحلة المتابعة", "0"),
                                })
                return True
        return False


# ------------------------------------------- الحضور (PRD §11، §9.3)

_ATTENDANCE_TO_EVENT = {
    ATTENDANCE_ATTENDED: events.BOOKING_COMPLETED,
    ATTENDANCE_NO_SHOW: events.NO_SHOW,
}


def record_attendance(lead_id: str, attendance: str) -> bool:
    """
    يسجّل ما قالته العيادة عن الحضور، ويُصدر حدثه. يُرجع True عند
    التسجيل، وFalse عند رفضه.

    [لا مسار إنتاجي يستدعي هذه الدالة اليوم - وهذا مقصود]
    §11 يحدّد نقطة اللمس (زر «حضرت» / «لم تحضر» صباح اليوم التالي)،
    ومَن تضغطها موظفة الدفع، وهو **سلوك جديد كلياً** لم يُتفق عليه بعد
    (R3 مفتوح، §20). بناء الزر قبل الاتفاق يبني ما قد لا يُستعمل.
    الموجود هنا هو الحقل والحدث والمَعبر بينهما - وهي وحدها ما يستحيل
    استرجاعه بأثر رجعي. من يبني نقطة اللمس يجدها جاهزة ولا يخترع
    مفرداتها تحت ضغط.

    [الحضور واقعة، لا نتيجة تُشتق]
    القيمتان الوحيدتان المقبولتان رصدٌ بشري: `attended` قالتها العيادة،
    و`no_show` قالتها العيادة. الصمت لا يُنتج أياً منهما، ولا يوجد
    مسار يستنتج غياباً من عدم ورود تأكيد - §11: «عدم الرد حالة صريحة
    وليس افتراض نجاح»، وعكسه صحيح كذلك: ليس افتراض فشل.

    [حارس الحالة]
    الـLead الذي لم يبلغ `booking_requested` لا يملك موعداً أصلاً،
    فتسجيل حضوره ادّعاء عن لقاء لم يُطلب قط. §7 يجعل كل انتقال بعد
    REQUESTED فرعاً منه لا مساراً موازياً له.

    [ما لا يتغيّر]
    `الحالة` تبقى `booking_requested`. لا حالة `completed` في دورة
    الحياة اليوم، وإضافتها تحرّك OPEN_STATES وis_unbooked ومقام كل
    نسبة في compute_funnel_metrics - و§9.3 لا يحتاجها: وحدة الفوترة
    تُشتق من `events.jsonl` (BOOKING_COMPLETED بعد FOLLOWUP_SENT بعد
    BOOKING_REQUESTED)، لا من عمود حالة.

    الحدث على الانتقال وحده: إعادة تسجيل نفس القيمة لا تُصدر حدثاً
    ثانياً - نفس تحفّظ `mark_expired` و`pause_automation`.
    """
    if not lead_id:
        return False
    if attendance not in _ATTENDANCE_TO_EVENT:
        return False

    with _locked():
        _migrate_file_if_needed_locked()
        rows = _read_all_rows_unlocked()
        for row in rows:
            if row.get(LEAD_ID_COLUMN) != lead_id:
                continue
            if row.get("الحالة") != STATE_BOOKING_REQUESTED:
                return False

            already = (row.get(ATTENDANCE_COLUMN) or "") == attendance
            row[ATTENDANCE_COLUMN] = attendance
            _write_all_rows_unlocked(rows)

            if not already:
                events.emit(
                    _ATTENDANCE_TO_EVENT[attendance],
                    lead_id=lead_id,
                    channel=row.get("القناة", ""),
                    payload={
                        "user_id": row.get("معرف العميل", ""),
                        "service_name": row.get("الخدمة المطلوبة", ""),
                        "price": row.get("سعر الخدمة وقت الإنشاء", ""),
                        "followup_stage": row.get("مرحلة المتابعة", "0"),
                        "outcome": row.get("نتيجة المتابعة", ""),
                        "holdout_group": row.get(HOLDOUT_COLUMN, ""),
                    },
                )
            return True
        return False


def is_unbooked(row: dict, now: datetime | None = None,
                hours_threshold: float = SILENCE_WINDOW_HOURS) -> bool:
    """
    Unbooked Lead (PRD §8): Qualified Lead لم يصل BOOKING_REQUESTED
    خلال نافذة الصمت.

    مشتقّة لا مخزَّنة: لا شيء في النظام يعمل بجدولة ليكتب هذه الحالة
    لحظة انقضاء النافذة، ولو خُزِّنت لصارت قديمة بين تشغيل وآخر.
    الشرط الزمني نفسه هو التعريف، فتُحسب عند القراءة.

    الرفض الصريح **مستثنى** من هذا المقام: §7 يجعل DECLINED وUNBOOKED
    فرعين شقيقين لا متداخلين، ونصّ §8 وحده يقرأ كأنه يشملهما. اعتُمد
    §7 - وهو قراءة تصحيحية موثّقة للـPRD، لا تعديل عليه. الأثر: مقام
    Recovery Rate يبقى "الصامتات" وحدهن، فلا يُخفَّض المعدل بمن رفضن
    صراحةً - وهو مقلوب الخطأ الذي رصده الـAudit (مقام منقوص يضخّم
    المعدل). legacy_unknown مستثنى كذلك: لا دليل يضعه في أي مقام.

    صف "منتهي" يبقى Unbooked: كان صامتاً ولم يحجز أبداً - وهو بالضبط
    ما يقيسه المقام.
    """
    if row.get("الحالة") != STATE_PRICE_QUOTED:
        return False
    try:
        created = datetime.strptime(row["التاريخ والوقت"], TIMESTAMP_FORMAT)
    except (ValueError, KeyError, TypeError):
        return False
    reference = now or datetime.now()
    return (reference - created).total_seconds() / 3600 >= hours_threshold


def _sum_prices(rows: list[dict]) -> int:
    return sum(_parse_price_to_number(r.get("سعر الخدمة وقت الإنشاء", "")) for r in rows)


def compute_funnel_metrics() -> dict:
    """
    مؤشرات القمع بمفردات PRD §8 حرفياً - وبلا رقم واحد اسمه "إيراد".

    كل صف في الملف هو Qualified Lead بحكم وجوده: لا يُكتب صف إلا بعد
    عرض سعر (record_price_quote) أو حجز (save_lead)، وكلاهما يعني أن
    السعر عُرِض فعلاً.

    طبقات الإيراد الأربع (§8) تُرجَع كلها بأسمائها الكاملة:

      potential_revenue  - Σ سعر كل Qualified Lead      (حجم الفرصة)
      requested_revenue  - Σ سعر كل Booking Request     (مؤشر مبكر)
      booked_revenue     - None: يتطلب تأكيد الموظفة، والنظام لا يملكه
      revenue            - None: يتطلب الحضور، ولا بيانات حضور إطلاقاً

    None وليس صفراً - والفرق ليس شكلياً: الصفر قياسٌ ("قِسنا فوجدنا
    لا شيء")، وNone غياب قياس ("لا بيانات"). صفرٌ في تقرير أمام عيادة
    يُقرأ رقماً حقيقياً، وهذا بالضبط نوع الكذب الذي يعالجه F3.

    recovered_completed_bookings هي وحدة الفوترة الوحيدة (§9.3)، وهي
    None لنفس السبب. الدالة السابقة كانت تُرجع `bookings_recovered`
    مساوياً لـ`leads_recovered` تماماً - نفس القائمة تُعدّ مرتين،
    وأحد الاسمين يوحي بحجوزات مكتملة. الاسم الموحي حُذف، ومكانه رقم
    صادق واحد: لا نعرف.

    وتُرجع `recovered_requested_revenue` بدل `revenue_recovered`:
    نفس الحساب حرفياً، باسم يقول ما يقيسه فعلاً - مجموع أسعار مقتبَسة
    لحظة تسليم الهاتف، لا مالاً وصل العيادة.
    """
    rows = _read_all_rows()
    now = datetime.now()

    booking_requests = [r for r in rows if r.get("الحالة") == STATE_BOOKING_REQUESTED]
    unbooked = [r for r in rows if is_unbooked(r, now)]
    recovered = [r for r in rows if r.get("نتيجة المتابعة") == OUTCOME_RECOVERED]

    return {
        # الأعداد
        "qualified_leads": len(rows),
        "unbooked_leads": len(unbooked),
        "booking_requests": len(booking_requests),
        "recovered_leads": len(recovered),
        "recovered_completed_bookings": None,
        # طبقات الإيراد (§8)
        "potential_revenue": _sum_prices(rows),
        "requested_revenue": _sum_prices(booking_requests),
        "recovered_requested_revenue": _sum_prices(recovered),
        "booked_revenue": None,
        "revenue": None,
    }
