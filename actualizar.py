
import json
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher

import requests

# ============================================================
# CONFIGURACIÓN SHARKTV
# ============================================================

YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()

# Sustituye este valor por el ID COMPLETO de tu lista de YouTube.
# Ejemplo de URL: https://www.youtube.com/playlist?list=ID_DE_LA_LISTA
PLAYLIST_ID = "PLaQN5GT3v1ko"

YOUTUBE_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_MOVIE_URL = "https://api.themoviedb.org/3/movie"
IMAGE_BASE = "https://image.tmdb.org/t/p/"
CATALOG_FILE = "peliculas.json"

session = requests.Session()


# ============================================================
# PETICIONES HTTP
# ============================================================

def api_get(url, params):
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


# ============================================================
# CARGAR CATÁLOGO ANTERIOR
# ============================================================

def load_previous_catalog():
    if not os.path.exists(CATALOG_FILE):
        return []

    try:
        with open(CATALOG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(
            f"No se puede leer {CATALOG_FILE}: {error}. "
            "No se modificará el catálogo."
        )

    if not isinstance(data, list):
        raise SystemExit(
            "peliculas.json debe contener una lista JSON. "
            "No se modificará el catálogo."
        )

    return [
        item for item in data
        if isinstance(item, dict) and item.get("youtube_id")
    ]


# ============================================================
# LEER TODOS LOS VÍDEOS DE LA LISTA DE YOUTUBE
# ============================================================

def get_youtube_videos():
    videos = []
    page_token = None
    seen_ids = set()

    while True:
        params = {
            "part": "snippet,contentDetails",
            "playlistId": PLAYLIST_ID,
            "maxResults": 50,
            "key": YOUTUBE_KEY,
        }

        if page_token:
            params["pageToken"] = page_token

        data = api_get(YOUTUBE_URL, params)

        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            resource = snippet.get("resourceId", {})
            video_id = resource.get("videoId")
            title = (snippet.get("title") or "").strip()

            if not video_id or video_id in seen_ids:
                continue

            if title.lower() in {
                "private video",
                "deleted video",
                "vídeo privado",
                "video privado",
                "vídeo eliminado",
                "video eliminado",
            }:
                continue

            seen_ids.add(video_id)

            videos.append({
                "youtube_id": video_id,
                "youtube_title": title,
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
            })

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return videos


# ============================================================
# LIMPIEZA DE TÍTULOS Y DETECCIÓN DE CALIDAD
# ============================================================

def clean_title(title):
    title = re.sub(
        r"\b(tráiler oficial|trailer oficial|official trailer|"
        r"trailer|película completa|pelicula completa|full movie)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\b(4k|2160p|1080p|720p|uhd|fhd|full hd|hd)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", title).strip(" -|")


def normalize(text):
    text = (text or "").lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )
    text = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def detect_quality(title, description):
    text = f"{title} {description}".lower()

    if re.search(r"\b(4k|2160p|uhd)\b", text):
        return "4K"
    if re.search(r"\b(fhd|1080p|full hd)\b", text):
        return "FHD"
    if re.search(r"\b(hd|720p)\b", text):
        return "HD"

    return ""


# ============================================================
# BUSCAR LA PELÍCULA EN TMDB
# ============================================================

def find_tmdb_movie(youtube_title):
    original = clean_title(youtube_title)

    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", original)
    year = year_match.group(1) if year_match else None

    query = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", original)
    query = re.sub(r"\s+", " ", query).strip()

    if not query:
        query = original

    if not query:
        return None

    wanted = normalize(query)
    candidates = {}

    for language in ("es-ES", "en-US"):
        params = {
            "api_key": TMDB_KEY,
            "language": language,
            "query": query,
            "include_adult": "false",
            "page": 1,
        }

        if year:
            params["year"] = year

        data = api_get(TMDB_SEARCH_URL, params)

        for movie in data.get("results", []):
            movie_id = movie.get("id")
            if movie_id:
                candidates[movie_id] = movie

        time.sleep(0.1)

    best_movie = None
    best_score = -1

    for movie in candidates.values():
        names = [
            movie.get("title", ""),
            movie.get("original_title", ""),
        ]

        name_score = max(
            (
                SequenceMatcher(
                    None, wanted, normalize(name)
                ).ratio()
                for name in names if name
            ),
            default=0,
        )

        score = name_score
        release_date = movie.get("release_date", "")

        if year:
            if release_date.startswith(year):
                score += 0.20
            elif re.match(r"^\d{4}", release_date):
                score -= 0.15

        if score > best_score:
            best_score = score
            best_movie = movie

    if not best_movie or best_score < 0.72:
        print(f"TMDB: no se encontró coincidencia fiable para {youtube_title!r}")
        return None

    print(
        f"TMDB: {youtube_title!r} -> "
        f"{best_movie.get('title')!r}"
    )
    return best_movie


# ============================================================
# DETALLES, CLASIFICACIÓN Y LOGO
# ============================================================

def get_movie_details(movie_id):
    return api_get(
        f"{TMDB_MOVIE_URL}/{movie_id}",
        {
            "api_key": TMDB_KEY,
            "language": "es-ES",
        },
    )


def get_spain_age_rating(movie_id):
    try:
        data = api_get(
            f"{TMDB_MOVIE_URL}/{movie_id}/release_dates",
            {"api_key": TMDB_KEY},
        )

        for country in data.get("results", []):
            if country.get("iso_3166_1") != "ES":
                continue

            for release in country.get("release_dates", []):
                rating = (release.get("certification") or "").strip()
                if rating:
                    return rating

    except requests.RequestException as error:
        print(f"No se pudo consultar la clasificación por edades: {error}")

    return ""


