"""Разбор матча Dota 2 по данным OpenDota: перелом, кто слил катку, награды, чат, итог по игроку.

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


def gold_fed(p: dict) -> int:
    return sum(d.get("gold_fed", 0) for d in p.get("deaths_log") or [])


def shares(values: list[float]) -> list[float]:
    total = sum(values)
    return [v / total for v in values] if total else [1 / len(values)] * len(values)


def missing(values: list[float]) -> list[float]:
    """Доля «недоданного»: чем меньше вклад игрока, тем больше его доля. Сумма — 1."""
    s = shares(values)
    n = len(s)
    return [(1 - x) / (n - 1) for x in s] if n > 1 else [1.0]


def blame_section(losers: list[dict], me: dict, heroes: dict) -> str:
    """Кто вероятнее всего слил катку: вероятность по каждому игроку проигравшей команды.

    Вклад в «вину»: 45% — доля смертей команды, 20% — сколько золота отдал врагу,
    20% — мало убийств и помощи, 15% — мало урона. Ливер получает тройной вес.
    Разница усиливается возведением в куб, итог нормируется до 100%.
    """
    deaths = shares([p["deaths"] for p in losers])
    fed_values = [gold_fed(p) for p in losers]
    fed = shares(fed_values) if any(fed_values) else deaths
    low_impact = missing([p["kills"] + p["assists"] for p in losers])
    low_damage = missing([p.get("hero_damage") or 0 for p in losers])

    weights = []
    for i, p in enumerate(losers):
        w = 0.45 * deaths[i] + 0.20 * fed[i] + 0.20 * low_impact[i] + 0.15 * low_damage[i]
        if p.get("leaver_status", 0) > 1:
            w *= 3
        weights.append(w ** 3)
    chances = shares(weights)
    ranked = sorted(zip(losers, chances), key=lambda pc: -pc[1])

    worst, top = ranked[0]
    team_deaths = sum(p["deaths"] for p in losers) or 1
    reasons = [f"{worst['deaths']} из {team_deaths} смертей команды"]
    if gold_fed(worst):
        reasons.append(f"отдал врагу {fmt_int(gold_fed(worst))} золота")
    reasons.append(f"K/D/A {worst['kills']}/{worst['deaths']}/{worst['assists']}")
    if worst.get("leaver_status", 0) > 1:
        reasons.append("ушёл из игры")

    def label(p: dict) -> str:
        name = html.escape(hero_name(p, heroes))
        return f"{name} (это ты 😬)" if p is me else name

    lines = [
        "<b>🎯 Кто слил катку</b>",
        f"Скорее всего — <b>{label(worst)}</b>: {top:.0%}",
        "<i>" + ", ".join(reasons) + "</i>",
        "",
    ]
    for p, chance in ranked:
        bar = "▰" * round(chance * 10) + "▱" * (10 - round(chance * 10))
        lines.append(f"{bar} {chance:>4.0%}  {label(p)}")
    return "\n".join(lines)


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


def find_me(match: dict, account_id: int) -> dict:
    players = match["players"]
    return next((p for p in players if p.get("account_id") == account_id), players[0])


def build_card(match: dict, account_id: int, heroes: dict) -> tuple[str, str | None]:
    """Сообщение 1 — сразу после игры: результат, K/D/A, режим. Возвращает (HTML, картинка героя)."""
    me = find_me(match, account_id)
    won = is_radiant(me) == match["radiant_win"]
    mode = GAME_MODES.get(match.get("game_mode"), "Матч")
    if match.get("lobby_type") == 7:
        mode = f"Рейтинг · {mode}"
    link = f"https://www.opendota.com/matches/{match['match_id']}"
    text = "\n".join([
        f"{'🏆' if won else '💀'} <a href=\"{link}\"><b>{'ПОБЕДА' if won else 'ПОРАЖЕНИЕ'}</b></a>"
        f" — {html.escape(hero_name(me, heroes))}",
        f"⚔️ {me['kills']} / {me['deaths']} / {me['assists']}   💰 {me.get('gold_per_min', 0)} GPM   "
        f"⏱ {mmss(match['duration'])}",
        f"🎮 {html.escape(mode)} · счёт {match.get('radiant_score', 0)}:{match.get('dire_score', 0)}",
    ])
    return text, hero_image(me, heroes)


def build_analysis(match: dict, account_id: int, heroes: dict) -> str:
    """Сообщение 2 — когда реплей разобран: ход игры, кто слил, твой вклад, награды."""
    players = match["players"]
    me = find_me(match, account_id)
    team = [p for p in players if is_radiant(p) == is_radiant(me)]
    losers = [p for p in players if is_radiant(p) != match["radiant_win"]]

    analysis = [line for line in (
        turning_point(match),
        thrown_lead(match),
        me_line(me, team, heroes),
        chat_line(match, heroes),
    ) if line]

    sections = ["<b>🔎 Разбор матча</b>\n" + "\n".join(analysis), blame_section(losers, me, heroes)]
    award_lines = awards(players, heroes)
    if award_lines:
        sections.append("<b>🏅 Награды</b>\n" + "\n".join(award_lines))
    if not (match.get("od_data") or {}).get("has_parsed"):
        sections.append("<i>OpenDota не успел разобрать реплей, поэтому без перелома, вардов и чата.</i>")
    return "\n\n".join(sections)
