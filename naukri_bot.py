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

TARGET_KEYWORDS = ["NetSuite Developer", "NetSuite Technical Consultant", "NetSuite Integration", "NetSuite"]
MAX_APPLICATIONS_PER_DAY = 30
MAX_PAGES_PER_KEYWORD = 30

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
        # STRICTLY restricted to prevent catching Medical Consultants or Civil Engineers
        core_tech = ["netsuite", "erp", "boomi", "celigo"]
        return any(tech in title.lower() for tech in core_tech)

    def answer_question(self, question_text):
        """
        Instant heuristic-based question answering.
        Replaces the slow, rate-limited LLM.
        """
        q = question_text.lower()
        
        if any(word in q for word in ["notice", "period", "joining", "join"]):
            return "0"
        elif any(word in q for word in ["ctc", "salary", "expected", "current", "compensation", "lpa"]):
            return "Negotiable"
        elif any(word in q for word in ["location", "city", "base", "relocate", "where"]):
            return "Coimbatore"
        elif any(word in q for word in ["experience", "exp", "years"]):
            return "4"
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
            
            # --- RATIONAL FIX: Network-resilient navigation ---
            try:
                page.goto(search_url, timeout=30000)
            except Exception as e:
                logging.error(f"Network error loading page {page_num} for {keyword}: {e}")
                logging.info("Internet might be unstable. Pausing for 5 seconds before moving to next keyword...")
                time.sleep(5)
                break # Break out of this keyword's pagination and try the next keyword

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

            # --- SEQUENTIAL CASCADING FORM & CHATBOT LOGIC ---
            max_loops = 15
            loops = 0
            
            while loops < max_loops:
                job_page.wait_for_timeout(1000)
                progress_made = False
                
                # 1. Standard Text Inputs (STRICTLY excludes dropdowns/suggestors to prevent timeouts)
                valid_text_selector = "textarea, input[type='text']:not(.ddInput):not(.suggestor-input):not(.nonSearched), input[type='number']"
                for field in job_page.locator(valid_text_selector).all():
                    if field.is_visible() and not field.input_value().strip():
                        try:
                            context_text = field.evaluate("el => el.closest('div').innerText")
                        except:
                            context_text = field.get_attribute("placeholder") or ""
                        
                        answer = self.answer_question(context_text)
                        field.fill("")
                        field.type(answer)
                        self.human_delay(1, 2)
                        progress_made = True

                # 2. Standard Dropdowns (.ddInput)
                for dd in job_page.locator("input.ddInput").all():
                    if dd.is_visible() and not dd.input_value().strip():
                        dd.click()
                        job_page.wait_for_timeout(800)
                        option = job_page.locator("ul.dropdown li:not(.heading):not(.disabled)").first
                        if option.is_visible():
                            option.click()
                            self.human_delay(1, 2)
                            progress_made = True
                        else:
                            dd.click() # Close it if no options are visible
                            
                # 3. Auto-Suggestors (.suggestor-input)
                for sugg in job_page.locator("input.suggestor-input").all():
                    if sugg.is_visible() and not sugg.input_value().strip():
                        sugg.click()
                        sugg.type("NetSuite", delay=100)
                        job_page.wait_for_timeout(1500)
                        sugg_opt = job_page.locator(".suggestor-tag, .drop-layer .opt:not(.opt-head)").first
                        if sugg_opt.is_visible():
                            sugg_opt.click()
                            self.human_delay(1, 2)
                            progress_made = True

                # 4. Chatbot Text Areas
                for cb_text in job_page.locator(".textArea[contenteditable='true']").all():
                    if cb_text.is_visible() and not cb_text.inner_text().strip():
                        try:
                            context_text = job_page.locator(".botMsg").last.inner_text()
                        except:
                            context_text = "Question"
                        answer = self.answer_question(context_text)
                        cb_text.click()
                        job_page.keyboard.type(answer)
                        self.human_delay(1, 2)
                        progress_made = True

                # 5. Chatbot Interactive Options (Chips, Radio, Checkboxes)
                if not progress_made:
                    chatbot_options = job_page.locator(".chatbot_Chip:not(.chatbot_Selected), .ssrc__label, .mcc__label").all()
                    visible_options = [opt for opt in chatbot_options if opt.is_visible()]
                    if visible_options:
                        # Find the best option if possible, otherwise click the first one
                        best_opt = visible_options[0]
                        for opt in visible_options:
                            text = opt.inner_text().lower()
                            if "netsuite" in text or "yes" in text or "3" in text or "4" in text or "5" in text:
                                best_opt = opt
                                break
                        best_opt.click()
                        self.human_delay(1, 2)
                        progress_made = True

                # 6. Chatbot Save/Next Button
                chatbot_save_btn = job_page.locator(".sendMsg").first
                if chatbot_save_btn.is_visible():
                    # Ensure the button is actually enabled by checking its parent wrapper
                    is_disabled = chatbot_save_btn.evaluate("el => el.parentElement.classList.contains('disabled')")
                    if not is_disabled:
                        chatbot_save_btn.click()
                        self.human_delay(1, 2)
                        progress_made = True

                if not progress_made:
                    break 
                    
                loops += 1

            # --- SUBMIT CHECK ---
            submit_btn = job_page.locator("button:has-text('Submit'), button:has-text('Save & Apply')").first
            if submit_btn.is_visible():
                submit_btn.click()
                job_page.wait_for_timeout(3000)
                
            error_msg = job_page.locator(".error-message, .required, text='Required'").first
            if error_msg.is_visible():
                logging.warning("   -> FORM INCOMPLETE: UI Validation failed.")
                self.db.log_job(job_id, title, company, "MANUAL_INCOMPLETE")
                return True 
                
            # If standard submit button is STILL visible after clicking it, it failed.
            if submit_btn.is_visible():
                logging.warning("   -> FORM INCOMPLETE: Submit button still present.")
                self.db.log_job(job_id, title, company, "MANUAL_INCOMPLETE")
                return True
                
            # If it's a chatbot and the send button is still active/visible, we stalled out.
            chatbot_send = job_page.locator(".sendMsg").first
            if chatbot_send.is_visible():
                logging.warning("   -> FORM INCOMPLETE: Chatbot stalled out.")
                self.db.log_job(job_id, title, company, "MANUAL_INCOMPLETE")
                return True
                
            logging.info(f"   -> SUCCESS! Application submitted.")
            self.db.log_job(job_id, title, company, "APPLIED")
            self.applied_count += 1
            return False 

        except Exception as e:
            logging.error(f"   -> ERROR processing job: {e}")
            self.db.log_job(job_id, title, company, "FAILED_ERROR")
            return True 

if __name__ == "__main__":
    bot = NaukriBot()
    bot.run()