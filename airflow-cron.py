"""
Example Airflow DAG for scraping Instagram data, calling GPT, and storing
results in Astra DB using a single PythonOperator.
"""

import os
import json
import random
import datetime
from datetime import datetime as dt

# Airflow imports
from airflow import DAG
from airflow.operators.python_operator import PythonOperator

# Cassandra/Astra
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider

# Instaloader
import instaloader

# Default DAG arguments
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': dt(2023, 12, 1),   # Adjust as needed
    'retries': 1,
}

##############################################################################
# GPT HELPER FUNCTIONS (place your actual GPT logic here)
##############################################################################
def get_genre_from_caption(caption: str) -> str:
    """
    Placeholder function calling GPT to get a one-word genre.
    Replace with your actual GPT call logic.
    """
    possible_genres = ["Fashion", "Sports", "Food", "Nature", "Entertainment", "Technology"]
    return random.choice(possible_genres)  # Mock for demonstration

def get_sentiment_from_caption(caption: str) -> float:
    """
    Placeholder function calling GPT to get a sentiment score between 0 and 5.
    Replace with your actual GPT call logic.
    """
    return round(random.uniform(0, 5), 2)  # Mock for demonstration

##############################################################################
# ASTRA DB / CASSANDRA HELPERS
##############################################################################
def get_astra_session():
    """
    Connect to Astra DB (or Cassandra) and return a session.
    Replace with your actual secure bundle path, client ID, secret, etc.
    """
    cloud_config = {
        'secure_connect_bundle': '/path/to/secure-connect-database_name.zip'
    }
    auth_provider = PlainTextAuthProvider('ASTRA_CLIENT_ID', 'ASTRA_CLIENT_SECRET')
    cluster = Cluster(cloud=cloud_config, auth_provider=auth_provider)
    
    # Replace 'your_keyspace' with your actual Astra keyspace name
    session = cluster.connect('your_keyspace')
    return session

def get_last_fetched_date(session, handle: str) -> datetime.datetime:
    """
    Query your table to get the last fetched date for a specific handle.
    Suppose your table has a column 'last_fetched' (type TIMESTAMP).
    Replace with your actual logic.
    """
    query = "SELECT last_fetched FROM your_table WHERE handle = %s"
    row = session.execute(query, [handle]).one()
    if row and row.last_fetched:
        return row.last_fetched
    else:
        return datetime.datetime(1970, 1, 1)

def set_last_fetched_date(session, handle: str, last_date: datetime.datetime):
    """
    Update the 'last_fetched' column for this handle.
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
    Then, for each post, call GPT for genre/sentiment, and insert into Astra DB.
    """
    loader = instaloader.Instaloader()
    # If needed: loader.login("YOUR_IG_USERNAME", "YOUR_IG_PASSWORD")

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

        # GPT-based placeholders
        genre = get_genre_from_caption(caption)
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

        insert_post_record(session, handle, post_data)
        if post.date > latest_post_datetime:
            latest_post_datetime = post.date

    # Update last_fetched in DB if new posts were found
    if latest_post_datetime > last_fetched:
        set_last_fetched_date(session, handle, latest_post_datetime)

    print(f"Fetched {post_count} new posts from @{handle}.")

def run_scraper(**context):
    """
    This function will be called by the PythonOperator inside the Airflow DAG.
    It:
      1) Reads handles from JSON
      2) Connects to Astra DB
      3) For each handle, checks the last fetched date
      4) Scrapes new posts, calling GPT placeholders
      5) Inserts them into Astra DB
      6) Updates the last fetched date
    """
    # If you placed `handles.json` in your DAG folder, build a path like:
    #   dag_dir = os.path.dirname(os.path.realpath(__file__))
    #   handles_json_path = os.path.join(dag_dir, "handles.json")
    # Otherwise, specify the absolute path to your JSON:
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


# Define the DAG
with DAG(
    dag_id='instagram_scraper_dag',
    default_args=default_args,
    description='Scrapes Instagram posts, calls GPT placeholders, saves to Astra DB',
    schedule_interval='0 0 * * *',  # Runs daily at midnight
    catchup=False,
    tags=['instagram', 'gpt', 'astra']
) as dag:

    # Single PythonOperator that runs the entire scraping + GPT flow
    scrape_task = PythonOperator(
        task_id='scrape_instagram_posts',
        python_callable=run_scraper,
        provide_context=True
    )

    scrape_task
