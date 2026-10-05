"""Parse IMDb Top 250 and store movie metadata in SQLite."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "imdb_top_250.db"
CHART_URL = "https://www.imdb.com/chart/top/"
REQUEST_DELAY_SECONDS = 0.4
EXPECTED_MOVIE_COUNT = 250
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Movie:
    rank: int
    imdb_id: str
    title: str
    year: int
    rating: float
    genres: list[str]
    director: str
    url: str


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def request_html(session: requests.Session, url: str) -> str:
    try:
        response = session.get(url, timeout=(10, 30))
    except requests.RequestException as error:
        raise RuntimeError(f"IMDb request failed for {url}: {error}") from error
    if response.status_code != 200:
        raise RuntimeError(
            f"IMDb returned HTTP {response.status_code} for {url}. "
            "The site may be rate-limiting automated requests; try again later."
        )
    if not response.text.strip():
        raise RuntimeError(
            f"IMDb returned an empty page for {url}; no database changes were made."
        )
    return response.text


def json_ld_objects(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    objects: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except json.JSONDecodeError:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph", [])
            objects.extend(item for item in graph if isinstance(item, dict))
            objects.append(candidate)
    return objects


def find_movie_object(html: str) -> dict[str, Any]:
    for item in json_ld_objects(html):
        movie_type = item.get("@type")
        if movie_type == "Movie" or (
            isinstance(movie_type, list) and "Movie" in movie_type
        ):
            return item
    raise RuntimeError("Could not find Movie JSON-LD metadata on an IMDb page.")


def as_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, list):
        return [
            entry["name"].strip()
            for entry in value
            if isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and entry["name"].strip()
        ]
    return []


def movie_from_chart_item(
    item: dict[str, Any], position: int
) -> tuple[int, str, str, float]:
    rank = item.get("position", position)
    movie_data = item.get("item")
    try:
        rank = int(rank)
    except (TypeError, ValueError):
        rank = position
    if not isinstance(movie_data, dict):
        raise RuntimeError("IMDb Top 250 chart entry is missing its rank or title.")
    url = movie_data.get("url")
    title = movie_data.get("name")
    rating_data = movie_data.get("aggregateRating")
    rating = rating_data.get("ratingValue") if isinstance(rating_data, dict) else None
    if not all((isinstance(url, str), isinstance(title, str), rating is not None)):
        raise RuntimeError(f"Incomplete chart metadata for rank {rank}.")
    match = re.search(r"/title/(tt\d+)/", url)
    if match is None:
        raise RuntimeError(f"IMDb title URL is malformed: {url}")
    return rank, match.group(1), title, float(rating)


def parse_year(movie_data: dict[str, Any], page_title: str = "") -> int:
    match = re.search(r"\b((?:18|19|20)\d{2})\b", page_title)
    if match:
        return int(match.group(1))
    for key in ("datePublished", "releaseDate"):
        value = movie_data.get(key)
        if isinstance(value, str):
            match = re.match(r"(\d{4})", value)
            if match:
                return int(match.group(1))
    raise RuntimeError(f"Release year is missing for {movie_data.get('name')!r}.")


def scrape_top_250() -> list[Movie]:
    session = make_session()
    chart_html = request_html(session, CHART_URL)
    chart = next(
        (
            item
            for item in json_ld_objects(chart_html)
            if item.get("@type") == "ItemList"
            and isinstance(item.get("itemListElement"), list)
        ),
        None,
    )
    if chart is None:
        raise RuntimeError(
            "IMDb Top 250 list was not found in the page's JSON-LD. "
            "IMDb may have changed its page format or blocked this request."
        )

    entries = chart["itemListElement"]
    if len(entries) != EXPECTED_MOVIE_COUNT:
        raise RuntimeError(
            f"IMDb returned {len(entries)} chart entries; expected "
            f"{EXPECTED_MOVIE_COUNT}. The database was not updated."
        )

    movies: list[Movie] = []
    for index, entry in enumerate(entries, start=1):
        rank, imdb_id, chart_title, rating = movie_from_chart_item(entry, index)
        if rank != index:
            raise RuntimeError(f"Unexpected chart rank {rank} at position {index}.")

        detail_url = f"https://www.imdb.com/title/{imdb_id}/"
        time.sleep(REQUEST_DELAY_SECONDS)
        detail_html = request_html(session, detail_url)
        detail = find_movie_object(detail_html)
        page_title = BeautifulSoup(detail_html, "html.parser").title
        detail_page_title = page_title.get_text(" ", strip=True) if page_title else ""
        genres = as_names(detail.get("genre"))
        directors = as_names(detail.get("director"))
        title = detail.get("name")
        if not isinstance(title, str) or not title.strip():
            title = chart_title
        if not genres or not directors:
            raise RuntimeError(
                f"IMDb detail metadata is incomplete for {chart_title!r}."
            )
        movies.append(
            Movie(
                rank=rank,
                imdb_id=imdb_id,
                title=chart_title.strip(),
                year=parse_year(detail, detail_page_title),
                rating=rating,
                genres=genres,
                director=", ".join(directors),
                url=detail_url,
            )
        )
        if rank % 25 == 0:
            LOGGER.info("Parsed %s of %s movies.", rank, EXPECTED_MOVIE_COUNT)
    return movies


def load_json(path: Path) -> list[Movie]:
    """Load previously parsed records, useful when IMDb requests are blocked."""
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not read parsed movie data from {path}: {error}") from error
    if not isinstance(records, list):
        raise RuntimeError("The JSON import must contain a list of movie records.")
    try:
        movies = [Movie(**record) for record in records]
    except (TypeError, KeyError) as error:
        raise RuntimeError(f"Invalid movie record in {path}: {error}") from error
    return movies


def save_movies(movies: list[Movie], database_path: Path = DATABASE_PATH) -> None:
    if len(movies) != EXPECTED_MOVIE_COUNT:
        raise ValueError(
            f"Refusing to save {len(movies)} movies; "
            f"exactly {EXPECTED_MOVIE_COUNT} are required."
        )
    if len({movie.rank for movie in movies}) != EXPECTED_MOVIE_COUNT:
        raise ValueError("Movie ranks must be unique.")
    if len({movie.imdb_id for movie in movies}) != EXPECTED_MOVIE_COUNT:
        raise ValueError("IMDb title IDs must be unique.")
    if sorted(movie.rank for movie in movies) != list(
        range(1, EXPECTED_MOVIE_COUNT + 1)
    ):
        raise ValueError("Movie ranks must range from 1 to 250.")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS movies (
                rank INTEGER PRIMARY KEY CHECK (rank BETWEEN 1 AND 250),
                imdb_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                year INTEGER NOT NULL CHECK (year > 1800),
                rating REAL NOT NULL CHECK (rating BETWEEN 0 AND 10),
                genres TEXT NOT NULL,
                director TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                scraped_at TEXT NOT NULL
            )
            """
        )
        connection.execute("DELETE FROM movies")
        connection.executemany(
            """
            INSERT INTO movies (
                rank, imdb_id, title, year, rating, genres,
                director, url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    movie.rank,
                    movie.imdb_id,
                    movie.title,
                    movie.year,
                    movie.rating,
                    json.dumps(movie.genres, ensure_ascii=False),
                    movie.director,
                    movie.url,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                )
                for movie in movies
            ],
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse IMDb Top 250 and save it to SQLite."
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        help="Import previously parsed movie records from a JSON file.",
    )
    args = parser.parse_args()

    movies = load_json(args.input_json) if args.input_json else scrape_top_250()
    save_movies(movies)
    LOGGER.info("Saved %s movies to %s", len(movies), DATABASE_PATH)


if __name__ == "__main__":
    main()
