"""Разбор матча Dota 2 по данным OpenDota: перелом, кто слил, награды, чат, итог по игроку.

Подробные данные (график золота, варды, оглушения, чат) есть только у матчей,
которые OpenDota уже разобрал по реплею. Для остальных разбор получается короче.
"""

import html

GAME_MODES = {
    1: "All Pick", 2: "Captains Mode", 3: "Random Draft", 4: "Single Draft", 5: "All Random",
    16: "Captains Draft", 18: "Ability Draft", 22: "All Pick", 23: "Турбо",
}
SIDES = {True: "Силы Света", False: "Силы Тьмы"}
TURN_RATIO = 0.25   # отставание по золоту, которое считаем переломом
TURN_MINUTES = 3    # …если оно держится столько минут подряд
TURN_MIN_GOLD = 3000  # и разница не меньше этой — иначе в начале игры 25% набегает на копейках


def mmss(seconds: int) -> str:
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    return f"{sign}{seconds // 60}:{seconds % 60:02d}"


def fmt_int(n: float) -> str:
    """12345 -> «12 345»."""
    return f"{int(n):,}".replace(",", " ")


def hero_name(p: dict, heroes: dict) -> str:
    return heroes.get(str(p["hero_id"]), {}).get("localized_name", "Герой")


def hero_image(p: dict, heroes: dict) -> str | None:
    img = heroes.get(str(p["hero_id"]), {}).get("img")
    return "https://cdn.cloudflare.steamstatic.com" + img.rstrip("?") if img else None


def is_radiant(p: dict) -> bool:
    return p["player_slot"] < 128


def turning_point(match: dict) -> str | None:
    """Первая минута, с которой проигравшие отставали по золоту на 25%+ три минуты подряд."""
    players = match["players"]
    if not all(p.get("gold_t") for p in players):
        return None
    minutes = min(len(p["gold_t"]) for p in players)
    winners_radiant = match["radiant_win"]
    streak = 0
    for minute in range(minutes):
        win_gold = sum(p["gold_t"][minute] for p in players if is_radiant(p) == winners_radiant)
        lose_gold = sum(p["gold_t"][minute] for p in players if is_radiant(p) != winners_radiant)
        gap = win_gold - lose_gold
        behind = win_gold > 0 and gap >= TURN_MIN_GOLD and gap / win_gold >= TURN_RATIO
        streak = streak + 1 if behind else 0
        if streak == TURN_MINUTES:
            start = minute - TURN_MINUTES + 1
            return (f"📈 <b>Перелом:</b> с {start}:00 {SIDES[not winners_radiant]} отставали "
                    f"по золоту на {int(TURN_RATIO * 100)}%+ {TURN_MINUTES} минуты подряд")

    # Явного перелома не было — показываем, насколько далеко ушли победители
    adv = match.get("radiant_gold_adv")
    if not adv:
        return None
    lead = [a if winners_radiant else -a for a in adv]
    minute = max(range(len(lead)), key=lead.__getitem__)
    return (f"📈 <b>Ход игры:</b> явного перелома не было, {SIDES[winners_radiant]} "
            f"дожимали постепенно — максимальный отрыв {fmt_int(lead[minute])} золота к {minute}:00")


def thrown_lead(match: dict) -> str | None:
    """Сколько золота максимум вели проигравшие — если много, значит, упустили игру."""
    adv = match.get("radiant_gold_adv")
    if not adv:
        return None
    losers_radiant = not match["radiant_win"]
    lead = max((a if losers_radiant else -a) for a in adv)
    if lead < 5000:
        return None
    minute = max(range(len(adv)), key=lambda i: adv[i] if losers_radiant else -adv[i])
    return f"🫠 <b>Упустили:</b> {SIDES[losers_radiant]} вели на {fmt_int(lead)} золота к {minute}:00"


def feeder_line(team: list[dict], heroes: dict, whose: str) -> str | None:
    """Кто слил: больше всех смертей в команде, их доля и сколько золота отдал врагу."""
    team_deaths = sum(p["deaths"] for p in team)
    if not team_deaths:
        return None
    worst = max(team, key=lambda p: (p["deaths"], -p["kills"] - p["assists"]))
    share = worst["deaths"] / team_deaths
    kda = (worst["kills"] + worst["assists"]) / max(worst["deaths"], 1)
    if worst["deaths"] >= 8 and share >= 0.35 and kda < 1.5:
        verdict = "Слил?"
    elif worst["deaths"] >= 8 and share >= 0.3:
        verdict = "Под вопросом"
    else:
        return None
    fed = sum(d.get("gold_fed", 0) for d in worst.get("deaths_log") or [])
    fed_text = f", отдал врагу {fmt_int(fed)} золота" if fed else ""
    leaver = " · ушёл из игры" if worst.get("leaver_status", 0) > 1 else ""
    return (f"💀 <b>{verdict}</b> {html.escape(hero_name(worst, heroes))} ({whose}) — {worst['deaths']} из "
            f"{team_deaths} смертей команды ({share:.0%}){fed_text}{leaver}")


