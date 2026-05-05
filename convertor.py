import json
import os
import zipfile
import math
import re
import datetime

# File paths
INPUT_DIR = 'shinjikai_data'      # Updated to target the folder
OUTPUT_ZIP = 'Shinjikai_Dictionary.zip'
IMAGES_DIR = 'yomitan_images' 
TERMS_PER_BANK = 10000

# --- Auto-Update Configuration ---
# Matches the repository shown in your screenshots
GITHUB_REPO = "kaihouguide/Shinjikai"
INDEX_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/index.json"
DOWNLOAD_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/{OUTPUT_ZIP}"


def parse_arabic(text):
    """Parses Shinjikai special characters into Yomitan-compliant structured content."""
    text = text.replace("$", " : ")
    if text.endswith("؛"):
        text = text[:-1]
    text = text.replace("(|", "(").replace("|)", ")")
    
    parts =[]
    tokens = re.split(r'(\{.*?\})', text)
    for token in tokens:
        if token.startswith("{") and token.endswith("}"):
            inner = token[1:-1]
            if ":" in inner:
                # Linked word / Redirect (Converted into an interactive Yomitan hyperlink)
                word = inner.split(":", 1)[0]
                parts.append({
                    "tag": "a", 
                    "href": f"?query={word}", 
                    "content": word
                })
            else:
                # Green Pill Badge
                parts.append({
                    "tag": "span",
                    "style": {
                        "backgroundColor": "#4a7c49",
                        "color": "#ffffff",
                        "paddingLeft": "5px",
                        "paddingRight": "5px",
                        "marginLeft": "5px",
                        "marginRight": "5px",
                        "fontSize": "0.85em",
                        "borderRadius": "3px"
                    },
                    "content": inner
                })
        else:
            if token.strip():
                parts.append({"tag": "span", "content": token})
    return parts

