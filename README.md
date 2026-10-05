# IMDb Top 250 в SQLite

`main.py` загружает список IMDb Top 250 и сохраняет его в `imdb_top_250.db`.
Таблица `movies` содержит ранг, идентификатор IMDb, название, год, рейтинг,
жанры, режиссёра и ссылку. Жанры хранятся в SQLite как JSON-массив.

## Запуск в Windows PowerShell

```powershell
cd "C:\Users\Lenovo\Desktop\Course\les9"
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

Для чтения базы без установки SQLite-пакетов:

```sql
SELECT rank, title, year, rating, genres, director, url
FROM movies
ORDER BY rank;
```

Парсер прекращает работу с ошибкой, если IMDb блокирует запросы или отдаёт
неполный список, и не заменяет базу частичными данными. Повторите запуск позже.
