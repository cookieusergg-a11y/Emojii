# Emoji Studio

## Railway
1. Создайте проект Railway и подключите GitHub-репозиторий или загрузите проект через CLI.
2. В Variables добавьте `BOT_TOKEN` и `XROCKET_API_KEY`.
3. Deploy. Dockerfile и railway.toml уже задают запуск `python bot.py`.
4. JSON-шаблоны находятся в `templates/`.

Локально: `pip install -r requirements.txt` затем `python bot.py`.

Важно: SQLite и локальные output/packs на Railway без Volume являются эфемерными. Для постоянного хранения нужен Volume или внешняя БД.