def create_dictionary():
    terms =[]
    missing_images = 0
    
    # Check if directory exists
    if not os.path.exists(INPUT_DIR):
        print(f"Error: Directory '{INPUT_DIR}' not found!")
        return

    # Process all .jsonl files in the shinjikai_data directory
    json_files = sorted([f for f in os.listdir(INPUT_DIR) if f.endswith('.jsonl')])
    
    for filename in json_files:
        file_path = os.path.join(INPUT_DIR, filename)
        print(f"Reading data from {file_path}...")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                    
                word = data.get("Word", {})
                if not word:
                    continue
                    
                word_id = word.get("Id", 0)
                kana = word.get("Kana", "")
                writings = word.get("Writings",[])
                meanings = word.get("Meanings",[])
                
                sentence_map = word.get("SentenceMap", {})
                
                # --- 1. BUILD THE DEFINITIONS ---
                structured_content_body =[]
                meanings_list_content =[]
                
                for i, meaning in enumerate(meanings, 1):
                    content_blocks =[]
                    
                    # Arabic Meaning / Word Origin
                    ar_text = meaning.get("Arabic", "")
                    if ar_text:
                        if ar_text.startswith("$") and "أصل الكلمة" in ar_text:
                            origin_text = ar_text.replace("$أصل الكلمة:\n", "").replace("$أصل الكلمة:", "").strip()
                            origin_lines = origin_text.split("\n")
                            
                            # Apply RTL Embedding to origin lines to securely fix Bidi wrapping and direction
                            origin_content_blocks =[]
                            for line in origin_lines:
                                line_content = [{"tag": "span", "content": "\u202B"}] # Right-to-Left Embedding
                                line_content.extend(parse_arabic(line))
                                line_content.append({"tag": "span", "content": "\u202C"}) # Pop Directional Formatting
                                origin_content_blocks.append({
                                    "tag": "div", 
                                    "lang": "ar", 
                                    "style": {
                                        "marginBottom": "4px",
                                        "textAlign": "right"
                                    }, 
                                    "content": line_content
                                })
                            
                            content_blocks.append({
                                "tag": "details",
                                "lang": "ar",
                                "style": {
                                    "marginBottom": "10px",
                                    "backgroundColor": "rgba(128, 128, 128, 0.1)", 
                                    "padding": "8px 12px"
                                },
                                "content":[
                                    {
                                        "tag": "summary",
                                        "lang": "ar",
                                        "style": {
                                            "fontWeight": "bold", 
                                            "textAlign": "right",
                                            "fontSize": "1.05em",
                                            "cursor": "pointer"
                                        },
                                        "content": f"\u202B({i}) أصل الكلمة\u202C"
                                    },
                                    {
                                        "tag": "div",
                                        "lang": "ar", 
                                        "style": {
                                            "textAlign": "right",
                                            "marginTop": "8px",
                                            "fontSize": "0.95em"
                                        },
                                        "content": origin_content_blocks
                                    }
                                ]
                            })
                        else:
                            # Standard Arabic Meaning Construction
                            ar_content =[]
                            ar_content.append({"tag": "span", "content": f"\u202B({i}) "})
                            ar_content.extend(parse_arabic(ar_text))
                            
                            # Add 'Related' arrows inline if they exist (making them clickable too)
                            related_blocks =[]
                            if meaning.get("Related"):
                                for rel in meaning["Related"]:
                                    for item in rel.get("Items",[]):
                                        rel_text = item.get("Text", "")
                                        rel_kana = item.get("Kana", "")
                                        if rel_text:
                                            ruby_content = [rel_text]
                                            if rel_kana and rel_kana != rel_text:
                                                ruby_content.append({"tag": "rt", "content": rel_kana})
                                            
                                            related_blocks.append({"tag": "span", "content": " ("})
                                            related_blocks.append({
                                                "tag": "a",
                                                "href": f"?query={rel_text}",
                                                "content":[
                                                    {
                                                        "tag": "ruby",
                                                        "content": ruby_content
                                                    }
                                                ]
                                            })
                                            related_blocks.append({"tag": "span", "content": " ⬅)"})
                            
                            if related_blocks:
                                ar_content.extend(related_blocks)
                                
                            ar_content.append({"tag": "span", "content": "\u202C"}) # Pop Directional Formatting
                            
                            content_blocks.append({
                                "tag": "div",
                                "lang": "ar",
                                "data": {"shinjikai": "arabic"},
                                "style": {
                                    "fontWeight": "bold", 
                                    "textAlign": "right",
                                    "fontSize": "1.15em",
                                    "marginBottom": "6px"
                                },
                                "content": ar_content
                            })
                        
                    # Japanese Meaning + Source Bracket (Toggleable with Background Bar)
                    jp_text = meaning.get("Japanese", "")
                    source = meaning.get("Source", "")
                    if jp_text or source:
                        jp_content = jp_text
                        if source:
                            jp_content += f" 〔{source}〕"

                        content_blocks.append({
                            "tag": "details",
                            "lang": "ar", 
                            "style": {
                                "marginBottom": "8px",
                                "backgroundColor": "rgba(128, 128, 128, 0.1)", 
                                "padding": "6px 10px"
                            },
                            "content":[
                                {
                                    "tag": "summary",
                                    "style": {
                                        "textAlign": "right",
                                        "fontSize": "0.95em",
                                        "fontWeight": "bold",
                                        "cursor": "pointer"
                                    },
                                    "content": "【日】 التعريف الياباني"
                                },
                                {
                                    "tag": "div",
                                    "lang": "ja",
                                    "data": {"shinjikai": "japanese"},
                                    "style": {
                                        "textAlign": "right", 
                                        "marginTop": "8px",
                                        "fontSize": "0.95em"
                                    },
                                    "content": jp_content
                                }
                            ]
                        })
                        
                    # Notes
                    note_text = meaning.get("Note", "")
                    if note_text:
                        content_blocks.append({
                            "tag": "div",
                            "lang": "ar",
                            "data": {"shinjikai": "note"},
                            "style": {
                                "fontSize": "0.9em", 
                                "textAlign": "right",
                                "marginBottom": "8px",
                                "backgroundColor": "rgba(128, 128, 128, 0.05)",
                                "padding": "4px 8px"
                            },
                            "content": f"\u202Bملاحظة: {note_text}\u202C" 
                        })
                        
                    # Images (Aligned to the Right)
                    pictures = meaning.get("Pictures",[])
                    for pic in pictures:
                        filename = pic.get("Filename")
                        if filename:
                            local_image_path = os.path.join(IMAGES_DIR, filename)
                            if os.path.exists(local_image_path):
                                content_blocks.append({
                                    "tag": "div",
                                    "style": {"textAlign": "right", "marginTop": "10px", "marginBottom": "10px"},
                                    "content":[{
                                        "tag": "img",
                                        "path": f"yomitan_images/{filename}",
                                        "width": 180 
                                    }]
                                })
                            else:
                                missing_images += 1
                            
                    # Example Sentences (Toggleable with Background Bar)
                    sentence_ids = meaning.get("SentenceIds",[])
                    if sentence_ids and sentence_map:
                        sent_list =[]
                        for sid in sentence_ids:
                            s_data = sentence_map.get(str(sid))
                            if s_data:
                                j_text = s_data.get("Text", "")
                                j_kana = s_data.get("Kana", "")
                                a_text = s_data.get("Arabic", "")
                                
                                sent_item_content =[]
                                
                                if j_kana and j_kana != j_text:
                                    sent_item_content.append({
                                        "tag": "div",
                                        "lang": "ja",
                                        "data": {"shinjikai": "sent-kana"},
                                        "style": {"fontSize": "0.75em", "color": "gray"},
                                        "content": j_kana
                                    })
                                
                                sent_item_content.append({
                                    "tag": "div",
                                    "lang": "ja",
                                    "data": {"shinjikai": "sent-jp"},
                                    "style": {"fontSize": "1.05em"},
                                    "content": j_text
                                })
                                
                                sent_item_content.append({
                                    "tag": "div",
                                    "lang": "ar",
                                    "data": {"shinjikai": "sent-ar"},
                                    "style": {"marginTop": "2px", "fontSize": "0.95em"},
                                    "content": f"\u202B{a_text}\u202C" 
                                })
                                
                                sent_list.append({
                                    "tag": "li",
                                    "style": {
                                        "paddingTop": "6px",
                                        "paddingBottom": "6px",
                                        "marginBottom": "4px",
                                        "backgroundColor": "rgba(128, 128, 128, 0.05)", 
                                        "paddingLeft": "8px",
                                        "paddingRight": "8px",
                                        "textAlign": "right"
                                    },
                                    "content": sent_item_content
                                })
                                
                        if sent_list:
                            content_blocks.append({
                                "tag": "details",
                                "lang": "ar", 
                                "style": {
                                    "marginBottom": "8px",
                                    "backgroundColor": "rgba(128, 128, 128, 0.1)", 
                                    "padding": "6px 10px"
                                },
                                "content":[
                                    {
                                        "tag": "summary",
                                        "style": {
                                            "textAlign": "right",
                                            "fontSize": "0.95em",
                                            "fontWeight": "bold",
                                            "cursor": "pointer"
                                        },
                                        "content": "【例】 أمثلة توضيحية"
                                    },
                                    {
                                        "tag": "ul",
                                        "data": {"shinjikai": "sentences"},
                                        "style": {"listStyleType": "none", "padding": "0", "marginTop": "8px", "marginBottom": "0"},
                                        "content": sent_list
                                    }
                                ]
                            })

                    meanings_list_content.append({
                        "tag": "li",
                        "data": {"shinjikai": "meaning-container"},
                        "style": {
                            "marginBottom": "12px", 
                            "paddingBottom": "12px"
                        },
                        "content": content_blocks
                    })
                
                if meanings_list_content:
                    structured_content_body.append({
                        "tag": "ul",
                        "style": {"listStyleType": "none", "padding": "0", "margin": "0"},
                        "content": meanings_list_content
                    })
                
                dict_entries =[]
                if structured_content_body:
                    dict_entries.append({
                        "type": "structured-content",
                        "content": structured_content_body
                    })
                else:
                    dict_entries =["(No definition provided)"]

                # --- 2. CREATE ENTRIES FOR EVERY WRITING ---
                if not writings:
                    terms.append([kana, "", "", "", 0, dict_entries, word_id, ""])
                else:
                    for w in writings:
                        text = w.get("Text", "")
                        if text:
                            term_kana = "" if text == kana else kana
                            terms.append([text, term_kana, "", "", 0, dict_entries, word_id, ""])

    print(f"Processed {len(terms)} term entries.")
    if missing_images > 0:
        print(f"Skipped {missing_images} missing images.")
    
    # --- 3. PACKAGE EVERYTHING INTO A ZIP ---
    print(f"Packaging into {OUTPUT_ZIP}...")
    
    # Generate dynamic revision based on current UTC date for Auto-Update triggering
    current_revision = datetime.datetime.utcnow().strftime("1.8.%Y%m%d")
    
    index_data = {
        "title": "深辞海",
        "format": 3,
        "revision": current_revision,
        "sequenced": True,
        "author": "Selxo",
        "description": "Japanese-Arabic Dictionary imported from Shinjikai.",
        "attribution": "Shinjikai",
        "isUpdatable": True,
        "indexUrl": INDEX_URL,
        "downloadUrl": DOWNLOAD_URL
    }

    # Save index.json to disk so it can be pushed/uploaded alongside the ZIP
    with open("index.json", "w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False, indent=4)
        print("Generated 'index.json' locally for auto-update routing.")

    with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.json", json.dumps(index_data, ensure_ascii=False))
        
        num_banks = math.ceil(len(terms) / TERMS_PER_BANK)
        for i in range(num_banks):
            chunk = terms[i * TERMS_PER_BANK : (i + 1) * TERMS_PER_BANK]
            bank_filename = f"term_bank_{i + 1}.json"
            zf.writestr(bank_filename, json.dumps(chunk, ensure_ascii=False))
            print(f"Created {bank_filename} with {len(chunk)} entries.")
            
        if os.path.isdir(IMAGES_DIR):
            print(f"Adding images from '{IMAGES_DIR}/' folder...")
            for root, _, files in os.walk(IMAGES_DIR):
                for file in files:
                    file_path = os.path.join(root, file)
                    archive_path = f"yomitan_images/{file}"
                    zf.write(file_path, archive_path)
        else:
            print(f"Warning: Image folder '{IMAGES_DIR}' not found.")

    print("Yomitan dictionary creation complete!")

if __name__ == "__main__":
    create_dictionary()
