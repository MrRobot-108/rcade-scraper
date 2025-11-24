# -*- coding: utf-8 -*-
# Version: FINAL (Cleaned, no debug output)
import os, hashlib, requests, csv, configparser, xml.etree.ElementTree as ET, base64, json, argparse, uuid, re
from pathlib import Path

# --- Constants ---
BASE_ROM_PATH, MEDIA_FOLDER, GAMELIST_XML = "/rcade/share/roms", "downloaded_images", "gamelist.xml"
SCREENSCRAPER_API = "https://www.screenscraper.fr/api2/jeuInfos.php"
SYSTEM_ID_MAP = {}

def guess_game_titles_with_gemini(filename, api_key):
    # Use Google Gemini API, to guess game name.
    if not api_key:
        return []

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    prompt = f"Based on the ROM filename \"{filename}\", what are the three most likely official game titles? Provide just the titles, one per line, no numbering, no bullet points, no extra text."
    data = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        response = requests.post(url, headers=headers, json=data, timeout=20)
        response.raise_for_status()
        result = response.json()
        content = result.get("candidates")[0].get("content").get("parts")[0].get("text")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return lines[:3]
    except requests.exceptions.RequestException as e:
        error_message = f"Gemini Guesser API request failed: {e}"
        if e.response is not None:
            error_message += f"\n-> Response: {e.response.text}"
        log_error(error_message)
        return []

def append_to_alt_romnames(csv_path, src_romname, alt_name, src_system):
    """Adds successful AI-guess to alt_rom_names.csv."""
    try:
        file_exists = os.path.isfile(csv_path)
        is_empty = not file_exists or os.path.getsize(csv_path) == 0
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f, delimiter=';')
            if is_empty:
                writer.writerow(['src_romname', 'alt_name', 'src_system', 'dest_system'])
            writer.writerow([src_romname, alt_name, src_system.lower(), ''])
        return f"[AI] Saved new mapping to CSV: '{src_romname}' -> '{alt_name}'"
    except Exception as e:
        return f"[ERROR] Could not write to alt_rom_names.csv: {e}"
		
def log_error(message):
    print(f"[ERROR-LOG] {message}")

def decode_if_base64(s):
    try:
        if isinstance(s, str) and (any(c in s for c in ['=','/','+']) or len(s)%4==0):
            decoded = base64.b64decode(s.encode()).decode("utf-8")
            if decoded.isalnum() or "@" in decoded or decoded.startswith("sk-or-"): return decoded
        return s
    except Exception: return s

def read_config(cfg_path):
    config = configparser.ConfigParser()
    config.read(cfg_path, encoding="utf-8")
    creds = {"devid": "", "devpassword": "", "ssid": "", "sspassword": "", "lang": "en"}
    if "credentials" in config:
        for key in creds:
            creds[key] = decode_if_base64(config["credentials"].get(key, creds[key]))
    return creds

def download_media(url, dest):
    if not url: return None, "[FAIL] No URL provided to download."
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        r = requests.get(url, stream=True, timeout=15)
        r.raise_for_status()
        content_type = r.headers.get('content-type', '')
        if 'text/html' in content_type:
            return None, f"[FAIL] Received HTML instead of media for: {os.path.basename(dest)}"
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1024): f.write(chunk)
        return dest, f"[SUCCESS] Saved: {os.path.basename(dest)}"
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else "N/A"
        msg = f"[FAIL] HTTP error for {os.path.basename(dest)}: Status {status_code}"
        log_error(msg)
        return None, msg
    except Exception as e:
        return None, f"[FAIL] Exception downloading media: {e}"

def sha1_hash(filepath):
    h = hashlib.sha1()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(2**20);
            if not chunk: break
            h.update(chunk)
    return h.hexdigest()

def load_alt_romnames(csv_path):
    mappings = {}
    if not csv_path or not os.path.exists(csv_path): return mappings
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            header = next(reader, None)
            if not header: return mappings

            for row in reader:
                if len(row) >= 2 and row[0].strip():
                    src_name, alt_name = row[0].strip(), row[1].strip()
                    src_system = row[2].strip().lower() if len(row) > 2 and row[2].strip() else None
                    dest_system = row[3].strip().lower() if len(row) > 3 and row[3].strip() else None
                    if src_name not in mappings: mappings[src_name] = []
                    mappings[src_name].append({'alt_name': alt_name, 'src_system': src_system, 'dest_system': dest_system})
    except Exception as e: print(f"[ERROR] Could not read alt rom names CSV '{csv_path}': {e}")
    return mappings

