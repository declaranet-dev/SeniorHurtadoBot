"""Diagnóstico de la sincronización de balance con el otro bot (solo lee, no cambia nada).

Uso en el servidor, desde ~/SeniorHurtadoBot:
    .venv/bin/python scripts/diagnostico_balance.py [mensajes a revisar, por defecto 400]

Muestra, por servidor de Discord: el canal que se lee, hasta qué mensaje se
sincronizó, quién escribe en el canal, cuántos mensajes del otro bot se
entienden y cuáles no (con muestra), y en qué jugadores difiere el último
saldo que mostró el otro bot de lo que tiene este bot. El token se lee de
.env y nunca se imprime.
"""

import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import BALANCE_FILE, BALANCE_SYNC_BOT_ID, get_token  # noqa: E402
from bot.cogs.balance import key_of, parse_old_bot  # noqa: E402
from bot.settings import SETTINGS_FILE  # noqa: E402

API = "https://discord.com/api/v10"
TOKEN = get_token()


def api(path: str):
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bot {TOKEN}", "User-Agent": "DiscordBot (seniorhurtadobot, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_error": e.code}


def text_of(m: dict) -> str:
    parts = [m.get("content") or ""]
    for e in m.get("embeds", []):
        parts += [e.get("title") or "", e.get("description") or ""]
        for f in e.get("fields", []):
            parts += [f.get("name") or "", f.get("value") or ""]
        if e.get("footer"):
            parts.append(e["footer"].get("text") or "")
    return "\n".join(p for p in parts if p)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    print(f"BALANCE_SYNC_BOT_ID = {BALANCE_SYNC_BOT_ID}")
    as_user = api(f"/users/{BALANCE_SYNC_BOT_ID}")
    as_channel = api(f"/channels/{BALANCE_SYNC_BOT_ID}")
    print("  como usuario:", as_user.get("_error") or f"{as_user.get('username')} bot={as_user.get('bot')}")
    print("  como canal:  ", as_channel.get("_error") or f"#{as_channel.get('name')} servidor={as_channel.get('guild_id')}")

    balance = json.loads(BALANCE_FILE.read_text(encoding="utf-8")) if BALANCE_FILE.exists() else {"servidores": {}}
    settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) if SETTINGS_FILE.exists() else {}

    for guild in api("/users/@me/guilds"):
        gid = guild["id"]
        print(f"\n=== {guild['name']} ({gid})")
        server = balance["servidores"].get(gid, {})
        saldos = server.get("saldos", {})
        conf = (settings.get(gid) or {}).get("canal_balance")
        channels = api(f"/guilds/{gid}/channels")
        if isinstance(channels, dict):
            print("  no se pudieron leer los canales:", channels)
            continue
        by_id = {c["id"]: c for c in channels}
        candidates = [c for c in channels if c["type"] == 0 and c["name"].casefold().startswith("balance")]
        print(f"  canal_balance configurado: {conf} -> {by_id.get(str(conf), {}).get('name') if conf else '-'}")
        print("  canales 'balance*':", ", ".join(f"#{c['name']} ({c['id']})" for c in candidates) or "ninguno")
        source = by_id.get(str(conf)) if conf else (candidates[0] if candidates else None)
        print(f"  jugadores: {len(saldos)} · con saldo_otro: {sum('saldo_otro' in p for p in saldos.values())}"
              f" · sync_ultimo: {server.get('sync_ultimo')}")
        if source is None:
            continue
        msgs, before = [], None
        while len(msgs) < limit:
            page = api(f"/channels/{source['id']}/messages?limit=100" + (f"&before={before}" if before else ""))
            if not isinstance(page, list):
                print("  no se pudo leer el canal:", page)
                break
            if not page:
                break
            msgs += page
            before = page[-1]["id"]
        msgs.reverse()  # del más viejo al más nuevo
        authors = Counter(f"{m['author']['username']} ({m['author']['id']}, bot={m['author'].get('bot', False)})"
                          for m in msgs)
        print(f"  últimos {len(msgs)} mensajes de #{source['name']}, autores:")
        for a, n in authors.most_common(8):
            print(f"    {n:4} {a}")
        sync_last = int(server.get("sync_ultimo") or 0)
        other = [m for m in msgs if m["author"]["id"] == str(BALANCE_SYNC_BOT_ID)]
        with_embeds = sum(bool(m.get("embeds")) for m in other)
        edited = sum(bool(m.get("edited_timestamp")) for m in other)
        print(f"  del otro bot: {len(other)} (con embed: {with_embeds}, editados: {edited}, "
              f"después de sync_ultimo: {sum(int(m['id']) > sync_last for m in other)})")
        last_seen, unparsed_old, unparsed_new = {}, [], []
        for m in other:
            old = parse_old_bot(m.get("content") or "")
            new = parse_old_bot(text_of(m))
            for name, n in new:
                last_seen[key_of(name)] = (name, n, m["timestamp"][:16])
            if not old and new:
                unparsed_old.append(m)
            if not new:
                unparsed_new.append(m)
        print(f"  que el código actual NO entiende (sí con embeds): {len(unparsed_old)}")
        print(f"  que ni así se entienden: {len(unparsed_new)}; muestra:")
        for m in (unparsed_old[-4:] + unparsed_new[-6:]):
            t = text_of(m).replace("\n", " ⏎ ")[:300]
            print(f"    [{m['timestamp'][:16]}] {t!r}")
        print("  diferencias (último saldo mostrado por el otro bot vs este bot):")
        for key, (name, n, when) in sorted(last_seen.items()):
            p = saldos.get(key)
            mine = p["saldo"] if p else None
            if mine != n:
                print(f"    {name:24} otro={n:>13,} ({when})  aquí={mine if mine is None else format(mine, ','):>13}"
                      f"  saldo_otro={p.get('saldo_otro') if p else '-'}")



if __name__ == "__main__":
    main()
