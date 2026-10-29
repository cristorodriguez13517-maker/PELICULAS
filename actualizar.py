import json
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests

# ============================================================
# CONFIGURACIÓN SHARKTV
# ============================================================

YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()

PLAYLIST_ID = "PLaQN5GT3v1ko"

YOUTUBE_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_MOVIE_URL = "https://api.themoviedb.org/3/movie"
IMAGE_BASE = "https://image.tmdb.org/t/p/"
CATALOG_FILE = "peliculas.json"

RETRY_NOT_FOUND_HOURS = 12
MATCHER_VERSION = 3

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
# LEER TODOS LOS VÍDEOS DE YOUTUBE
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

    title = re.sub(
        r"\[[^\]]*(documental|serie|pel[ií]cula|4k|fhd|hd|1080p|720p)[^\]]*\]",
        " ",
        title,
        flags=re.IGNORECASE,
    )

    title = re.sub(
        r"\b(documental|calidad 4k|calidad fhd)\b",
        " ",
        title,
        flags=re.IGNORECASE,
    )

    return re.sub(r"\s+", " ", title).strip(" -|")


def normalize(text):
    """Normaliza títulos conservando alfabetos no latinos."""

    text = (text or "").lower().strip()
    text = unicodedata.normalize("NFKD", text)

    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )

    text = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", text)

    text = " ".join(
        "".join(
            char if (char.isalnum() or char.isspace()) else " "
            for char in text
        ).split()
    )

    return text


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
    """Busca títulos traducidos y originales, con o sin año."""

    original = clean_title(youtube_title)

    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", original)
    year = year_match.group(1) if year_match else None

    without_year = re.sub(
        r"\b(19\d{2}|20\d{2})\b",
        " ",
        original,
    )

    parenthetical = re.findall(r"\(([^()]*)\)", without_year)

    spanish_title = re.sub(
        r"\([^()]*\)",
        " ",
        without_year,
    )

    spanish_title = re.sub(
        r"\s+",
        " ",
        spanish_title,
    ).strip(" -|,;")

    title_variants = []

    if spanish_title:
        title_variants.append(spanish_title)

    for group in parenthetical:
        for part in re.split(r"[,;|]", group):
            part = re.sub(
                r"\b(19\d{2}|20\d{2})\b",
                " ",
                part,
            )

            part = re.sub(
                r"\s+",
                " ",
                part,
            ).strip(" -|,;")

            if part:
                title_variants.append(part)

    if not title_variants:
        title_variants.append(without_year.strip())

    unique_variants = []
    seen = set()

    for variant in title_variants:
        key = normalize(variant)

        if key and key not in seen:
            seen.add(key)
            unique_variants.append(variant)

    candidates = {}

    for query in unique_variants:
        for language in ("es-ES", "en-US"):
            for use_year in (True, False):
                params = {
                    "api_key": TMDB_KEY,
                    "language": language,
                    "query": query,
                    "include_adult": "false",
                    "page": 1,
                }

                if year and use_year:
                    params["year"] = year

                data = api_get(TMDB_SEARCH_URL, params)

                for movie in data.get("results", []):
                    movie_id = movie.get("id")

                    if movie_id:
                        candidates[movie_id] = movie

                time.sleep(0.1)

    best_movie = None
    best_score = -1

    wanted_names = [
        normalize(variant)
        for variant in unique_variants
        if normalize(variant)
    ]

    for movie in candidates.values():
        names = [
            normalize(movie.get("title", "")),
            normalize(movie.get("original_title", "")),
        ]

        name_score = max(
            (
                SequenceMatcher(None, wanted, name).ratio()
                for wanted in wanted_names
                for name in names
                if wanted and name
            ),
            default=0,
        )

        score = name_score

        release_date = movie.get("release_date", "")

        if year and release_date.startswith(year):
            score += 0.15

        if any(
            wanted and wanted in names
            for wanted in wanted_names
        ):
            score += 0.20

        if score > best_score:
            best_score = score
            best_movie = movie

    if not best_movie or best_score < 0.68:
        print(
            f"TMDB: no se encontró coincidencia fiable para "
            f"{youtube_title!r}. "
            f"Consultas: {unique_variants!r}; año={year!r}"
        )
        return None

    print(
        f"TMDB: {youtube_title!r} -> "
        f"{best_movie.get('title')!r} "
        f"(original: {best_movie.get('original_title')!r}, "
        f"año TMDB: {best_movie.get('release_date', '')[:4]}, "
        f"puntuación: {best_score:.2f})"
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


def format_age_rating(rating):
    """Convierte 18 en 18+, 16 en 16+, etc."""

    rating = str(rating or "").strip()

    if rating.isdigit():
        return f"{rating}+"

    return rating


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
                rating = (
                    release.get("certification") or ""
                ).strip()

                if rating:
                    return format_age_rating(rating)

    except requests.RequestException as error:
        print(
            "No se pudo consultar la clasificación por edades: "
            f"{error}"
        )

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
                return (
                    IMAGE_BASE
                    + "w780"
                    + logo["file_path"]
                )

    for logo in logos:
        if logo.get("file_path"):
            return (
                IMAGE_BASE
                + "w780"
                + logo["file_path"]
            )

    return None


# ============================================================
# PROCESAR UNA PELÍCULA
# ============================================================

def process_video(video):
    video_id = video["youtube_id"]
    youtube_title = video["youtube_title"]

    entry = {
        **video,
        "matcher_version": MATCHER_VERSION,
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
            youtube_title,
            video.get("description", ""),
        ),
        "age_rating": "",
        "match_status": "pendiente",
        "youtube_url": (
            f"https://www.youtube.com/watch?v={video_id}"
        ),
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
        print(
            f"Error de API al procesar {youtube_title!r}: {error}"
        )
        entry["match_status"] = "error_api"

    return entry


# ============================================================
# SINCRONIZAR Y GUARDAR EL CATÁLOGO
# ============================================================

def main():
    if not YOUTUBE_KEY:
        raise SystemExit(
            "Falta el secreto YOUTUBE_API_KEY en GitHub."
        )

    if not TMDB_KEY:
        raise SystemExit(
            "Falta el secreto TMDB_API_KEY en GitHub."
        )

    if not PLAYLIST_ID or PLAYLIST_ID.startswith("PEGA_AQUI"):
        raise SystemExit(
            "Configura el ID completo de tu lista de YouTube."
        )

    previous = load_previous_catalog()

    old_by_id = {
        item["youtube_id"]: item
        for item in previous
    }

    # Si falla YouTube, no se modifica el JSON.
    youtube_videos = get_youtube_videos()

    print(
        f"Vídeos encontrados en YouTube: "
        f"{len(youtube_videos)}"
    )

    catalog = []

    for video in youtube_videos:
        video_id = video["youtube_id"]
        old_entry = old_by_id.get(video_id)

        # Los registros correctos no vuelven a consultar TMDB.
        # Los no encontrados se reintentan cada 12 horas,
        # salvo que cambie el título o la versión del buscador.

        if old_entry:
            old_status = old_entry.get("match_status", "")
            old_title = old_entry.get("youtube_title", "")

            title_changed = (
                old_title != video.get("youtube_title", "")
            )

            retry_at = old_entry.get("next_retry_at", "")
            now = datetime.now(timezone.utc)
            retry_due = True

            if retry_at:
                try:
                    retry_due = now >= datetime.fromisoformat(
                        retry_at.replace("Z", "+00:00")
                    )
                except ValueError:
                    retry_due = True

            matcher_changed = (
                old_entry.get("matcher_version", 0)
                != MATCHER_VERSION
            )

            should_skip = (
                old_status == "encontrada"
                or (
                    old_status == "no_encontrada"
                    and not title_changed
                    and not retry_due
                    and not matcher_changed
                )
            )

            if should_skip:
                merged = dict(old_entry)
                merged.update(video)

                # Conservar la URL personalizada si existe.
                merged["youtube_url"] = (
                    old_entry.get("youtube_url_manual")
                    or old_entry.get("youtube_url")
                    or (
                        "https://www.youtube.com/watch?v="
                        f"{video_id}"
                    )
                )

                catalog.append(merged)
                continue

        print(f"Procesando: {video['youtube_title']}")

        entry = process_video(video)

        if entry.get("match_status") == "no_encontrada":
            now = datetime.now(timezone.utc)

            entry["last_tmdb_attempt"] = (
                now.isoformat(timespec="seconds")
            )

            entry["next_retry_at"] = (
                now + timedelta(
                    hours=RETRY_NOT_FOUND_HOURS
                )
            ).isoformat(timespec="seconds")

        elif entry.get("match_status") == "encontrada":
            entry.pop("next_retry_at", None)

            entry["last_tmdb_attempt"] = (
                datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
            )

        if old_entry:
            # =================================================
            # CORRECCIÓN: CONSERVAR IMÁGENES PERSONALIZADAS
            # =================================================

            merged = dict(old_entry)
            merged.update(entry)

            # Mantener los campos manuales anteriores.
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

            # Conservar las imágenes anteriores cuando existan.
            # Así TMDB no reemplaza una imagen que ya tenías puesta.
            for field in ("poster", "logo", "backdrop"):
                old_image = old_entry.get(field)

                if old_image:
                    merged[field] = old_image

            # Las imágenes manuales tienen prioridad absoluta.
            for field in ("poster", "logo", "backdrop"):
                manual_field = f"{field}_manual"
                manual_image = old_entry.get(manual_field)

                if manual_image:
                    merged[field] = manual_image

            # Aplicar el título manual si está configurado.
            if merged.get("title_manual"):
                merged["title"] = merged["title_manual"]

            # Aplicar la calidad manual si está configurada.
            if merged.get("quality_manual"):
                merged["quality"] = merged["quality_manual"]

            # Aplicar la clasificación manual si está configurada.
            if merged.get("age_rating_manual"):
                merged["age_rating"] = format_age_rating(
                    merged["age_rating_manual"]
                )

            # Conservar la URL personalizada.
            merged["youtube_url"] = (
                merged.get("youtube_url_manual")
                or old_entry.get("youtube_url")
                or entry["youtube_url"]
            )

            catalog.append(merged)

        else:
            # Para vídeos nuevos, utilizar los datos obtenidos.
            catalog.append(entry)

        time.sleep(0.2)

    # ============================================================
    # NORMALIZAR LAS EDADES
    # ============================================================

    for item in catalog:
        if item.get("age_rating_manual"):
            item["age_rating_manual"] = format_age_rating(
                item["age_rating_manual"]
            )

            item["age_rating"] = item["age_rating_manual"]

        elif item.get("age_rating"):
            item["age_rating"] = format_age_rating(
                item["age_rating"]
            )

    # ============================================================
    # GUARDAR EL CATÁLOGO DE FORMA SEGURA
    # ============================================================

    temp_file = CATALOG_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(
            catalog,
            file,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(temp_file, CATALOG_FILE)

    print("======================================")
    print(f"Películas guardadas: {len(catalog)}")
    print(f"Archivo actualizado: {CATALOG_FILE}")
    print("======================================")


if __name__ == "__main__":
    main()