def update_gamelist(gamelist_path, entry_data, force=False):
    try:
        tree = ET.parse(gamelist_path) if os.path.exists(gamelist_path) else ET.ElementTree(ET.Element("gameList"))
        root = tree.getroot()
        game_el = None
        if 'rom_path' in entry_data:
            for node in root.findall("game"):
                if node.get("path") == entry_data['rom_path']:
                    game_el = node
                    break
        if game_el is None and 'rom_path' in entry_data:
            game_el = ET.SubElement(root, "game", path=entry_data["rom_path"])
        elif game_el is None:
            return
        def update_tag(parent, tag_name, text):
            if text is None: return
            el = parent.find(tag_name)
            if el is None: el = ET.SubElement(parent, tag_name)
            el.text = str(text)
        media_keys = {"image_path": "image", "video_path": "video", "thumbnail_path": "thumbnail", "marquee_path": "marquee"}
        for data_key, xml_tag_name in media_keys.items():
            if data_key in entry_data:
                update_tag(game_el, xml_tag_name, entry_data[data_key])
        metadata_keys = {"name": "name", "description": "desc", "releasedate": "releasedate", "developer": "developer", 
                         "publisher": "publisher", "genre": "genre", "players": "players"}
        if force or game_el.find("name") is None:
            for data_key, xml_tag_name in metadata_keys.items():
                if data_key in entry_data:
                    update_tag(game_el, xml_tag_name, entry_data[data_key])
        tree.write(gamelist_path, encoding="utf-8", xml_declaration=True)
    except Exception as e:
        log_error(f"Failed to update gamelist.xml for {entry_data.get('rom_path', 'N/A')}: {e}")

def query_screenscraper(creds, sha1=None, romname=None, systeme=None):
    params = {"devid": creds["devid"], "devpassword": creds["devpassword"], "ssid": creds["ssid"], "sspassword": creds["sspassword"], "softname": "lite_scraper_v2_module", "output": "json"}
    if creds.get("lang") not in [None, "", "none"]: params["langue"] = creds["lang"]
    if sha1: params["sha1"] = sha1
    elif romname: 
        system_id = SYSTEM_ID_MAP.get(systeme, systeme)
        if not system_id: return None
        params["romnom"], params["systemeid"] = romname, system_id
    else: return None
    try:
        r = requests.get(SCREENSCRAPER_API, params=params, timeout=15)
        r.raise_for_status()

        try:
            clean_text = r.text.replace(u'\xa0', u' ')
            clean_text = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', clean_text)
            data = json.loads(clean_text) 
        except json.JSONDecodeError as json_err:
            log_error(f"JSONDecodeError: {json_err}. API response was malformed and could not be parsed.")
            return None

        if "response" not in data or not isinstance(data["response"].get("jeu"), dict): return None
        return data
        
    except requests.exceptions.RequestException as e:
        status_code = e.response.status_code if e.response is not None else "N/A"
        log_error(f"Request failed for {romname or sha1}. Status: {status_code}")
        return None

def diagnose_rom(rom_name, system_name, creds, temp_dir, flags):
    downloaded_files = []
    try:
        data = query_screenscraper(creds, romname=rom_name, systeme=system_name)
        if not data:
            return {"error": f"No entry found for {rom_name}", "files": []}
        
        source_for_image = flags.get('source_for_image', 'ss')
        source_for_box = flags.get('source_for_box', 'box-2D')
        
        medias = data["response"]["jeu"].get("medias", [])
        media_list = medias if isinstance(medias, list) else [medias]
        
        downloaded_urls = set()

        for item in media_list:
            source_type = item.get("type")
            url, ext = item.get("url"), item.get("format", "dat")
            
            if not all([source_type, url, ext]) or url in downloaded_urls:
                continue

            target_types_for_this_source = []
            if source_type == source_for_image:
                target_types_for_this_source.append("image")
            if source_type == source_for_box:
                target_types_for_this_source.append("thumbnail")
            if source_type == "video":
                target_types_for_this_source.append("video")
            if source_type in ["wheel", "wheel-hd"]:
                target_types_for_this_source.append("marquee")

            if not target_types_for_this_source:
                continue

            temp_filename = f"{uuid.uuid4()}.{ext}"
            new_file_path, log_msg = download_media(url, os.path.join(temp_dir, temp_filename))
            
            if new_file_path:
                downloaded_urls.add(url)
                for target_type in target_types_for_this_source:
                    downloaded_files.append({"filename": os.path.basename(new_file_path), "media_type": target_type})

        return {"files": downloaded_files}

    except Exception as e:
        log_error(f"Diagnose exception: {e}")
        return {"error": str(e), "files": []}

