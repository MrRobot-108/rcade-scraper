# -*- coding: utf-8 -*-
# Version: FINAL-13 (Config & Path Restructure)
import os, json, re, subprocess, threading, configparser, base64, xml.etree.ElementTree as ET, uuid, csv, shutil, requests
import scraper_module
import sys
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote, urlencode, parse_qs

# --- PATH DEFINITIONS ---
PROJECT_DIR = os.path.abspath(os.path.dirname(__file__))
SAVES_DIR = "/rcade/share/saves"
BACKUP_DIR = os.path.join(SAVES_DIR, "gamelist_backups")
SETTINGS_DIR = os.path.join(SAVES_DIR, "scraper")
SETTINGS_CFG_PATH = os.path.join(SETTINGS_DIR, "settings.cfg")
DEFAULT_SETTINGS_CFG_PATH = os.path.join(PROJECT_DIR, "default_settings.cfg")
SS_DEV_CFG_PATH = os.path.join(PROJECT_DIR, "ss_dev.cfg")

WEB_DIR = os.path.join(PROJECT_DIR, "web")
LANG_DIR = os.path.join(PROJECT_DIR, "lang")
LOG_PATH = os.path.join(PROJECT_DIR, "log.txt")
TEMP_MEDIA_DIR = os.path.join(PROJECT_DIR, "temp_media")
BASE_DIR = "/rcade/share/roms"
ALT_ROM_CSV = os.path.join(PROJECT_DIR, "alt_rom_names.csv")
# --- END PATH DEFINITIONS ---

stop_scrape_event, all_systems_data = threading.Event(), {}
scrape_lock = threading.Lock()

def decode_if_base64(s):
    try:
        if isinstance(s, str) and (any(c in s for c in ['=','/','+']) or len(s)%4==0):
            d=base64.b64decode(s.encode()).decode("utf-8")
            if d.isalnum() or "@" in d or d.lower() in ['true','false'] or d.startswith("sk-or-"): return d
        return s
    except Exception: return s
def read_config(path, section, fields):
    config = configparser.ConfigParser()
    config.read(path)
    data = {}
    if config.has_section(section):
        for field in fields:
            raw_value = config.get(section, field, fallback=fields.get(field))
            decoded_value = decode_if_base64(raw_value)
            if isinstance(fields.get(field), bool):
                data[field] = str(decoded_value).lower() == 'true'
            else:
                data[field] = decoded_value
    return data
def write_config(path, section, data):
    config = configparser.ConfigParser(); config.read(path)
    if not config.has_section(section): config.add_section(section)
    for key, value in data.items(): config.set(section, str(key), str(value))
    with open(path, "w") as f: config.write(f)

def read_ss_credentials():
    dev_creds = read_config(SS_DEV_CFG_PATH, "credentials", {"devid": "", "devpassword": ""})
    user_creds = read_config(SETTINGS_CFG_PATH, "user_credentials", {"ssid": "", "sspassword": ""})
    return {**dev_creds, **user_creds}

def read_ui_settings():
    flags = read_config(SETTINGS_CFG_PATH, "scraper_flags", {"force": False, "force_metadata": False, "removestockpics": False})
    lang = read_config(SETTINGS_CFG_PATH, "general", {"language": "none"})
    return {**flags, **lang}
def read_directory_settings():
    return read_config(SETTINGS_CFG_PATH, "directories", {"save_media_in_rom_dir": False, "name_media_dir": "downloaded_images"})
def read_media_type_settings():
    return read_config(SETTINGS_CFG_PATH, "media_types", {"scrape_image": True, "scrape_video": True, "scrape_marquee": True, "scrape_thumbnail": True, "source_for_image": "ss", "source_for_box": "box-2D"})
def read_media_selection_settings():
    return read_config(SETTINGS_CFG_PATH, "media_selection", {"strategy_for_image": "best_resolution", "strategy_for_video": "best_resolution", "strategy_for_marquee": "best_resolution", "strategy_for_thumbnail": "best_resolution"})
def read_google_ai_credentials():
    return read_config(SETTINGS_CFG_PATH, "google_ai", {"api_key": ""})

