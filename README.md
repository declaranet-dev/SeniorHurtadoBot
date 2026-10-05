# SeniorHurtadoBot

Bot de Discord para gremios de Albion Online (Python + discord.py). Un mismo bot sirve para varios servidores de Discord: cada servidor tiene su propia configuración, que se hace con `/configuracion`.

## Invitar el bot a un servidor
https://discord.com/oauth2/authorize?client_id=1556691846376325131&scope=bot+applications.commands&permissions=17601178749952

Después, en el servidor:
1. Pon el rol del bot **por encima** de los roles que va a asignar (Ajustes del servidor > Roles).
2. Usa `/configuracion` (requiere el permiso "Gestionar servidor").

## /configuracion
Asistente de 4 páginas (solo lo ve quien lo usa):
1. **Canales de la comunidad:** bienvenida, registro, eventos.
2. **Canales del killbot:** kills, muertes, battle board, gucci kills.
3. **Roles:** rol al entrar, rol para miembros del gremio, rol para otros gremios, rol que crea eventos.
4. **Gremio de Albion:** nombre del gremio (se valida con la API), servidor (americas/europe/asia), jugadores mínimos para el battle board y fama mínima de las gucci kills.

El resumen avisa si al bot le faltan permisos en un canal o si un rol está por encima del bot. Lo que no se configure simplemente no se usa en ese servidor. La configuración se guarda en `data/servidores.json`.

## Funciones
- **Bienvenida:** al entrar alguien, le da el rol de nuevos y envía uno de 10 mensajes aleatorios.
- **Registro** (`/registro name guild [alianza]` o `!registro`): valida con la API de Albion que el jugador exista y esté en ese gremio. Si es del gremio configurado recibe el rol de miembros; si no, el de otros gremios. Se le quita el rol de nuevos y se le cambia el apodo al nombre del personaje.
- **Eventos** (`/evento` o `!evento`): formulario con nombre, hora UTC, participantes, roles necesarios e información extra. Crea un evento de Discord y un anuncio con botones para anotarse por rol.
- **Killbot:** cada 15 s lee el feed general de Albion y publica las kills (verde) y muertes (rojo) del gremio, con una tarjeta con el equipo, la fama, el valor aproximado en plata y el inventario sobre el fondo `assets/fondo_vecindad.webp`. Las kills de todo Albion con mucha fama salen en el canal de gucci kills.
- **Battle board:** cada 3 min publica las batallas grandes del gremio (zona, duración, kills, fama y tabla de gremios y jugadores).
- `/helphurtado` (o `!hurtadohelp`): instrucciones de instalación y de uso.

## Requisitos
- Python 3.12
- Dependencias en `requirements.txt`: `discord.py`, `python-dotenv`, `pillow`
- Archivo `.env` con `DISCORD_TOKEN` (no se sube a Git; ver `.env.example`)
- En el Developer Portal: Bot > Privileged Gateway Intents > activar **Message Content Intent** y **Server Members Intent**

## Instalación y ejecución
```
py -m pip install -r requirements.txt
py main.py
```
En el servidor (vcn-serverus) corre como servicio `seniorhurtadobot`:
```
cd ~/SeniorHurtadoBot && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart seniorhurtadobot
journalctl -u seniorhurtadobot -f
```

## Estructura
- `main.py`: punto de entrada.
- `bot/config.py`: constantes y token.
- `bot/settings.py`: configuración por servidor de Discord.
- `bot/client.py`: clase del bot y lista `EXTENSIONS`.
- `bot/cogs/`: un archivo por función (`configuracion`, `general`, `welcome`, `registro`, `eventos`, `killbot`, `battleboard`).
- `bot/albion.py`: cliente de la API de Albion.
- `bot/killcard.py`: dibuja la tarjeta de cada kill (Pillow).
- `bot/prices.py`: precios aproximados de objetos (albion-online-data.com).
