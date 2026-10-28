"""Steam-часть бота: разбор матчей Dota 2 для всех, кто написал боту свой Steam, и друзья онлайн владельца.

Запускается тем же workflow после news_bot.py. За один запуск:
  1. читает новые сообщения боту (ссылка на Steam — подписка, номер матча — разовый разбор);
  2. каждому подписчику присылает карточку нового матча, а после разбора реплея — разбор ответом на неё;
  3. владельцу (TELEGRAM_CHAT_ID + STEAM_ID) — ещё и сводку о друзьях в Steam.
Состояние (подписчики, последние матчи, ожидающие разбора) хранится в steam_state.json —
не в репозитории, а в кэше GitHub Actions, чтобы Steam- и Telegram-id пользователей не были публичными.
Секреты: STEAM_ID (SteamID64 владельца), STEAM_API_KEY (https://steamcommunity.com/dev/apikey).
"""

import html
import json
import os
import re
import time
import urllib.request
from pathlib import Path

from match_report import build_analysis, build_card
from news_bot import CHAT_ID, telegram, tg_call

STEAM_ID = os.environ.get("STEAM_ID", "")
STEAM_KEY = os.environ.get("STEAM_API_KEY", "")
STEAM64_BASE = 76561197960265728  # SteamID64 = это число + 32-битный id, который использует OpenDota
STATE_FILE = Path(__file__).parent / "steam_state.json"
# Ручной запуск с галочкой «demo»: прислать владельцу последний матч и кто сейчас в сети
DEMO = os.environ.get("DEMO") == "true"
MAX_MATCHES = 5  # сколько новых матчей одного игрока обрабатывать за одну проверку
PARSE_WAIT_MIN = 60  # сколько ждать разбора реплея, прежде чем прислать короткий разбор

HELP = (
    "👋 <b>Разбор матчей Dota 2</b>\n\n"
    "Пришли ссылку на свой профиль Steam — и после каждой твоей игры я пришлю результат, "
    "а когда реплей разберётся — разбор: перелом, кто слил катку, награды.\n\n"
    "Ещё можно прислать <b>номер матча</b> или ссылку на него (OpenDota, Dotabuff, Stratz) — разберу любой.\n\n"
    "<i>В Dota 2 должно быть включено: Настройки → Социальные → «Открыть доступ к данным матчей».\n"
    "Отвечаю не мгновенно: проверяю сообщения раз в несколько минут.</i>\n\n"
    "/stop — перестать присылать разборы"
)


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NewsBot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def say(chat: str, text: str) -> None:
    tg_call("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "link_preview_options": {"is_disabled": True}})


# --- разбор матчей -------------------------------------------------------------------------------

_heroes = None


def heroes() -> dict:
    global _heroes
    if _heroes is None:
        _heroes = get_json("https://api.opendota.com/api/constants/heroes")
    return _heroes


def send_card(chat: str, match_id: int, account: int | None) -> int | None:
    """Сообщение 1: картинка героя и результат. Возвращает message_id для ответа разбором."""
    match = get_json(f"https://api.opendota.com/api/matches/{match_id}")
    text, image = build_card(match, account, heroes())
    if image:
        sent = tg_call("sendPhoto", {"chat_id": chat, "photo": image, "caption": text, "parse_mode": "HTML"})
    else:
        sent = tg_call("sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML",
                                       "link_preview_options": {"is_disabled": True}})
    return sent.get("message_id")


def send_analysis(chat: str, match_id: int, account: int | None, reply_to: int | None) -> None:
    """Сообщение 2: разбор матча ответом на карточку."""
    match = get_json(f"https://api.opendota.com/api/matches/{match_id}")
    params = {"chat_id": chat, "text": build_analysis(match, account, heroes()), "parse_mode": "HTML",
              "link_preview_options": {"is_disabled": True}}
    if reply_to:
        params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    tg_call("sendMessage", params)


def request_parse(match_id: int) -> None:
    """Просим OpenDota разобрать реплей — без этого не будет перелома, вардов и чата."""
    req = urllib.request.Request(f"https://api.opendota.com/api/request/{match_id}", data=b"",
                                 headers={"User-Agent": "Mozilla/5.0 NewsBot"})
    urllib.request.urlopen(req, timeout=30).close()


