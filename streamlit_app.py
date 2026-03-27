import requests
import re
import io
import streamlit as st
from bs4 import BeautifulSoup, NavigableString, Tag
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from fpdf import FPDF
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

BASE_URL = "https://chantsdefrance.fr"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
}

END_KEYWORDS = ("newsletter", "a propos du chant", "boutique", "retrouvez")
ARTICLES = ["l'", "le ", "la ", "les ", "un ", "une ", "des ", "au ", "aux ", "du "]


class PDF(FPDF):
    """Classe personnalisée pour la pagination du PDF."""
    def footer(self):
        self.set_y(-15)
        self.set_font("Roboto", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def sanitize_text(text: str) -> str:
    """Nettoie le texte pour éviter les plantages de la police PDF (ex: œ)."""
    if not text:
        return ""
    return text.replace("œ", "oe").replace("Œ", "OE").replace("’", "'").replace("…", "...")


def get_sort_key_and_display(title: str) -> tuple[str, str]:
    lower = title.lower()
    for article in ARTICLES:
        if lower.startswith(article):
            rest = title[len(article):]
            if rest:
                rest = rest[0].upper() + rest[1:]
            article_display = title[:len(article)].rstrip()
            return rest.lower(), f"{rest} ({article_display})"
    
    if title:
        title = title[0].upper() + title[1:]

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


def get_song_links(playlist_url: str) -> tuple[str, list[tuple[str, str]]]:
    r = requests.get(playlist_url, headers=HEADERS, timeout=20)
    r.raise_for_status()

    # 1. On récupère le vrai titre de la playlist avec BeautifulSoup (le titre H1 est toujours visible)
    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1")
    playlist_title = h1.get_text(strip=True) if h1 else "Carnet de Chants"

    # 2. LA REGEX MAGIQUE : On cherche spécifiquement la boîte "song"
    # Cela ignore automatiquement les carnets ("book") et les auteurs ("songwriter")
    pattern = r'\\?"song\\?"\s*:\s*\{[^\}]*?\\?"slug\\?"\s*:\s*\\?"([^"\\]+)\\?",\s*\\?"title\\?"\s*:\s*\\?"([^"\\]+)\\?"'
    matches = re.findall(pattern, r.text)

    songs = {}
    for slug, title in matches:
        full_url = urljoin(BASE_URL, f"/repertoire/chants/{slug}")
        if full_url not in songs:
            # On récupère directement le titre propre depuis les données cachées !
            songs[full_url] = title

    return playlist_title, list(songs.items())


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

# --- GÉNÉRATEUR TXT ---
def generate_txt_content(playlist_title: str, songs_data: list[tuple[str, str]]) -> str:
    sorted_songs = sorted(songs_data, key=lambda s: get_sort_key_and_display(s[0])[0])
    output = io.StringIO()
    output.write(f"{playlist_title.upper()}\n")
    output.write("=" * 60 + "\n\n")
    for i, (title, lyrics) in enumerate(sorted_songs, start=1):
        _, display_title = get_sort_key_and_display(title)
        output.write(f"{'=' * 60}\n")
        output.write(f"{i}. {display_title}\n")
        output.write(f"{'=' * 60}\n\n")
        output.write(lyrics if lyrics else "(Paroles non disponibles)")
        output.write("\n\n\n")
    return output.getvalue()


# --- GÉNÉRATEUR PDF ---
def generate_pdf_content(playlist_title: str, songs_data: list[tuple[str, str]]) -> bytes:
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.add_font("Roboto", "", "fonts/RobotoRegular-3m4L.ttf")
    pdf.add_font("Roboto", "B", "fonts/RobotoBold-Xdoj.ttf")
    pdf.add_font("Roboto", "I", "fonts/RobotoItalic-W0gE.ttf")

    pdf.add_page()
    pdf.set_font("Roboto", "B", 24)
    pdf.cell(0, 80, "", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 20, "Carnet de Chants", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Roboto", "I", 16)
    pdf.multi_cell(0, 10, playlist_title, align="C")

    sorted_songs = sorted(songs_data, key=lambda s: get_sort_key_and_display(s[0])[0])

    for title, lyrics in sorted_songs:
        pdf.add_page()
        _, display_title = get_sort_key_and_display(title)
        safe_title = display_title
        pdf.start_section(safe_title)

        pdf.set_font("Roboto", "B", 18)
        pdf.multi_cell(0, 10, safe_title, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 10, "", new_x="LMARGIN", new_y="NEXT")

        pdf.set_font("Roboto", "", 12)
        if not lyrics:
            pdf.cell(0, 10, "(Paroles non disponibles)", align="C")
            continue

        for line in lyrics.split('\n'):
            clean_line = line.strip()
            if "refrain" in clean_line.lower():
                pdf.set_font("Roboto", "I", 12)
            elif clean_line == "":
                pdf.set_font("Roboto", "", 12)
            pdf.multi_cell(0, 6, clean_line, new_x="LMARGIN", new_y="NEXT", align="L")
    return bytes(pdf.output())

# --- GÉNÉRATEUR DOCX ---
def generate_docx_content(playlist_title: str, songs_data: list[tuple[str, str]]) -> bytes:
    doc = Document()
    
    # --- CONFIGURATION DES STYLES ---
    style_normal = doc.styles['Normal']
    style_normal.font.name = 'Roboto'
    style_normal.paragraph_format.space_after = Pt(0)
    
    style_heading1 = doc.styles['Heading 1']
    style_heading1.paragraph_format.space_after = Pt(18)
    
    # --- CRÉATION DU DOCUMENT ---
    # Page de garde
    titre_doc = doc.add_heading('Carnet de Chants', 0)
    titre_doc.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # On force la police directement sur le texte du titre
    for run in titre_doc.runs:
        run.font.name = 'Roboto'
        
    sous_titre = doc.add_paragraph(playlist_title)
    sous_titre.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # On force la police sur le sous-titre
    for run in sous_titre.runs:
        run.font.name = 'Roboto'

    sorted_songs = sorted(songs_data, key=lambda s: get_sort_key_and_display(s[0])[0])

    for i, (title, lyrics) in enumerate(sorted_songs):
        doc.add_page_break()
        _, display_title = get_sort_key_and_display(title)
        
        # Titre du chant centré
        heading = doc.add_heading(display_title, level=1)
        heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        # On force la police directement sur chaque titre de chant
        for run in heading.runs:
            run.font.name = 'Roboto'
        
        if not lyrics:
            doc.add_paragraph("(Paroles non disponibles)")
            continue
            
        in_refrain = False
        for line in lyrics.split('\n'):
            if not line.strip():
                in_refrain = False
                doc.add_paragraph()
                continue

            p = doc.add_paragraph()
            
            if "refrain" in line.lower():
                in_refrain = True
                
            run = p.add_run(line.strip())
            
            if in_refrain:
                run.italic = True

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# --- INTERFACE STREAMLIT ---
def main():
    st.set_page_config(page_title="LyricsGrabber", page_icon="🎵")
    
    st.title("LyricsGrabber (chantsdefrance.fr) 🎶")
    st.write("Générez un carnet prêt à imprimer ou à modifier à partir d'une playlist chantsdefrance.fr.")

    # 1. Initialisation de la mémoire
    if 'carnet_pret' not in st.session_state:
        st.session_state.carnet_pret = False

    default_url = "https://chantsdefrance.fr/repertoire/carnet-de-chant/carnet-de-chant-clan-saint-michel-archange-2040"
    playlist_url = st.text_input("Collez l'URL de la playlist :", value=default_url)

    # Sécurité : Si l'URL change, on cache les boutons de l'ancien téléchargement
    if 'last_url' not in st.session_state or st.session_state.last_url != playlist_url:
        st.session_state.carnet_pret = False
        st.session_state.last_url = playlist_url

    if st.button("Générer le Carnet"):
        if not playlist_url:
            st.warning("Veuillez entrer une URL valide.")
            return

        with st.spinner("📋 Analyse de la playlist..."):
            try:
                playlist_title, links = get_song_links(playlist_url)
            except Exception as e:
                st.error(f"Erreur de connexion : {e}")
                return

        if not links:
            st.error("❌ Aucun chant trouvé sur cette page.")
            return

        # 2. Zone d'affichage temporaire pour l'animation de chargement
        progression_container = st.empty()
        with progression_container.container():
            st.success(f"🎵 {len(links)} chants détectés dans : {playlist_title}")
            progress_bar = st.progress(0)
            status_text = st.empty()

            songs_data = []
            completed = 0
            total = len(links)

            with ThreadPoolExecutor(max_workers=30) as executor:
                futures = {executor.submit(fetch_one, url, temp_title): url for url, temp_title in links}
                
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        songs_data.append(result)
                    
                    completed += 1
                    progress_bar.progress(completed / total)
                    status_text.text(f"Téléchargement en cours : {completed}/{total} chants...")

        if songs_data:
            # On efface l'animation de chargement
            progression_container.empty()
            
            # 3. On sauvegarde les informations en mémoire pour l'affichage permanent
            st.session_state.playlist_title = playlist_title
            st.session_state.total_links = total
            st.session_state.total_downloaded = len(songs_data)
            
            st.session_state.txt_data = generate_txt_content(playlist_title, songs_data)
            st.session_state.pdf_data = generate_pdf_content(playlist_title, songs_data)
            st.session_state.docx_data = generate_docx_content(playlist_title, songs_data)
            st.session_state.safe_filename = playlist_title.replace(' ', '_').replace('/', '-')
            
            st.session_state.carnet_pret = True
        else:
            st.error("❌ Aucun texte récupéré au final.")

    # 4. AFFICHAGE PERMANENT (Indépendant du bouton "Générer")
    if st.session_state.carnet_pret:
        # Les messages sont réécrits ici à partir de la mémoire pour résister au rafraîchissement
        st.success(f"🎵 {st.session_state.total_links} chants détectés dans : {st.session_state.playlist_title}")
        st.text(f"Téléchargement en cours : {st.session_state.total_downloaded}/{st.session_state.total_links} chants...")
        st.success("✅ Téléchargement et mise en page terminés !")
        
        st.write("### 📥 Choisissez votre format de téléchargement :")

        # Injection CSS pour forcer le 3ème bouton (DOCX) en bleu
        st.markdown("""
        <style>
        div[data-testid="stColumn"]:nth-of-type(2) button,
        div[data-testid="column"]:nth-of-type(2) button {
            background-color: #007BFF !important;
            color: white !important;
            border-color: #007BFF !important;
        }
        div[data-testid="stColumn"]:nth-of-type(2) button:hover,
        div[data-testid="column"]:nth-of-type(2) button:hover {
            background-color: #0056b3 !important;
            color: white !important;
            border-color: #0056b3 !important;
        }
        </style>
        """, unsafe_allow_html=True)

        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.download_button(
                label="📄 Télécharger en TXT",
                data=st.session_state.txt_data,
                file_name=f"Carnet_{st.session_state.safe_filename}.txt",
                mime="text/plain"
            )
        with col2:
            st.download_button(
                label="📝 Télécharger en DOCX",
                data=st.session_state.docx_data,
                file_name=f"Carnet_{st.session_state.safe_filename}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
        with col3:
            st.download_button(
                label="📕 Télécharger en PDF",
                data=st.session_state.pdf_data,
                file_name=f"Carnet_{st.session_state.safe_filename}.pdf",
                mime="application/pdf",
                type="primary"
            )
        

if __name__ == "__main__":
    main()