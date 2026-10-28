# 📰 Новостной бот

Каждые 15 минут проверяет новости Dota 2 (Dota2.ru и официальные новости Valve в Steam) и присылает новые в Telegram карточками с картинками.

**Разбор матчей Dota 2 для всех:** любой пишет боту [@sqixwnewsbot](https://t.me/sqixwnewsbot) ссылку на свой Steam — и после каждой игры получает результат, а потом разбор: перелом, кто слил катку, награды. Номер матча — разовый разбор любого матча. `/stop` — отписаться.

Работает бесплатно на GitHub Actions — компьютер может быть выключен.

- **Источники и оформление** — `FEEDS` и `CATEGORIES` в начале `news_bot.py`.
- **Частота** — `cron` в `.github/workflows/news.yml`.
- **Подписчики** хранятся в кэше Actions (`steam_state.json`), а не в репозитории.
- **Секреты** (Settings → Secrets and variables → Actions): `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
- **Выключить** — вкладка Actions → «Новости в Telegram» → `⋯` → Disable workflow.
