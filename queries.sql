-- Найти фильмы, в названии которых встречается слово (подстрока).
SELECT rank, title, year, rating, director
FROM movies
WHERE title LIKE '%' || :word || '%' COLLATE NOCASE
ORDER BY rank;

-- Топ-10 фильмов, выпущенных строго после 2015 года.
SELECT rank, title, year, rating, director
FROM movies
WHERE year > 2015
ORDER BY rating DESC, year DESC, title
LIMIT 10;

-- "=" ищет полное точное совпадение названия.
SELECT rank, title, year, rating
FROM movies
WHERE title = :title
ORDER BY rank;

-- LIKE с процентами ищет название, содержащее заданный фрагмент.
SELECT rank, title, year, rating
FROM movies
WHERE title LIKE '%' || :word || '%' COLLATE NOCASE
ORDER BY rank;

-- Удалить фильмы, выпущенные строго раньше 2005 года.
DELETE FROM movies
WHERE year < 2005;

-- Уплотнить файл базы после удаления строк.
VACUUM;
