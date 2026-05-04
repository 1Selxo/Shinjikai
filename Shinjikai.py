import requests
import json
import os
import glob
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

# --- Configuration ---
CHUNK_SIZE = 10000
MAX_WORKERS = 20  
CLIENT_ID = "rlViYQFTKkM"
API_URL = "https://shinjikai.app/rpc/LoadWordDetails"
IMAGE_BASE_URL = "https://shinjikai.app/static/word_pictures/"
DATA_DIR = "data"
IMAGE_DIR = "yomitan_images"

# Create necessary folders to circumvent GitHub file/directory size limits
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)

session = requests.Session()
retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS, max_retries=retries)
session.mount('https://', adapter)

def get_finished_ids():
    """Reads all saved files (legacy + new chunks) to log what we already have."""
    finished = set()
    
    # 1. Read from legacy single file if it exists
    if os.path.exists("raw_shinjikai_data.jsonl"):
        with open("raw_shinjikai_data.jsonl", "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if "Word" in entry and "Id" in entry["Word"]:
                        finished.add(entry["Word"]["Id"])
                except: continue

    # 2. Read from new chunked files
    for filepath in glob.glob(os.path.join(DATA_DIR, "*.jsonl")):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if "Word" in entry and "Id" in entry["Word"]:
                        finished.add(entry["Word"]["Id"])
                except: continue
                
    return finished

def get_chunk_filename(word_id):
    """Calculates the corresponding split file (e.g., data/raw_shinjikai_data_10000_19999.jsonl)"""
    chunk_index = word_id // CHUNK_SIZE
    start = chunk_index * CHUNK_SIZE
    end = start + CHUNK_SIZE - 1
    return os.path.join(DATA_DIR, f"raw_shinjikai_data_{start}_{end}.jsonl")

def download_image(filename, word_id):
    if not filename: return
    
    # Bundle images into subdirectories to stop Git from choking on huge folders
    chunk_folder = os.path.join(IMAGE_DIR, f"{(word_id // CHUNK_SIZE) * CHUNK_SIZE}")
    os.makedirs(chunk_folder, exist_ok=True)
    
    path = os.path.join(chunk_folder, filename)
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
                word_info = data["Word"]
                if "Meanings" in word_info:
                    for m in word_info["Meanings"]:
                        if "Pictures" in m:
                            for pic in m["Pictures"]:
                                fname = pic.get("Filename")
                                if fname:
                                    download_image(fname, word_id)
                return word_id, data
        return word_id, None
    except Exception:
        return word_id, None

def main():
    finished_ids = get_finished_ids()
    highest_successful_id = max(finished_ids) if finished_ids else 0
    
    # We always start from ID 1 so missing IDs (500 errors) organically self-heal/retry daily, 
    # but actual network requests are skipped for IDs already inside 'finished_ids'.
    current_id = 1
    batch_size = 500
    stop_threshold = 300
    highest_found_this_run = highest_successful_id

    print(f"DB currently holds {len(finished_ids)} finished entries. Highest ID: {highest_successful_id}")
    pbar = tqdm(desc="Fetching Daily Data", unit="req")
    
    while True:
        # Create a batch of numbers, EXCLUDING ones we already downloaded
        batch_ids =[i for i in range(current_id, current_id + batch_size) if i not in finished_ids]
        
        # Only run network requests if there are IDs in this batch missing from our database
        if batch_ids:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_id = {executor.submit(fetch_worker, i): i for i in batch_ids}
                
                for future in as_completed(future_to_id):
                    word_id, raw_data = future.result()
                    
                    if raw_data:
                        highest_found_this_run = max(highest_found_this_run, word_id)
                        
                        chunk_filename = get_chunk_filename(word_id)
                        with open(chunk_filename, "a", encoding="utf-8") as f:
                            f.write(json.dumps(raw_data, ensure_ascii=False) + "\n")
                        
                        finished_ids.add(word_id) # Log memory so we don't fetch it twice
                        
                        word_label = raw_data["Word"].get("Kana", str(word_id))
                        pbar.set_postfix({"latest": word_label})
                    
                    pbar.update(1)

        highest_checked = current_id + batch_size - 1
        
        # Streak breaking logic: 
        # Once we surpass the historical peak, stop if we see an empty streak of 300 IDs.
        if highest_checked > highest_found_this_run:
            if highest_checked - highest_found_this_run > stop_threshold:
                print(f"\n[!] Threshold reached. No new entries found within {stop_threshold} IDs after {highest_found_this_run}.")
                break
                
        current_id += batch_size

    pbar.close()
    print(f"Finished. Highest successful ID: {highest_found_this_run}")

if __name__ == "__main__":
    main()
