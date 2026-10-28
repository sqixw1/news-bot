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

from match_report import build_report
from news_bot import telegram, tg_call

STEAM_ID = os.environ.get("STEAM_ID", "")
STEAM_KEY = os.environ.get("STEAM_API_KEY", "")
# 32-битный id, который использует OpenDota
ACCOUNT_ID = int(STEAM_ID) - 76561197960265728 if STEAM_ID else 0
STATE_FILE = Path(__file__).parent / "steam_state.json"
# Ручной запуск с галочкой «demo»: прислать последний матч и кто сейчас в сети
DEMO = os.environ.get("DEMO") == "true"
MAX_MATCHES = 5  # сколько отчётов о матчах за одну проверку
PARSE_WAIT_MIN = 60  # сколько ждать разбора реплея, прежде чем прислать короткий отчёт


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NewsBot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def send_report(match_id: int, heroes: dict) -> None:
    """Одно сообщение: результат матча + разбор, с картинкой героя над текстом."""
    match = get_json(f"https://api.opendota.com/api/matches/{match_id}")
    text, image = build_report(match, ACCOUNT_ID, heroes)
    params = {"text": text, "parse_mode": "HTML"}
    if image:
        params["link_preview_options"] = {"url": image, "prefer_large_media": True, "show_above_text": True}
    else:
        params["link_preview_options"] = {"is_disabled": True}
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
    pending = state.setdefault("pending", {})  # match_id -> когда заметили новый матч

    if DEMO:
        latest = state["last_match_id"]
        if not is_parsed(latest):
            request_parse(latest)
        send_report(latest, get_json("https://api.opendota.com/api/constants/heroes"))
        return
    if last is None:
        return  # первый запуск: только запоминаем, где остановились

    for m in sorted(matches, key=lambda m: m["match_id"]):
        if m["match_id"] > last:
            request_parse(m["match_id"])
            pending[str(m["match_id"])] = time.time()

    # Отчёт уходит, когда реплей разобран — или после PARSE_WAIT_MIN минут ожидания
    heroes = None
    for match_id, seen_at in sorted(pending.items())[:MAX_MATCHES]:
        waited_long = time.time() - seen_at > PARSE_WAIT_MIN * 60
        if is_parsed(int(match_id)) or waited_long:
            heroes = heroes or get_json("https://api.opendota.com/api/constants/heroes")
            send_report(int(match_id), heroes)
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
