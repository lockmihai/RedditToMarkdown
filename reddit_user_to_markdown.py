import os
import time
import random
import requests
import json
import http.cookiejar
from slugify import slugify

# --- Configuration ---
# The Reddit username to scrape
USERNAME = "armadaofgold"

# Number of posts to fetch (maximum ~1000 due to Reddit API limits)
LIMIT = 300

# Name of the Netscape-format cookie file (exported from browser)
COOKIE_FILE = "cookies.txt"

# File to track already processed posts to allow resuming
PROGRESS_FILE = "progress.jsonl"

# Directory where markdown files will be saved
OUTPUT_DIR = USERNAME

# Standard browser User-Agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"

# Create output directory if it doesn't exist
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def load_cookies(file_path):
    """
    Loads cookies from a Netscape-format cookie file.
    Note: To use this script, export your cookies from your browser
    (e.g., using 'Get cookies.txt LOCALLY' extension) into a file
    named 'cookies.txt' in the same directory as this script.
    """
    if not os.path.exists(file_path):
        print(f"Warning: {file_path} not found. Reddit will likely block requests from datacenter IPs without cookies.")
        return None
    try:
        cj = http.cookiejar.MozillaCookieJar(file_path)
        cj.load(ignore_discard=True, ignore_expires=True)
        return cj
    except Exception as e:
        print(f"Error loading cookies: {e}")
        return None

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

def fetch_json(url, session):
    """Fetches JSON data from Reddit with random delay and headers."""
    # Random delay 2-4 seconds to avoid rate limiting
    time.sleep(random.uniform(2, 4))

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://www.reddit.com/"
    }

    try:
        response = session.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 403:
            print(f"Error 403: Forbidden for {url}. Your cookies might be expired or Reddit is blocking your IP.")
            return None
        else:
            print(f"Error {response.status_code} fetching {url}")
            return None
    except Exception as e:
        print(f"Exception fetching {url}: {e}")
        return None

def process_comments(comment_list):
    """Processes comments (limited to top 5, no replies)."""
    output = "## Comments\n\n"

    # Filter only actual comments (kind 't1'), ignoring 'more' elements or deleted entries
    actual_comments = [c for c in comment_list if c['kind'] == 't1'][:5]

    if not actual_comments:
        output += "No comments found.\n"
        return output

    for comment in actual_comments:
        data = comment['data']
        body = data.get('body', '[deleted]')
        author = data.get('author', '[deleted]')
        ups = data.get('ups', 0)
        downs = data.get('downs', 0)

        # Formatting: body followed by author and upvotes/downvotes
        output += f"##### {body} ⏤ by *{author}* (↑ {ups}/ ↓ {downs})\n\n"

    return output

def save_markdown(post_data, comments_data):
    """Saves post and comments into two separate markdown files."""
    post_id = post_data['id']
    title = post_data['title']

    # Use python-slugify to create a filesystem-safe filename
    slug = slugify(title)
    if not slug:
        slug = "untitled-post"
    filename_base = f"{slug}-{post_id}"

    # 1. Author's post file content
    author_content = f"# {title}\n"
    if post_data.get('selftext'):
        author_content += f"\n{post_data['selftext']}\n"

    # Include the URL if it's not a self-post (e.g., image, video, or external link)
    is_self_post = post_data.get('is_self', False)
    if not is_self_post and post_data.get('url'):
        author_content += f"\n**Link/Media:** {post_data['url']}\n"

    author_content += f"\n[permalink](https://reddit.com{post_data['permalink']})\n"
    author_content += f"by *{post_data['author']}* (↑ {post_data['ups']}/ ↓ {post_data['downs']})\n"

    author_path = os.path.join(OUTPUT_DIR, f"{filename_base}.md")
    with open(author_path, "w", encoding="utf-8") as f:
        f.write(author_content)

    # 2. Comments file content
    comments_content = process_comments(comments_data)
    comments_path = os.path.join(OUTPUT_DIR, f"{filename_base}-comments.md")
    with open(comments_path, "w", encoding="utf-8") as f:
        f.write(comments_content)

    print(f"Saved: {filename_base}.md and {filename_base}-comments.md")

def main():
    session = requests.Session()
    cookies = load_cookies(COOKIE_FILE)
    if cookies:
        session.cookies = cookies

    processed_ids = get_processed_posts()
    print(f"Starting to fetch posts for u/{USERNAME} (skipping {len(processed_ids)} already processed)...")

    after = None
    posts_fetched = 0

    while posts_fetched < LIMIT:
        url = f"https://www.reddit.com/user/{USERNAME}/submitted.json?limit=100"
        if after:
            url += f"&after={after}"

        data = fetch_json(url, session)
        if not data or 'data' not in data or 'children' not in data['data']:
            print("Could not fetch user posts. Check your connection/cookies/IP.")
            break

        children = data['data']['children']
        if not children:
            print("No more posts found.")
            break

        for post in children:
            if posts_fetched >= LIMIT:
                break

            post_data = post['data']
            post_id = post_data['id']

            # Skip if already downloaded (based on progress file)
            if post_id in processed_ids:
                continue

            print(f"Processing post: {post_data['title']} ({post_id})")

            # Fetch full post JSON which contains [post_listing, comments_listing]
            post_url = f"https://www.reddit.com{post_data['permalink']}.json"
            post_full_data = fetch_json(post_url, session)

            if post_full_data and isinstance(post_full_data, list) and len(post_full_data) >= 2:
                comments_data = post_full_data[1]['data']['children']
                save_markdown(post_data, comments_data)
                mark_as_processed(post_id, post_data['title'])
                posts_fetched += 1
            else:
                print(f"Failed to fetch comments for post {post_id}")

        after = data['data'].get('after')
        if not after:
            break

    print(f"Done! Processed {posts_fetched} new posts.")

if __name__ == "__main__":
    main()