class CustomHandler(SimpleHTTPRequestHandler):
    def handle_list_backups(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        try:
            backups = sorted([d for d in os.listdir(BACKUP_DIR) if os.path.isdir(os.path.join(BACKUP_DIR, d))], reverse=True)
            self._send_json(backups)
        except Exception as e:
            self._send_json({"error": f"Failed to list backups: {e}"}, status=500)

    def handle_create_backup(self):
        from datetime import datetime
        payload = self._get_post_payload()
        backup_name = payload.get("backup_name", "").strip()
        
        # Sanitize backup name to prevent path traversal
        safe_backup_name = re.sub(r'[^\w\-_\. ]', '_', backup_name)
        if not safe_backup_name:
            safe_backup_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        
        target_backup_path = os.path.join(BACKUP_DIR, safe_backup_name)
        if os.path.exists(target_backup_path):
            self._send_json({"error": f"Backup name '{safe_backup_name}' already exists."}, status=409)
            return
            
        try:
            os.makedirs(target_backup_path, exist_ok=True)
            systems_with_gamelist = []
            for system_name in os.listdir(BASE_DIR):
                system_path = os.path.join(BASE_DIR, system_name)
                gamelist_path = os.path.join(system_path, "gamelist.xml")
                if os.path.isfile(gamelist_path):
                    dest_dir = os.path.join(target_backup_path, system_name)
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.copy2(gamelist_path, dest_dir)
                    systems_with_gamelist.append(system_name)
            
            self._send_json({"status": "success", "backup_name": safe_backup_name, "backed_up_systems": systems_with_gamelist})
        except Exception as e:
            self._send_json({"error": f"Failed to create backup: {e}"}, status=500)

    def handle_get_backup_details(self):
        query = parse_qs(urlparse(self.path).query)
        backup_name = query.get("backup_name", [""])[0]
        safe_backup_name = re.sub(r'[^\w\-_\. ]', '_', backup_name)
        
        backup_path = os.path.join(BACKUP_DIR, safe_backup_name)
        if not os.path.isdir(backup_path):
            self._send_json({"error": "Backup not found"}, status=404)
            return
        
        try:
            systems = sorted([s for s in os.listdir(backup_path) if os.path.isdir(os.path.join(backup_path, s))])
            self._send_json(systems)
        except Exception as e:
            self._send_json({"error": f"Failed to read backup details: {e}"}, status=500)
            
    def handle_restore_backup(self):
        payload = self._get_post_payload()
        backup_name = payload.get("backup_name", "")
        systems_to_restore = payload.get("systems_to_restore", [])
        
        safe_backup_name = re.sub(r'[^\w\-_\. ]', '_', backup_name)
        backup_path = os.path.join(BACKUP_DIR, safe_backup_name)

        if not systems_to_restore or not os.path.isdir(backup_path):
            self._send_json({"error": "Invalid request. Missing systems or backup not found."}, status=400)
            return

        try:
            restored = []
            for system_name in systems_to_restore:
                src_gamelist = os.path.join(backup_path, system_name, "gamelist.xml")
                dest_gamelist = os.path.join(BASE_DIR, system_name, "gamelist.xml")
                
                if os.path.isfile(src_gamelist):
                    os.makedirs(os.path.dirname(dest_gamelist), exist_ok=True)
                    shutil.copy2(src_gamelist, dest_gamelist)
                    restored.append(system_name)
            self._send_json({"status": "success", "restored_systems": restored})
        except Exception as e:
            self._send_json({"error": f"Failed during restore: {e}"}, status=500)
    def translate_path(self, path):
        p = urlparse(unquote(path)).path

        if p.startswith("/rcade/"):
            return p

        if p.startswith("/roms/"): 
            return os.path.join(BASE_DIR, p[len("/roms/"):])
        if p.startswith("/temp_media/"): 
            return os.path.join(TEMP_MEDIA_DIR, p[len("/temp_media/"):])
        if p.startswith("/lang/"): 
            return os.path.join(LANG_DIR, p[len("/lang/"):])

        return os.path.join(WEB_DIR, p.lstrip('/'))
    def do_POST(self):
        path = urlparse(self.path).path
        endpoints = {
            "/scrape": self.handle_scrape,
            "/diagnose-scrape": self.handle_diagnose_scrape,
            "/confirm-scrape": self.handle_confirm_scrape,
            "/stop-scrape": self.handle_stop_scrape,
            "/save-settings": self.handle_save_settings,
            "/cleanup-session": self.handle_cleanup_session,
            "/create-backup": self.handle_create_backup,
            "/restore-backup": self.handle_restore_backup,
            "/test-api-key": self.handle_test_api_key,
            "/reset-settings-to-default": self.handle_reset_settings, # <-- NEW ENDPOINT
        }
        handler = endpoints.get(path)
        if handler: handler()
        else: self.send_error(404, "Unknown POST path")
    def do_GET(self):
        path = urlparse(self.path).path
        endpoints = {
            "/log": self.handle_get_log,
            "/get-settings": self.handle_get_settings,
            "/get-system-id-map": self.handle_get_system_id_map,
            "/get-rom-details": self.handle_get_rom_details,
            "/get-system-data": self.handle_get_system_data,
            "/list-backups": self.handle_list_backups,
            "/get-backup-details": self.handle_get_backup_details,
            "/check-update": self.handle_check_update,	
        }
        handler = endpoints.get(path)
        if handler: handler()
        else: super().do_GET()
    def _send_json(self, data, status=200):
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))
    def _get_post_payload(self):
        cl = int(self.headers.get('Content-Length', 0)); return json.loads(self.rfile.read(cl)) if cl > 0 else {}
    def handle_get_log(self):
        try:
            content = Path(LOG_PATH).read_text(encoding="utf-8") if Path(LOG_PATH).exists() else ""
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except Exception as e: self.send_error(500, f"Failed to read log: {e}")
    def handle_get_settings(self):
        settings = {
            **read_ss_credentials(), 
            **read_ui_settings(), 
            **read_media_type_settings(),
            **read_directory_settings(),
            **read_media_selection_settings(),
            **read_google_ai_credentials()
        }
        self._send_json(settings)
    def handle_get_system_id_map(self):
        try: self._send_json(json.loads(Path(PROJECT_DIR, "systems.json").read_text(encoding="utf-8")))
        except Exception as e: self.send_error(500, f"Error reading systems.json: {e}")
    def handle_get_rom_details(self):
        query = parse_qs(urlparse(self.path).query)
        rom_path, system_name = query.get("romPath", [""])[0], query.get("system", [""])[0]
        selected_rom = next((rom for rom in all_systems_data.get(system_name, []) if rom.get("rom_path") == rom_path), None)
        if selected_rom: self._send_json(selected_rom)
        else: self.send_error(404, "ROM not found in cache")
    def handle_get_system_data(self):
        global all_systems_data
        all_systems_data = {"ALL": []}
        
        for system_name in sorted(os.listdir(BASE_DIR)):
            system_path = os.path.join(BASE_DIR, system_name)
            gamelist_path = os.path.join(system_path, "gamelist.xml")
            
            if not (os.path.isdir(system_path) and os.path.exists(gamelist_path)):
                continue
                
            system_games = []

            try:
                gamelist_root = ET.parse(gamelist_path).getroot()
                gamelist_dir = os.path.dirname(gamelist_path)

                for game_el in gamelist_root.findall("game"):
                    path_raw = game_el.get("path")
                    if not path_raw or game_el.get("deleted") == "yes":
                        continue
                    
                    name_el = game_el.find("name")
                    game_name = name_el.text if name_el is not None else Path(path_raw).stem

                    def get_media_info(tag_name):
                        tag = game_el.find(tag_name)
                        path = tag.text.strip() if tag is not None and tag.text and tag.text.strip() else None
                        if not path:
                            return None, False
                        
                        full_path = path if path.startswith('/') else os.path.join(gamelist_dir, path)
                        return path, os.path.exists(full_path)

                    image_path, image_exists = get_media_info("image")
                    video_path, video_exists = get_media_info("video")
                    marquee_path, marquee_exists = get_media_info("marquee")
                    thumbnail_path, thumbnail_exists = get_media_info("thumbnail")

                    game_entry = {
                        "rom_path": path_raw,
                        "game_name": game_name,
                        "actual_system": system_name,
                        "image_path": image_path, "image_exists": image_exists,
                        "video_path": video_path, "video_exists": video_exists,
                        "marquee_path": marquee_path, "marquee_exists": marquee_exists,
                        "thumbnail_path": thumbnail_path, "thumbnail_exists": thumbnail_exists,
                    }
                    
                    system_games.append(game_entry)
                    all_systems_data["ALL"].append(game_entry)
                    
            except Exception as e:
                print(f"Error processing gamelist for {system_name}: {e}")
                
            if system_games:
                all_systems_data[system_name] = system_games
                
        self._send_json(all_systems_data)

    def handle_scrape(self):
        if not scrape_lock.acquire(blocking=False):
            self.send_error(409, "A scrape is already in progress.")
            return
    
        # Reset the stop event flag and clear the log file for the new scrape
        stop_scrape_event.clear()
        with open(LOG_PATH, "w", encoding="utf-8") as logf:
            logf.write("=== Scrape started ===\n\n")
            logf.flush()
    
        payload = self._get_post_payload()
        roms_to_scrape = payload.get("roms_to_scrape_data", [])
    
        # Start the scrape in a new thread
        threading.Thread(target=self.run_scrape_thread, args=(roms_to_scrape,)).start()
    
        self._send_json({"status": "started"})
		
    def handle_diagnose_scrape(self):
        payload = self._get_post_payload()
        rom_name, system_name = payload.get("romName"), payload.get("systemName")
        if not rom_name or not system_name: 
            return self.send_error(400, "ROM Name or System Name missing.")
        
        session_id = str(uuid.uuid4())
        session_temp_dir = os.path.join(TEMP_MEDIA_DIR, session_id)
        os.makedirs(session_temp_dir, exist_ok=True)
    
        with open(LOG_PATH, "a", encoding="utf-8") as logf: 
            logf.write(f"\n--- Starting Diagnose Scrape for '{rom_name}' ---\n")
        
        try:
            creds = read_ss_credentials()
            scraper_module.SYSTEM_ID_MAP = json.loads(Path(PROJECT_DIR, "systems.json").read_text(encoding="utf-8"))

            flags = {
                **read_ui_settings(), 
                **read_directory_settings(), 
                **read_media_type_settings(),
                **read_media_selection_settings()
            }
            scraped_data = scraper_module.diagnose_rom(rom_name, system_name, creds, session_temp_dir, flags)
        
            files = [
                {
                    "url": f"/temp_media/{session_id}/{f['filename']}",
                    "original_filename": f['filename'],
                    "media_type": f['media_type']
                } 
                for f in scraped_data.get("files", [])
            ]
        
            self._send_json({"session_id": session_id, "files": files})
    
        except Exception as e:
            err_msg = f"Diagnose scrape failed: {e}"
            with open(LOG_PATH, "a", encoding="utf-8") as logf: logf.write(f"[ERROR] {err_msg}\n")
            self.send_error(500, err_msg)
			
    def handle_confirm_scrape(self):
        payload = self._get_post_payload()
        keys = ["session_id", "original_rom_path", "original_system", "new_rom_name", "new_system", "files_to_save"]
        if not all(payload.get(k) is not None for k in keys): return self.send_error(400, "Missing data.")
        
        temp_dir = os.path.join(TEMP_MEDIA_DIR, payload["session_id"])
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as logf: 
                logf.write(f"\n--- Confirming Scrape for {payload['original_rom_path']} ---\n")
            
            saved_media_paths = self.move_media_files(payload, temp_dir)
            
            self.update_alt_rom_names(payload)
            
            self.update_gamelist_after_deep_scrape(payload, saved_media_paths)

            with open(LOG_PATH, "a", encoding="utf-8") as logf: logf.write(f"--- Confirmation Complete ---\n")
            self._send_json({"status": "confirmed"})
        except Exception as e:
            with open(LOG_PATH, "a", encoding="utf-8") as logf: logf.write(f"[ERROR] Confirmation failed: {e}\n")
            self.send_error(500, f"Confirmation failed: {e}")
        finally:
            if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
    def handle_cleanup_session(self):
        payload = self._get_post_payload()
        session_id = payload.get("session_id")
        if not session_id or not re.match(r'^[a-f0-9\-]+$', session_id): return self.send_error(400, "Invalid or missing Session ID.")
        session_dir = os.path.join(TEMP_MEDIA_DIR, session_id)
        if os.path.isdir(session_dir):
            try:
                shutil.rmtree(session_dir)
                with open(LOG_PATH, "a", encoding="utf-8") as logf: logf.write(f"Cleaned up temporary session: {session_id}\n")
            except Exception as e:
                with open(LOG_PATH, "a", encoding="utf-8") as logf: logf.write(f"Failed to cleanup session {session_id}: {e}\n")
        self._send_json({"status": "cleaned"})
    def handle_stop_scrape(self): stop_scrape_event.set(); self._send_json({"status": "stopping"})
    def handle_save_settings(self):
        payload = self._get_post_payload()

        if "ssid" in payload: 
            write_config(SETTINGS_CFG_PATH, "user_credentials", {"ssid": payload["ssid"], "sspassword": payload["sspassword"]})
			
        if "api_key" in payload: 
            write_config(SETTINGS_CFG_PATH, "google_ai", {"api_key": payload["api_key"]})
			
        media_keys = [k for k in payload if k.startswith('scrape_') or k.startswith('source_for_')]
        if media_keys:
            media_settings = {key: payload[key] for key in media_keys}
            write_config(SETTINGS_CFG_PATH, "media_types", media_settings)

        dir_keys = [k for k in payload if k in ['save_media_in_rom_dir', 'name_media_dir']]
        if dir_keys:
            dir_settings = {key: payload[key] for key in dir_keys}
            write_config(SETTINGS_CFG_PATH, "directories", dir_settings)

        selection_keys = [k for k in payload if k.startswith('strategy_for_')]
        if selection_keys:
            selection_settings = {key: payload[key] for key in selection_keys}
            write_config(SETTINGS_CFG_PATH, "media_selection", selection_settings)

        general_keys = [k for k in payload if k in ['language', 'force', 'force_metadata', 'removestockpics']]
        if general_keys:
            general_settings_to_write = {}
            if 'language' in payload:
                write_config(SETTINGS_CFG_PATH, "general", {"language": payload.get("language", "none")})
            
            flag_settings = {k: payload[k] for k in ['force', 'force_metadata', 'removestockpics'] if k in payload}
            if flag_settings:
                 write_config(SETTINGS_CFG_PATH, "scraper_flags", flag_settings)

        user_friendly_msg = ""
        if payload.get("perform_login_check") and "ssid" in payload:
            creds = read_config(SS_DEV_CFG_PATH, "credentials", {"devid":"", "devpassword":""}) # Read dev creds
            params = urlencode({"devid": decode_if_base64(creds["devid"]), "devpassword": decode_if_base64(creds["devpassword"]), "ssid": payload["ssid"], "sspassword": payload["sspassword"], "output": "json"})
            try:
                resp = requests.get(f"https://www.screenscraper.fr/api2/ssuserInfos.php?{params}", timeout=10)
                if resp.status_code == 200 and resp.json().get("header", {}).get("success") == "true": login_msg = "Login OK"
                else: login_msg = f"Login failed: {resp.json().get('header', {}).get('error', 'Unknown') if resp.status_code == 200 else f'HTTP {resp.status_code}'}"
            except Exception as e: login_msg = f"Login test error: {e}"
            user_friendly_msg = login_msg if "Login OK" in login_msg else "Login failed. Please check your credentials."
            final_message = f"[SETTINGS] Settings saved. Login Test: {user_friendly_msg}"
            with open(LOG_PATH, "a", encoding="utf-8") as logf: logf.write(f"\n{final_message}\n")

        self._send_json({"status": "saved", "login_test": user_friendly_msg})
		
    def handle_reset_settings(self):
        """ Overwrites the current settings.cfg with the default one. """
        try:
            shutil.copy2(DEFAULT_SETTINGS_CFG_PATH, SETTINGS_CFG_PATH)
            self._send_json({"status": "success", "message": "Settings have been reset to default."})
        except Exception as e:
            self._send_json({"error": f"Failed to reset settings: {e}"}, status=500)
		
    def handle_test_api_key(self):
        # handler to test the provided Google AI API key
        payload = self._get_post_payload()
        api_key = payload.get("api_key")
        if not api_key:
            return self._send_json({"success": False, "error": "No API key provided"}, status=400)
        
        try:
            # simple test case to see if the API responds correctly
            result = scraper_module.guess_game_titles_with_gemini("test", api_key)
            if isinstance(result, list):
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": "Test failed, check server console for details."})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, status=500)
    def move_media_files(self, payload, temp_dir):
        from collections import defaultdict
        
        dir_settings = read_directory_settings()
        save_in_rom_dir = dir_settings.get('save_media_in_rom_dir', False)
        media_folder_name = dir_settings.get('name_media_dir', 'downloaded_images')
        gamelist_path = os.path.join(BASE_DIR, payload['original_system'], "gamelist.xml")

        final_media_dir = ""
        if save_in_rom_dir:
            rom_abs_path = os.path.join(BASE_DIR, payload['original_system'], payload['original_rom_path'].lstrip('./'))
            rom_dir = os.path.dirname(rom_abs_path)
            final_media_dir = os.path.join(rom_dir, media_folder_name)
        else:
            final_media_dir = os.path.join(BASE_DIR, payload['original_system'], media_folder_name)

        os.makedirs(final_media_dir, exist_ok=True)
        
        source_to_targets = defaultdict(list)
        for info in payload['files_to_save']:
            source_to_targets[info.get("original_filename")].append(info.get("media_type"))

        saved_paths = {}
        suffix_map = {"image": "image", "video": "video", "marquee": "marquee", "thumbnail": "thumb"}

        for filename, media_types in source_to_targets.items():
            if not filename or not media_types or not os.path.exists(os.path.join(temp_dir, filename)):
                continue

            primary_media_type = media_types[0]
            suffix = suffix_map.get(primary_media_type)
            if not suffix: continue

            new_filename = f"{Path(payload['original_rom_path']).stem}-{suffix}{Path(filename).suffix}"
            destination_path = os.path.join(final_media_dir, new_filename)
            
            shutil.move(os.path.join(temp_dir, filename), destination_path)
            
            relative_path = f"./{os.path.relpath(destination_path, os.path.dirname(gamelist_path)).replace(os.sep, '/')}"

            for mt in media_types:
                saved_paths[mt] = relative_path
        
        return saved_paths
    def update_alt_rom_names(self, payload):
        rom_stem, new_name, src_system, new_system = Path(payload['original_rom_path']).stem, payload['new_rom_name'], payload['original_system'], payload['new_system']
        if new_name == rom_stem and new_system == src_system: return
        rows = [row for row in csv.reader(open(ALT_ROM_CSV, 'r', newline='', encoding='utf-8')) if not (len(row) >= 3 and (row[0].strip(), row[2].strip()) == (rom_stem, src_system))] if os.path.exists(ALT_ROM_CSV) else []
        rows.append([rom_stem, new_name, src_system, new_system])
        with open(ALT_ROM_CSV, 'w', newline='', encoding='utf-8') as f: csv.writer(f).writerows(rows)
        with open(LOG_PATH, "a", encoding="utf-8") as logf: logf.write(f"Updated {ALT_ROM_CSV} with new mapping.\n")
    def update_gamelist_after_deep_scrape(self, payload, saved_media_paths):
        with open(LOG_PATH, "a", encoding="utf-8") as logf:
            logf.write("--- Updating gamelist with deep scrape results ---\n")

        creds = read_ss_credentials()
        settings = read_ui_settings()
        creds["lang"] = settings.get("language")

        # Get metadata from ScreenScraper
        data = scraper_module.query_screenscraper(creds, romname=payload['new_rom_name'], systeme=payload['new_system'])
        if not data:
            logf.write(f"Could not fetch metadata for {payload['new_rom_name']}. Only media paths will be updated.\n")
            return

        jeu = data["response"]["jeu"]
        scraped_name = jeu.get("noms")[0].get("text") if jeu.get("noms") else jeu.get("nom")
        
        # Prepare the entry for the gamelist
        entry_data = {
            "rom_path": payload['original_rom_path'],
            "name": scraped_name or payload['new_rom_name'],
            "description": next((s.get("text", "") for s in jeu.get("synopsis", []) if s.get("langue") == creds["lang"]), ""),
            "developer": jeu.get("developpeur", {}).get("text", ""),
            "publisher": jeu.get("editeur", {}).get("text", ""),
            "players": jeu.get("joueurs", {}).get("text", ""),
            "genre": next((g["noms"][0].get("text", "") for g in jeu.get("genres", []) if g.get("noms")), ""),
            "releasedate": f"{next((d.get('text', '') for d in jeu.get('dates', [])), '')}0101T000000"
        }

        # Add only the paths for the media files we actually saved
        for media_type, path in saved_media_paths.items():
            entry_data[f"{media_type}_path"] = path

        # Update the gamelist.xml, forcing all data to be written.
        gamelist_path = os.path.join(BASE_DIR, payload['original_system'], "gamelist.xml")
        scraper_module.update_gamelist(gamelist_path, entry_data, force=True)
				
    def run_scrape_thread(self, roms_to_scrape_data):
        try:
            # Load configs once at the beginning of the thread
            settings = {
                **read_ui_settings(), 
                **read_directory_settings(),
                **read_media_type_settings(),
                **read_media_selection_settings()
            }
            creds = read_ss_credentials()
            creds["lang"] = settings.get("language")
            google_ai_creds = read_google_ai_credentials()
            alt_mappings = scraper_module.load_alt_romnames(ALT_ROM_CSV)
            
            try:
                scraper_module.SYSTEM_ID_MAP = json.loads(Path(PROJECT_DIR, "systems.json").read_text(encoding="utf-8"))
            except Exception as e:
                with open(LOG_PATH, "a", encoding="utf-8") as logf:
                    logf.write(f"[FATAL_ERROR] Could not load systems.json: {e}\n")
                return

            # Main loop for processing each ROM
            total_roms = len(roms_to_scrape_data) # <--- NEU: Gesamtzahl ermitteln
            for current_idx, entry in enumerate(roms_to_scrape_data, 1): 
                if stop_scrape_event.is_set():
                    with open(LOG_PATH, "a", encoding="utf-8") as logf:
                        logf.write("\n=== Scrape interrupted by user ===\n")
                    break
                
                xml_path_str, system = entry.get("rom_path"), entry.get("actual_system")
                if not xml_path_str or not system: continue
                
                rom_abs_path = os.path.join(BASE_DIR, system, xml_path_str.lstrip('./'))

                try:
                    with open(LOG_PATH, "a", encoding="utf-8", errors="replace") as logf:
                        logf.write(f"\n--- Progress: [{current_idx}/{total_roms}] ---\n")
                        for log_message in scraper_module.scrape_rom(rom_abs_path, xml_path_str, system, creds, alt_mappings, settings, google_ai_creds.get("api_key"), ALT_ROM_CSV):
                            if stop_scrape_event.is_set():
                                break
                            logf.write(log_message + "\n")
                            logf.flush()
                except Exception as e:
                    with open(LOG_PATH, "a", encoding="utf-8") as logf:
                        logf.write(f"[FATAL_ERROR] Scraping {Path(xml_path_str).name} failed with an unhandled exception: {e}\n")

            # Final log message after the loop
            if not stop_scrape_event.is_set():
                with open(LOG_PATH, "a", encoding="utf-8") as logf:
                   logf.write("Scraping complete.\n")
        finally:
            # Always release the lock when the thread finishes
            scrape_lock.release()

    def handle_check_update(self):
        """Checks for a new version on GitHub."""
        local_version = ""
        try:
            with open(os.path.join(PROJECT_DIR, "version.txt"), 'r') as f:
                local_version = f.read().strip()
        except FileNotFoundError:
            self._send_json({"error": "Local version.txt not found."}, status=500)
            return

        try:
            url = "https://raw.githubusercontent.com/MrRobot-108/rcade-scraper/main/version.txt"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            remote_version = r.text.strip()

            response = {
                "local_version": local_version,
                "remote_version": remote_version,
                "update_available": remote_version > local_version
            }
            self._send_json(response)
        except Exception as e:
            self._send_json({"error": f"Failed to fetch remote version: {e}"}, status=500)
	
			
