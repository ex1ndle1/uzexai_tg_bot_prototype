# Birja bitimlari bo‘yicha AI-agent — vazifa

## Qisqa mazmuni

Birja bitimlari reyestri (`uzex_spot_data/`, 500 000 bitim) asosida savollarga
**rus va o‘zbek tillarida** javob beradigan AI-agent qurish. Javob aniq
raqamlar, nomlar va kodlarga tayanishi shart; ma’lumotda javob bo‘lmasa —
agent buni ochiq aytadi, o‘ylab topmaydi. Format: Telegram-bot yoki veb-servis.

## To‘liq vazifa

Birja bitimlari haqidagi savollarga rus va o‘zbek tillarida javob beradigan
AI-agent — Telegram-bot yoki veb-servis yarating.

## Asosiy talablar

- **Format: Telegram-bot yoki veb-servis** — qaysi birini tanlashni o‘zingiz hal qilasiz.
- **RU + UZ** — agent rus va o‘zbek tillarida javob berishi kerak.
- **Aniq va ma’lumotga asoslangan javob** — javob to‘g‘ri va ma’lumotlarga
  asoslangan bo‘lishi shart: aniq raqamlar, nomlar, kodlar.
- **Javob yo‘qmi — shuni ayting** — ma’lumotlarda javob bo‘lmasa, agent buni
  ochiq aytishi kerak, o‘ylab topmasligi kerak.
- **Yondashuvni o‘zingiz tanlaysiz** — agentni qanday qurish va javoblarni
  qanday taqdim etishni o‘zingiz hal qilasiz. Istalgan vositalardan, jumladan
  LLM va AI-yordamchilardan foydalanish mumkin.

## Ma’lumotlar

Manba — `uzex_spot_data/` papkasidagi rejestr:

- `deals_2022.csv` … `deals_2026.csv` — bitimlar (500 000 ta, 2022–2026)
- `contracts.csv` — shartnoma kartochkalari (6 616)
- `participants.csv` — sotuvchi va xaridor kompaniyalar (14 392)
- `brokers.csv` — broker idoralari (120)
- `usd_rates.csv` — 1 USD uchun so‘m kursi (har bir kun)

Barcha ma’lumotlar o‘ylab topilgan: kompaniyalar, STIR, brokerlar,
shartnomalar, bitimlar, narxlar va dollar kursi. Format tovar-xomashyo
birjasining ochiq bitimlar reyestrini takrorlaydi.

## Qanday hisoblanadi

- Bitim hajmi (o‘lchov birligida) = `Количество × Лот`.
- Birlik narxi = `Сумма сделки ÷ (Количество × Лот)`.
- Eksport bitimining so‘mdagi summasi = summa × bitim sanasidagi kurs
  (`usd_rates.csv`).

## Bitim holatlari

| Holat | Ma’nosi |
|---|---|
| Новый | oxirgi kun (30.10.2026) tuzilgan |
| Не подписан | hali imzolanmagan |
| Заключен | forvard yoki fyuchers imzolangan, to‘lov muddati o‘tmagan |
| Ждёт оплату | spot imzolangan, to‘lov muddati o‘tmagan |
| Оплачен | to‘langan, hali yetkazilmagan |
| Исполнен | to‘langan va to‘liq yetkazilgan |
| Исполнен(Частично) | qisman yetkazilgan (`Поставлено` ustuniga qarang) |
| Аннулирован(Не оплачен) | muddatida to‘lanmagan va bekor qilingan |
