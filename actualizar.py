import os
import re
import json
import time
import requests
import unicodedata
from difflib import SequenceMatcher


CONFIGURACIÓN


YOUTUBE_KEY = os.environ["YOUTUBE_API_KEY"]
TMDB_KEY = os.environ["TMDB_API_KEY"]

PLAYLIST_ID = "PLaQN5GT3v1ko"

YOUTUBE_URL = "https://www.googleapis.com/youtube/v3/playlistItems"
TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
TMDB_MOVIE_URL = "https://api.themoviedb.org/3/movie"
IMAGE_BASE = "https://image.tmdb.org/t/p/"
CATALOG_FILE = "peliculas.json"

session = requests.Session()



PETICIONES A LAS API


def api_get(url, params):
response = session.get(url, params=params, timeout=30)
response.raise_for_status()
return response.json()



LEER EL CATÁLOGO ANTERIOR


def load_previous_catalog():
if not os.path.exists(CATALOG_FILE):
return []

try:
with open(CATALOG_FILE, "r", encoding="utf-8") as file:
data = json.load(file)

if not isinstance(data, list):
raise SystemExit(
"peliculas.json no contiene una lista válida. "
"Se cancela para evitar perder el catálogo."
)

return [
item for item in data
if isinstance(item, dict) and item.get("youtube_id")
 ]

except (OSError, json.JSONDecodeError) as error:
raise SystemExit(
f"No se puede leer peliculas.json: {error}. "
"No se modificará el catálogo."
)



OBTENER LA LISTA COMPLETA DE YOUTUBE


def get_youtube_videos():
videos = []
page_token = None
seen_ids = set()

while True:
params = {
"part": "snippet,contentDetails",
"playlistId": PLAYLIST_ID,
"maxResults": 50,
"key": YOUTUBE_KEY
}

if page_token:
params["pageToken"] = page_token

# Si falla cualquier página, api_get lanza un error.
# No se guardará un catálogo incompleto.
data = api_get(YOUTUBE_URL, params)

for item in data.get("items", []):
snippet = item.get("snippet", {})
resource = snippet.get("resourceId", {})
video_id = resource.get("videoId")
title = (snippet.get("title") or "").strip()

if not video_id or video_id in seen_ids:
continue

if title.lower() in (
"private video",
"deleted video",
"vídeo privado",
"video privado",
"vídeo eliminado",
"video eliminado"
):
continue

seen_ids.add(video_id)

videos.append({
"youtube_id": video_id,
"youtube_title": title,
"description": snippet.get("description", ""),
"published_at": snippet.get("publishedAt", "")
})

page_token = data.get("nextPageToken")

if not page_token:
break

return videos



LIMPIAR Y NORMALIZAR TÍTULOS


def clean_title(title):
title = re.sub(
r"\s*[[(](?:oficial|official trailer|trailer oficial|"
r"tráiler oficial|película completa|pelicula completa|"
r"full movie|4k|2160p|1080p|720p)[^])][])]\s",
" ",
title,
flags=re.IGNORECASE
)

return re.sub(r"\s+", " ", title).strip()


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



DETECTAR CALIDAD


def detect_quality(title, description):
text = f"{title} {description}".lower()

if re.search(r"\b(4k|2160p|uhd)\b", text):
return "4K"

if re.search(r"\b(fhd|1080p|full hd)\b", text):
return "FHD"

if re.search(r"\b(hd|720p)\b", text):
return "HD"

return ""



BUSCAR LA PELÍCULA EN TMDB


def find_tmdb_movie(title):
original_title = clean_title(title)

year_match = re.search(
r"\b(19\d{2}|20\d{2})\b",
original_title
)
year = year_match.group(1) if year_match else None

query = re.sub(
r"\s*[([]?(19\d{2}|20\d{2})[)]]?\s*",
" ",
original_title
)
query = re.sub(r"\s+", " ", query).strip()

if not query:
query = original_title

wanted = normalize(query)
candidates_by_id = {}

for language in ("es-ES", "en-US"):
searches = [True, False] if year else [False]

for use_year in searches:
params = {
"api_key": TMDB_KEY,
"language": language,
"query": query,
"include_adult": "false",
"page": 1
}

if use_year and year:
params["year"] = year

data = api_get(TMDB_SEARCH_URL, params)

for movie in data.get("results", []):
movie_id = movie.get("id")

if not movie_id:
continue

previous = candidates_by_id.get(movie_id)

if previous is None:
candidates_by_id[movie_id] = movie
elif (
not previous.get("poster_path")
and movie.get("poster_path")
):
candidates_by_id[movie_id] = movie

time.sleep(0.05)

if not candidates_by_id:
print(f"TMDB: no hay resultados para '{title}'")
return None

best_movie = None
best_score = -1

for movie in candidates_by_id.values():
names = [
movie.get("title", ""),
movie.get("original_title", "")
 ]

name_score = max(
(
SequenceMatcher(
None, wanted, normalize(name)
).ratio()
for name in names if name
),
default=0
)

release_date = movie.get("release_date", "")
score = name_score

if year:
if release_date.startswith(year):
score += 0.20
elif release_date and re.match(r"\d{4}", release_date):
score -= 0.15

if score > best_score:
best_score = score
best_movie = movie

if not best_movie or best_score < 0.72:
print(
f"TMDB: coincidencia insuficiente para '{title}'. "
f"Puntuación: {best_score:.2f}"
)
return None