def run_server():
    print("? Checking for required directories and configuration...")
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        os.makedirs(BACKUP_DIR, exist_ok=True)
        if not os.path.exists(SETTINGS_CFG_PATH):
            print(f"?? No settings file found. Copying default settings to {SETTINGS_CFG_PATH}")
            shutil.copy2(DEFAULT_SETTINGS_CFG_PATH, SETTINGS_CFG_PATH)
        else:
            print("? Settings file found.")
    except Exception as e:
        print(f"?? FATAL: Could not create directories or copy settings: {e}")
        sys.exit(1)

    if os.path.exists(LOG_PATH):
        try: os.remove(LOG_PATH); print("? Previous log file deleted.")
        except OSError as e: print(f"??  Could not delete log file: {e}")
    if os.path.isdir(TEMP_MEDIA_DIR):
        print("?? Cleaning up old temporary media sessions...")
        for item_name in os.listdir(TEMP_MEDIA_DIR):
            item_path = os.path.join(TEMP_MEDIA_DIR, item_name)
            if os.path.isdir(item_path):
                try: shutil.rmtree(item_path); print(f"  - Removed old session: {item_name}")
                except Exception as e: print(f"  ?? Could not remove {item_path}: {e}")
    os.makedirs(TEMP_MEDIA_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer(('0.0.0.0', 2020), CustomHandler)
    print(f"? Server running at http://<IP>:2020")
    httpd.serve_forever()

if __name__ == "__main__":
    run_server()