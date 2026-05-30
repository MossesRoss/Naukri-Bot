import os
import time
import random
import sqlite3
import logging
from urllib.parse import quote
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Load variables
load_dotenv()

# --- CONFIGURATION ---
TARGET_KEYWORDS = ["NetSuite Developer", "NetSuite Technical Consultant", "NetSuite Integration", "NetSuite"]
LOCATION = "India" # Change to "Remote", "Coimbatore", etc. as needed.
MAX_APPLICATIONS_PER_DAY = 30
MAX_PAGES_PER_KEYWORD = 10 # Indeed usually shows 10-15 jobs per page.
INDEED_DOMAIN = "in.indeed.com" # Use www.indeed.com for US

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

class IndeedBot:
    def __init__(self):
        self.db = Database()
        self.applied_count = 0

    def human_delay(self, min_sec=1.5, max_sec=4.0):
        time.sleep(random.uniform(min_sec, max_sec))
        
    def is_relevant_job(self, title):
        core_tech = ["netsuite", "erp", "boomi", "celigo"]
        return any(tech in title.lower() for tech in core_tech)

    def answer_question(self, question_text):
        """Instant heuristic-based question answering."""
        q = question_text.lower()
        if any(word in q for word in ["notice", "period", "joining", "join", "start"]):
            return "0"
        elif any(word in q for word in ["ctc", "salary", "expected", "current", "compensation", "lpa", "pay"]):
            return "Negotiable"
        elif any(word in q for word in ["location", "city", "base", "relocate", "where", "commute"]):
            return "Coimbatore"
        elif any(word in q for word in ["experience", "exp", "years"]):
            return "4"
        else:
            return "Please refer to my attached resume."

    def run(self):
        with sync_playwright() as p:
            try:
                logging.info("Connecting to active Chrome session on port 9222...")
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
                context = browser.contexts[0]
                page = context.new_page()
            except Exception as e:
                logging.error(f"CRITICAL: Could not connect to Chrome. Ensure remote debugging is active. {e}")
                return

            try:
                # Assuming you are already logged in via the persistent profile.
                page.goto(f"https://{INDEED_DOMAIN}/", wait_until="domcontentloaded")
                self.human_delay(2, 4)
                
                for keyword in TARGET_KEYWORDS:
                    if self.applied_count >= MAX_APPLICATIONS_PER_DAY:
                        break
                    self.search_and_apply(page, keyword)
                    
            except Exception as e:
                logging.error(f"Critical failure in run loop: {e}")
            finally:
                logging.info("=== OUTREACH COMPLETE ===")
                logging.info(f"Total Applied: {self.applied_count}")
                logging.info("Bot finished. LEAVING BROWSER OPEN so you can finish manual tabs.")
                input("Press ENTER in this terminal when you are done... (Your Chrome will stay open)")
                page.close()

    def search_and_apply(self, page, keyword):
        encoded_keyword = quote(keyword)
        encoded_location = quote(LOCATION)
        
        for page_num in range(MAX_PAGES_PER_KEYWORD):
            if self.applied_count >= MAX_APPLICATIONS_PER_DAY:
                return

            # Indeed uses 'start' parameter. 0 = page 1, 10 = page 2, 20 = page 3.
            start_offset = page_num * 10
            search_url = f"https://{INDEED_DOMAIN}/jobs?q={encoded_keyword}&l={encoded_location}&start={start_offset}"
            
            logging.info(f"Scanning for: '{keyword}' (Page {page_num + 1})")
            
            try:
                page.goto(search_url, timeout=40000, wait_until="domcontentloaded")
                self.human_delay(3, 5) # Indeed is sensitive, keep delays reasonable
            except Exception as e:
                logging.error(f"Network error loading page {page_num + 1}: {e}")
                time.sleep(5)
                break 

            # Check for Cloudflare/Captcha blocks
            if "hcaptcha" in page.content().lower() or "cloudflare" in page.content().lower():
                logging.error("CRITICAL: Detected Captcha/Cloudflare block. Please solve it manually in the browser.")
                input("Press ENTER here after you have solved the Captcha...")
                page.reload()
                self.human_delay(3, 5)

            try:
                # Wait for job cards (Usually wrapped in div with class job_seen_beacon)
                page.wait_for_selector("div.job_seen_beacon, td.resultContent", timeout=15000)
                job_cards = page.locator("div.job_seen_beacon, td.resultContent").all()
                logging.info(f"Found {len(job_cards)} listings on this page.")
            except PlaywrightTimeoutError:
                logging.warning(f"No jobs loaded on page {page_num + 1}. Ending keyword search.")
                break

            skipped_due_to_db = 0
            
            for index in range(len(job_cards)):
                if self.applied_count >= MAX_APPLICATIONS_PER_DAY: return
                
                # Re-fetch cards to avoid stale element reference exceptions after DOM updates
                current_cards = page.locator("div.job_seen_beacon, td.resultContent").all()
                if index >= len(current_cards): break
                    
                card = current_cards[index]
                try:
                    # Scroll to card so it registers as "seen" and avoids interception
                    card.scroll_into_view_if_needed()
                    
                    # Extract Data
                    title_elem = card.locator("h2.jobTitle, a[id^='job_']").first
                    title = title_elem.inner_text().strip() if title_elem.is_visible() else "Unknown Job"
                    
                    company_elem = card.locator("[data-testid='company-name'], span.companyName").first
                    company = company_elem.inner_text().strip() if company_elem.is_visible() else "Unknown"
                    
                    # Indeed Job ID (jk) is usually in the href of the title link or a data attribute
                    link_elem = card.locator("a[data-jk]").first
                    if link_elem.is_visible():
                        job_id = link_elem.get_attribute("data-jk")
                    else:
                        # Fallback parsing ID from URL if data attribute is missing
                        href = title_elem.get_attribute("href") or ""
                        job_id = href.split("jk=")[-1].split("&")[0] if "jk=" in href else str(random.randint(10000, 99999))

                    if not job_id or self.db.has_applied(job_id):
                        skipped_due_to_db += 1
                        continue

                    if not self.is_relevant_job(title):
                        logging.info(f"   -> RATIONAL SKIP: '{title}' is not a relevant tech role.")
                        self.db.log_job(job_id, title, company, "SKIPPED_GARBAGE")
                        continue

                    logging.info(f"Evaluating: {title} at {company}")
                    
                    # Click the card to open the right-pane details
                    card.click()
                    self.human_delay(2, 4)
                    
                    # Wait for right pane to load
                    right_pane = page.locator(".jobsearch-RightPane, .jobsearch-ViewJobLayout-jobDisplay").first
                    try:
                        right_pane.wait_for(state="visible", timeout=10000)
                    except:
                        logging.warning("   -> Right pane did not load. Skipping.")
                        continue

                    needs_manual = self.process_job_pane(page, job_id, title, company)
                    
                    if needs_manual:
                        logging.info(f"   -> MANUAL INTERVENTION REQUIRED for {company}. Proceeding to next.")
                    
                    self.human_delay(1, 2)

                except Exception as e:
                    logging.warning(f"Failed processing a job card. {e}")
                    continue
            
            if skipped_due_to_db > 0:
                logging.info(f"   -> Skipped {skipped_due_to_db} jobs on this page because they were already in the database.")

    def process_job_pane(self, page, job_id, title, company):
        try:
            # 1. Look for Indeed "Easily apply" button (Usually an ID or specific text)
            apply_button = page.locator("#indeedApplyButton, button:has-text('Apply now')").first
            
            if not apply_button.is_visible():
                logging.info("   -> EXTERNAL LINK: No native Apply button (Likely 'Apply on company site').")
                self.db.log_job(job_id, title, company, "MANUAL_EXTERNAL")
                return True 

            logging.info("   -> FOUND native Apply button. Clicking...")
            apply_button.click()
            self.human_delay(3, 5) # Modal takes time to initialize

            # --- INDEED APPLY MODAL LOGIC ---
            # Indeed's application is a multi-step overlay (div#ia-container)
            max_steps = 10
            steps = 0
            
            while steps < max_steps:
                self.human_delay(1, 2)
                
                # Check for completion (Submit application button)
                final_submit_btn = page.locator("button:has-text('Submit your application')").first
                if final_submit_btn.is_visible():
                    final_submit_btn.click()
                    self.human_delay(3, 5)
                    logging.info(f"   -> SUCCESS! Application submitted.")
                    self.db.log_job(job_id, title, company, "APPLIED")
                    self.applied_count += 1
                    
                    # Close the post-apply confirmation modal if it appears
                    close_btn = page.locator("button#close-popup, button[aria-label='Close']").first
                    if close_btn.is_visible(): close_btn.click()
                    
                    return False # Success, no manual intervention needed

                # Identify visible inputs in the modal
                modal_inputs = page.locator("#ia-container input:not([type='hidden']), #ia-container textarea, #ia-container select").all()
                progress_made = False

                for field in modal_inputs:
                    if field.is_visible():
                        tag_name = field.evaluate("el => el.tagName.toLowerCase()")
                        input_type = field.get_attribute("type")
                        
                        # Handle Text & Number Inputs
                        if tag_name == "textarea" or (tag_name == "input" and input_type in ["text", "number", "tel"]):
                            if not field.input_value().strip():
                                try:
                                    context_text = field.evaluate("el => el.closest('div').innerText")
                                except:
                                    context_text = field.get_attribute("aria-labelledby") or ""
                                
                                answer = self.answer_question(context_text)
                                field.fill("")
                                field.type(answer)
                                self.human_delay(0.5, 1)
                                progress_made = True
                                
                        # Handle Select/Dropdowns
                        elif tag_name == "select":
                            # Extremely basic logic: Just select the second option (usually index 1 as 0 is "Select...")
                            try:
                                options = field.locator("option").all()
                                if len(options) > 1:
                                    val = options[1].get_attribute("value")
                                    field.select_option(val)
                                    self.human_delay(0.5, 1)
                                    progress_made = True
                            except:
                                pass
                                
                        # Handle Checkboxes / Radios
                        elif tag_name == "input" and input_type in ["radio", "checkbox"]:
                            is_checked = field.evaluate("el => el.checked")
                            if not is_checked:
                                # We blindly check visible options. For Indeed this usually means clicking "Yes" or agreeing to terms
                                try:
                                    label_text = field.evaluate("el => el.parentElement.innerText").lower()
                                    if "yes" in label_text or "agree" in label_text or "netsuite" in label_text:
                                        field.click()
                                        progress_made = True
                                except:
                                    pass

                # Look for navigation buttons (Continue, Next, Review)
                nav_btn = page.locator("button:has-text('Continue'), button:has-text('Review your application')").first
                if nav_btn.is_visible():
                    # Check if disabled due to mandatory fields we failed to fill
                    is_disabled = nav_btn.evaluate("el => el.disabled || el.hasAttribute('aria-disabled')")
                    if not is_disabled:
                        nav_btn.click()
                        self.human_delay(2, 3)
                        steps += 1
                        continue
                    else:
                        logging.warning("   -> FORM STALLED: Continue button disabled. Requires manual fill.")
                        break # Break out to manual intervention

                # If no progress can be made and no nav buttons are clickable, we are stuck
                if not progress_made and not final_submit_btn.is_visible():
                    break
                    
                steps += 1

            # If the loop exhausted or broke without hitting submit, flag for manual intervention
            logging.warning("   -> FORM INCOMPLETE or Multi-Step stalled. Requires manual completion.")
            self.db.log_job(job_id, title, company, "MANUAL_INCOMPLETE")
            
            # Click the exit/X button on the modal so we can proceed to the next job in the background loop
            close_modal = page.locator("button[aria-label='Close application'], #close-popup").first
            if close_modal.is_visible():
                close_modal.click()
                self.human_delay(1)
                confirm_close = page.locator("button:has-text('Discard'), button:has-text('Exit')").first
                if confirm_close.is_visible(): confirm_close.click()
            
            return True 

        except Exception as e:
            logging.error(f"   -> ERROR processing job pane: {e}")
            self.db.log_job(job_id, title, company, "FAILED_ERROR")
            
            # Attempt cleanup
            try:
                page.locator("button[aria-label='Close application']").first.click(timeout=1000)
            except:
                pass
            return True 

if __name__ == "__main__":
    bot = IndeedBot()
    bot.run()