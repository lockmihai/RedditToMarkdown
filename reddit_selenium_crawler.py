import os
import time
import random
import json
from typing import List, Dict, Any

from slugify import slugify
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- Load Environment Variables ---
load_dotenv()

# --- Configuration ---
USERNAME_TO_SCRAPE = "armadaofgold"
LIMIT = 200 # Target number of posts to crawl
PROGRESS_FILE = "progress.jsonl"
OUTPUT_DIR = USERNAME_TO_SCRAPE
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

# Create output directory if it doesn't exist
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# --- Common Utilities ---

def get_processed_posts():
    """Reads the progress file to see which posts have already been processed."""
    processed = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    processed.add(data["id"])
                except json.JSONDecodeError:
                    continue
    return processed

def mark_as_processed(post_id, title):
    """Appends a processed post ID to the progress file."""
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": post_id, "title": title}) + "\n")

def save_markdown(post_data: Dict[str, Any], comments_data: List[Dict[str, Any]]):
    """Saves post and comments into two separate markdown files."""
    post_id = post_data['id']
    title = post_data['title']

    slug = slugify(title)
    if not slug:
        slug = "untitled-post"
    filename_base = f"{slug}-{post_id}"

    # 1. Author's post file content
    author_content = f"# {title}\n"
    if post_data.get('selftext'):
        author_content += f"\n{post_data['selftext']}\n"

    if post_data.get('url') and not post_data.get('is_self', False):
        author_content += f"\n**Link/Media:** {post_data['url']}\n"

    author_content += f"\n[permalink](https://reddit.com{post_data['permalink']})\n"
    author_content += f"by *{post_data['author']}* (↑ {post_data['ups']}/ ↓ {post_data['downs']})\n"

    author_path = os.path.join(OUTPUT_DIR, f"{filename_base}.md")
    with open(author_path, "w", encoding="utf-8") as f:
        f.write(author_content)

    # 2. Comments file content
    comments_content = "## Comments\n\n"
    if not comments_data:
        comments_content += "No comments found.\n"
    else:
        for comment in comments_data:
            comments_content += f"##### {comment['body']} ⏤ by *{comment['author']}* (↑ {comment['ups']}/ ↓ {comment['downs']})\n\n"

    comments_path = os.path.join(OUTPUT_DIR, f"{filename_base}-comments.md")
    with open(comments_path, "w", encoding="utf-8") as f:
        f.write(comments_content)

    print(f"Saved: {filename_base}.md and {filename_base}-comments.md")

# --- Selenium Crawler Logic ---

def init_driver():
    chrome_options = Options()
    # If running in a server environment (like this sandbox), headless is necessary.
    # For local execution, you might want to comment this out to see what's happening.
    chrome_options.add_argument("--headless")
    chrome_options.add_argument(f"user-agent={USER_AGENT}")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    return driver

def login_reddit(driver):
    """Performs login on Reddit using credentials from .env."""
    username = os.getenv("REDDIT_USERNAME")
    password = os.getenv("REDDIT_PASSWORD")

    if not username or not password:
        print("Error: Missing REDDIT_USERNAME or REDDIT_PASSWORD in .env.")
        return False

    print(f"Logging in to Reddit as /u/{username}...")
    driver.get("https://www.reddit.com/login/")

    try:
        # Wait for username field and enter it
        user_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "login-username"))
        )
        user_field.send_keys(username)

        # Enter password
        pass_field = driver.find_element(By.ID, "login-password")
        pass_field.send_keys(password)
        pass_field.send_keys(Keys.ENTER)

        # Wait for login to complete (check for profile icon or URL change)
        time.sleep(10) # Longer wait for MFA/Redirects
        print("Login attempt complete.")
        return True
    except Exception as e:
        print(f"Login failed: {e}")
        return False

def get_post_urls(driver, username_to_scrape, limit):
    """Navigates to the user's profile and collects post URLs by scrolling."""
    print(f"Collecting post URLs for u/{username_to_scrape}...")
    driver.get(f"https://www.reddit.com/user/{username_to_scrape}/submitted/")
    time.sleep(5)

    post_links = []
    last_height = driver.execute_script("return document.body.scrollHeight")

    # Try multiple selectors for better coverage across UI versions
    link_selectors = [
        'a[slot="full-post-link"]',
        'a[data-click-id="body"]',
        'a[href*="/comments/"]'
    ]

    while len(post_links) < limit:
        # Collect links
        links_found = []
        for selector in link_selectors:
            links_found.extend(driver.find_elements(By.CSS_SELECTOR, selector))

        for link in links_found:
            url = link.get_attribute("href")
            # Only collect actual comment post links, not profile links or other sub-elements
            if url and "/comments/" in url and "reddit.com" in url:
                # Clean URL (remove trailing slashes or query params if any)
                url = url.split("?")[0]
                if url.endswith("/"): url = url[:-1]

                if url not in post_links:
                    post_links.append(url)
                    if len(post_links) >= limit:
                        break

        print(f"Collected {len(post_links)} URLs...")

        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(random.uniform(3, 5))

        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            # Scroll up a bit and down again to trigger lazy loading if stuck
            driver.execute_script("window.scrollBy(0, -300);")
            time.sleep(1)
            driver.execute_script("window.scrollBy(0, 600);")
            time.sleep(2)
            if driver.execute_script("return document.body.scrollHeight") == last_height:
                break
        last_height = new_height

    return post_links[:limit]