def is_parsed(match_id: int) -> bool:
    match = get_json(f"https://api.opendota.com/api/matches/{match_id}")
    return bool((match.get("od_data") or {}).get("has_parsed"))


def start_report(state: dict, chat: str, match_id: int, account: int | None) -> None:
    """Карточка сейчас; разбор — сразу, если реплей готов, иначе в одной из следующих проверок."""
    card = send_card(chat, match_id, account)
    if is_parsed(match_id):
        send_analysis(chat, match_id, account, card)
        return
    request_parse(match_id)
    state.setdefault("pending", []).append(
        {"chat": chat, "match": match_id, "account": account, "card": card, "seen": time.time()})


def finish_reports(state: dict) -> None:
    """Досылаем разборы, когда реплей разобран — или после PARSE_WAIT_MIN минут ожидания."""
    still_waiting = []
    for job in state.get("pending", []):
        waited_long = time.time() - job["seen"] > PARSE_WAIT_MIN * 60
        try:
            if waited_long or is_parsed(job["match"]):
                send_analysis(job["chat"], job["match"], job["account"], job["card"])
            else:
                still_waiting.append(job)
        except Exception as e:
            print(f"разбор {job['match']}: ошибка — {e}")
            if not waited_long:
                still_waiting.append(job)
    state["pending"] = still_waiting


def check_new_matches(state: dict) -> None:
    for chat, user in state.get("users", {}).items():
        try:
            matches = get_json(f"https://api.opendota.com/api/players/{user['account']}/recentMatches")
        except Exception as e:
            print(f"матчи {chat}: ошибка — {e}")
            continue
        if not matches:
            continue
        last = user.get("last_match_id")
        user["last_match_id"] = max(m["match_id"] for m in matches)
        if last is None:
            continue  # только подписался: запоминаем, где остановились
        new = sorted(m["match_id"] for m in matches if m["match_id"] > last)
        for match_id in new[-MAX_MATCHES:]:
            start_report(state, chat, match_id, user["account"])


# --- сообщения боту ------------------------------------------------------------------------------

