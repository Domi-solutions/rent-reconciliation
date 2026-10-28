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
        ]),
        'sw': ('Ujumbe', 'Maswali yako na maombi ya matengenezo, kwa maandishi, pamoja na majibu.', [
            'Maswali ya pesa yanaonekana na usimamizi tu. Matengenezo yanaonekana pia na caretaker.',
            'Utapata SMS jibu likija. Ujumbe hauwezi kubadilishwa wala kufutwa — ni kumbukumbu kwa pande zote.',
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
    'tenant.maintenance': {
        'en': ('Repairs', 'Repairs you have reported and whether they are fixed.', [
            'Tap a repair to see the conversation about it.',
            'Fixed but it broke again? Reply in the same conversation — it is reopened.',
        ]),
        'sw': ('Matengenezo', 'Matengenezo uliyoripoti na kama yamerekebishwa.', [
            'Bonyeza tengenezo kuona mazungumzo yake.',
            'Ilirekebishwa lakini imeharibika tena? Jibu kwenye mazungumzo hayo — yanafunguliwa upya.',
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