def get_movie_logo(movie_id):
    try:
        data = api_get(
            f"{TMDB_MOVIE_URL}/{movie_id}/images",
            {
                "api_key": TMDB_KEY,
                "include_image_language": "es,en,null",
            },
        )
    except requests.RequestException as error:
        print(f"No se pudo consultar el logo de TMDB: {error}")
        return None

    logos = data.get("logos", [])

    for language in ("es", "en", None):
        for logo in logos:
            if (
                logo.get("iso_639_1") == language
                and logo.get("file_path")
            ):
                return IMAGE_BASE + "w780" + logo["file_path"]

    for logo in logos:
        if logo.get("file_path"):
            return IMAGE_BASE + "w780" + logo["file_path"]

    return None


# ============================================================
# PROCESAR UNA PELÍCULA
# ============================================================

def process_video(video):
    video_id = video["youtube_id"]
    youtube_title = video["youtube_title"]

    entry = {
        **video,
        "title": clean_title(youtube_title) or youtube_title,
        "tmdb_id": None,
        "poster": None,
        "logo": None,
        "backdrop": None,
        "overview": "",
        "release_date": "",
        "runtime": None,
        "genres": [],
        "quality": detect_quality(
            youtube_title, video.get("description", "")
        ),
        "age_rating": "",
        "match_status": "pendiente",
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
    }

    try:
        movie = find_tmdb_movie(youtube_title)

        if not movie:
            entry["match_status"] = "no_encontrada"
            return entry

        movie_id = movie["id"]
        details = get_movie_details(movie_id)

        poster_path = (
            details.get("poster_path")
            or movie.get("poster_path")
        )
        backdrop_path = (
            details.get("backdrop_path")
            or movie.get("backdrop_path")
        )

        entry.update({
            "title": (
                details.get("title")
                or movie.get("title")
                or youtube_title
            ),
            "tmdb_id": movie_id,
            "poster": (
                IMAGE_BASE + "w780" + poster_path
                if poster_path else None
            ),
            "backdrop": (
                IMAGE_BASE + "original" + backdrop_path
                if backdrop_path else None
            ),
            "overview": details.get("overview", ""),
            "release_date": (
                details.get("release_date")
                or movie.get("release_date", "")
            ),
            "runtime": details.get("runtime"),
            "genres": [
                genre["name"]
                for genre in details.get("genres", [])
                if genre.get("name")
            ],
            "age_rating": get_spain_age_rating(movie_id),
            "match_status": "encontrada",
        })

        entry["logo"] = get_movie_logo(movie_id)

    except requests.RequestException as error:
        print(f"Error de API al procesar {youtube_title!r}: {error}")
        entry["match_status"] = "error_api"

    return entry


# ============================================================
# SINCRONIZAR Y GUARDAR EL CATÁLOGO
# ============================================================

def main():
    if not YOUTUBE_KEY:
        raise SystemExit("Falta el secreto YOUTUBE_API_KEY en GitHub.")

    if not TMDB_KEY:
        raise SystemExit("Falta el secreto TMDB_API_KEY en GitHub.")

    if not PLAYLIST_ID or PLAYLIST_ID.startswith("PEGA_AQUI"):
        raise SystemExit("Configura el ID completo de tu lista de YouTube.")

    previous = load_previous_catalog()
    old_by_id = {
        item["youtube_id"]: item
        for item in previous
    }

    # Si falla YouTube, no se modifica el JSON.
    youtube_videos = get_youtube_videos()

    print(f"Vídeos encontrados en YouTube: {len(youtube_videos)}")

    catalog = []

    for video in youtube_videos:
        video_id = video["youtube_id"]
        old_entry = old_by_id.get(video_id)

        # Mantener los registros ya procesados evita consultar TMDB
        # en cada ejecución de GitHub Actions.
        if old_entry and old_entry.get("match_status") not in (
            "error_api",
            "pendiente",
        ):
            merged = dict(old_entry)
            merged.update(video)

            # Respetar el enlace manual si existe.
            merged["youtube_url"] = (
                old_entry.get("youtube_url_manual")
                or old_entry.get("youtube_url")
                or f"https://www.youtube.com/watch?v={video_id}"
            )

            catalog.append(merged)
            continue

        print(f"Procesando: {video['youtube_title']}")
        entry = process_video(video)

        if old_entry:
            # Conservar los campos manuales y ajustes previos.
            merged = dict(old_entry)
            merged.update(entry)

            for field in (
                "title_manual",
                "poster_manual",
                "logo_manual",
                "backdrop_manual",
                "quality_manual",
                "age_rating_manual",
                "youtube_url_manual",
            ):
                if old_entry.get(field):
                    merged[field] = old_entry[field]

            merged["youtube_url"] = (
                merged.get("youtube_url_manual")
                or old_entry.get("youtube_url")
                or entry["youtube_url"]
            )

            catalog.append(merged)
        else:
            catalog.append(entry)

        time.sleep(0.2)

    # Escribir primero un archivo temporal y sustituir el JSON al final.
    temp_file = CATALOG_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(catalog, file, ensure_ascii=False, indent=2)

    os.replace(temp_file, CATALOG_FILE)

    print("======================================")
    print(f"Películas guardadas: {len(catalog)}")
    print(f"Archivo actualizado: {CATALOG_FILE}")
    print("======================================")


if __name__ == "__main__":
    main()