def resolve_account(text: str) -> int | None:
    """32-битный id игрока из ссылки на Steam/OpenDota/Dotabuff/Stratz или из SteamID64."""
    m = re.search(r"steamcommunity\.com/profiles/(\d{17})", text) or re.search(r"\b(7656\d{13})\b", text)
    if m:
        return int(m.group(1)) - STEAM64_BASE
    m = re.search(r"(?:opendota\.com|dotabuff\.com|stratz\.com)/players/(\d+)", text)
    if m:
        return int(m.group(1))
    m = re.search(r"steamcommunity\.com/id/([\w-]+)", text)
    if m:
        req = urllib.request.Request(f"https://steamcommunity.com/id/{m.group(1)}/?xml=1",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            found = re.search(r"<steamID64>(\d{17})</steamID64>", resp.read().decode("utf-8", "ignore"))
        return int(found.group(1)) - STEAM64_BASE if found else None
    return None


def resolve_match(text: str) -> int | None:
    m = re.search(r"/matches/(\d{8,11})", text) or re.fullmatch(r"\s*(\d{8,11})\s*", text)
    return int(m.group(1)) if m else None


def handle_message(state: dict, chat: str, text: str) -> None:
    users = state.setdefault("users", {})
    if text.startswith("/stop"):
        users.pop(chat, None)
        say(chat, "🔕 Больше не присылаю разборы. Чтобы вернуться — пришли ссылку на свой Steam.")
        return

    match_id = resolve_match(text)
    if match_id:
        say(chat, f"🔎 Разбираю матч {match_id}…")
        start_report(state, chat, match_id, users.get(chat, {}).get("account"))
        return

    account = resolve_account(text)
    if account is None:
        say(chat, HELP)
        return
    matches = get_json(f"https://api.opendota.com/api/players/{account}/recentMatches")
    if not matches:
        say(chat, "😕 Не вижу матчей этого профиля. Проверь, что в Dota 2 включено: "
                  "Настройки → Социальные → «Открыть доступ к данным матчей», и что профиль Steam открытый.")
        return
    profile = get_json(f"https://api.opendota.com/api/players/{account}").get("profile") or {}
    name = html.escape(profile.get("personaname") or "игрок")
    latest = max(m["match_id"] for m in matches)
    users[chat] = {"account": account, "last_match_id": latest}
    say(chat, f"✅ Запомнил: <b>{name}</b>. После каждой игры пришлю разбор. "
              "А вот твой последний матч 👇")
    start_report(state, chat, latest, account)


def read_messages(state: dict) -> None:
    first_time = "tg_offset" not in state
    updates = tg_call("getUpdates", {"offset": state.get("tg_offset", 0), "timeout": 0,
                                     "allowed_updates": ["message"]})
    for upd in updates or []:
        state["tg_offset"] = upd["update_id"] + 1
        if first_time:
            continue  # старые сообщения, написанные до появления этой функции, не разбираем
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = str((msg.get("chat") or {}).get("id", ""))
        if not chat or not text:
            continue
        try:
            handle_message(state, chat, text)
        except Exception as e:
            print(f"сообщение от {chat}: ошибка — {e}")
            say(chat, "⚠️ Что-то пошло не так. Попробуй ещё раз через несколько минут.")


# --- друзья владельца в Steam --------------------------------------------------------------------

def check_friends(state: dict) -> None:
    if not (STEAM_KEY and STEAM_ID):
        return
    api = "https://api.steampowered.com"
    friends = get_json(f"{api}/ISteamUser/GetFriendList/v1/?key={STEAM_KEY}&steamid={STEAM_ID}"
                       "&relationship=friend")["friendslist"]["friends"]
    ids = [f["steamid"] for f in friends]
    players = []
    for i in range(0, len(ids), 100):  # Steam отдаёт максимум 100 профилей за запрос
        chunk = ",".join(ids[i:i + 100])
        players += get_json(f"{api}/ISteamUser/GetPlayerSummaries/v2/?key={STEAM_KEY}"
                            f"&steamids={chunk}")["response"]["players"]

    before = state.get("friends")
    now = {p["steamid"]: {"online": p.get("personastate", 0) != 0, "game": p.get("gameextrainfo")}
           for p in players}
    state["friends"] = now

    if DEMO:
        lines = []
        for p in players:
            cur = now[p["steamid"]]
            if not cur["online"]:
                continue
            name = html.escape(p.get("personaname", "Друг"))
            lines.append(f"🎮 <b>{name}</b> — {html.escape(cur['game'])}" if cur["game"]
                         else f"🟢 <b>{name}</b>")
        telegram(f"👥 <b>Друзья в Steam</b> · {len(lines)} из {len(players)} в сети\n\n"
                 + ("\n".join(lines) if lines else "<i>Сейчас никого нет в сети.</i>"))
        return
    if before is None:
        return  # первый запуск: только запоминаем

    events = []
    for p in players:
        old = before.get(p["steamid"], {"online": False, "game": None})
        cur = now[p["steamid"]]
        name = html.escape(p.get("personaname", "Друг"))
        if cur["game"] and cur["game"] != old["game"]:
            events.append(f"🎮 <b>{name}</b> играет в {html.escape(cur['game'])}")
        elif cur["online"] and not old["online"]:
            events.append(f"🟢 <b>{name}</b> в сети")
    if events:
        # одной сводкой, чтобы не засыпать чат отдельными сообщениями
        telegram("👥 <b>Друзья в Steam</b>\n\n" + "\n".join(events))


# -------------------------------------------------------------------------------------------------

def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    # Прежний формат (один владелец) → владелец становится первым подписчиком
    users = state.setdefault("users", {})
    if STEAM_ID and str(CHAT_ID) not in users:
        users[str(CHAT_ID)] = {"account": int(STEAM_ID) - STEAM64_BASE,
                               "last_match_id": state.pop("last_match_id", None)}
    if isinstance(state.get("pending"), dict):
        state["pending"] = []
    return state


def main() -> None:
    state = load_state()
    if DEMO and STEAM_ID:
        owner = str(CHAT_ID)
        latest = max(m["match_id"] for m in get_json(
            f"https://api.opendota.com/api/players/{state['users'][owner]['account']}/recentMatches"))
        steps = [lambda s: start_report(s, owner, latest, s["users"][owner]["account"]), check_friends]
    else:
        steps = [read_messages, check_new_matches, finish_reports, check_friends]
    for step in steps:
        try:
            step(state)
        except Exception as e:  # сбой одного шага не должен ломать остальные
            print(f"{getattr(step, '__name__', 'шаг')}: ошибка — {e}")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