def awards(players: list[dict], heroes: dict) -> list[str]:
    rules = [
        ("🗡", "Урон по героям", lambda p: p.get("hero_damage") or 0, "{} урона"),
        ("💚", "Лекарь", lambda p: p.get("hero_healing") or 0, "{} лечения"),
        ("🌀", "Мастер контроля", lambda p: p.get("stuns") or 0, "оглушения: {} с"),
        ("👁", "Глаза команды", lambda p: (p.get("obs_placed") or 0) + (p.get("sen_placed") or 0), "вардов: {}"),
        ("🏰", "Сносит строения", lambda p: p.get("tower_damage") or 0, "{} по строениям"),
    ]
    lines = []
    for icon, title, value, fmt in rules:
        best = max(players, key=value)
        if value(best) > 0:
            amount = fmt.format(fmt_int(value(best)))
            lines.append(f"{icon} {title} — {html.escape(hero_name(best, heroes))} ({amount})")
    return lines


def chat_line(match: dict, heroes: dict) -> str | None:
    messages = [c for c in match.get("chat") or [] if c.get("type") == "chat"]
    if len(messages) < 3:
        return None
    by_slot = {}
    for c in messages:
        by_slot[c["player_slot"]] = by_slot.get(c["player_slot"], 0) + 1
    slot, count = max(by_slot.items(), key=lambda kv: kv[1])
    player = next((p for p in match["players"] if p["player_slot"] == slot), None)
    who = html.escape(hero_name(player, heroes)) if player else "Кто-то"
    return f"💬 <b>Чат:</b> сообщений — {len(messages)}, больше всех писал {who} ({count})"


def me_line(me: dict, team: list[dict], heroes: dict) -> str:
    team_damage = sum(p.get("hero_damage") or 0 for p in team) or 1
    nw_place = sorted(team, key=lambda p: -(p.get("net_worth") or 0)).index(me) + 1
    parts = [f"{(me.get('hero_damage') or 0) / team_damage:.0%} урона команды",
             f"{nw_place}-й по золоту в команде"]
    if me.get("teamfight_participation") is not None:
        parts.append(f"участие в драках {me['teamfight_participation']:.0%}")
    return "⚖️ <b>Ты:</b> " + ", ".join(parts)


def build_report(match: dict, account_id: int, heroes: dict) -> tuple[str, str | None]:
    """Возвращает (текст сообщения в HTML, картинку героя игрока)."""
    players = match["players"]
    me = next((p for p in players if p.get("account_id") == account_id), players[0])
    my_side = is_radiant(me)
    won = my_side == match["radiant_win"]
    team = [p for p in players if is_radiant(p) == my_side]
    enemies = [p for p in players if is_radiant(p) != my_side]

    mode = GAME_MODES.get(match.get("game_mode"), "Матч")
    if match.get("lobby_type") == 7:
        mode = f"Рейтинг · {mode}"
    link = f"https://www.opendota.com/matches/{match['match_id']}"
    gpm = me.get("gold_per_min", 0)

    head = [
        f"{'🏆' if won else '💀'} <a href=\"{link}\"><b>{'ПОБЕДА' if won else 'ПОРАЖЕНИЕ'}</b></a>"
        f" — {html.escape(hero_name(me, heroes))}",
        f"⚔️ {me['kills']} / {me['deaths']} / {me['assists']}   💰 {gpm} GPM   "
        f"⏱ {mmss(match['duration'])}",
        f"🎮 {html.escape(mode)} · счёт {match.get('radiant_score', 0)}:{match.get('dire_score', 0)}",
    ]

    analysis = [line for line in (
        turning_point(match),
        thrown_lead(match),
        feeder_line(team, heroes, "твоя команда"),
        feeder_line(enemies, heroes, "враги"),
        me_line(me, team, heroes),
        chat_line(match, heroes),
    ) if line]

    sections = ["\n".join(head), "<b>🔎 Разбор матча</b>\n" + "\n".join(analysis)]
    award_lines = awards(players, heroes)
    if award_lines:
        sections.append("<b>🏅 Награды</b>\n" + "\n".join(award_lines))
    if not (match.get("od_data") or {}).get("has_parsed"):
        sections.append("<i>OpenDota не успел разобрать реплей, поэтому без перелома, вардов и чата.</i>")
    return "\n\n".join(sections), hero_image(me, heroes)
