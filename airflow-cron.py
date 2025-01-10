

import os
import json
import datetime
from datetime import datetime as dt

# Airflow imports
from airflow import DAG
from airflow.operators.python_operator import PythonOperator

# Env variables (from .env)
from dotenv import load_dotenv
load_dotenv()  # Load variables from .env into environment

######################################
# Get environment variables
######################################
LANGFLOW_ENDPOINT = os.getenv("LANGFLOW_ENDPOINT")
LANGFLOW_TOKEN = os.getenv("LANGFLOW_TOKEN")

ASTRA_CLIENT_ID = os.getenv("ASTRA_CLIENT_ID")
ASTRA_CLIENT_SECRET = os.getenv("ASTRA_CLIENT_SECRET")
ASTRA_SECURE_BUNDLE = os.getenv("ASTRA_SECURE_BUNDLE")
ASTRA_KEYSPACE = os.getenv("ASTRA_KEYSPACE")

IG_USERNAME = os.getenv("IG_USERNAME", None)  # Optional
IG_PASSWORD = os.getenv("IG_PASSWORD", None)  # Optional

######################################
# External libraries
######################################
import requests
import instaloader

# Cassandra/Astra
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider


##############################################################################
# LANGFLOW CALLS
##############################################################################
def call_langflow_api(message: str) -> dict:
    """
    Makes a POST request to the LangFlow endpoint with the given `message`.
    Returns the JSON response as a Python dictionary.
    """
    payload = {
        "input_value": message,
        "output_type": "chat",
        "input_type": "chat",
        "tweaks": {
            "TextInput-ceQJO": {},
            "TextOutput-y3BFe": {},
            "OpenAIModel-Tg9et": {},
            "JSONCleaner-lA2mt": {}
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LANGFLOW_TOKEN}"
    }

    response = requests.post(LANGFLOW_ENDPOINT, headers=headers, json=payload)
    response.raise_for_status()
    
    # Return the JSON as a Python dict
    return response.json()


def get_genre_from_caption(caption: str) -> str:
    """
    Calls LangFlow with a prompt to determine a single-word genre from the caption.
    """
    prompt = (
        f"Please determine a single-word genre for the following Instagram caption. "
        f"The valid genres are: Fashion, Sports, Food, Nature, Entertainment, Technology.\n\n"
        f"Caption: {caption}"
    )

    data = call_langflow_api(prompt)
    # Adjust parsing based on your flow's output structure
    raw_result = data.get("result", "").strip()
    return raw_result or "Unknown"


def get_sentiment_from_caption(caption: str) -> float:
    """
    Calls LangFlow to retrieve a sentiment score from 0.0 to 5.0 based on the caption.
    """
    prompt = (
        f"Please provide a numeric sentiment score (float) between 0 and 5 "
        f"(0 is very negative, 5 is very positive) for the following caption:\n\n"
        f"Caption: {caption}"
    )

    data = call_langflow_api(prompt)
    
    # Parse the numeric sentiment from the response
    sentiment_str = data.get("result", "").strip()
    try:
        score = float(sentiment_str)
    except ValueError:
        score = 3.0

    # Clamp between 0 and 5
    return max(0.0, min(score, 5.0))


##############################################################################
# ASTRA DB HELPERS
##############################################################################
def get_astra_session():
    """
    Connect to Astra DB (Cassandra) using environment variables.
    """
    cloud_config = {
        'secure_connect_bundle': ASTRA_SECURE_BUNDLE
    }
    auth_provider = PlainTextAuthProvider(
        username=ASTRA_CLIENT_ID,
        password=ASTRA_CLIENT_SECRET
    )
    cluster = Cluster(cloud=cloud_config, auth_provider=auth_provider)
    session = cluster.connect(ASTRA_KEYSPACE)
    return session


def get_last_fetched_date(session, handle: str) -> datetime.datetime:
    """
    Query your table to get the last fetched date for a specific handle.
    Suppose your table has a column 'last_fetched' (type TIMESTAMP).
    Replace with your actual logic and table name.
    """
    query = "SELECT last_fetched FROM your_table WHERE handle = %s"
    row = session.execute(query, [handle]).one()
    if row and getattr(row, 'last_fetched', None):
        return row.last_fetched
    else:
        return datetime.datetime(1970, 1, 1)


def set_last_fetched_date(session, handle: str, last_date: datetime.datetime):
    """
    Update the 'last_fetched' column for this handle in your_table.
    """
    query = "UPDATE your_table SET last_fetched = %s WHERE handle = %s"
    session.execute(query, [last_date, handle])


