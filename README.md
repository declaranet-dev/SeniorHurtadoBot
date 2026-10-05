# SeniorHurtadoBot

Bot de Discord (Python + discord.py).

## Requisitos
- Python 3.12
- Dependencias en `requirements.txt`: `discord.py`, `python-dotenv`, `pillow`
- Archivo `.env` (no se sube a Git; ver `.env.example`):
  - `DISCORD_TOKEN`: token del bot
  - `REGISTRO_CHANNEL_ID`: ID del canal donde se usa `!registro` (si está vacío, funciona en cualquier canal)
  - `ALBION_SERVER`: `americas` (por defecto), `europe` o `asia`. Servidor de Albion donde se validan los jugadores.
  - `EVENTOS_CHANNEL_ID`: ID del canal donde se crean eventos.
  - `KILLBOT_GUILD_ID`, `KILLBOT_CHANNEL_ID`, `KILLBOT_DEATHS_CHANNEL_ID`, `KILLBOT_MIN_FAME`: configuración del killbot.
  - `BATTLEBOARD_CHANNEL_ID`, `BATTLE_MIN_PLAYERS`: configuración del battle board.
  - `WELCOME_CHANNEL_ID`: ID del canal de bienvenida (Discord con Modo desarrollador activo > clic derecho en el canal > Copiar ID del canal)
- En el Developer Portal: Bot > Privileged Gateway Intents > activar **Message Content Intent** y **Server Members Intent**
- En el servidor: el bot necesita un rol con **Gestionar roles** colocado por encima de `KikinJR`

## Instalación
```
py -m pip install -r requirements.txt
```

## Ejecución
```
py main.py
```
Sin Message Content Intent el bot arranca igual, pero los comandos solo responden por mensaje directo (DM).

## Comandos
- `!hurtadohelp`: muestra la ayuda.
- `/registro name guild [alianza]`: comando de barra con campos para nombre, gremio y alianza (opcional). `!registro` abre un formulario con los mismos campos. También acepta `!registro Nombre [X] Gremio [Y] Alianza [Z]` en una línea. Si el gremio es "Vecindad del Chavo" o "vecindad" (sin importar mayúsculas ni acentos) recibe el rol `chavo`; si no, `Chusma`. En ambos casos se le quita `KikinJR`. Antes de registrar, el bot consulta la API oficial de Albion (gameinfo) y solo registra si el jugador existe y está en ese gremio (y alianza, si la escribió). Se guardan los nombres oficiales. Los registros se guardan en `data/registros.json` (no se sube a Git).

- `!evento`, `!eventos` o `/evento` (solo rol `Admin` o administradores, en `EVENTOS_CHANNEL_ID`): abre un formulario con nombre, hora UTC, participantes, roles necesarios (lista desplegable) e información extra. Crea un evento de Discord y publica el anuncio en el canal con un botón por rol: los miembros pulsan su rol (o Salir) y la tabla de anotados se actualiza sola. Cada persona va en un solo rol y no se pasa del número de participantes. Los anotados se guardan en `data/eventos.json`. Las opciones de roles están en `EVENT_ROLES` de `bot/config.py`.

## Killbot
Cada 60 s consulta la API de Albion y publica las kills (verde) en `KILLBOT_CHANNEL_ID` y las muertes (rojo) en `KILLBOT_DEATHS_CHANNEL_ID` del gremio `KILLBOT_GUILD_ID`, con asesino, víctima, IP, fama, asistencias, arma y enlace al killboard. Al arrancar por primera vez no publica el historial. Las kills de todo Albion (de cualquier gremio) de 5.000.000 de fama o más (`GUCCI_MIN_FAME` en `bot/config.py`) salen en `GUCCI_CHANNEL_ID` como 💵 GUCCI KILL. Cada mensaje lleva una tarjeta (imagen) con el equipo de los dos jugadores, la fama, el valor aproximado en plata (precios de albion-online-data.com) y el inventario de la víctima, sobre el fondo `assets/fondo_vecindad.webp`. Si la tarjeta falla, el mensaje sale sin imagen. `KILLBOT_MIN_FAME` filtra por fama mínima. El último evento publicado se guarda en `data/killbot.json`.

## Battle board
Cada 3 min revisa las batallas del gremio (`KILLBOT_GUILD_ID`) y publica en `BATTLEBOARD_CHANNEL_ID` las que tienen al menos `BATTLE_MIN_PLAYERS` jugadores (10 por defecto), 10 min después de terminar: zona, duración, jugadores, kills, fama, tabla de gremios y los jugadores del gremio con sus kills/muertes/fama. Las publicadas se guardan en `data/battles.json`.

## Bienvenida
Al entrar un miembro nuevo, el bot le asigna el rol `KikinJR` y envía uno de 10 mensajes aleatorios en el canal `WELCOME_CHANNEL_ID`. Los logs llevan el prefijo `[WELCOME]`. El bot no crea roles ni cambia permisos.

## Estructura
- `main.py`: punto de entrada.
- `bot/config.py`: lee `DISCORD_TOKEN` desde `.env`.
- `bot/client.py`: clase del bot y lista `EXTENSIONS`.
- `bot/cogs/welcome.py`: bienvenida y rol KikinJR.
- `bot/cogs/registro.py`: comando `!registro` y `/registro`.
- `bot/cogs/eventos.py`: comando `!evento` y `/evento`.
- `bot/cogs/killbot.py`: killbot del gremio.
- `bot/cogs/battleboard.py`: resumen de batallas del gremio.
- `bot/killcard.py`: dibuja la tarjeta de cada kill (Pillow).
- `bot/prices.py`: precios aproximados de objetos (albion-online-data.com).
- `bot/albion.py`: cliente de la API de Albion (reutilizable para futuros comandos).
- `bot/cogs/`: un archivo por grupo de comandos. Para añadir comandos (p. ej. Albion Online), crea `bot/cogs/albion.py` y agrégalo a `EXTENSIONS`.