def scrape_rom(rom_path_str, xml_path_str, system_name, creds, alt_mappings, flags, google_api_key=None, alt_rom_csv_path=None):
    rom = Path(rom_path_str)
    romname = rom.stem
    gamelist_path = os.path.join(BASE_ROM_PATH, system_name, GAMELIST_XML)

    save_in_rom_dir = flags.get('save_media_in_rom_dir', False)
    media_folder_name = flags.get('name_media_dir', MEDIA_FOLDER)

    media_dir = ""
    if save_in_rom_dir:
        rom_directory = os.path.dirname(rom_path_str)
        media_dir = os.path.join(rom_directory, media_folder_name)
    else:
        media_dir = os.path.join(BASE_ROM_PATH, system_name, media_folder_name)
    
    source_for_image = flags.get('source_for_image', 'ss')
    source_for_box = flags.get('source_for_box', 'box-2D')
    media_source_map = {
        source_for_image: "image",
        source_for_box: "thumbnail",
        "video": "video",
        "wheel": "marquee",
        "wheel-hd": "marquee"
    }

    media_types = list(set(media_source_map.values()))

    try:
        gamelist_tree = ET.parse(gamelist_path) if os.path.exists(gamelist_path) else None
    except ET.ParseError:
        yield f"[ERROR] Could not parse gamelist.xml for system {system_name}. It might be corrupt."
        gamelist_tree = None

    game_node = None
    if gamelist_tree:
        for node in gamelist_tree.getroot().findall("game"):
            if node.get("path") == xml_path_str:
                game_node = node
                break
    
    final_media_status = {mtype: False for mtype in media_types}
    has_absolute_path_in_tag = False
    if game_node is not None:
        for media_type in media_types:
            tag = game_node.find(media_type)
            if tag is not None and tag.text and tag.text.strip():
                path_from_tag = tag.text.strip()
                full_path_to_check = path_from_tag if path_from_tag.startswith('/') else os.path.join(os.path.dirname(gamelist_path), path_from_tag)
                if os.path.exists(full_path_to_check):
                    final_media_status[media_type] = True
                if path_from_tag.startswith('/'):
                    has_absolute_path_in_tag = True

    has_all_metadata = False
    if game_node is not None:
        if game_node.find("name") is not None and game_node.find("name").text:
            has_all_metadata = True
            
    if has_all_metadata and not flags.get('force') and not flags.get('force_metadata'):
        local_files_found = {}
        all_local_files_ok = True
        suffix_map = {"image": "image", "video": "video", "marquee": "marquee", "thumbnail": "thumb"}
        
        for media_type in media_types:
            if not flags.get(f"scrape_{media_type}", True):
                continue

            if final_media_status[media_type]:
                local_files_found[media_type] = game_node.find(media_type).text
                continue

            suffix = suffix_map.get(media_type)
            found_files = list(Path(media_dir).glob(f"{romname}-{suffix}.*"))
            
            if found_files:
                relative_path = f"./{os.path.relpath(found_files[0], os.path.dirname(gamelist_path)).replace(os.sep, '/')}"
                local_files_found[media_type] = relative_path
            else:
                all_local_files_ok = False
                break
                
        if all_local_files_ok:
            yield f"[SKIP] Metadata present. Linking existing local media for '{romname}'."
            entry_data = {"rom_path": xml_path_str}
            for media_type, path in local_files_found.items():
                entry_data[f"{media_type}_path"] = path
            update_gamelist(gamelist_path, entry_data, force=False)
            return

    if not flags.get('force') and all(final_media_status.values()) and not (flags.get('removestockpics') and has_absolute_path_in_tag):
        yield f"[SKIP] All media files are present and no action is required for '{romname}'."
        if game_node is None or game_node.find("name") is None:
            update_gamelist(gamelist_path, {"rom_path": xml_path_str, "name": romname})
        return

    yield f"--- [SCRAPE] Processing '{romname}' ---"
    data = None
    
    if rom.is_file() and rom.suffix.lower() not in ['.daphne', '.singe']:
        data = query_screenscraper(creds, sha1=sha1_hash(rom))
        if data:
            jeu = data.get("response", {}).get("jeu", {})
            if jeu.get("notgame") == 'true':
                yield f"[INFO] SHA1 match found a 'notgame' entry. Discarding result and falling back to name search."
                data = None
            else:
                yield f"[INFO] Found match via SHA1 Hash."

    if not data:
        data = query_screenscraper(creds, romname=romname, systeme=system_name)
        if data: yield f"[INFO] Found match via ROM Name."
        
    if not data and romname in alt_mappings:
        for alt in alt_mappings[romname]:
            if alt['src_system'] is None or alt['src_system'] == system_name.lower():
                alt_romname, alt_system = alt['alt_name'], alt.get('dest_system') or system_name
                yield f"[ALT] Trying alternative name: '{alt_romname}' on system '{alt_system}'..."
                data = query_screenscraper(creds, romname=alt_romname, systeme=alt_system)
                if data:
                    yield f"[INFO] Found match via Alternative Name ('{alt_romname}')."
                    break

    if not data and google_api_key:
        yield f"[AI] No match found. Trying to guess game name with Gemini for '{rom.name}'..."
        guessed_titles = guess_game_titles_with_gemini(rom.name, google_api_key)
        if not guessed_titles:
            yield "[AI] Could not get guesses from Gemini."
        else:
            for i, title in enumerate(guessed_titles):
                yield f"[AI] Trying guess #{i+1}: '{title}'..."
                data = query_screenscraper(creds, romname=title, systeme=system_name)
                if data:
                    yield f"[INFO] Found match via AI Guess ('{title}')."
                    if alt_rom_csv_path:
                        yield append_to_alt_romnames(alt_rom_csv_path, romname, title, system_name)
                        if romname not in alt_mappings:
                            alt_mappings[romname] = []
                        alt_mappings[romname].append({'alt_name': title, 'src_system': system_name.lower(), 'dest_system': None})
                        yield "[AI] In-memory mapping updated for current session."
                    break
    
    if data:
        jeu = data["response"]["jeu"]
        scraped_name = jeu.get("noms")[0].get("text") if jeu.get("noms") else jeu.get("nom")
        entry = {"rom_path": xml_path_str, "name": scraped_name or romname}
        entry.update({
            "description": next((s.get("text", "") for s in jeu.get("synopsis", []) if s.get("langue") == creds.get("lang")), ""),
            "developer": jeu.get("developpeur", {}).get("text", ""), "publisher": jeu.get("editeur", {}).get("text", ""),
            "players": jeu.get("joueurs", {}).get("text", ""), "genre": next((g["noms"][0].get("text", "") for g in jeu.get("genres", []) if g.get("noms")), ""),
            "releasedate": f"{next((d.get('text', '') for d in jeu.get('dates', [])), '')}0101T000000"
        })
        
        source_for_image = flags.get('source_for_image', 'ss')
        source_for_box = flags.get('source_for_box', 'box-2D')
        
        media_options = {mtype: [] for mtype in media_types}
        api_medias = jeu.get("medias", [])
        
        for item in api_medias if isinstance(api_medias, list) else [api_medias]:
            source_type = item.get("type")
            if source_type == source_for_image and "image" in media_options:
                media_options["image"].append(item)
            if source_type == source_for_box and "thumbnail" in media_options:
                media_options["thumbnail"].append(item)
            if source_type == "video" and "video" in media_options:
                media_options["video"].append(item)
            if source_type in ["wheel", "wheel-hd"] and "marquee" in media_options:
                media_options["marquee"].append(item)

        downloaded_files_count = 0
        for target_type, options in media_options.items():
            if not flags.get(f"scrape_{target_type}", True):
                continue
            if not options: continue
            
            is_present = final_media_status.get(target_type, False)
            is_absolute = False
            if game_node:
                tag = game_node.find(target_type)
                if tag is not None and tag.text and tag.text.strip().startswith('/'):
                    is_absolute = True

            should_download = flags.get('force') or not is_present or (is_absolute and flags.get('removestockpics'))
            
            if should_download:
                strategy = flags.get(f"strategy_for_{target_type}", "best_resolution")
                chosen_option = None

                if strategy == "first":
                    chosen_option = options[0]
                elif strategy == "last":
                    chosen_option = options[-1]
                elif strategy == "largest_size":
                    chosen_option = max(options, key=lambda x: int(x.get('size', 0)))
                elif strategy == "smallest_size":
                    chosen_option = min(options, key=lambda x: int(x.get('size', 0)))
                else: 
                    chosen_option = max(options, key=lambda x: int(x.get('width', 0)) * int(x.get('height', 0)))

                if not chosen_option: continue

                url, ext = chosen_option.get("url"), chosen_option.get("format", "dat")
                
                suffix = "thumb" if target_type == "thumbnail" else target_type
                filename = f"{romname}-{suffix}.{ext}"
                destination_path = os.path.join(media_dir, filename)

                if os.path.exists(destination_path) and not flags.get('force'):
                    yield f"[SKIP] Media file already exists: {filename}"
                    entry[f"{target_type}_path"] = f"./{os.path.relpath(destination_path, os.path.dirname(gamelist_path)).replace(os.sep, '/')}"
                    continue

                new_file_path, log_msg = download_media(url, destination_path)
                yield log_msg
                if new_file_path is not None:
                    entry[f"{target_type}_path"] = f"./{os.path.relpath(new_file_path, os.path.dirname(gamelist_path)).replace(os.sep, '/')}"
                    downloaded_files_count += 1
        
        if downloaded_files_count > 0: yield f"[SUCCESS] Downloaded {downloaded_files_count} new media file(s) for '{romname}'."
        else: yield f"[SUCCESS] No new media downloaded. Updating gamelist entry for '{romname}'."
        update_gamelist(gamelist_path, entry, force=(flags.get('force') or flags.get('force_metadata')))
    else:
        yield f"[FAIL] No match found for '{romname}' after all attempts."
        if not google_api_key:
            yield "[INFO] Tip: Add a free Google AI API key in Advanced Settings to improve results for difficult filenames."

