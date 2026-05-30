import os
import time
import random
import sqlite3
import logging
import re
from urllib.parse import quote
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- CONFIGURATION ---
MAX_NEW_CONNECTIONS = 50
SEARCH_QUERIES = [
    "NetSuite Recruiter",
    "NetSuite Talent Acquisition",
    "NetSuite ERP Manager"
]
MAX_PAGES_PER_QUERY = 10

# --- MESSAGES ---
# Connection requests have a strict 300 character limit.
CONNECTION_NOTE = (
    "Hi {first_name}, I'm a NetSuite specialist available for immediate deployments. "
    "I usually work on contract basis (no notice periods), "
    "but I'm looking to transition into an Full time role with the right team. Let's connect!"
)

# Direct messages (for 1st degree connections) can be longer.
DIRECT_MESSAGE = (
    "Hi {first_name}, How're you doing!\n\n"
    "I'm texted coz I'm a NetSuite specialist currently wrapping up my projects "
    "and available for immediate deployment."
    "\n"
    "Let me know if you or your clients need immediate bandwidth!"
)

class LinkedInDB:
    def __init__(self, db_name="linkedin_outreach.db"):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS interactions (
                profile_url TEXT PRIMARY KEY,
                name TEXT,
                status TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def has_interacted(self, profile_url):
        # Normalize URL to prevent duplicates (remove trailing slashes or query params)
        clean_url = profile_url.split('?')[0].rstrip('/')
        self.cursor.execute('SELECT 1 FROM interactions WHERE profile_url LIKE ?', (f"{clean_url}%",))
        return self.cursor.fetchone() is not None

    def log_interaction(self, profile_url, name, status):
        clean_url = profile_url.split('?')[0].rstrip('/')
        self.cursor.execute('''
            INSERT OR REPLACE INTO interactions (profile_url, name, status)
            VALUES (?, ?, ?)
        ''', (clean_url, name, status))
        self.conn.commit()

class LinkedInBot:
    def __init__(self):
        self.db = LinkedInDB()
        self.new_connections_sent = 0
        self.messages_sent = 0

    def human_delay(self, min_sec=2, max_sec=5):
        """Randomized delays to prevent bot detection."""
        time.sleep(random.uniform(min_sec, max_sec))

    def get_first_name(self, full_name):
        """Extracts first name for personalization."""
        if not full_name: return "there"
        # Handle titles like "Dr.", "Mr.", or emojis by taking the first actual word
        clean_name = full_name.split()[0].strip(',.')
        return clean_name if clean_name else "there"

    def run(self):
        with sync_playwright() as p:
            try:
                logging.info("Connecting to active Chrome session on port 9222...")
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
                context = browser.contexts[0]
                
                # Close lingering tabs from previous runs if desired, or just create a new one
                page = context.new_page()
            except Exception as e:
                logging.error(f"CRITICAL: Could not connect to Chrome. Ensure remote debugging is active. {e}")
                return

            try:
                # Ensure we are logged in - use domcontentloaded to avoid hanging on background tracking pixels
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=60000)
                self.human_delay(3, 6)
                if "login" in page.url:
                    logging.error("You are not logged into LinkedIn in the active Chrome session. Please log in and restart.")
                    return

                for query in SEARCH_QUERIES:
                    if self.new_connections_sent >= MAX_NEW_CONNECTIONS:
                        break
                    self.execute_search(page, query, context)

            except Exception as e:
                logging.error(f"Critical failure in run loop: {e}")
            finally:
                logging.info(f"=== OUTREACH COMPLETE ===")
                logging.info(f"New Connections Sent: {self.new_connections_sent}")
                logging.info(f"Existing Connections Messaged: {self.messages_sent}")
                logging.info("Bot finished. LEAVING BROWSER OPEN.")
                input("Press ENTER in this terminal to close the script (Browser will stay open)...")
                
                # Cleanup floating message tabs if any
                self.close_all_message_bubbles(page)
                page.close()

    def execute_search(self, page, query, context):
        encoded_query = quote(query)
        
        for page_num in range(1, MAX_PAGES_PER_QUERY + 1):
            if self.new_connections_sent >= MAX_NEW_CONNECTIONS:
                logging.info("Reached maximum new connection limit (50). Stopping search.")
                return

            # LinkedIn People search URL
            search_url = f"https://www.linkedin.com/search/results/people/?keywords={encoded_query}&page={page_num}"
            logging.info(f"Scanning: '{query}' (Page {page_num})")
            
            page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            self.human_delay(4, 7) # Slower delay for page loads

            # Scroll down to load all results
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.human_delay(2, 3)

            # Find all profile links on the page
            try:
                # Wait for results to populate (avoids extracting too early)
                try:
                    page.wait_for_selector("main a[href*='/in/']", timeout=5000)
                except:
                    pass
                
                # Target ALL profile links in the main content area
                # This bypasses LinkedIn's new randomized CSS class names entirely.
                link_locators = page.locator("main a[href*='/in/']").all()
                
                profile_urls = []
                for link in link_locators:
                    href = link.get_attribute("href")
                    if href and "/in/" in href:
                        # Clean tracking params from URL (e.g. ?miniProfileUrn=...)
                        clean_url = href.split('?')[0].rstrip('/')
                        
                        # Filter out generic directory links or 'me' links
                        if not clean_url.endswith("/in") and not clean_url.endswith("/in/me"):
                            if clean_url not in profile_urls:
                                profile_urls.append(clean_url)
                        
                logging.info(f"Found {len(profile_urls)} profiles on this page.")
                
            except Exception as e:
                logging.warning(f"Failed to extract profiles on page {page_num}: {e}")
                continue

            if not profile_urls:
                logging.info("No more results found for this query.")
                break

            # Process each profile
            for url in profile_urls:
                if self.new_connections_sent >= MAX_NEW_CONNECTIONS:
                    break
                    
                if self.db.has_interacted(url):
                    logging.info(f"Skipping (Already interacted): {url.split('?')[0]}")
                    continue

                self.process_profile(context, url)

    def process_profile(self, context, url):
        # Open profile in a NEW TAB so we don't lose our search pagination state
        profile_page = context.new_page()
        
        try:
            profile_page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self.human_delay(3, 5)

            # --- EXTRACT NAME ---
            full_name = ""
            # 1. Try Standard UI H1
            name_locator = profile_page.locator("h1.text-heading-xlarge").first
            if name_locator.is_visible():
                full_name = name_locator.inner_text().strip()
            else:
                # 2. Fallback for new Server-Driven UI (SDUI) using the page title
                title = profile_page.title()
                if title:
                    # Remove notification counts like "(3) "
                    clean_title = re.sub(r'^\(\d+\)\s*', '', title)
                    if "|" in clean_title:
                        full_name = clean_title.split("|")[0].strip()
                    elif "-" in clean_title:
                        full_name = clean_title.split("-")[0].strip()

            if not full_name or full_name.lower() == "linkedin":
                logging.warning("Could not find profile name. Skipping.")
                self.db.log_interaction(url, "Unknown", "FAILED_NO_NAME")
                return

            first_name = self.get_first_name(full_name)
            logging.info(f"Evaluating: {full_name} ({url.split('?')[0]})")

            # --- DETERMINE ROOT CONTENT AREA ---
            # To avoid clicking buttons in the 'People you may know' sidebar
            root = profile_page.locator("main section[aria-label='Primary content'], main.scaffold-layout__main").first
            if not root.is_visible():
                root = profile_page.locator("main").first

            # --- FIND ACTION BUTTONS (Supports Standard & SDUI) ---
            message_btn = root.locator("button[aria-label^='Message'], a[href*='/messaging/compose']").first
            connect_btn = root.locator("button[aria-label^='Invite'], button:has-text('Connect'), a[aria-label^='Invite'], a[href*='/preload/custom-invite']").first
            pending_btn = root.locator("button[aria-label^='Pending'], a[aria-label^='Pending']").first

            if pending_btn.is_visible():
                logging.info(f"  -> Invitation already pending for {full_name}. Skipping.")
                status = "PENDING"
                
            elif message_btn.is_visible():
                logging.info(f"  -> Already connected to {full_name}. Sending DM.")
                success = self.send_direct_message(profile_page, message_btn, first_name)
                status = "MESSAGED" if success else "FAILED_DM"
                if success: self.messages_sent += 1
                
            elif connect_btn.is_visible():
                logging.info(f"  -> Not connected to {full_name}. Sending Connection Request.")
                success = self.send_connection_request(profile_page, connect_btn, first_name)
                status = "CONNECTED" if success else "FAILED_CONNECT"
                if success: self.new_connections_sent += 1
                
            else:
                # Sometimes "Connect" is hidden under the "More" dropdown
                more_btn = root.locator("button[aria-label='More actions'], button[aria-label='More']").first
                if more_btn.is_visible():
                    more_btn.click()
                    self.human_delay(1, 2)
                    dropdown_connect = profile_page.locator("div.artdeco-dropdown__content button:has-text('Connect'), div.artdeco-dropdown__content a[aria-label^='Invite']").first
                    
                    if dropdown_connect.is_visible():
                        logging.info(f"  -> Not connected (Found in More). Sending Connection Request.")
                        success = self.send_connection_request(profile_page, dropdown_connect, first_name)
                        status = "CONNECTED" if success else "FAILED_CONNECT"
                        if success: self.new_connections_sent += 1
                    else:
                        logging.info(f"  -> Connect button unavailable (Likely Premium/InMail only). Skipping.")
                        status = "SKIPPED_UNAVAILABLE"
                else:
                    logging.info(f"  -> Action buttons unclear. Skipping.")
                    status = "SKIPPED_UNKNOWN_UI"

            # Log to Database
            self.db.log_interaction(url, full_name, status)

        except Exception as e:
            logging.error(f"  -> Error processing profile: {e}")
            self.db.log_interaction(url, "Unknown", "ERROR")
            
        finally:
            # We close the tab after processing so the browser doesn't crash from 50 open tabs
            profile_page.close()
            self.human_delay(2, 4)

    def send_connection_request(self, page, connect_button, first_name):
        try:
            connect_button.click()
            self.human_delay(2, 3)

            # Look for the "Add a note" button
            add_note_btn = page.locator("button[aria-label='Add a note']").first
            if add_note_btn.is_visible():
                add_note_btn.click()
                self.human_delay(1, 2)
            
            # Fill the note
            textarea = page.locator("textarea[name='message'], textarea#custom-message").first
            if textarea.is_visible():
                message = CONNECTION_NOTE.format(first_name=first_name)
                textarea.fill(message)
                self.human_delay(2, 3)
                
                # Send
                send_btn = page.locator("button[aria-label='Send invitation'], button:has-text('Send')").first
                if send_btn.is_visible():
                    send_btn.click()
                    self.human_delay(2, 3)
                    return True
                else:
                    logging.warning("  -> Could not find Send button in modal.")
                    return False
            else:
                # Some profiles trigger a different flow (e.g. asking for email to connect). We skip these.
                logging.warning("  -> Note textarea not found. Skipping to avoid empty request.")
                close_btn = page.locator("button[aria-label='Dismiss']").first
                if close_btn.is_visible(): close_btn.click()
                return False

        except Exception as e:
            logging.error(f"  -> Failed to send connection: {e}")
            return False

    def send_direct_message(self, page, message_button, first_name):
        try:
            # BUG 2 FIX: Close any lingering chat bubbles before opening a new one.
            # This guarantees we don't type the new message into an old, wrong chat window.
            self.close_all_message_bubbles(page)

            message_button.click()
            self.human_delay(3, 4)

            # LinkedIn pops up a messaging bubble at the bottom right.
            # We target the currently active text box inside the msg-form
            msg_box = page.locator(".msg-form__contenteditable p").first
            
            if msg_box.is_visible():
                msg_box.click()
                self.human_delay(1)

                # BUG 1 FIX: Check if there's already a conversation history.
                # Give the history a moment to load in the DOM, then check for message elements.
                page.wait_for_timeout(1500)
                history_count = page.locator(".msg-s-message-list__event, .msg-s-event-listitem").count()

                if history_count > 0:
                    logging.info(f"  -> Chat history found with {first_name}. Drafting manual check-in.")
                    page.keyboard.insert_text("Hello, how's it going.")
                    # DO NOT send and DO NOT close the bubble, so you can manually take over.
                    return True
                
                # If no history, proceed with the standard direct message pitch
                message = DIRECT_MESSAGE.format(first_name=first_name)
                
                # Using page.keyboard to type ensures formatting is preserved
                page.keyboard.insert_text(message)
                self.human_delay(2, 4)

                # Find the send button inside the messaging form
                send_btn = page.locator("button.msg-form__send-button").first
                if send_btn.is_visible() and not send_btn.is_disabled():
                    send_btn.click()
                    self.human_delay(2, 3)
                    
                    # Close the chat bubble so it doesn't clutter the screen
                    self.close_all_message_bubbles(page)
                    return True
                else:
                    logging.warning("  -> Send button disabled or not found.")
                    self.close_all_message_bubbles(page)
                    return False
            else:
                logging.warning("  -> Message box not found.")
                return False

        except Exception as e:
            logging.error(f"  -> Failed to send direct message: {e}")
            return False

    def close_all_message_bubbles(self, page):
        """Helper to close messaging popups on LinkedIn so they don't block the UI."""
        try:
            close_buttons = page.locator("button.msg-overlay-bubble-header__control--close-btn").all()
            for btn in close_buttons:
                if btn.is_visible():
                    btn.click()
                    self.human_delay(1, 2)
        except:
            pass # Ignore errors here, just a cleanup task

if __name__ == "__main__":
    bot = LinkedInBot()
    bot.run()