print(
f"TMDB: '{title}' -> '{best_movie.get('title')}' "
f"(ID {best_movie.get('id')})"
)

return best_movie



DETALLES DE LA PELÍCULA


def get_movie_details(movie_id):
return api_get(
f"{TMDB_MOVIE_URL}/{movie_id}",
{
"api_key": TMDB_KEY,
"language": "es-ES"
}
)



CLASIFICACIÓN POR EDADES DE ESPAÑA


def get_spain_age_rating(movie_id):
try:
data = api_get(
f"{TMDB_MOVIE_URL}/{movie_id}/release_dates",
{"api_key": TMDB_KEY}
)

for country in data.get("results", []):
if country.get("iso_3166_1") != "ES":
continue

for release in country.get("release_dates", []):
certification = str(
release.get("certification") or ""
).strip()

if certification:
print(f"Clasificación española: {certification}")
return certification

return ""

return ""

except requests.RequestException as error:
print(f"Error al consultar clasificación: {error}")
return ""



LOGO DE LA PELÍCULA


def get_movie_logo(movie_id):
try:
data = api_get(
f"{TMDB_MOVIE_URL}/{movie_id}/images",
{
"api_key": TMDB_KEY,
"include_image_language": "es,en,null"
}
)
except requests.RequestException as error:
print(f"Error al cargar logos de {movie_id}: {error}")
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



PROCESAR UN VÍDEO NUEVO


def process_new_video(video):
title = video["youtube_title"]
description = video["description"]
video_id = video["youtube_id"]

entry = {
**video,
"title": title,
"tmdb_id": None,
"poster": None,
"logo": None,
"backdrop": None,
"overview": "",
"release_date": "",
"runtime": None,
"genres": [],
"quality": detect_quality(title, description),
"age_rating": "",
"age_rating_manual": "",
"match_status": "pendiente",
"youtube_url": (
"https://www.youtube.com/watch?v=" + video_id
)
}

try:
movie = find_tmdb_movie(title)

if not movie:
entry["match_status"] = "no_encontrada"
return entry

movie_id = movie.get("id")
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
or title
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
genre.get("name")
for genre in details.get("genres", [])
if genre.get("name")
],
"age_rating": get_spain_age_rating(movie_id),
"match_status": "encontrada"
})

entry["logo"] = get_movie_logo(movie_id)

except requests.RequestException as error:
print(f"Error de API con '{title}': {error}")
entry["match_status"] = "error_api"

return entry



SINCRONIZAR CATÁLOGO CON YOUTUBE


def main():
if not PLAYLIST_ID or "PEGA_AQUI" in PLAYLIST_ID:
raise SystemExit("Configura PLAYLIST_ID en actualizar.py.")

previous_catalog = load_previous_catalog()

existing_by_id = {
item["youtube_id"]: item
for item in previous_catalog
}

print(f"Catálogo anterior: {len(existing_by_id)} películas")

# Si esta consulta falla, el programa termina y no escribe JSON.
youtube_videos = get_youtube_videos()

print(f"Vídeos actuales en YouTube: {len(youtube_videos)}")

youtube_ids = {
video["youtube_id"]
for video in youtube_videos
}

# Solo se conservan registros que siguen en la lista.
# Los vídeos nuevos se procesan por separado.
catalog = []
new_count = 0
retry_count = 0
removed_count = len(
set(existing_by_id.keys()) - youtube_ids
)

for video in youtube_videos:
video_id = video["youtube_id"]
old_entry = existing_by_id.get(video_id)

if old_entry is None:
# Vídeo nuevo: consulta TMDB una vez.
print(f"\nNUEVO: {video['youtube_title']}")
entry = process_new_video(video)
catalog.append(entry)
new_count += 1
time.sleep(0.15)
continue

if old_entry.get("match_status") == "error_api":
# Reintentar errores anteriores sin procesar de nuevo
# el resto de películas.
print(f"\nREINTENTO: {video['youtube_title']}")
refreshed = process_new_video(video)

if refreshed.get("match_status") != "error_api":
# Mantener los campos adicionales/manuales anteriores.
merged = dict(old_entry)
merged.update(refreshed)

merged["age_rating_manual"] = (
old_entry.get("age_rating_manual") or ""
)

catalog.append(merged)
else:
# Si vuelve a fallar, conservar el registro anterior.
catalog.append(old_entry)

retry_count += 1
time.sleep(0.15)
continue

# Película ya procesada: no se consulta TMDB.
# Se conservan todos sus datos y ajustes manuales.
catalog.append(old_entry)

# Orden temporal según la lista actual de YouTube.
# Si la lista se leyó correctamente, los ausentes se eliminan.
temp_file = CATALOG_FILE + ".tmp"

with open(temp_file, "w", encoding="utf-8") as file:
json.dump(catalog, file, ensure_ascii=False, indent=2)

os.replace(temp_file, CATALOG_FILE)

print("\n===================================")
print(f"Vídeos en la lista de YouTube: {len(youtube_videos)}")
print(f"Películas conservadas: {len(catalog)}")
print(f"Películas nuevas procesadas: {new_count}")
print(f"Registros con error reintentados: {retry_count}")
print(f"Películas eliminadas del catálogo: {removed_count}")
print(f"Archivo actualizado: {CATALOG_FILE}")
print("===================================")


if name == "main":
main()

