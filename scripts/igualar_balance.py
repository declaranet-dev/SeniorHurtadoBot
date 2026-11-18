"""Iguala cada saldo al último que mostró el otro bot de balance (saldo_otro).

Uso en el servidor, desde ~/SeniorHurtadoBot (respalda data/balance.json antes):
    .venv/bin/python scripts/igualar_balance.py <id del servidor de Discord> [--aplicar]

Sin --aplicar solo muestra lo que cambiaría. Detén el bot o no: el archivo se
escribe de una vez.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.cogs.balance import MAX_HISTORY, _load, _save, _server  # noqa: E402


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    apply = "--aplicar" in sys.argv[2:]
    data = _load()
    server = _server(data, int(sys.argv[1]))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changes = 0
    for p in sorted(server["saldos"].values(), key=lambda p: p["nombre"].casefold()):
        if "saldo_otro" not in p or p["saldo"] == p["saldo_otro"]:
            continue
        changes += 1
        print(f"{p['nombre']:24} {p['saldo']:>14,} -> {p['saldo_otro']:>14,}")
        if apply:
            server["historial"].append({
                "fecha": now, "por": None, "por_nombre": "igualar con el otro bot", "jugador": p["nombre"],
                "accion": "sincronizar", "cantidad": p["saldo_otro"] - p["saldo"], "pago_deuda": 0,
                "saldo": p["saldo_otro"], "deuda": p.get("deuda", 0), "razon": "corrección de doble conteo"})
            p["saldo"] = p["saldo_otro"]
    sin = [p["nombre"] for p in server["saldos"].values() if "saldo_otro" not in p]
    if sin:
        print("Sin saldo del otro bot (no se tocan):", ", ".join(sin))
    if apply:
        server["historial"] = server["historial"][-MAX_HISTORY:]
        _save(data)
    print(f"{changes} jugadores {'corregidos' if apply else 'cambiarían (usa --aplicar)'}.")


if __name__ == "__main__":
    main()
