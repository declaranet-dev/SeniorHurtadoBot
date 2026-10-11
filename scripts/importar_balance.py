"""Carga saldos iniciales de tortas de jamón en data/balance.json.

Uso (con el bot detenido o no; solo escribe el archivo):
    .venv/bin/python scripts/importar_balance.py <id del servidor de Discord> saldos.json [--reemplazar]

saldos.json es {"Jugador": saldo, ...}. Por defecto no toca a los jugadores
que ya tienen saldo en el bot; con --reemplazar los sobrescribe.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.cogs.balance import _load, _save, _server, key_of  # noqa: E402


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    guild_id, source = sys.argv[1], Path(sys.argv[2])
    replace = "--reemplazar" in sys.argv[3:]
    saldos = json.loads(source.read_text(encoding="utf-8"))
    data = _load()
    server = _server(data, int(guild_id))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    added = skipped = 0
    for name, saldo in saldos.items():
        key = key_of(name)
        if key in server["saldos"] and not replace:
            skipped += 1
            continue
        server["saldos"][key] = {"nombre": " ".join(name.split()), "saldo": int(saldo)}
        server["historial"].append({"fecha": now, "por": None, "por_nombre": "importación",
                                    "jugador": name, "accion": "importar", "cantidad": int(saldo),
                                    "saldo": int(saldo), "razon": f"importado de {source.name}"})
        added += 1
    _save(data)
    print(f"Importados {added} jugadores, {skipped} ya existían y no se tocaron.")


if __name__ == "__main__":
    main()
