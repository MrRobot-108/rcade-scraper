# RCADE SCRAPER
A web-based metadata scraper for the rcade software ecosystem. This tool allows users to fetch game metadata, artwork, and videos from ScreenScraper.fr via a user-friendly interface. Features include individual game scraping, a deep-scrape diagnostic tool, and gamelist.xml backup management.

R-Cade ROM Scraper - Quick Start Guide
1. Installation
This tool runs as a system overlay. No complex installation is required.

Copy the overlay file (.img or squashfs file) to the designated overlay/userscript folder on your USB drive (usually /rcade/share/userscripts/ or similar, depending on your specific boot setup).

Ensure your USB drive is plugged into the console.

Reboot your arcade machine.

The scraper server will start automatically in the background once the system is ready.

2. Accessing the Dashboard
You control the scraper via a web browser from your PC, Tablet, or Smartphone connected to the same network as your arcade machine.

Find the IP Address of your arcade machine (usually found in the Network Settings menu of the console).

Open a web browser and enter: http://<YOUR-ARCADE-IP>:2020 (Example: http://192.168.1.50:2020)

3. Configuration (First Run)
Before scraping, you must set up your credentials.

On the dashboard, look at the Actions & Settings panel (right side).

Enter your ScreenScraper User and Password.

Note: You need a free account from screenscraper.fr.

(Optional) Select your preferred language for game descriptions.

Click "Save & Check Login".

Check the Live Log below to confirm the login was successful.

4. How to Scrape
Select System: Use the dropdown menu on the top left to choose a console (e.g., "snes", "mame").

Select Games: Check the boxes next to the games you want to scrape.

Tip: Use the checkbox in the table header to select all visible games.

Start: Click the green SCRAPE button.

Monitor: Watch the Live Log. It will show the progress (e.g., [5/100]) and status of each file download.

Finish: Once the log says "Scraping complete," you may need to refresh your arcade's game list (or reboot the UI) to see the new artwork.

5. Advanced Features
Deep Scrape (Diagnose): If a game is identified incorrectly, select only that specific game and click DEEP SCRAPE. This opens a visual tool to search manually, preview results, and assign the correct media.

AI Naming (Gemini): In Advanced Settings, you can add a free Google AI API Key. This helps the scraper guess the correct game titles for messy filenames that ScreenScraper cannot identify automatically.

Backups: Use the Manage Backups button to save your current gamelist.xml files before performing large scrapes.

Important Note
This tool runs from a Read-Only Overlay.

Settings: Your scraper settings (passwords, preferences) are saved permanently to /rcade/share/saves/scraper/.

Updates: The internal update function is disabled for stability. To update the scraper version, simply replace the overlay file on your USB drive with a newer one.
