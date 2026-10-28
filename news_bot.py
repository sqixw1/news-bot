"""Новостной бот для GitHub Actions: проверяет новости Dota 2 и присылает новые в Telegram.

Запускается по расписанию (.github/workflows/news.yml), делает одну проверку и завершается.
Какие новости уже отправлены, хранится в seen.json — workflow коммитит его обратно в репозиторий.
Токен и chat_id берутся из секретов репозитория: TELEGRAM_TOKEN и TELEGRAM_CHAT_ID.
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

FEEDS = {
    "Dota2.ru": "https://dota2.ru/news/rss/",
    "Dota 2 (Steam)": "https://store.steampowered.com/feeds/news/app/570/?l=russian",
}
# Оформление карточки по источнику: значок перед заголовком и перед названием источника
CATEGORIES = {
    "Dota2.ru": ("🎮", "🎮"),
    "Dota 2 (Steam)": ("🛠", "🎮"),
    "default": ("⚡️", "🌍"),
}
MAX_TELEGRAM = 10  # сколько карточек за одну проверку, остальное — одной строкой
SEEN_LIMIT = 3000

SEEN_FILE = Path(__file__).parent / "seen.json"
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def tg_call(method: str, params: dict) -> None:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=json.dumps({"chat_id": CHAT_ID, **params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=30).close()


def telegram(text: str) -> None:
    tg_call("sendMessage", {
        "text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True},
    })


def clean_text(raw: str, limit: int = 280) -> str:
    """Описание из RSS без HTML-тегов, обрезанное по слову."""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:—-") + "…"
    return text


def short_summary(text: str, limit: int = 220) -> str:
    """Первые 1–2 предложения анонса — чтобы карточка оставалась короткой."""
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    result = ""
    for s in sentences[:2]:
        if result and len(result) + len(s) > limit:
            break
        result = f"{result} {s}".strip()
    if len(result) > limit:
        result = result[:limit].rsplit(" ", 1)[0].rstrip(",.;:—-") + "…"
    return result


def page_image(link: str) -> str | None:
    """Картинка статьи (og:image) — если в самой RSS-ленте её нет."""
    try:
        # в адресе могут быть русские буквы (DW) — urllib их не принимает без кодирования
        safe_link = urllib.parse.quote(link, safe=":/?&=%#+,;@")
        req = urllib.request.Request(safe_link, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            head = resp.read(300_000).decode("utf-8", "ignore")
    except Exception:
        return None
    for tag in re.findall(r"<meta[^>]+>", head):
        if 'og:image"' in tag:
            m = re.search(r'content="([^"]+)"', tag)
            if m:
                return html.unescape(m.group(1))
    return None


def send_news(n: dict) -> None:
    """Карточка в стиле новостного канала: картинка, заголовок, пара предложений, источник."""
    lead, icon = CATEGORIES.get(n["source"], CATEGORIES["default"])
    # заголовок сам является ссылкой на статью — отдельная кнопка не нужна
    parts = [f"{lead} <a href=\"{html.escape(n['link'])}\"><b>{html.escape(n['title'])}</b></a>"]
    summary = short_summary(n["summary"])
    if summary and summary != n["title"]:
        parts.append(html.escape(summary))
    parts.append(f"{icon} {html.escape(n['source'])}")
    text = "\n\n".join(parts)

    image = n["image"] or page_image(n["link"])
    if image:
        try:
            tg_call("sendPhoto", {"photo": image, "caption": text, "parse_mode": "HTML"})
            return
        except urllib.error.HTTPError:
            pass  # Telegram не принял картинку (например, формат webp) — шлём с превью ссылки
    tg_call("sendMessage", {
        "text": text, "parse_mode": "HTML",
        "link_preview_options": {"url": n["link"], "prefer_large_media": True, "show_above_text": True},
    })


def find_image(it: ET.Element, description: str) -> str | None:
    for tag in ("thumbnail", "content"):
        el = it.find(f"{{http://search.yahoo.com/mrss/}}{tag}")
        if el is not None and el.get("url"):
            # BBC отдаёт миниатюру 240px — берём ту же картинку крупнее
            return re.sub(r"(ichef\.bbci\.co\.uk/ace/\w+)/240/", r"\1/800/", el.get("url"))
    enc = it.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image") and enc.get("url"):
        return enc.get("url")
    m = re.search(r'<img[^>]+src="([^"]+)"', html.unescape(description))
    return m.group(1) if m else None


def fetch(source: str, url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NewsBot"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        root = ET.fromstring(resp.read())
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "").timestamp()
        except (TypeError, ValueError):
            published = time.time()
        description = it.findtext("description") or ""
        items.append({
            "id": (it.findtext("guid") or link).strip(),
            "title": title,
            "link": link,
            "source": source,
            "time": published,
            "summary": clean_text(description),
            "image": find_image(it, description),
        })
    return items


def main() -> None:
    first_run = not SEEN_FILE.exists()
    seen = [] if first_run else json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    seen_set = set(seen)

    all_items = []
    for source, url in FEEDS.items():
        try:
            all_items += fetch(source, url)
        except Exception as e:  # одна упавшая лента не должна останавливать бота
            print(f"{source}: ошибка загрузки — {e}")
    if not all_items:
        return

    all_items.sort(key=lambda n: n["time"], reverse=True)
    fresh = [n for n in all_items if n["id"] not in seen_set]

    # Сначала id из текущих лент, потом старые — так при обрезке теряются только давние
    current = [n["id"] for n in all_items]
    current_set = set(current)
    seen = current + [i for i in seen if i not in current_set]
    SEEN_FILE.write_text(json.dumps(seen[:SEEN_LIMIT], ensure_ascii=False), encoding="utf-8")

    if first_run:
        # Первый запуск в облаке: не присылаем всю ленту разом, только сообщаем о переезде
        telegram("☁️ <b>Новостной бот переехал в облако</b>\n\n"
                 "Теперь новости приходят, даже когда компьютер выключен.\n\n"
                 "🌍 Мир — BBC, DW, Euronews\n🎮 Dota 2 — Dota2.ru, Steam")
        return

    for n in reversed(fresh[:MAX_TELEGRAM]):  # от старых к новым, чтобы свежая была внизу чата
        try:
            send_news(n)
        except Exception as e:
            print(f"Telegram: не отправилось «{n['title']}» — {e}")
    if len(fresh) > MAX_TELEGRAM:
        telegram(f"➕ <i>И ещё {len(fresh) - MAX_TELEGRAM} новостей — показал только самые свежие.</i>")
    print(f"новых новостей: {len(fresh)}")


if __name__ == "__main__":
    main()