def extract_post_data(driver, url):
    """Extracts post title, content, and top 5 comments from a post page."""
    driver.get(url)
    time.sleep(random.uniform(5, 8)) # Wait for comments to load

    # Extract Post ID from URL
    post_id = url.split("/comments/")[1].split("/")[0]

    try:
        # Title extraction (common selector for both old and new Reddit)
        try:
            title = driver.find_element(By.TAG_NAME, "h1").text
        except:
            title = driver.title.split(":")[0].strip()

        # Self-text content (using a wide range of common Reddit selectors)
        selftext = ""
        content_selectors = [
            'div[data-click-id="text_content"]',
            'div[slot="text-body"]',
            'div.usertext-body',
            'div[data-testid="post-container"] div[data-click-id="text_content"]'
        ]
        for selector in content_selectors:
            try:
                elem = driver.find_element(By.CSS_SELECTOR, selector)
                if elem.text:
                    selftext = elem.text
                    break
            except: continue

        # Author extraction
        try:
            author = driver.find_element(By.CSS_SELECTOR, 'a[data-click-id="user_link"]').text.replace("u/", "")
        except:
            author = USERNAME_TO_SCRAPE

        # Upvotes
        try:
            ups_selector = '[id^="vote-arrows-t3_"] [class*="score"], .score.unvoted'
            ups = driver.find_element(By.CSS_SELECTOR, ups_selector).text
        except:
            ups = "?"

        post_data = {
            'id': post_id,
            'title': title,
            'selftext': selftext,
            'author': author,
            'ups': ups,
            'downs': "0",
            'permalink': f"/comments/{post_id}/",
            'is_self': True if selftext else False,
            'url': url
        }

        # Extract top 5 comments
        print(f"Extracting comments for {title}...")
        comments_data = []

        # Target comment blocks
        comment_selectors = [
            'div[id^="t1_"]',
            'div[data-testid="comment"]',
            '.comment'
        ]

        comment_elements = []
        for selector in comment_selectors:
            elems = driver.find_elements(By.CSS_SELECTOR, selector)
            if elems:
                comment_elements = elems
                break

        # Process first 5
        for elem in comment_elements[:10]: # Check first 10 to find 5 valid ones
            if len(comments_data) >= 5: break
            try:
                # Find body
                c_body = ""
                try:
                    c_body = elem.find_element(By.CSS_SELECTOR, 'div[data-testid="comment"], .md').text
                except: continue

                if not c_body: continue

                # Find author
                try:
                    c_author = elem.find_element(By.CSS_SELECTOR, 'a[id^="CommentTopMeta--Author"], .author').text
                except:
                    c_author = "[unknown]"

                # Find score
                try:
                    c_ups = elem.find_element(By.CSS_SELECTOR, '[id^="score_t1_"], .score').text
                except:
                    c_ups = "?"

                comments_data.append({
                    'body': c_body,
                    'author': c_author,
                    'ups': c_ups,
                    'downs': "0"
                })
            except:
                continue

        return post_data, comments_data

    except Exception as e:
        print(f"Error extracting data from {url}: {e}")
        return None, None

def main():
    driver = init_driver()
    processed_ids = get_processed_posts()

    try:
        # Note: Depending on Reddit's current UI and login flow,
        # automated login might be blocked or require MFA.
        # It's often more reliable to use Method 2 (Cookies) if possible,
        # but here we follow the Selenium requirement.
        if not login_reddit(driver):
            print("Login failed, but attempting to scrape public view...")

        post_urls = get_post_urls(driver, USERNAME_TO_SCRAPE, LIMIT)
        print(f"Found {len(post_urls)} posts to process.")

        posts_fetched = 0
        for url in post_urls:
            try:
                post_id = url.split("/comments/")[1].split("/")[0]
            except: continue

            if post_id in processed_ids:
                print(f"Skipping {post_id} (already processed).")
                continue

            print(f"Crawling: {url}")
            post_data, comments_data = extract_post_data(driver, url)

            if post_data:
                save_markdown(post_data, comments_data)
                mark_as_processed(post_id, post_data['title'])
                posts_fetched += 1
                # Random delay between posts
                time.sleep(random.uniform(3, 6))
            else:
                print(f"Failed to extract data for {url}")

        print(f"Done! Crawled {posts_fetched} new posts.")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
