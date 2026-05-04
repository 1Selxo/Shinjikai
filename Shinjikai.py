import requests
import json
import os
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

# --- Configuration ---
MAX_WORKERS = 20  
CLIENT_ID = "rlViYQFTKkM"
API_URL = "https://shinjikai.app/rpc/LoadWordDetails"
IMAGE_BASE_URL = "https://shinjikai.app/static/word_pictures/"
DATA_DIR = "shinjikai_data"
IMAGE_DIR = "yomitan_images"
CHUNK_SIZE = 10000 # Breaks the DB into chunks to avoid GitHub's 100MB file limit

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)

session = requests.Session()
retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS, max_retries=retries)
session.mount('https://', adapter)

def get_finished_ids():
    finished = set()
    
    # 1. Read your old original file if it exists
    if os.path.exists("raw_shinjikai_data.jsonl"):
        with open("raw_shinjikai_data.jsonl", "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if "Word" in entry and "Id" in entry["Word"]:
                        finished.add(entry["Word"]["Id"])
                except: continue
                
    # 2. Read the new chunked directory files
    for filepath in glob.glob(os.path.join(DATA_DIR, "*.jsonl")):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if "Word" in entry and "Id" in entry["Word"]:
                        finished.add(entry["Word"]["Id"])
                except: continue
                
    return finished

def download_image(filename):
    if not filename: return
    path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(path): return
    try:
        img_res = session.get(f"{IMAGE_BASE_URL}{filename}", timeout=10)
        if img_res.status_code == 200:
            with open(path, 'wb') as f:
                f.write(img_res.content)
    except: pass

def fetch_worker(word_id):
    headers = {"Content-Type": "text/plain;charset=UTF-8", "X-Client-Id": CLIENT_ID}
    payload = {"Id": word_id}
    
    try:
        response = session.post(API_URL, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and "Word" in data:
                # --- Image Extraction ---
                word_info = data["Word"]
                if "Meanings" in word_info:
                    for m in word_info["Meanings"]:
                        if "Pictures" in m:
                            for pic in m["Pictures"]:
                                fname = pic.get("Filename")
                                if fname:
                                    download_image(fname)
                return word_id, data
        return word_id, None
    except Exception:
        return word_id, None

def main():
    finished_ids = get_finished_ids()
    
    # Instead of starting at 1, start EXACTLY at the highest ID we've ever found + 1.
    start_id = max(finished_ids) + 1 if finished_ids else 1
    
    # Set a massive ceiling (250,000) so it can rip the whole DB on the first run.
    # It will safely abort as soon as it hits the 300 empty streak.
    todo_ids = list(range(start_id, start_id + 250000))
    
    print(f"DB currently holds {len(finished_ids)} finished entries.")
    print(f"Fast-forwarding to ID {start_id}...")
    
    pbar = tqdm(total=len(todo_ids), desc="Fetching Database", unit="req")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {executor.submit(fetch_worker, i): i for i in todo_ids}
        
        empty_streak = 0
        for future in as_completed(future_to_id):
            word_id, raw_data = future.result()
            
            if raw_data:
                empty_streak = 0
                
                # Write to chunked files instead of 1 massive file
                chunk_file = os.path.join(DATA_DIR, f"data_{word_id // CHUNK_SIZE}.jsonl")
                with open(chunk_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(raw_data, ensure_ascii=False) + "\n")
                
                word_label = raw_data["Word"].get("Kana", str(word_id))
                pbar.set_postfix({"last": word_label})
            else:
                empty_streak += 1
            
            pbar.update(1)
            
            # If we hit a streak of 300 missing words, we know we've reached the absolute end of the DB
            if empty_streak > 300:
                print(f"\n[!] Reached end of database. Threshold reached at ID {word_id}.")
                # Force the progress bar to finish gracefully
                pbar.n = len(todo_ids) 
                pbar.refresh()
                break

    pbar.close()

if __name__ == "__main__":
    main()
