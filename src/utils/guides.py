"""
Short "what is this page" guides, shown at the top of each page the first time.

One place for every word of help, keyed by Flask endpoint, so the text can be
corrected without touching templates. Tenant and caretaker guides come in
English and Kiswahili; admin guides in English. Each guide: a title, one line
of what the page shows, and up to four points on what to do here.

Kiswahili was written for clarity, not by a native editor — have it read by
someone at the property before relying on it.
"""

from flask import request, session

LANGS = ('en', 'sw')

_T = {
    # ── Tenant sign-in ───────────────────────────────────────────────────────
    'tenant.verify': {
        'en': ('First time here?', 'Two quick steps, once only.', [
            'We send a 6-digit code by SMS to the phone number on your tenancy.',
            'You choose a 4-digit PIN. Next time you only need your phone number and PIN.',
            'Forgot your PIN later? Come back here and get a new code.',
        ]),
        'sw': ('Mara ya kwanza?', 'Hatua mbili fupi, mara moja tu.', [
            'Tunatuma nambari ya tarakimu 6 kwa SMS kwenye simu iliyo kwenye upangaji wako.',
            'Unachagua PIN ya tarakimu 4. Wakati ujao unahitaji tu nambari ya simu na PIN.',
            'Umesahau PIN? Rudi hapa upate nambari mpya.',
        ]),
    },
    'tenant.login': {
        'en': ('Sign in', 'Your phone number and the 4-digit PIN you chose.', [
            'No PIN yet, or forgot it? Tap "First time, or forgot your PIN?" — an SMS code lets you set a new one.',
            'Keep your PIN to yourself, like your M-Pesa PIN.',
        ]),
        'sw': ('Ingia', 'Nambari yako ya simu na PIN ya tarakimu 4 uliyochagua.', [
            'Huna PIN, au umeisahau? Bonyeza "First time, or forgot your PIN?" — nambari ya SMS itakuwezesha kuweka mpya.',
            'Usimwambie mtu PIN yako, kama PIN ya M-Pesa.',
        ]),
    },
    # ── Tenant portal ────────────────────────────────────────────────────────
    'tenant.portal': {
        'en': ('Your account', 'This is your rent account: what is owed, what has been paid, and how to pay.', [
            'Still owed (red) = your bills minus your confirmed payments, as of today. Extra paid (green) = you have paid more than your bills so far; it goes to your next bills.',
            'A payment you reported that is not confirmed yet is shown beside it, not taken off.',
            'Statement shows every bill and payment. Pay shows the only account for rent.',
            'Questions or repairs? Use Messages — you get an SMS when there is a reply.',
        ]),
        'sw': ('Akaunti yako', 'Hii ni akaunti yako ya kodi: unachodaiwa, ulicholipa, na jinsi ya kulipa.', [
            'Bado unadaiwa (nyekundu) = bili zako ukitoa malipo yaliyothibitishwa, hadi leo. Ulilipa zaidi (kijani) = umelipa zaidi ya bili zako; kinaenda kwenye bili zijazo.',
            'Malipo uliyoripoti ambayo bado hayajathibitishwa yanaonyeshwa kando, hayajatolewa.',
            'Taarifa (Statement) inaonyesha kila bili na kila malipo. Lipa (Pay) inaonyesha akaunti pekee ya kodi.',
            'Maswali au matengenezo? Tumia Ujumbe (Messages) — utapata SMS jibu likija.',
        ]),
    },
    'tenant.statement': {
        'en': ('Statement', 'Every bill and every payment on your account, newest first.', [
            'The last column shows what was still owed (red) or extra paid (green) after each line.',
            'Tap a row for meter readings, payment details or "Ask about this".',
            'Monthly invoices are under "Account details & monthly invoices". Print includes all entry details.',
        ]),
        'sw': ('Taarifa ya akaunti', 'Kila bili na kila malipo kwenye akaunti yako, ya karibuni kwanza.', [
            'Safu ya mwisho inaonyesha kiasi ulichokuwa bado unadaiwa (nyekundu) au ulicholipa zaidi (kijani) baada ya kila mstari.',
            'Gusa mstari kuona maelezo ya maji, malipo au "Ask about this".',
            'Ankara ziko chini ya "Account details & monthly invoices". "Print" inajumuisha maelezo yote.',
        ]),
    },
    'tenant.charges': {
        'en': ('Bills', 'Each month\'s rent, service charge and water, and how much of each is paid.', [
            'A payment pays the oldest unpaid bill first.',
            'Water you use in one month is applied to the next month\'s bill.',
        ]),
        'sw': ('Bili', 'Kodi, ada ya huduma na maji ya kila mwezi, na kiasi kilicholipwa kwa kila moja.', [
            'Malipo hulipa bili ya zamani zaidi ambayo bado haijalipwa kwanza.',
            'Maji unayotumia mwezi mmoja yanaongezwa kwenye bili ya mwezi unaofuata.',
        ]),
    },
    'tenant.payments': {
        'en': ('Payments', 'Payments received on your account, with their M-Pesa code.', [
            'Tap a payment to see which bills it paid.',
            'Paid but it is not here? Go to Pay and add the M-Pesa code.',
        ]),
        'sw': ('Malipo', 'Malipo yaliyopokelewa kwenye akaunti yako, pamoja na nambari ya M-Pesa.', [
            'Bonyeza malipo kuona bili ilizolipa.',
            'Ulilipa lakini hayapo hapa? Nenda Lipa (Pay) uweke nambari ya M-Pesa.',
        ]),
    },
    'tenant.pay': {
        'en': ('Paying rent', 'How to pay, and how to add a payment that is not showing yet.', [
            'Pay only to the paybill and account shown here. Nobody — caretaker included — can give you a different one.',
            'Check the name M-Pesa shows before you enter your PIN.',
            'Paid already but it is not showing? Paste the M-Pesa SMS. It is taken off what you owe once the payment is confirmed.',
        ]),
        'sw': ('Kulipa kodi', 'Jinsi ya kulipa, na jinsi ya kuongeza malipo ambayo bado hayaonekani.', [
            'Lipa tu kwenye paybill na akaunti zilizo hapa. Hakuna mtu — hata caretaker — anayeweza kukupa nyingine.',
            'Angalia jina M-Pesa inaonyesha kabla ya kuweka PIN.',
            'Umeshalipa lakini hayaonekani? Bandika SMS ya M-Pesa. Yanatolewa kwenye deni lako malipo yakithibitishwa.',
        ]),
    },
    'tenant.messages': {
        'en': ('Messages', 'Your questions and repair requests, in writing, with the replies.', [
            'Money questions are seen by management only. Repairs are also seen by the caretaker.',
            'You get an SMS when there is a reply. Messages cannot be edited or deleted — a record for both sides.',
            'Repairs show here too, marked Open or Fixed. Fixed but it broke again? Reply in the same conversation.',
        ]),
        'sw': ('Ujumbe', 'Maswali yako na maombi ya matengenezo, kwa maandishi, pamoja na majibu.', [
            'Maswali ya pesa yanaonekana na usimamizi tu. Matengenezo yanaonekana pia na caretaker.',
            'Utapata SMS jibu likija. Ujumbe hauwezi kubadilishwa wala kufutwa — ni kumbukumbu kwa pande zote.',
            'Matengenezo yanaonekana hapa pia, yakiwa Open au Fixed. Yamerekebishwa lakini yameharibika tena? Jibu kwenye mazungumzo hayo hayo.',
        ]),
    },
    'tenant.message_new': {
        'en': ('New message', 'Say what it is about, then write it in your own words.', [
            'For a repair, add up to 3 photos — it helps the right person bring the right tools.',
            'Photos are stored without your phone\'s location.',
        ]),
        'sw': ('Ujumbe mpya', 'Chagua unahusu nini, kisha andika kwa maneno yako.', [
            'Kwa matengenezo, ongeza picha hadi 3 — inasaidia kuleta vifaa sahihi.',
            'Picha huhifadhiwa bila mahali simu yako ilipokuwa.',
        ]),
    },
    'tenant.my_lease': {
        'en': ('My lease', 'The terms of your tenancy, from the record management keeps.', [
            'Month to month: you stay as long as you like and give notice to leave. Fixed term: it has an end date.',
            'If your rent is going to change, it shows here before it does.',
            'Something does not match your paper lease? Tell management.',
        ]),
        'sw': ('Mkataba wangu', 'Masharti ya upangaji wako, kutoka kwa rekodi ya usimamizi.', [
            'Mwezi kwa mwezi: unakaa kadri unavyotaka na unatoa notisi kuhama. Muda maalum: una tarehe ya mwisho.',
            'Kodi yako ikibadilika, itaonyeshwa hapa kabla haijabadilika.',
            'Kitu hakilingani na mkataba wako wa karatasi? Mwambie usimamizi.',
        ]),
    },
    'tenant.settings': {
        'en': ('Settings', 'Your details, your PIN and your language.', [
            'Only management can change your details. If something is wrong, tell them.',
            'Keep your PIN secret, like your M-Pesa PIN.',
        ]),
        'sw': ('Mipangilio', 'Maelezo yako, PIN yako na lugha yako.', [
            'Usimamizi pekee unaweza kubadilisha maelezo yako. Kama kuna kosa, waambie.',
            'Usimwambie mtu PIN yako, kama PIN ya M-Pesa.',
        ]),
    },
    'tenant.move_out': {
        'en': ('Moving out', 'Tell management the date you plan to leave, and where your deposit refund should go.', [
            'Your lease asks for one month\'s notice.',
            'Management checks the house with you before you leave. Your deposit pays anything still owed; the rest comes back to you by M-Pesa.',
            'Rent is due until the day you move out. Changed your mind? Withdraw the notice here.',
        ]),
        'sw': ('Kuhama', 'Mwambie usimamizi tarehe unayopanga kuhama, na mahali pa kurudishiwa amana (deposit).', [
            'Mkataba wako unataka notisi ya mwezi mmoja.',
            'Usimamizi utakagua nyumba pamoja nawe kabla hujaondoka. Amana inalipa deni lolote lililobaki; kinachobaki kinarudishwa kwako kwa M-Pesa.',
            'Kodi inadaiwa hadi siku unayohama. Umebadilisha nia? Ondoa notisi hapa.',
        ]),
    },
    'tenant.invoice': {
        'en': ('Invoice', 'One month: what was still owed before it, that month\'s bills, payments that month, and what is left.', []),
        'sw': ('Ankara', 'Mwezi mmoja: deni la kabla yake, bili za mwezi, malipo ya mwezi, na kilichobaki.', []),
    },
    'tenant.receipt': {
        'en': ('Receipt', 'One payment: when, how much, the M-Pesa code, and which bills it paid.', [
            'The "your account today" line is for today, not for the day of the payment.',
        ]),
        'sw': ('Risiti', 'Malipo moja: lini, kiasi gani, nambari ya M-Pesa, na bili ilizolipa.', [
            'Mstari wa "your account today" ni wa leo, si wa siku ya malipo.',
        ]),
    },
    # ── Caretaker ────────────────────────────────────────────────────────────
    'caretaker.dashboard': {
        'en': ('Today', 'What needs you at this property.', [
            'Follow up: who is more than a month behind, to call or visit.',
            'Water: enter every unit\'s meter reading each month — a month without readings stays open.',
            'Issues: repairs tenants report, with their photos. Reply, then mark fixed.',
        ]),
        'sw': ('Leo', 'Kinachohitaji umakini wako kwenye jengo hili.', [
            'Fuatilia (Follow up): walio nyuma zaidi ya mwezi mmoja, wa kupigia simu au kutembelea.',
            'Maji: weka usomaji wa mita wa kila nyumba kila mwezi — mwezi bila usomaji unabaki wazi.',
            'Matatizo (Issues): matengenezo yaliyoripotiwa na wapangaji, pamoja na picha. Jibu, kisha weka "fixed".',
        ]),
    },
    'caretaker.arrears': {
        'en': ('Follow up', 'Tenants behind on rent — more than one month behind first.', [
            'Only rent already past its due date counts. "Months behind" is overdue rent ÷ the monthly rent.',
            'Tap Call to phone the tenant. If they say they paid, ask for the M-Pesa message and send it to management.',
            '"Anything overdue" shows everyone with something late, even a few days.',
        ]),
        'sw': ('Fuatilia', 'Wapangaji walio nyuma kwa kodi — walio nyuma zaidi ya mwezi mmoja kwanza.', [
            'Kodi iliyopita tarehe ya kulipwa pekee ndiyo inahesabiwa. "Miezi nyuma" ni kodi iliyochelewa ÷ kodi ya mwezi.',
            'Bonyeza Call kumpigia mpangaji. Akisema amelipa, omba ujumbe wa M-Pesa na umpelekee usimamizi.',
            '"Anything overdue" inaonyesha kila mtu aliyechelewa, hata kwa siku chache.',
        ]),
    },
    'caretaker.management_chat': {
        'en': ('Management', 'Your conversation with management.', [
            'Send reports and questions here. "Start a report" fills in an outline: water, repairs, visitors, tenants, other.',
            'Add photos of anything that needs fixing. Management gets your message on their screen.',
            'When management writes to you, you get an SMS and the Management tab shows a red number.',
        ]),
        'sw': ('Usimamizi', 'Mazungumzo yako na usimamizi.', [
            'Tuma ripoti na maswali hapa. "Start a report" inaweka muhtasari: maji, matengenezo, wageni, wapangaji, mengine.',
            'Ongeza picha za kitu chochote kinachohitaji kurekebishwa. Usimamizi unaona ujumbe wako kwenye skrini yao.',
            'Usimamizi ukikuandikia, unapata SMS na kichupo cha Management kinaonyesha nambari nyekundu.',
        ]),
    },
    'caretaker.expenses': {
        'en': ('Expenses', 'Things you bought or paid for the building.', [
            'Fill in what it was, how much, and take a photo of the receipt.',
            'Management approves it. If you paid with your own money, they pay you back and it shows "Paid back".',
        ]),
        'sw': ('Matumizi', 'Vitu ulivyonunua au kulipia jengo.', [
            'Jaza ni nini, kiasi gani, na upige picha ya risiti.',
            'Usimamizi unaidhinisha. Kama ulilipa kwa pesa yako, wanakurudishia na inaonyesha "Paid back".',
        ]),
    },
    'caretaker.water_list': {
        'en': ('Water', 'Meter readings entered so far.', [
            'Every month needs a reading for every occupied unit.',
            'Enter the number on the meter exactly; the system works out usage from last month.',
        ]),
        'sw': ('Maji', 'Usomaji wa mita ulioingizwa hadi sasa.', [
            'Kila mwezi unahitaji usomaji kwa kila nyumba yenye mpangaji.',
            'Andika nambari ya mita kama ilivyo; mfumo unahesabu matumizi kutoka mwezi uliopita.',
        ]),
    },
    'caretaker.water_new': {
        'en': ('Enter readings', 'Today\'s meter reading for each unit.', [
            'A reading lower than last month\'s is refused — check the meter again.',
            'Unit vacant or meter unreadable? Leave it blank and say why in Issues.',
        ]),
        'sw': ('Ingiza usomaji', 'Usomaji wa mita wa leo kwa kila nyumba.', [
            'Usomaji ulio chini ya mwezi uliopita unakataliwa — angalia mita tena.',
            'Nyumba iko wazi au mita haisomeki? Acha wazi na eleza kwenye Issues.',
        ]),
    },
    'caretaker.issues': {
        'en': ('Issues', 'Repairs reported by tenants and by you.', [
            'Tenant repairs have a conversation link — reply there so the tenant gets an SMS.',
            'Mark fixed when done, with a note of what was done.',
        ]),
        'sw': ('Matatizo', 'Matengenezo yaliyoripotiwa na wapangaji na wewe.', [
            'Matengenezo ya wapangaji yana kiungo cha mazungumzo — jibu hapo ili mpangaji apate SMS.',
            'Weka "fixed" ukimaliza, na maelezo ya ulichofanya.',
        ]),
    },
    'caretaker.repair_thread': {
        'en': ('Repair conversation', 'What the tenant reported, their photos, and the replies.', [
            'Say when you will come. The tenant gets an SMS; your name is shown.',
            'Messages cannot be edited or deleted. Management can read this conversation.',
        ]),
        'sw': ('Mazungumzo ya tengenezo', 'Alichoripoti mpangaji, picha zake, na majibu.', [
            'Sema utakuja lini. Mpangaji atapata SMS; jina lako linaonyeshwa.',
            'Ujumbe hauwezi kubadilishwa wala kufutwa. Usimamizi unaweza kusoma mazungumzo haya.',
        ]),
    },
    'caretaker.tenants': {
        'en': ('Tenants', 'Who lives in each unit, with their phone number.', [
            'Someone moved in or out? Tell management so their account is right.',
        ]),
        'sw': ('Wapangaji', 'Anayeishi kila nyumba, na nambari yake ya simu.', [
            'Mtu amehamia au kuhama? Waambie usimamizi ili akaunti yake iwe sahihi.',
        ]),
    },
    'caretaker.log_payment': {
        'en': ('Log a payment', 'A tenant shows you an M-Pesa message: paste it here.', [
            'It is checked against the bank record. A code that is not there is flagged, never assumed paid.',
        ]),
        'sw': ('Rekodi malipo', 'Mpangaji akikuonyesha ujumbe wa M-Pesa: ubandike hapa.', [
            'Unalinganishwa na rekodi ya benki. Nambari isiyopatikana inawekwa alama, haichukuliwi kuwa imelipwa.',
        ]),
    },
    # ── Admin ────────────────────────────────────────────────────────────────
    'dashboard': {
        'en': ('Overview', "Your starting point every day: this month's money, and everything waiting for you.", [
            'The green card: rent collected this month against what is expected. "Overdue" is money owed after the due date.',
            'To do lists only what needs you. Tap a row to deal with it. "Nothing waiting ✓" means you are up to date.',
            'New here? Open "How Domi works" in the menu — it explains every word and the monthly routine.',
        ]),
    },
    'manage_units': {
        'en': ('Units & tenants', 'Every house in the building, who lives there, and what they pay each month.', [
            'Tap a value with a pencil (rent, phone…) to change it. Changes are recorded.',
            '"Statement" shows everything a tenant was billed and paid.',
            "The ••• menu moves a tenant out or changes a unit's status (empty, owner use).",
        ]),
    },
    'manage_tenants': {
        'en': ('Tenants', 'Everyone renting a unit, with their phone and what they owe.', [
            'Tap a tenant to see their statement.',
        ]),
    },
    'add_tenant': {
        'en': ('Add a tenant', 'Put a new tenant in an empty unit.', [
            'Better: use Leases → Record a lease, which also keeps the lease terms and deposit.',
            'The phone number is how the tenant signs in and gets receipts — check it carefully.',
        ]),
    },
    'add_unit': {
        'en': ('Add a unit', 'Add a house or apartment to the building.', [
            'Enter its number (e.g. "A7"), the monthly rent and the service charge.',
        ]),
    },
    'tenant_statement': {
        'en': ('Tenant statement', "One tenant's full history: every bill, every payment, and what they owe now.", [
            'Tap "Still owed" to see which bills make it up. Tap any bill or payment below for its details.',
            'Open "Check an M-Pesa code" to see whether a payment the tenant mentions reached the bank.',
            'The balance only counts payments confirmed against the bank or M-Pesa.',
        ]),
    },
    'move_out_tenant': {
        'en': ('Record a move-out', 'Close a tenancy: the move-out date, the deposit, and anything still owed.', [
            'The deposit first pays anything still owed; the rest is the refund to send the tenant.',
            'If they still owe after the deposit, choose to keep following up or write it off (with a reason).',
            'If the tenant gave notice in their portal, the date is already filled in.',
        ]),
    },
    'review': {
        'en': ('Payments', 'Money that came into the bank or paybill, and which tenant each payment belongs to.', [
            'Most payments are matched to a tenant automatically.',
            '"Unassigned" ones need you: pick the tenant the payment belongs to.',
            'Not sure whose it is? Leave it — it stays here until someone decides. Never guess.',
        ]),
    },
    'flagged_claims_admin': {
        'en': ('Payments held for checking', 'Payments a tenant reported that Domi could not confirm, or that did not look like theirs.', [
            "Each one says why it was held — e.g. the code was not in the bank statement, or the name on the payment was not the tenant's.",
            'Check with the tenant or caretaker, then add a note. If it belongs to the tenant, match it on Payments.',
        ]),
    },
    'report_payment': {
        'en': ('Record a payment', 'Note a payment a tenant says they made, by pasting their M-Pesa message.', [
            'It is confirmed when the same code appears in the bank statement or paybill records.',
            'Until then it is not taken off what the tenant owes.',
        ]),
    },
    'upload_statement': {
        'en': ('Upload a bank statement', "Give Domi the bank's PDF so it can find who paid.", [
            'Download the statement from the bank as a PDF (a photo or scan works, but is less reliable).',
            'After uploading, check Payments for anything Domi could not match.',
        ]),
    },
    'manage_statements': {
        'en': ('Bank statements', 'Every bank statement uploaded, and which months each one covers.', [
            'A month with no statement means payments for that month cannot be confirmed yet.',
            'Open one to see its payments and any lines Domi could not read.',
        ]),
    },
    'statement_detail': {
        'en': ('Bank statement', 'The payments in one bank statement and which tenant each was matched to.', [
            'Match any unmatched payment to its tenant. Lines Domi could not read are listed under parse errors.',
        ]),
    },
    'verify_payments': {
        'en': ('Check payments', 'Confirms payments tenants reported against this bank statement.', [
        ]),
    },
    'generate_charges': {
        'en': ("Create the month's bills", "Makes this month's rent and service charge bill for every occupied unit.", [
            'Do this once at the start of each month. Running it twice does not double-bill anyone.',
            'Water is billed separately, from the meter readings.',
            'A rent change confirmed under Leases → Renewals takes effect here, from its month.',
        ]),
    },
    'water_uploads_list': {
        'en': ('Water charges', "Each month's water meter readings and the water bills made from them.", [
            "Water is billed a month later: September's readings go on the October bill.",
            'The caretaker can enter readings on their phone; or upload a spreadsheet here.',
        ]),
    },
    'upload_water_charges': {
        'en': ('Upload water readings', "Add a month's meter readings from a spreadsheet.", [
            "Each unit's bill = (new reading − previous reading) × the price per unit.",
        ]),
    },
    'reports.report_history': {
        'en': ('Reports', 'Monthly reports for the owner: money in, money out, who owes.', [
            'A report for a recent month updates itself when new payments come in.',
            'Print or save as PDF to send to the owner.',
        ]),
    },
    'reports.generate_report': {
        'en': ('Make a report', 'Create the monthly report for the owner.', [
        ]),
    },
    'reports.arrears_report': {
        'en': ('Arrears report', 'Everyone who owes, how much and for how long — to print or share.', [
        ]),
    },
    'reports.payment_lookup': {
        'en': ('Find a payment', 'Search for a person by name or phone to see what they paid and when.', [
            'Use it when a tenant says "I paid" — you see whether, when and where the money arrived.',
        ]),
    },
    'reports.unassigned_payments': {
        'en': ('Unassigned payments', 'Payments received that are not yet matched to any tenant.', [
            'Match each one to its tenant on the Payments page.',
        ]),
    },
    'reports.tenant_evidence': {
        'en': ('Tenant evidence', 'Everything on record for one tenant, to settle a dispute.', [
        ]),
    },
    'activity': {
        'en': ('Activity', 'A record of everything done in Domi and who did it.', [
            'Nothing can be removed from it. Use it to see who changed what, and when.',
        ]),
    },
    'messaging.dashboard': {
        'en': ('Messages to tenants', 'Notices and reminders sent to tenants.', [
            'To write to everyone, use "Write to tenants".',
            "For one tenant's question, use the Tenant inbox instead.",
        ]),
    },
    'messaging.thread': {
        'en': ('Message one tenant', 'Write a title and a message; it goes to this tenant only.', [
            'Tap a quick message to start from a reminder filled in with this tenant\'s figures.',
            'It is kept in their portal. Their phone gets an SMS with the title and a link to read and reply in Domi.',
            'For a notice to many tenants, use Broadcast: that SMS carries the whole message.',
        ]),
    },
    'messaging.broadcast': {
        'en': ('Write to tenants', 'Send a notice to all tenants, or some of them.', [
            "It appears in each tenant's portal and goes to them by SMS in full (SMS costs money per message).",
            'Keep it short and say what the tenant should do, e.g. "Water off Saturday 9am–1pm".',
        ]),
    },
    'messaging.inbox_thread': {
        'en': ('Conversation', "One tenant's question or repair, and every reply.", [
            'Your reply is signed "Management" and the tenant gets an SMS that a reply is waiting.',
            'Ticks: grey ✓✓ sent, blue ✓✓ the tenant has seen it.',
            'Close the conversation when it is sorted.',
        ]),
    },
    'messaging.caretaker_chats': {
        'en': ('Caretaker chat', 'Your messages with the caretaker.', [
            'Use it to ask for a repair, a meter reading, or to pass on something a tenant asked.',
        ]),
    },
    'messaging.templates_list': {
        'en': ('Message templates', 'Ready-made wording for common messages.', [
        ]),
    },
    'messaging.reminders': {
        'en': ('Reminders', 'Automatic payment reminders to tenants.', [
            'These are switched off until the records are approved; nothing is sent automatically.',
        ]),
    },
    'messaging.reminder_schedules': {
        'en': ('Reminder schedule', 'When reminders would go out, if switched on.', [
        ]),
    },
    'money.new_expense': {
        'en': ('Record an expense', 'Money spent on the building — repairs, cleaning, security, bills.', [
            'Attach a photo of the receipt. Expenses are never deleted; a mistake is voided with a reason.',
        ]),
    },
    'manage_caretakers': {
        'en': ('Caretakers', 'The people who look after the building and their sign-ins.', [
            'Replacing a caretaker: add the new one first, then remove the old one — removing someone signs them out at once.',
        ]),
    },
    'manage_owners': {
        'en': ('Owners', "The building's owners and their access.", [
            'Owners see the money, reports and every conversation. They sign in with their own email and password.',
            "An owner's payout number can only be set by the owner — never here.",
        ]),
    },
    'org_settings': {
        'en': ('Account settings', 'Your name, email and password.', [
        ]),
    },
    'tools_index': {
        'en': ('Monthly workflow', 'The monthly jobs in order: bills, water, bank statement, reports.', [
            'Work from top to bottom at the start of each month.',
        ]),
    },
    'export_page': {
        'en': ('Export data', 'Download spreadsheets: what each unit owes, the activity record, payment checks.', [
        ]),
    },
    'property_list': {
        'en': ('Properties', 'The buildings you manage. Choose one to work on.', [
        ]),
    },
    'messaging.inbox': {
        'en': ('Tenant inbox', 'Tenant questions and repairs. Waiting longest comes first.', [
            'Replies are signed "Management"; this page and the activity log record who replied.',
            'Repairs are also visible to the caretaker; money questions are not.',
        ]),
    },
    'messaging.invite': {
        'en': ('Inviting tenants', 'Sends each tenant their own sign-in link by SMS.', [
            'Send only once a tenant\'s phone number and opening balance are confirmed.',
            'The link opens nothing on its own: the tenant confirms their phone with an SMS code, then sets a PIN.',
        ]),
    },
    'team.team': {
        'en': ('Team', 'One account per administrator.', [
            'A lead admin adds people and switches accounts off. Switched-off accounts keep their name on the record.',
            'Give a temporary password in person; they change it under Account Settings.',
        ]),
    },
    'admin_arrears': {
        'en': ('Follow up', 'Who to contact about rent: more than one month behind first.', [
            'Only charges past their due date count, so this month\'s rent is not "behind" before the due day.',
            'A payment a tenant reported but the bank records don\'t show yet is listed beside the amount, not taken off it.',
            'Nothing here texts tenants. Use Message to write to one, or call.',
        ]),
    },
    'messaging.caretaker_chat': {
        'en': ('Caretaker chat', 'One ongoing conversation with this caretaker.', [
            'Reports from the caretaker are marked REPORT. Photos open full size.',
            'Your message reaches them with an SMS saying a message is waiting — never its content.',
            'Messages cannot be edited or deleted; the activity log records who wrote.',
        ]),
    },
    'money.expenses': {
        'en': ('Expenses', 'Every shilling paid out of the rent collected.', [
            'Record an expense yourself, or approve one the caretaker sent (with their receipt photo).',
            'A caretaker who paid out of pocket is paid back once you mark the expense paid.',
            'Nothing is edited or deleted — a mistake is voided with a reason. The owner sees all of it.',
        ]),
    },
    'money.cashbook': {
        'en': ('Cash book', 'Rent collected − expenses − paid to owner = balance held.', [
            'Collected is rent matched to bank or M-Pesa records. Expenses count once paid.',
            'The owner sees this same page in their portal.',
        ]),
    },
    'viewer.property_conversations': {
        'en': ('Conversations', 'Every conversation at the property, as it happened.', [
            'Tenants with management, the caretaker\'s replies to tenants on repairs, and the caretaker with management.',
            '"Waiting for a reply" means the tenant wrote last and management has not answered yet.',
            'You are reading only — replies are written by management.',
        ]),
    },
    'leases.renewals': {
        'en': ('Renewals & rent reviews', 'Fixed-term leases ending in the next 60 days, and rent increases the lease allows.', [
            'Renew (new end date), switch to month to month, or let it end. The tenant sees a notice in their portal.',
            'A confirmed new rent is billed from the month you choose, when that month\'s charges are generated. The tenant is told now.',
        ]),
    },
    'leases.moving_out': {
        'en': ('Moving out', 'Tenants who gave notice in their portal, soonest move-out first.', [
            'Mark a notice as seen so the tenant knows it reached you; reply in its conversation.',
            'Arrange the inspection, then record the move-out — it sets the deposit against anything owed and shows the refund due.',
            'A refund number that is not the one on the tenancy is shown in orange: confirm it with the tenant first.',
        ]),
    },
    'leases.board': {
        'en': ('Leases', 'Every unit and where its lease stands. Work down this list to onboard a property.', [
            '"Old record only" means someone is on the old records but no lease has been entered for them yet.',
            'Record a lease as soon as it is agreed. If the signed copy is not back, mark it signed later.',
            'Nothing here texts tenants.',
        ]),
    },
    'leases.new': {
        'en': ('Record a lease', 'Opens the tenant\'s account on the lease\'s terms.', [
            'Renewing someone already recorded keeps their old record as history. The new account starts clean.',
            'Old debt carries over only as a brought-forward amount you enter, with what it is based on.',
            'If someone else signed, record the previous tenant\'s move-out first.',
        ]),
    },
    'leases.detail': {
        'en': ('Lease', 'Terms, documents and the opening balance for this tenancy.', [
            'Correct the brought-forward amount here once old statements are reconciled. Every change is logged.',
            'Documents (lease, ID copy) are visible to admins only.',
        ]),
    },
    'mpesa_admin.payments': {
        'en': ('M-Pesa payments', 'Where tenants are told to pay, and payments into the property\'s own paybill.', [
            'Changing where tenants pay is SMSed to the owners and reported to Domi.',
            'A paybill payment with an account number that names nobody waits here until you name the tenant.',
        ]),
    },
}


