import sqlite3
import json
import os
import glob
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).parent.parent
SUBMISSIONS_DIR = REPO_ROOT / "submissions"
DB_PATH = REPO_ROOT / "index.db"

def init_db():
    """Ensures the database exists and has the correct schema."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS repositories
                 (name text PRIMARY KEY, url text, description text)''')
    conn.commit()
    return conn

def process_submissions():
    if not SUBMISSIONS_DIR.exists():
        print("No submissions directory found.")
        return

    json_files = list(SUBMISSIONS_DIR.glob("*.json"))
    if not json_files:
        print("No new submissions to process.")
        return

    conn = init_db()
    c = conn.cursor()
    
    processed_count = 0

    print(f"Found {len(json_files)} submissions.")

    for file_path in json_files:
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)

            # Validation
            name = data.get("name")
            url = data.get("url")
            desc = data.get("description", "No description")

            if not name or not url:
                print(f"Skipping {file_path.name}: Missing name or url.")
                continue

            # Update DB
            print(f"Processing '{name}'...")
            c.execute("INSERT OR REPLACE INTO repositories VALUES (?, ?, ?)", 
                      (name, url, desc))
            
            # Remove the JSON file after successful ingestion
            os.remove(file_path)
            processed_count += 1
            
        except json.JSONDecodeError:
            print(f"Error decoding {file_path.name}. Skipping.")
        except Exception as e:
            print(f"Unexpected error on {file_path.name}: {e}")

    conn.commit()
    conn.close()
    print(f"Successfully processed {processed_count} submissions.")

if __name__ == "__main__":
    process_submissions()