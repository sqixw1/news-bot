"""Steam-часть бота: разбор твоих игр в Dota 2 и друзья онлайн — в Telegram.

Запускается тем же workflow после news_bot.py.
Состояние (последний матч, матчи в ожидании разбора, кто из друзей был в сети) хранится в steam_state.json,
но НЕ в репозитории, а в кэше GitHub Actions — чтобы активность друзей не была публичной.
Секреты: STEAM_ID (SteamID64), STEAM_API_KEY (https://steamcommunity.com/dev/apikey).
"""

import html
import json
import os
import time
import urllib.request
from pathlib import Path

from match_report import build_analysis, build_card
from news_bot import telegram, tg_call

STEAM_ID = os.environ.get("STEAM_ID", "")
STEAM_KEY = os.environ.get("STEAM_API_KEY", "")
# 32-битный id, который использует OpenDota
ACCOUNT_ID = int(STEAM_ID) - 76561197960265728 if STEAM_ID else 0
STATE_FILE = Path(__file__).parent / "steam_state.json"
# Ручной запуск с галочкой «demo»: прислать последний матч и кто сейчас в сети
DEMO = os.environ.get("DEMO") == "true"
MAX_MATCHES = 5  # сколько новых матчей обрабатывать за одну проверку
PARSE_WAIT_MIN = 60  # сколько ждать разбора реплея, прежде чем прислать короткий разбор


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NewsBot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def send_card(match_id: int, heroes: dict) -> int | None:
    """Сообщение 1 — сразу: картинка героя и результат. Возвращает message_id для ответа разбором."""
    match = get_json(f"https://api.opendota.com/api/matches/{match_id}")
    text, image = build_card(match, ACCOUNT_ID, heroes)
    if image:
        sent = tg_call("sendPhoto", {"photo": image, "caption": text, "parse_mode": "HTML"})
    else:
        sent = tg_call("sendMessage", {"text": text, "parse_mode": "HTML"})
    return sent.get("message_id")


def send_analysis(match_id: int, heroes: dict, reply_to: int | None) -> None:
    """Сообщение 2 — когда реплей разобран: разбор ответом на карточку матча."""
    match = get_json(f"https://api.opendota.com/api/matches/{match_id}")
    params = {"text": build_analysis(match, ACCOUNT_ID, heroes), "parse_mode": "HTML",
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


def check_matches(state: dict) -> None:
    matches = get_json(f"https://api.opendota.com/api/players/{ACCOUNT_ID}/recentMatches")
    if not matches:
        return
    last = state.get("last_match_id")
    state["last_match_id"] = max(m["match_id"] for m in matches)
    # match_id -> {"seen": когда заметили, "card": message_id карточки}
    pending = state.setdefault("pending", {})
    heroes = get_json("https://api.opendota.com/api/constants/heroes")

    if DEMO:
        latest = state["last_match_id"]
        if not is_parsed(latest):
            request_parse(latest)
        send_analysis(latest, heroes, send_card(latest, heroes))
        return
    if last is None:
        return  # первый запуск: только запоминаем, где остановились

    new = sorted(m["match_id"] for m in matches if m["match_id"] > last)
    for match_id in new[-MAX_MATCHES:]:
        card = send_card(match_id, heroes)
        request_parse(match_id)
        pending[str(match_id)] = {"seen": time.time(), "card": card}

    # Разбор уходит, когда реплей разобран — или после PARSE_WAIT_MIN минут ожидания
    for match_id, info in sorted(pending.items()):
        waited_long = time.time() - info["seen"] > PARSE_WAIT_MIN * 60
        if is_parsed(int(match_id)) or waited_long:
            send_analysis(int(match_id), heroes, info.get("card"))
            del pending[match_id]


def check_friends(state: dict) -> None:
    if not STEAM_KEY:
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


def main() -> None:
    if not STEAM_ID:
        print("STEAM_ID не задан — Steam-часть пропущена")
        return
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    for step in (check_matches, check_friends):
        try:
            step(state)
        except Exception as e:  # сбой одного сервиса не должен ломать другой
            print(f"{step.__name__}: ошибка — {e}")
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
