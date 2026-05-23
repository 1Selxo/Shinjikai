import requests
import json
import os
import glob
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Configuration ---
MAX_WORKERS = 10  # LOWERED: 20 was pulling 128 req/s which triggered an IP ban.
CLIENT_ID = "rlViYQFTKkM"
API_URL = "https://shinjikai.app/rpc/LoadWordDetails"
IMAGE_BASE_URL = "https://shinjikai.app/static/word_pictures/"
DATA_DIR = "shinjikai_data"
IMAGE_DIR = "yomitan_images"
CHUNK_SIZE = 10000 

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)

session = requests.Session()
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504]) 
adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS, max_retries=retries)
session.mount('https://', adapter)

# --- KILL SWITCH ---
abort_flag = threading.Event()

def get_finished_ids():
    finished = set()
    if os.path.exists("raw_shinjikai_data.jsonl"):
        with open("raw_shinjikai_data.jsonl", "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if "Word" in entry and "Id" in entry["Word"]:
                        finished.add(entry["Word"]["Id"])
                except: continue
                
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
    if not filename or abort_flag.is_set(): return
    path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(path): return
    try:
        img_res = session.get(f"{IMAGE_BASE_URL}{filename}", timeout=10)
        if img_res.status_code == 200:
            with open(path, 'wb') as f:
                f.write(img_res.content)
    except: pass

def fetch_worker(word_id):
    # If the kill switch is pulled, instantly skip this task so the script can close
    if abort_flag.is_set():
        return word_id, None

    headers = {"Content-Type": "text/plain;charset=UTF-8", "X-Client-Id": CLIENT_ID}
    payload = {"Id": word_id}
    
    try:
        response = session.post(API_URL, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data and "Word" in data:
                word_info = data["Word"]
                if "Meanings" in word_info:
                    for m in word_info["Meanings"]:
                        if "Pictures" in m:
                            for pic in m["Pictures"]:
                                fname = pic.get("Filename")
                                if fname:
                                    download_image(fname)
                                    
                                    # --- FIX: Clean up leaked picture ID from the meaning text ---
                                    if "Text" in m and m["Text"] and fname in m["Text"]:
                                        # Remove the raw hash ID and strip dangling spaces
                                        m["Text"] = m["Text"].replace(fname, "").strip()
                                        # Remove hanging semicolons/commas (like "؛") that were separating the ID
                                        m["Text"] = m["Text"].strip("؛;،").strip()
                                        
                return word_id, data
        return word_id, None
    except Exception:
        return word_id, None

def main():
    finished_ids = get_finished_ids()
    start_id = max(finished_ids) + 1 if finished_ids else 1
    
    todo_ids = list(range(start_id, start_id + 250000))
    total_todos = len(todo_ids)
    
    print(f"DB currently holds {len(finished_ids)} finished entries.", flush=True)
    print(f"Fast-forwarding to ID {start_id}...", flush=True)
    
    processed = 0
    empty_streak = 0
    start_time = time.time()
    last_word = "N/A"
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {executor.submit(fetch_worker, i): i for i in todo_ids}
        
        for future in as_completed(future_to_id):
            if abort_flag.is_set():
                continue

            word_id, raw_data = future.result()
            processed += 1
            
            if raw_data:
                empty_streak = 0
                chunk_file = os.path.join(DATA_DIR, f"data_{word_id // CHUNK_SIZE}.jsonl")
                with open(chunk_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(raw_data, ensure_ascii=False) + "\n")
                
                last_word = raw_data["Word"].get("Kana", str(word_id))
            else:
                empty_streak += 1
            
            if processed % 250 == 0:
                elapsed_time = time.time() - start_time
                rate = processed / elapsed_time if elapsed_time > 0 else 0
                percent = (processed / total_todos) * 100
                print(f"Processed: {processed}/{total_todos} ({percent:.2f}%) | "
                      f"Speed: {rate:.1f} req/s | "
                      f"Streak: {empty_streak}/300 | "
                      f"Last: {last_word}", flush=True)
            
            # If we get 300 empty responses, either we finished OR we got IP banned
            if empty_streak > 300:
                print(f"\n[!] Reached end of database OR server blocked IP. Threshold reached.", flush=True)
                abort_flag.set() # Pull the kill switch
                break

    print("\n✅ Script completed and exited cleanly!", flush=True)

if __name__ == "__main__":
    main()