def current_lang():
    """Tenant/caretaker language: ?lang= sets it for the session; else the tenant's preference; else English."""
    lang = request.args.get('lang')
    if lang in LANGS:
        session['lang'] = lang
        return lang
    return session.get('lang') or 'en'


def for_endpoint(endpoint, lang='en'):
    """The guide for this page as {key, title, intro, points}, or None."""
    entry = _T.get(endpoint or '')
    if not entry:
        return None
    title, intro, points = entry.get(lang) or entry['en']
    return {'key': endpoint, 'title': title, 'intro': intro, 'points': points,
            'bilingual': 'sw' in entry, 'lang': lang if lang in entry else 'en'}


# ── Welcome page (shown once, then from "How to use Domi") ─────────────────────
# Short sentences on purpose: some tenants read slowly, some read best in Kiswahili.
WELCOME = {
    'en': {
        'title': 'Welcome to {property}',
        'intro': 'Unit {unit} · {name}. This is your rent account. Here is everything it does.',
        'steps': [
            ('wallet', 'See what you owe', 'Red means money you still owe. Green means you paid extra. Tap the red box to see which bills.', 'portal', 'Open my account'),
            ('phone', 'Pay with M-Pesa', 'Pay only to the paybill and account on the Pay page. Nobody — not even the caretaker — can give you another number.', 'pay', 'How to pay'),
            ('receipt', 'Get your receipts', 'Every payment gets a receipt, by SMS and here. The Statement shows every bill and every payment.', 'statement', 'My statement'),
            ('message', 'Ask, or report a repair', 'Write to management in Messages. For a repair, add a photo. You get an SMS when they reply.', 'messages', 'Messages'),
            ('mail', 'Read notices', 'Notices from management are at the top of Messages.', 'messages', None),
            ('home', 'Moving out', 'Give one month\'s notice on the Moving out page. It also shows your deposit.', 'move_out', 'Moving out'),
            ('user', 'Keep your PIN secret', 'Like your M-Pesa PIN. On a shared phone, tap Sign out when you finish.', None, None),
        ],
        'done': 'Go to my account',
        'again': 'You can open this page again from "How to use Domi" on your account page.',
    },
    'sw': {
        'title': 'Karibu {property}',
        'intro': 'Nyumba {unit} · {name}. Hii ni akaunti yako ya kodi. Hivi ndivyo inavyofanya kazi.',
        'steps': [
            ('wallet', 'Ona unachodaiwa', 'Nyekundu ni pesa bado unadaiwa. Kijani ni ulilipa zaidi. Gusa kisanduku chekundu kuona bili zipi.', 'portal', 'Fungua akaunti yangu'),
            ('phone', 'Lipa kwa M-Pesa', 'Lipa tu kwenye paybill na akaunti zilizo kwenye ukurasa wa Pay. Hakuna mtu — hata caretaker — anayeweza kukupa nambari nyingine.', 'pay', 'Jinsi ya kulipa'),
            ('receipt', 'Pata risiti zako', 'Kila malipo yanapata risiti, kwa SMS na hapa. Statement inaonyesha kila bili na kila malipo.', 'statement', 'Statement yangu'),
            ('message', 'Uliza, au ripoti matengenezo', 'Andikia usimamizi kwenye Messages. Kwa matengenezo, ongeza picha. Utapata SMS wakijibu.', 'messages', 'Messages'),
            ('mail', 'Soma matangazo', 'Matangazo ya usimamizi yako juu kwenye Messages.', 'messages', None),
            ('home', 'Kuhama', 'Toa notisi ya mwezi mmoja kwenye ukurasa wa Moving out. Pia unaonyesha amana (deposit) yako.', 'move_out', 'Kuhama'),
            ('user', 'Usimwambie mtu PIN yako', 'Kama PIN ya M-Pesa. Kwenye simu ya pamoja, gusa Sign out ukimaliza.', None, None),
        ],
        'done': 'Nenda kwenye akaunti yangu',
        'again': 'Unaweza kufungua ukurasa huu tena kupitia "How to use Domi" kwenye ukurasa wa akaunti yako.',
    },
}


def welcome(lang):
    return WELCOME.get(lang) or WELCOME['en']
