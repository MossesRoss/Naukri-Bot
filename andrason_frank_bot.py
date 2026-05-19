import os
import time
import random
import sqlite3
import logging
import urllib.parse
import re
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Load variables
load_dotenv()

# ==========================================
# ANDERSON FRANK - PROFILE CONFIGURATION
# ==========================================
TARGET_KEYWORDS = ["NetSuite Developer", "NetSuite Technical Consultant", "NetSuite Integration"]
CORE_TECH_KEYWORDS = ["netsuite", "erp", "boomi", "celigo"]

PROFILE = {
    "notice_period": "0",
    "ctc": "Negotiable",
    "location": "Chennai",
    "experience": "3",
    "default_answer": "Please refer to my attached resume for details."
}

DB_NAME = "anderson_jobs.db" 
MAX_APPLICATIONS_PER_DAY = 20
MAX_PAGES_PER_KEYWORD = 5
# ==========================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Database:
    def __init__(self, db_name=DB_NAME):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS applied_jobs (
                job_id TEXT PRIMARY KEY,
                job_title TEXT,
                company TEXT,
                status TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()

    def has_applied(self, job_id):
        self.cursor.execute('SELECT 1 FROM applied_jobs WHERE job_id = ?', (job_id,))
        return self.cursor.fetchone() is not None

    def log_job(self, job_id, title, company, status):
        self.cursor.execute('''
            INSERT OR IGNORE INTO applied_jobs (job_id, job_title, company, status)
            VALUES (?, ?, ?, ?)
        ''', (job_id, title, company, status))
        self.conn.commit()

class AndersonFrankBot:
    def __init__(self):
        self.db = Database()
        self.applied_count = 0

    def human_delay(self, min_sec=1, max_sec=3):
        time.sleep(random.uniform(min_sec, max_sec))
        
    def is_relevant_job(self, title):
        return any(tech in title.lower() for tech in CORE_TECH_KEYWORDS)

    def answer_question(self, question_text):
        q = question_text.lower()
        if any(word in q for word in ["notice", "period", "joining", "join"]):
            return PROFILE["notice_period"]
        elif any(word in q for word in ["ctc", "salary", "expected", "current", "compensation", "lpa"]):
            return PROFILE["ctc"]
        elif any(word in q for word in ["location", "city", "base", "relocate", "where"]):
            return PROFILE["location"]
        elif any(word in q for word in ["experience", "exp", "years"]):
            return PROFILE["experience"]
        else:
            return PROFILE["default_answer"]

    def run(self):
        with sync_playwright() as p:
            try:
                logging.info("Connecting to active Chrome session on port 9222...")
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
                context = browser.contexts[0]
                page = context.new_page()
            except Exception as e:
                logging.error("CRITICAL: Could not connect to Chrome. Ensure remote debugging is active.")
                return

            try:
                self.login(page)
                for keyword in TARGET_KEYWORDS:
                    if self.applied_count >= MAX_APPLICATIONS_PER_DAY:
                        break
                    self.search_and_apply(page, keyword)
            except Exception as e:
                logging.error(f"Critical failure in run loop: {e}")
            finally:
                logging.info("Bot finished. LEAVING BROWSER OPEN so you can finish manual tabs.")
                input("Press ENTER in this terminal when you are done... (Your Chrome will stay open)")
                page.close()

    def login(self, page):
        logging.info("Navigating to Anderson Frank profile page...")
        try:
            page.goto("https://www.andersonfrank.com/profile", wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            pass # Ignore if background scripts timeout, as long as DOM is loaded
        
        logging.info("=====================================================")
        logging.info("⏳ ACTION REQUIRED: Manual Login ⏳")
        logging.info("Please interact with the Chrome window if you need to log in.")
        logging.info("=====================================================")
        
        # Pauses the script until you press ENTER in the terminal
        input("\n>>> Press ENTER in this terminal once you are logged in (or ready to continue)... <<<\n")
        
        logging.info("Resuming automation...")

    def search_and_apply(self, page, keyword):
        # Format the keyword for a URL (e.g., "NetSuite Developer" -> "NetSuite+Developer")
        encoded_keyword = urllib.parse.quote_plus(keyword)
        
        for page_num in range(1, MAX_PAGES_PER_KEYWORD + 1):
            if self.applied_count >= MAX_APPLICATIONS_PER_DAY:
                return

            # Corrected Anderson Frank Search URL based on their routing
            search_url = f"https://www.andersonfrank.com/netsuite-jobs?keyword={encoded_keyword}"
            if page_num > 1:
                search_url += f"&page={page_num}"
                
            logging.info(f"Scanning for: '{keyword}' (Page {page_num})")
            
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            except PlaywrightTimeoutError:
                logging.warning("Page load timed out, but proceeding as the DOM might be ready...")
                
            self.human_delay(3, 5)
            
            try:
                # Wait for at least one job card to load before trying to grab them
                page.wait_for_selector("a[href*='/job/']", timeout=15000)
                job_cards = page.locator("a[href*='/job/']").all()
                
                if len(job_cards) == 0:
                    logging.warning(f"No job cards found on page {page_num}. Ending keyword search.")
                    break
                    
                logging.info(f"Found {len(job_cards)} listings on page {page_num}.")
            except PlaywrightTimeoutError:
                logging.warning(f"Timeout waiting for jobs on page {page_num}. Ending keyword search.")
                break

            skipped_due_to_db = 0
            
            for index in range(len(job_cards)):
                if self.applied_count >= MAX_APPLICATIONS_PER_DAY: return

                current_cards = page.locator("a[href*='/job/']").all()
                if index >= len(current_cards): break
                    
                card = current_cards[index]
                try:
                    # The card itself is the <a> tag on Anderson Frank
                    job_href = card.get_attribute("href") or f"unknown_{random.randint(1000,9999)}"
                    job_id = job_href
                    
                    title_elem = card.locator("h3.jobTitle").first
                    title = title_elem.inner_text() if title_elem.is_visible() else "Unknown Job"
                    
                    company = "Anderson Frank Client" # AF usually hides the actual client name

                    if self.db.has_applied(job_id):
                        skipped_due_to_db += 1
                        continue

                    if not self.is_relevant_job(title):
                        logging.info(f"   -> RATIONAL SKIP: '{title}' is not a relevant tech role.")
                        self.db.log_job(job_id, title, company, "SKIPPED_GARBAGE")
                        continue

                    logging.info(f"Evaluating: {title}")
                    
                    # Manually open in a new tab to avoid expect_page() timeout if AF doesn't use target="_blank"
                    job_url = urllib.parse.urljoin("https://www.andersonfrank.com", job_href)
                    job_page = page.context.new_page()
                    
                    try:
                        job_page.goto(job_url, wait_until="domcontentloaded", timeout=60000)
                    except PlaywrightTimeoutError:
                        logging.warning("Job page load timed out, attempting to proceed...")
                    
                    needs_manual = self.process_job_page(job_page, job_id, title, company)
                    if not needs_manual:
                        job_page.close()
                    else:
                        logging.info(f"   -> TAB LEFT OPEN: Please manually complete application.")
                        
                    self.human_delay()

                except Exception as e:
                    logging.warning(f"Failed processing a job card: {e}")
                    continue
            
            if skipped_due_to_db > 0:
                logging.info(f"   -> Skipped {skipped_due_to_db} jobs on this page because they were already in the database.")

    def process_job_page(self, job_page, job_id, title, company):
        try:
            job_page.wait_for_timeout(3500)
            
            # Generalized Apply button selector for the first step
            apply_button = job_page.locator("a:has-text('Apply'), button:has-text('Apply')").first
            if not apply_button.is_visible():
                logging.info("   -> EXTERNAL LINK: No native Apply button.")
                self.db.log_job(job_id, title, company, "MANUAL_EXTERNAL")
                return True 

            logging.info("   -> FOUND native Apply button. Clicking...")
            apply_button.click()
            
            # === NEW CODE: Handle the "Confirm resume and apply" modal ===
            try:
                # Wait up to 5 seconds for the confirmation modal/button to appear.
                # This regex looks for a button that contains EITHER "Apply with" OR "Confirm resume"
                # This perfectly captures "Apply with Moses Rose technical expert resume"
                confirm_button = job_page.locator("button").filter(
                    has_text=re.compile(r"(Apply with|Confirm resume)", re.IGNORECASE)
                ).first
                
                # Wait for the button to actually be visible on screen
                confirm_button.wait_for(state="visible", timeout=5000)
                
                button_text = confirm_button.inner_text().strip()
                logging.info(f"   -> FOUND final apply button: '{button_text}'. Clicking...")
                
                confirm_button.click()
                
                # Wait a few seconds for the application to successfully process
                job_page.wait_for_timeout(4000)
                logging.info("   -> Successfully applied!")
                
                # Update database status to success
                self.db.log_job(job_id, title, company, "APPLIED_SUCCESSFULLY")
                self.applied_count += 1
                
                # Close the tab because we succeeded
                return False
                
            except PlaywrightTimeoutError:
                # If the button doesn't appear, the form might require manual fields (like cover letter)
                logging.info("   -> Could not find final confirmation button within 5 seconds. Manual review needed.")
                self.db.log_job(job_id, title, company, "MANUAL_INCOMPLETE")

            return True 

        except Exception as e:
            logging.error(f"   -> ERROR processing job: {e}")
            self.db.log_job(job_id, title, company, "FAILED_ERROR")
            return True 

if __name__ == "__main__":
    bot = AndersonFrankBot()
    bot.run()