# --- Standalone Execution Block ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone Scraper CLI")
    parser.add_argument("--system", required=True, help="System folder name to scrape.")
    parser.add_argument("--rom", help="Path to a specific ROM file to scrape. If not provided, scrapes all ROMs in the system folder.")
    parser.add_argument("--force", action="store_true", help="Force re-downloading all media.")
    parser.add_argument("--force-metadata", action="store_true", help="Force updating metadata.")
    parser.add_argument("--removestockpics", action="store_true", help="Replace media with absolute paths.")
    cli_args = parser.parse_args()

    PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
    
    SAVES_DIR = "/rcade/share/saves"
    SETTINGS_DIR = os.path.join(SAVES_DIR, "scraper")
    SETTINGS_CFG_PATH = os.path.join(SETTINGS_DIR, "settings.cfg")
    SS_DEV_CFG_PATH = os.path.join(PROJECT_DIR, "ss_dev.cfg")
    ALT_ROM_CSV = os.path.join(PROJECT_DIR, "alt_rom_names.csv")

    print("--- Starting Scraper in Standalone Mode ---")
    
    dev_config = configparser.ConfigParser()
    dev_config.read(SS_DEV_CFG_PATH, encoding="utf-8")
    creds = {
        "devid": decode_if_base64(dev_config.get("credentials", "devid", fallback="")),
        "devpassword": decode_if_base64(dev_config.get("credentials", "devpassword", fallback=""))
    }

    user_config = configparser.ConfigParser()
    user_config.read(SETTINGS_CFG_PATH, encoding="utf-8")
    creds.update({
        "ssid": user_config.get("user_credentials", "ssid", fallback=""),
        "sspassword": user_config.get("user_credentials", "sspassword", fallback="")
    })
    google_api_key = user_config.get("google_ai", "api_key", fallback=None)

    alt_mappings = load_alt_romnames(ALT_ROM_CSV)
    try:
        SYSTEM_ID_MAP = json.loads(Path(PROJECT_DIR, "systems.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[FATAL_ERROR] Could not load systems.json: {e}")
        exit(1)

    flags = {
        "force": cli_args.force, 
        "force_metadata": cli_args.force_metadata, 
        "removestockpics": cli_args.removestockpics
    }
    settings_config = configparser.ConfigParser()
    settings_config.read(SETTINGS_CFG_PATH, encoding="utf-8")
    for section in ["directories", "media_types", "media_selection", "general", "scraper_flags"]:
        if settings_config.has_section(section):
            flags.update(settings_config.items(section))
            
    creds['lang'] = flags.get('language', 'none')

    if cli_args.rom:
        system_rom_dir = os.path.join(BASE_ROM_PATH, cli_args.system)
        rom_files = [Path(cli_args.rom)]
    else:
        system_rom_dir = os.path.join(BASE_ROM_PATH, cli_args.system)
        print(f"Scanning for ROMs in: {system_rom_dir}")
        rom_files = [p for ext in ("*.zip", "*.sfc", "*.smc", ".bin") for p in Path(system_rom_dir).glob(f"**/{ext}")]
    
    print(f"Found {len(rom_files)} ROM(s) to process.")
    for rom_file in rom_files:
        xml_path_for_rom = f"./{rom_file.relative_to(system_rom_dir).as_posix()}"
        for message in scrape_rom(str(rom_file), xml_path_for_rom, cli_args.system, creds, alt_mappings, flags, google_api_key, ALT_ROM_CSV):
            print(message)
            
    print("--- Standalone Scrape Complete ---")