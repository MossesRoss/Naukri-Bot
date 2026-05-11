import os
import time
import random
import sqlite3
import logging
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Load variables
load_dotenv()
NAUKRI_EMAIL = os.getenv("NAUKRI_EMAIL")
NAUKRI_PASS = os.getenv("NAUKRI_PASS")

TARGET_KEYWORDS = ["NetSuite Developer", "NetSuite Technical Consultant", "NetSuite Integration"]
MAX_APPLICATIONS_PER_DAY = 20
MAX_PAGES_PER_KEYWORD = 5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Database:
    def __init__(self, db_name="jobs.db"):
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

class NaukriBot:
    def __init__(self):
        self.db = Database()
        self.applied_count = 0

    def human_delay(self, min_sec=1, max_sec=3):
        time.sleep(random.uniform(min_sec, max_sec))
        
    def is_relevant_job(self, title):
        core_tech = ["netsuite", "erp", "boomi", "celigo", "integration", "software", "developer", "engineer", "consultant"]
        return any(tech in title.lower() for tech in core_tech)

    def answer_question(self, question_text):
        """
        Instant heuristic-based question answering.
        Replaces the slow, rate-limited LLM.
        """
        q = question_text.lower()
        
        # Notice Period
        if any(word in q for word in ["notice", "period", "joining", "join"]):
            return "0"
            
        # CTC / Salary
        elif any(word in q for word in ["ctc", "salary", "expected", "current", "compensation", "lpa"]):
            return "Negotiable"
            
        # Location
        elif any(word in q for word in ["location", "city", "base", "relocate", "where"]):
            return "Coimbatore"
            
        # Experience
        elif any(word in q for word in ["experience", "exp", "years"]):
            return "2"
            
        # Catch-all Default
        else:
            return "Please refer to my attached resume for details."

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
        logging.info("Checking authentication state...")
        page.goto("https://www.naukri.com/mnjuser/homepage")
        self.human_delay()
        
        if "login" in page.url.lower():
            logging.info("Not logged in. Entering credentials...")
            page.goto("https://www.naukri.com/nlogin/login")
            self.human_delay(2, 4)
            
            page.fill("input[id='usernameField']", NAUKRI_EMAIL)
            self.human_delay(1, 2)
            page.locator("input[id='passwordField']").type(NAUKRI_PASS, delay=100)
            self.human_delay(2, 3)
            
            submit_btn = page.locator("button[type='submit']").first
            if submit_btn.is_visible():
                submit_btn.click()
            
            try:
                page.wait_for_function("() => !window.location.href.toLowerCase().includes('login')", timeout=60000)
                logging.info("Authentication successful.")
                self.human_delay(2, 4)
            except PlaywrightTimeoutError:
                logging.error("Timeout waiting for redirect. Handle Captcha manually.")
                raise
        else:
            logging.info("Already logged in via Persistent Profile.")

    def search_and_apply(self, page, keyword):
        formatted_keyword = keyword.replace(" ", "-").lower()
        
        for page_num in range(1, MAX_PAGES_PER_KEYWORD + 1):
            if self.applied_count >= MAX_APPLICATIONS_PER_DAY:
                return

            search_url = f"https://www.naukri.com/{formatted_keyword}-jobs"
            if page_num > 1:
                search_url += f"-{page_num}"
                
            logging.info(f"Scanning for: '{keyword}' (Page {page_num})")
            page.goto(search_url)
            
            try:
                page.wait_for_selector("[data-job-id]", timeout=20000)
                job_cards = page.locator("[data-job-id]").all()
                logging.info(f"Found {len(job_cards)} listings on page {page_num}.")
            except PlaywrightTimeoutError:
                logging.warning(f"No jobs loaded on page {page_num}. Ending keyword search.")
                break

            skipped_due_to_db = 0
            
            for index in range(len(job_cards)):
                if self.applied_count >= MAX_APPLICATIONS_PER_DAY: return

                current_cards = page.locator("[data-job-id]").all()
                if index >= len(current_cards): break
                    
                card = current_cards[index]
                try:
                    job_id = card.get_attribute("data-job-id")
                    title = card.locator("a.title").first.inner_text() if card.locator("a.title").first.is_visible() else "Unknown Job"
                    company = card.locator("a.comp-name").first.inner_text() if card.locator("a.comp-name").first.is_visible() else "Unknown"

                    if self.db.has_applied(job_id):
                        skipped_due_to_db += 1
                        continue

                    if not self.is_relevant_job(title):
                        logging.info(f"   -> RATIONAL SKIP: '{title}' is not a relevant tech role.")
                        self.db.log_job(job_id, title, company, "SKIPPED_GARBAGE")
                        continue

                    logging.info(f"Evaluating: {title} at {company}")
                    
                    with page.context.expect_page() as new_page_info:
                        card.locator("a.title").first.click()
                    job_page = new_page_info.value
                    
                    job_page.wait_for_load_state("domcontentloaded")
                    
                    needs_manual = self.process_job_page(job_page, job_id, title, company)
                    if not needs_manual:
                        job_page.close()
                    else:
                        logging.info(f"   -> TAB LEFT OPEN: Please manually complete application for {company}.")
                        
                    self.human_delay()

                except Exception as e:
                    logging.warning(f"Failed processing a job card. Skipping.")
                    continue
            
            if skipped_due_to_db > 0:
                logging.info(f"   -> Skipped {skipped_due_to_db} jobs on this page because they were already in the database.")

    def process_job_page(self, job_page, job_id, title, company):
        try:
            job_page.wait_for_timeout(3500)
            
            apply_button = job_page.locator("button:has-text('Apply')").first
            if not apply_button.is_visible():
                logging.info("   -> EXTERNAL LINK: No native Apply button.")
                self.db.log_job(job_id, title, company, "MANUAL_EXTERNAL")
                return True 

            logging.info("   -> FOUND native Apply button. Clicking...")
            apply_button.click()
            job_page.wait_for_timeout(3000)

            # --- SEQUENTIAL CASCADING FORM LOGIC ---
            max_loops = 10
            loops = 0
            
            while loops < max_loops:
                job_page.wait_for_timeout(1000) # Give dynamic questions time to appear
                
                # Gather all currently visible inputs
                visible_inputs = []
                for field in job_page.locator("textarea, input[type='text'], input[type='number']").all():
                    if field.is_visible():
                        visible_inputs.append(field)
                
                # Filter down to inputs that haven't been typed in yet
                empty_inputs = [f for f in visible_inputs if not f.input_value().strip()]
                
                if not empty_inputs:
                    break 
                    
                logging.info(f"   -> Form Iteration {loops+1}: Found {len(empty_inputs)} new empty field(s). Answering...")
                
                for field in empty_inputs:
                    if not field.is_visible(): continue 
                    
                    try:
                        context_text = field.evaluate("el => el.closest('div').innerText")
                    except:
                        context_text = field.get_attribute("placeholder") or ""
                    
                    answer = self.answer_question(context_text)
                    field.fill("")
                    field.type(answer)
                    self.human_delay(1, 2)
                    
                loops += 1

            # --- SUBMIT CHECK ---
            submit_btn = job_page.locator("button:has-text('Submit'), button:has-text('Save & Apply')").first
            if submit_btn.is_visible():
                submit_btn.click()
                job_page.wait_for_timeout(3000)
                
                # Check for UI validation errors or if submit button is STILL visible
                error_msg = job_page.locator(".error-message, .required, text='Required'").first
                if error_msg.is_visible() or submit_btn.is_visible():
                    logging.warning("   -> FORM INCOMPLETE: Unfilled dropdowns, radio buttons, or validation failed.")
                    self.db.log_job(job_id, title, company, "MANUAL_INCOMPLETE")
                    return True 
                
                logging.info(f"   -> SUCCESS! Application submitted.")
                self.db.log_job(job_id, title, company, "APPLIED")
                self.applied_count += 1
                return False 
            else:
                logging.warning("   -> NO SUBMIT BUTTON: Modal might require complex interaction.")
                self.db.log_job(job_id, title, company, "MANUAL_NO_SUBMIT")
                return True 

        except Exception as e:
            logging.error(f"   -> ERROR processing job: {e}")
            self.db.log_job(job_id, title, company, "FAILED_ERROR")
            return True 

if __name__ == "__main__":
    bot = NaukriBot()
    bot.run()