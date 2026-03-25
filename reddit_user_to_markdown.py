import os
import time
import random
import json
import http.cookiejar
from typing import List, Optional, Dict, Any

import requests
import praw
from slugify import slugify
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By

# --- Load Environment Variables ---
load_dotenv()

# --- Configuration ---
USERNAME_TO_SCRAPE = "armadaofgold"
LIMIT = 300
COOKIE_FILE = "cookies.txt"
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

    # Include the URL if it's not a self-post
    is_self_post = post_data.get('is_self', False)
    if not is_self_post and post_data.get('url'):
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

# --- Method 1: PRAW (Official API) ---

def run_praw():
    """Uses PRAW with credentials from .env to fetch data."""
    print("Using Method 1: PRAW (Official API)")

    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    username = os.getenv("REDDIT_USERNAME")
    password = os.getenv("REDDIT_PASSWORD")

    if not all([client_id, client_secret, username, password]):
        print("Error: Missing PRAW credentials in .env file.")
        return

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=f"python:reddit_to_md:v1.0 (by /u/{username})",
        username=username,
        password=password
    )

    processed_ids = get_processed_posts()
    user = reddit.redditor(USERNAME_TO_SCRAPE)

    posts_fetched = 0
    try:
        for submission in user.submissions.new(limit=LIMIT):
            if submission.id in processed_ids:
                continue

            print(f"Processing post: {submission.title} ({submission.id})")

            post_data = {
                'id': submission.id,
                'title': submission.title,
                'selftext': submission.selftext,
                'author': str(submission.author),
                'ups': submission.ups,
                'downs': submission.downs,
                'permalink': submission.permalink,
                'is_self': submission.is_self,
                'url': submission.url
            }

            # Get top 5 comments
            submission.comment_sort = 'top'
            submission.comments.replace_more(limit=0)
            comments_data = []
            for comment in submission.comments[:5]:
                # If we get a 'MoreComments' object instead of a comment, skip it
                if isinstance(comment, praw.models.Comment):
                    comments_data.append({
                        'body': comment.body,
                        'author': str(comment.author),
                        'ups': comment.ups,
                        'downs': comment.downs
                    })

            save_markdown(post_data, comments_data)
            mark_as_processed(submission.id, submission.title)
            posts_fetched += 1
    except Exception as e:
        print(f"Error fetching posts with PRAW: {e}")

    print(f"Done! Processed {posts_fetched} new posts.")

# --- Method 2: Cookies (JSON API) ---

def load_cookies(file_path):
    if not os.path.exists(file_path):
        return None
    try:
        cj = http.cookiejar.MozillaCookieJar(file_path)
        cj.load(ignore_discard=True, ignore_expires=True)
        return cj
    except Exception:
        return None

def fetch_json(url, session):
    time.sleep(random.uniform(2, 4))
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        response = session.get(url, headers=headers)
        return response.json() if response.status_code == 200 else None
    except Exception:
        return None

def run_cookies():
    """Uses the JSON API with cookies from cookies.txt."""
    print("Using Method 2: Cookie Session")
    session = requests.Session()
    cookies = load_cookies(COOKIE_FILE)
    if cookies:
        session.cookies = cookies
    else:
        print(f"Warning: {COOKIE_FILE} not found. Reddit may block requests.")

    processed_ids = get_processed_posts()
    after = None
    posts_fetched = 0

    while posts_fetched < LIMIT:
        url = f"https://www.reddit.com/user/{USERNAME_TO_SCRAPE}/submitted.json?limit=100"
        if after: url += f"&after={after}"

        data = fetch_json(url, session)
        if not data or 'data' not in data: break

        children = data['data']['children']
        if not children: break

        for post in children:
            if posts_fetched >= LIMIT: break
            p_data = post['data']
            if p_data['id'] in processed_ids: continue

            print(f"Processing post: {p_data['title']} ({p_data['id']})")

            # Fetch comments
            post_url = f"https://www.reddit.com{p_data['permalink']}.json"
            post_full_data = fetch_json(post_url, session)

            comments_data = []
            if post_full_data and isinstance(post_full_data, list) and len(post_full_data) >= 2:
                raw_comments = post_full_data[1]['data']['children']
                for rc in [c for c in raw_comments if c['kind'] == 't1'][:5]:
                    d = rc['data']
                    comments_data.append({
                        'body': d.get('body', '[deleted]'),
                        'author': d.get('author', '[deleted]'),
                        'ups': d.get('ups', 0),
                        'downs': d.get('downs', 0)
                    })

            save_markdown(p_data, comments_data)
            mark_as_processed(p_data['id'], p_data['title'])
            posts_fetched += 1

        after = data['data'].get('after')
        if not after: break
    print(f"Done! Processed {posts_fetched} new posts.")