def insert_post_record(session, handle: str, post_data: dict):
    """
    Insert post data into your Astra DB table.
    Replace with your actual schema and table name.
    """
    query = """
    INSERT INTO your_posts_table (
      handle, 
      post_url, 
      media_type, 
      likes,
      comment_count,
      caption,
      post_date,
      genre,
      sentiment
    ) 
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    session.execute(
        query,
        [
            handle,
            post_data["post_url"],
            post_data["media_type"],
            post_data["likes"],
            post_data["comment_count"],
            post_data["caption"],
            post_data["post_date"],
            post_data["genre"],
            post_data["sentiment"],
        ]
    )


##############################################################################
# SCRAPER LOGIC
##############################################################################
def fetch_instagram_posts(session, handle: str, last_fetched: datetime.datetime, max_posts=100):
    """
    Fetch new posts from `handle` using instaloader, only if post date > last_fetched.
    Then, for each post, call LangFlow for genre and sentiment, and insert into Astra DB.
    """
    loader = instaloader.Instaloader()

    # If you need to log in:
    if IG_USERNAME and IG_PASSWORD:
        loader.login(IG_USERNAME, IG_PASSWORD)

    profile = instaloader.Profile.from_username(loader.context, handle)
    post_count = 0
    latest_post_datetime = last_fetched

    media_type_map = {
        "GraphImage": "static_image",
        "GraphSidecar": "carousel",
        "GraphVideo": "reel"
    }

    for post in profile.get_posts():
        if post_count >= max_posts:
            break

        # Only proceed with posts after last_fetched
        if post.date <= last_fetched:
            continue

        post_count += 1
        post_url = f"https://www.instagram.com/p/{post.shortcode}/"
        caption = post.caption or ""

        # Call LangFlow for genre
        genre = get_genre_from_caption(caption)

        # Call LangFlow for sentiment
        sentiment = get_sentiment_from_caption(caption)

        post_data = {
            "post_url": post_url,
            "image_url": post.url,
            "likes": post.likes,
            "comment_count": post.comments,
            "media_type": media_type_map.get(post.typename, "unknown"),
            "caption": caption,
            "post_date": post.date,
            "genre": genre,
            "sentiment": sentiment
        }

        # Insert into Astra DB
        insert_post_record(session, handle, post_data)

        if post.date > latest_post_datetime:
            latest_post_datetime = post.date

    # Update last_fetched in DB if new posts were found
    if latest_post_datetime > last_fetched:
        set_last_fetched_date(session, handle, latest_post_datetime)

    print(f"Fetched {post_count} new posts from @{handle}.")


def run_scraper(**context):
    """
    Airflow task function:
      1) Reads handles from JSON
      2) Connects to Astra DB
      3) For each handle, checks the last fetched date
      4) Scrapes new posts (via instaloader),
      5) Calls LangFlow for genre & sentiment,
      6) Inserts posts into Astra DB,
      7) Updates the last fetched date
    """
    # Adjust path to your JSON file. Or store it in .env as well.
    handles_json_path = "/path/to/handles.json"
    if not os.path.exists(handles_json_path):
        raise FileNotFoundError(f"handles.json not found at {handles_json_path}")

    with open(handles_json_path, 'r', encoding='utf-8') as f:
        handles_list = json.load(f)

    session = get_astra_session()

    for h in handles_list:
        handle = h.get("handle")
        if not handle:
            continue

        last_fetched_date = get_last_fetched_date(session, handle)
        print(f"[{handle}] Last fetched date: {last_fetched_date}")
        fetch_instagram_posts(session, handle, last_fetched_date, max_posts=100)

    print("Scraping complete.")


##############################################################################
# AIRFLOW DAG DEFINITION
##############################################################################
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': dt(2023, 12, 1),  # Adjust as needed
    'retries': 1,
}

with DAG(
    dag_id='instagram_scraper_langflow_env_dag',
    default_args=default_args,
    description='Scrapes Instagram posts, calls LangFlow for genre & sentiment, saves to Astra DB',
    schedule_interval='0 0 * * *',  # Runs daily at midnight
    catchup=False,
    tags=['instagram', 'langflow', 'astra']
) as dag:

    scrape_task = PythonOperator(
        task_id='scrape_instagram_posts',
        python_callable=run_scraper,
        provide_context=True
    )

    scrape_task
