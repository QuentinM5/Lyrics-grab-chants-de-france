import requests
import re
import io
import streamlit as st
from bs4 import BeautifulSoup, NavigableString, Tag
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://chantsdefrance.fr"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
}

END_KEYWORDS = ("newsletter", "a propos du chant", "boutique", "retrouvez")
ARTICLES = ["l'", "le ", "la ", "les ", "un ", "une ", "des ", "au ", "aux ", "du "]


def get_sort_key_and_display(title: str) -> tuple[str, str]:
    lower = title.lower()
    for article in ARTICLES:
        if lower.startswith(article):
            rest = title[len(article):]
            article_display = title[:len(article)].rstrip()
            return rest.lower(), f"{rest} ({article_display})"
    return title.lower(), title


def _collect_lines(node, current_line_parts: list[str], lines: list[str]) -> None:
    if isinstance(node, NavigableString):
        text = str(node).strip()
        if text:
            current_line_parts.append(text)
        return

    if not isinstance(node, Tag):
        return

    if node.name == "br":
        line = " ".join(current_line_parts).strip()
        if line:
            lines.append(line)
        current_line_parts.clear()
        return

    for child in node.children:
        _collect_lines(child, current_line_parts, lines)


def get_song_links(playlist_url: str) -> list[tuple[str, str]]:
    r = requests.get(playlist_url, headers=HEADERS, timeout=20)
    r.raise_for_status()

    # NOUVEAU MOTIF : On exige que le slug soit suivi d'une virgule puis de "title"
    # Cela exclut automatiquement les auteurs qui sont suivis de "firstName"
    pattern = r'\\?"slug\\?"\s*:\s*\\?"([^"\\]+)\\?",\s*\\?"title\\?"'
    matches = re.findall(pattern, r.text)

    songs = {}
    for slug in matches:
        if "carnet" in slug or "univers" in slug:
            continue
        full_url = urljoin(BASE_URL, f"/repertoire/chants/{slug}")
        if full_url not in songs:
            temp_title = slug.replace('-', ' ').title()
            songs[full_url] = temp_title

    return list(songs.items())


def get_lyrics(song_url: str) -> tuple[str, str]:
    r = requests.get(song_url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else song_url.rsplit("/", 1)[-1]
    title = title.removeprefix("Paroles de").strip()

    container = (
        soup.find("article")
        or soup.find("main")
        or soup.find(class_=lambda c: c and any(
            x in c for x in ("content", "entry", "lyrics", "paroles")
        ))
        or soup.body
    )

    strophes = []
    in_lyrics = False

    for elem in container.descendants:
        if not isinstance(elem, Tag):
            continue

        if elem.name == "h1" and not in_lyrics:
            in_lyrics = True
            continue

        if not in_lyrics:
            continue

        if elem.name in ("h2", "h3", "h4"):
            text = elem.get_text(" ", strip=True)
            if any(kw in text.lower() for kw in END_KEYWORDS):
                break

        if elem.name == "p" or elem.name == "b" and elem.parent.name not in ("p", "b"):
            lines_in_p = []
            current_line_parts = []

            for child in elem.children:
                _collect_lines(child, current_line_parts, lines_in_p)

            line = " ".join(current_line_parts).strip()
            if line:
                lines_in_p.append(line)

            if lines_in_p:
                strophes.append("\n".join(lines_in_p))

    return title, "\n\n".join(strophes).strip()


def fetch_one(url: str, temp_title: str) -> tuple[str, str] | None:
    try:
        title, lyrics = get_lyrics(url)
        return (title, lyrics)
    except Exception:
        return None


def generate_text_content(songs_data: list[tuple[str, str]]) -> str:
    """Génère le contenu texte en mémoire au lieu de l'écrire sur le disque."""
    sorted_songs = sorted(songs_data, key=lambda s: get_sort_key_and_display(s[0])[0])
    output = io.StringIO()
    
    for i, (title, lyrics) in enumerate(sorted_songs, start=1):
        _, display_title = get_sort_key_and_display(title)
        output.write(f"{'=' * 60}\n")
        output.write(f"{i}. {display_title}\n")
        output.write(f"{'=' * 60}\n\n")
        output.write(lyrics if lyrics else "(Paroles non disponibles)")
        output.write("\n\n\n")
        
    return output.getvalue()


def main():
    st.set_page_config(page_title="LyricsGrabber", page_icon="🎵")
    
    st.title("LyricsGrabber (chantsdefrance.fr) 🎶")
    st.write("Générez facilement un fichier texte contenant toutes les paroles d'un carnet de chant chantsdefrance.fr.")

    default_url = "https://chantsdefrance.fr/repertoire/carnet-de-chant/carnet-de-chant-clan-saint-michel-archange-2040"
    playlist_url = st.text_input("Collez l'URL de la playlist :", value=default_url)

    if st.button("Récupérer les paroles"):
        if not playlist_url:
            st.warning("Veuillez entrer une URL valide.")
            return

        # 1. Analyse de la playlist
        with st.spinner("📋 Analyse de la page en cours..."):
            try:
                links = get_song_links(playlist_url)
            except Exception as e:
                st.error(f"Erreur de connexion : {e}")
                return

        if not links:
            st.error("❌ Aucun chant trouvé sur cette page.")
            return

        st.success(f"🎵 {len(links)} liens trouvés. Début du téléchargement...")

        # 2. Barre de progression pour le téléchargement
        progress_bar = st.progress(0)
        status_text = st.empty()

        songs_data = []
        completed = 0
        total = len(links)

        with ThreadPoolExecutor(max_workers=20) as executor:
            # On lance toutes les requêtes en parallèle
            futures = {executor.submit(fetch_one, url, temp_title): url for url, temp_title in links}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    songs_data.append(result)
                
                # Mise à jour de la barre de progression
                completed += 1
                progress_bar.progress(completed / total)
                status_text.text(f"Téléchargement : {completed}/{total} chants traités...")

        # 3. Génération du fichier final
        if songs_data:
            st.success(f"✅ {len(songs_data)} chants récupérés et triés avec succès !")
            
            final_text = generate_text_content(songs_data)
            
            # Bouton de téléchargement natif de Streamlit
            st.download_button(
                label="📥 Télécharger le carnet (.txt)",
                data=final_text,
                file_name="paroles_playlist.txt",
                mime="text/plain"
            )
        else:
            st.error("❌ Aucun texte récupéré au final.")

if __name__ == "__main__":
    main()