# --- Method 3: Selenium ---

def run_selenium():
    """Uses Selenium to scrape the user's posts by navigating to their .json view."""
    print("Using Method 3: Selenium Browser Automation")

    chrome_options = Options()
    # If you run on a server, keep headless. For local, you can comment it out to see the window.
    chrome_options.add_argument("--headless")
    chrome_options.add_argument(f"user-agent={USER_AGENT}")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

    try:
        print(f"Navigating to u/{USERNAME_TO_SCRAPE}'s submitted posts via browser...")
        # Since scraping the dynamic UI with Selenium is very fragile,
        # we'll use Selenium to navigate to the .json pages, which still
        # uses the browser's identity and cookies.

        processed_ids = get_processed_posts()
        after = None
        posts_fetched = 0

        while posts_fetched < LIMIT:
            url = f"https://www.reddit.com/user/{USERNAME_TO_SCRAPE}/submitted.json?limit=100"
            if after: url += f"&after={after}"

            driver.get(url)
            time.sleep(random.uniform(5, 7)) # Allow browser to render/process

            # Extract text from pre or body
            try:
                content = driver.find_element(By.TAG_NAME, "pre").text
            except:
                content = driver.find_element(By.TAG_NAME, "body").text

            data = json.loads(content)
            if not data or 'data' not in data: break

            children = data['data']['children']
            if not children: break

            for post in children:
                if posts_fetched >= LIMIT: break
                p_data = post['data']
                if p_data['id'] in processed_ids: continue

                print(f"Processing post: {p_data['title']} ({p_data['id']})")

                # Fetch comments via Selenium
                post_url = f"https://www.reddit.com{p_data['permalink']}.json"
                driver.get(post_url)
                time.sleep(random.uniform(3, 5))

                try:
                    c_content = driver.find_element(By.TAG_NAME, "pre").text
                except:
                    c_content = driver.find_element(By.TAG_NAME, "body").text

                post_full_data = json.loads(c_content)

                comments_data = []
                if post_full_data and isinstance(post_full_data, list) and len(post_full_data) >= 2:
                    raw_comments = post_full_data[1]['data']['children']
                    for rc in [c for c in raw_comments if c['kind'] == 't1'][:5]:
                        d = rc['data']
                        comments_data.append({
                            'body': d.get('body', '[deleted]'),
                            'author': d.get('author', '[deleted]'),
                            'ups': d.get('ups', 0),
                            'downs': d.get('downs', 0)
                        })

                save_markdown(p_data, comments_data)
                mark_as_processed(p_data['id'], p_data['title'])
                posts_fetched += 1

            after = data['data'].get('after')
            if not after: break

    finally:
        driver.quit()
    print(f"Done! Processed {posts_fetched} new posts.")

# --- Main Entry Point ---

def main():
    print("Welcome to RedditToMarkdown!")
    print(f"Targeting u/{USERNAME_TO_SCRAPE}")
    print("\nPlease select your preferred login method:")
    print("1) Username and Password (from .env)")
    print("2) Cookie Session (from cookies.txt)")
    print("3) Selenium Browser Automation")

    choice = input("\nEnter choice (1/2/3): ").strip()

    if choice == "1":
        run_praw()
    elif choice == "2":
        run_cookies()
    elif choice == "3":
        run_selenium()
    else:
        print("Invalid choice.")

if __name__ == "__main__":
    main()
