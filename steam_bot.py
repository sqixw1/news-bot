"""Steam-часть бота: твои игры в Dota 2 и друзья онлайн — в Telegram.

Запускается тем же workflow после news_bot.py.
Состояние (последний матч, кто из друзей был в сети) хранится в steam_state.json,
но НЕ в репозитории, а в кэше GitHub Actions — чтобы активность друзей не была публичной.
Секреты: STEAM_ID (SteamID64), STEAM_API_KEY (https://steamcommunity.com/dev/apikey).
"""

import html
import json
import os
import urllib.request
from pathlib import Path

from news_bot import telegram, tg_call

STEAM_ID = os.environ.get("STEAM_ID", "")
STEAM_KEY = os.environ.get("STEAM_API_KEY", "")
# 32-битный id, который использует OpenDota
ACCOUNT_ID = int(STEAM_ID) - 76561197960265728 if STEAM_ID else 0
STATE_FILE = Path(__file__).parent / "steam_state.json"
MAX_MATCHES = 5  # сколько карточек матчей за одну проверку

GAME_MODES = {
    1: "All Pick", 2: "Captains Mode", 3: "Random Draft", 4: "Single Draft", 5: "All Random",
    18: "Ability Draft", 22: "All Pick", 23: "Турбо", 16: "Captains Draft",
}


def get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 NewsBot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def mmss(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def check_matches(state: dict) -> None:
    matches = get_json(f"https://api.opendota.com/api/players/{ACCOUNT_ID}/recentMatches")
    if not matches:
        return
    last = state.get("last_match_id")
    state["last_match_id"] = max(m["match_id"] for m in matches)
    if last is None:
        return  # первый запуск: только запоминаем, где остановились

    new = sorted((m for m in matches if m["match_id"] > last), key=lambda m: m["match_id"])
    if not new:
        return
    heroes = get_json("https://api.opendota.com/api/constants/heroes")

    for m in new[-MAX_MATCHES:]:
        hero = heroes.get(str(m["hero_id"]), {})
        hero_name = hero.get("localized_name", "Неизвестный герой")
        won = (m["player_slot"] < 128) == m["radiant_win"]
        mode = GAME_MODES.get(m["game_mode"], "Матч")
        if m.get("lobby_type") == 7:
            mode = f"Рейтинг · {mode}"
        link = f"https://www.opendota.com/matches/{m['match_id']}"
        caption = "\n\n".join([
            f"{'🏆' if won else '💀'} <a href=\"{link}\"><b>{'ПОБЕДА' if won else 'ПОРАЖЕНИЕ'}</b></a>"
            f" — {html.escape(hero_name)}",
            f"⚔️ {m['kills']} / {m['deaths']} / {m['assists']}   "
            f"💰 {m.get('gold_per_min', 0)} GPM   ⏱ {mmss(m['duration'])}",
            f"🎮 {html.escape(mode)}",
        ])
        image = "https://cdn.cloudflare.steamstatic.com" + hero["img"].rstrip("?") if hero.get("img") else None
        if image:
            tg_call("sendPhoto", {"photo": image, "caption": caption, "parse_mode": "HTML"})
        else:
            telegram(caption)


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
