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
3. **Roles del registro:** rol al entrar, rol para miembros del gremio, rol para la alianza, rol para otros gremios.
4. **Eventos y gremio de Albion:** rol que crea eventos; nombre del gremio (se valida con la API), servidor (americas/europe/asia), jugadores mínimos para el battle board y fama mínima de las gucci kills.

El resumen avisa si al bot le faltan permisos en un canal o si un rol está por encima del bot. Lo que no se configure simplemente no se usa en ese servidor. La configuración se guarda en `data/servidores.json`.

## Funciones
- **Bienvenida:** al entrar alguien, le da el rol de nuevos y envía uno de 10 mensajes aleatorios.
- **Registro** (`/registro name guild [alianza]` o `!registro`): revisa el personaje en la API de Albion. Si según Albion está en el gremio configurado, recibe el rol de miembros; si su gremio es de la misma alianza (p. ej. CLAY), el rol de la alianza; en cualquier otro caso (no existe, sin gremio, otro gremio o la API no responde) se registra igual con el rol de otros gremios. Se le quita el rol de nuevos y se le cambia el apodo al nombre del personaje.
- **Eventos** (`/evento` o `!evento`): asistente por pasos: 1) tipo de contenido, 2) formulario con horario UTC, lugar de salida y cantidad de jugadores (1 a 100), 3) rol de cada lugar ("Asignado por Caller" por defecto). Los roles de cada tipo salen de `roles-albion/<tipo>.txt` (un rol por línea); con más de 25 roles se ven por tandas con el botón "Más roles". Crea un evento de Discord y un anuncio con los lugares numerados; cada miembro elige su rol en una lista. El anuncio se borra 8 horas después de la hora del evento.
- **Killbot:** cada 15 s lee el feed general de Albion y publica las kills (verde) y muertes (rojo) del gremio, con una tarjeta con el equipo, la fama, el valor aproximado en plata y el inventario sobre el fondo `assets/fondo_vecindad.webp`. Las kills de todo Albion con mucha fama salen en el canal de gucci kills.
- **Battle board:** cada 3 min publica las batallas grandes del gremio (10+ jugadores en total y 5+ del gremio, configurable) como una tarjeta sobre el fondo de la vecindad: resultado, zona, duración, totales, destacados (más kills, mayor daño, más curación, peor desempeño), tabla de gremios y jugadores del gremio con kills, muertes, fama, daño y curación.
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
- `bot/battlecard.py`: dibuja la tarjeta de cada batalla.
- `roles-albion/`: roles de cada tipo de contenido para los eventos.
- `bot/prices.py`: precios aproximados de objetos (albion-online-data.com).
