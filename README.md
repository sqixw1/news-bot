# 📰 Новостной бот

Каждые 15 минут проверяет мировые новости (BBC, DW, Euronews) и новости Dota 2 (Dota2.ru, Steam) и присылает новые в Telegram карточками с картинками.

Работает бесплатно на GitHub Actions — компьютер может быть выключен.

- **Источники и оформление** — `FEEDS` и `CATEGORIES` в начале `news_bot.py`.
- **Частота** — `cron` в `.github/workflows/news.yml`.
- **Секреты** (Settings → Secrets and variables → Actions): `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
- **Выключить** — вкладка Actions → «Новости в Telegram» → `⋯` → Disable workflow.
