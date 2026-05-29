import json
import os
import zipfile
import math
import re
import datetime
import difflib

# File paths
INPUT_DIR = 'shinjikai_data'      # Target folder containing the chunked JSONL files
OUTPUT_ZIP = 'Shinjikai_Dictionary.zip'
IMAGES_DIR = 'yomitan_images' 
TERMS_PER_BANK = 10000

# --- Auto-Update Configuration ---
GITHUB_REPO = "kaihouguide/Shinjikai"
INDEX_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/index.json"
DOWNLOAD_URL = f"https://github.com/{GITHUB_REPO}/releases/latest/download/{OUTPUT_ZIP}"

# Regex for Japanese characters to isolate LTR text from RTL Arabic text.
JP_CHARS = r'\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf\u3400-\u4dbf\u3000-\u303f\uff00-\uffefa-zA-Z0-9'
# Removed , : ; . / from JP_GLUE so Arabic punctuation isn't swallowed into LTR isolates
JP_GLUE = r'[\s\(\)（）\-]'
JP_PATTERN = re.compile(rf'([{JP_CHARS}]+(?:{JP_GLUE}+[{JP_CHARS}]+)*)')


def generate_true_furigana(text, reading):
    """
    Intelligently generates precise Furigana only for Kanji.
    Matches kana between the text and reading to leave them out of the <ruby> tags.
    """
    if not text:
        return text
    if not reading or text == reading:
        return text
        
    sm = difflib.SequenceMatcher(None, text, reading)
    content = []
    
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            content.append(text[i1:i2])
        else:
            kanji = text[i1:i2]
            kana = reading[j1:j2]
            if kanji:
                if kana:
                    content.append({
                        "tag": "ruby",
                        "content": [
                            kanji,
                            {"tag": "rt", "content": kana}
                        ]
                    })
                else:
                    content.append(kanji)
            elif kana:
                content.append(kana)
                
    # Group contiguous string fragments back together
    merged_content = []
    for item in content:
        if isinstance(item, str) and merged_content and isinstance(merged_content[-1], str):
            merged_content[-1] += item
        else:
            merged_content.append(item)
            
    if len(merged_content) == 1 and isinstance(merged_content[0], str):
        return merged_content[0]
        
    return merged_content


def parse_arabic(text):
    """Parses Shinjikai special characters, extracts links, and applies Unicode Bidi isolates."""
    text = text.replace("$", " : ")
    if text.endswith("؛"):
        text = text[:-1]
    text = text.replace("(|", "(").replace("|)", ")")
    
    parts = []
    tokens = re.split(r'(\{.*?\}|\n)', text)
    for token in tokens:
        if not token:
            continue
            
        if token == '\n':
            parts.append({"tag": "br"})
        elif token.startswith("{") and token.endswith("}"):
            inner = token[1:-1]
            
            # Hide purely structural {anchor:...} elements used by Shinjikai databases
            if inner.lower().startswith("anchor:") or inner.lower() == "anchor":
                continue

            if ":" in inner:
                # The database format is {id:word}, not {word:id}.
                # We identify the ID (usually a hex string or numbers) to safely extract the readable word.
                p0, p1 = inner.split(":", 1)
                
                # If the first part is purely alphanumeric/hyphens (like a UUID), the word is the second part
                if re.fullmatch(r'[a-fA-F0-9\-]+', p0.strip()):
                    word = p1.strip()
                else:
                    word = p0.strip()
                    
                if word:
                    parts.append({
                        "tag": "a", 
                        "href": f"?query={word}", 
                        "content": [
                            {"tag": "span", "content": "\u2066"},
                            word,
                            {"tag": "span", "content": "\u2069"}
                        ]
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
                    "content": f"\u2066{inner}\u2069"
                })
        else:
            if token.strip() or token == " ":
                sub_tokens = JP_PATTERN.split(token)
                for sub in sub_tokens:
                    if not sub:
                        continue
                    if JP_PATTERN.fullmatch(sub):
                        parts.append({
                            "tag": "span", 
                            "lang": "ja", 
                            "content": f"\u2066{sub}\u2069"
                        })
                    else:
                        parts.append({"tag": "span", "content": sub})
    return parts


def format_sentence_item(j_text, j_kana, a_text):
    """Formats sentence items with proper ruby tags precisely over Kanji elements."""
    sent_item_content = []
    
    furigana_content = generate_true_furigana(j_text, j_kana)
    
    sent_item_content.append({
        "tag": "div",
        "lang": "ja",
        "style": {"fontSize": "1.05em", "marginBottom": "4px"},
        "content": furigana_content
    })
    
    # Arabic Translation
    sent_item_content.append({
        "tag": "div",
        "lang": "ar",
        "style": {
            "fontSize": "0.95em", 
            "marginTop": "2px",
            "textAlign": "right",
            "direction": "rtl"
        },
        "content": f"\u2067{a_text}\u2069"
    })
    
    return {
        "tag": "li",
        "style": {
            "paddingTop": "6px",
            "paddingBottom": "6px",
            "textAlign": "right",
            "direction": "rtl",
            "marginBottom": "4px"
        },
        "content": sent_item_content
    }


def create_dictionary():
    terms = []
    missing_images = 0
    
    if not os.path.exists(INPUT_DIR):
        print(f"Error: Directory '{INPUT_DIR}' not found!")
        return

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
                writings = word.get("Writings", [])
                meanings = word.get("Meanings", [])
                
                all_sentences = {}
                for sid, sdata in data.get("SentenceMap", {}).items():
                    all_sentences[str(sid)] = sdata
                for sdata in data.get("SentenceSearch", []):
                    all_sentences[str(sdata.get("Id"))] = sdata
                
                rendered_sentence_ids = set()
                structured_content_body = []
                meanings_list_content = []
                
                for i, meaning in enumerate(meanings, 1):
                    content_blocks = []
                    
                    ar_text = meaning.get("Arabic", "")
                    if ar_text:
                        if ar_text.startswith("$") and "أصل الكلمة" in ar_text:
                            origin_text = ar_text.replace("$أصل الكلمة:\n", "").replace("$أصل الكلمة:", "").strip()
                            
                            content_blocks.append({
                                "tag": "details",
                                "lang": "ar",
                                "style": {
                                    "marginBottom": "10px",
                                    "direction": "rtl",
                                    "backgroundColor": "rgba(128, 128, 128, 0.1)", 
                                    "paddingTop": "8px",
                                    "paddingBottom": "8px",
                                    "paddingLeft": "12px",
                                    "paddingRight": "12px"
                                },
                                "content": [
                                    {
                                        "tag": "summary",
                                        "lang": "ar",
                                        "style": {
                                            "fontWeight": "bold", 
                                            "textAlign": "right",
                                            "direction": "rtl",
                                            "fontSize": "1.05em"
                                        },
                                        "content": f"\u2067({i}) أصل الكلمة\u2069"
                                    },
                                    {
                                        "tag": "div",
                                        "lang": "ar", 
                                        "style": {
                                            "textAlign": "right",
                                            "direction": "rtl",
                                            "marginTop": "8px",
                                            "fontSize": "0.95em"
                                        },
                                        "content": ["\u2067"] + parse_arabic(origin_text) + ["\u2069"]
                                    }
                                ]
                            })
                        else:
                            ar_content = ["\u2067"]
                            ar_content.append({
                                "tag": "span", 
                                "style": {"fontWeight": "bold", "marginLeft": "4px"}, 
                                "content": f"({i})"
                            })
                            ar_content.extend(parse_arabic(ar_text))
                            
                            related_blocks = []
                            if meaning.get("Related"):
                                for rel in meaning["Related"]:
                                    for item in rel.get("Items", []):
                                        rel_text = item.get("Text", "")
                                        rel_kana = item.get("Kana", "")
                                        if rel_text:
                                            furigana_node = generate_true_furigana(rel_text, rel_kana)
                                            link_content = [{"tag": "span", "content": "\u2066"}]
                                            
                                            if isinstance(furigana_node, list):
                                                link_content.extend(furigana_node)
                                            else:
                                                link_content.append(furigana_node)
                                                
                                            link_content.append({"tag": "span", "content": "\u2069"})
                                            
                                            related_blocks.append({"tag": "span", "content": " ("})
                                            related_blocks.append({
                                                "tag": "a",
                                                "href": f"?query={rel_text}",
                                                "content": link_content
                                            })
                                            related_blocks.append({"tag": "span", "content": ")"}) 
                            
                            if related_blocks:
                                ar_content.extend(related_blocks)
                            
                            ar_content.append("\u2069")
                                
                            content_blocks.append({
                                "tag": "div",
                                "lang": "ar",
                                "data": {"shinjikai": "arabic"},
                                "style": {
                                    "textAlign": "right",
                                    "direction": "rtl",
                                    "fontSize": "1.15em",
                                    "marginBottom": "6px"
                                },
                                "content": ar_content
                            })
                        
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
                                "direction": "rtl",
                                "backgroundColor": "rgba(128, 128, 128, 0.1)", 
                                "paddingTop": "6px",
                                "paddingBottom": "6px",
                                "paddingLeft": "10px",
                                "paddingRight": "10px"
                            },
                            "content": [
                                {
                                    "tag": "summary",
                                    "style": {
                                        "textAlign": "right",
                                        "direction": "rtl",
                                        "fontSize": "0.95em",
                                        "fontWeight": "bold"
                                    },
                                    "content": "\u2067【日】 التعريف الياباني\u2069"
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
                                    "content": f"\u2066{jp_content}\u2069"
                                }
                            ]
                        })
                        
                    note_text = meaning.get("Note", "")
                    if note_text:
                        note_content = ["\u2067", {"tag": "span", "style": {"fontWeight": "bold"}, "content": "ملاحظة: "}]
                        note_content.extend(parse_arabic(note_text))
                        note_content.append("\u2069")
                        
                        content_blocks.append({
                            "tag": "div",
                            "lang": "ar",
                            "data": {"shinjikai": "note"},
                            "style": {
                                "fontSize": "0.9em", 
                                "textAlign": "right",
                                "direction": "rtl",
                                "marginBottom": "8px",
                                "backgroundColor": "rgba(128, 128, 128, 0.05)",
                                "paddingTop": "4px",
                                "paddingBottom": "4px",
                                "paddingLeft": "8px",
                                "paddingRight": "8px"
                            },
                            "content": note_content 
                        })
                        
                    pictures = meaning.get("Pictures", [])
                    for pic in pictures:
                        filename = pic.get("Filename")
                        if filename:
                            local_image_path = os.path.join(IMAGES_DIR, filename)
                            if os.path.exists(local_image_path):
                                content_blocks.append({
                                    "tag": "div",
                                    "style": {"textAlign": "right", "direction": "rtl", "marginTop": "10px", "marginBottom": "10px"},
                                    "content": [{
                                        "tag": "img",
                                        "path": f"yomitan_images/{filename}" 
                                    }]
                                })
                            else:
                                missing_images += 1
                            
                    sentence_ids = meaning.get("SentenceIds", [])
                    if sentence_ids and all_sentences:
                        sent_list = []
                        for sid in sentence_ids:
                            s_data = all_sentences.get(str(sid))
                            if s_data:
                                rendered_sentence_ids.add(str(sid))
                                j_text = s_data.get("Text", "")
                                j_kana = s_data.get("Kana", "")
                                a_text = s_data.get("Arabic", "")
                                
                                sent_list.append(format_sentence_item(j_text, j_kana, a_text))
                                
                        if sent_list:
                            content_blocks.append({
                                "tag": "details",
                                "lang": "ar", 
                                "style": {
                                    "marginBottom": "8px",
                                    "direction": "rtl",
                                    "backgroundColor": "rgba(128, 128, 128, 0.1)", 
                                    "paddingTop": "6px",
                                    "paddingBottom": "6px",
                                    "paddingLeft": "10px",
                                    "paddingRight": "10px"
                                },
                                "content": [
                                    {
                                        "tag": "summary",
                                        "style": {
                                            "textAlign": "right",
                                            "direction": "rtl",
                                            "fontSize": "0.95em",
                                            "fontWeight": "bold"
                                        },
                                        "content": "\u2067【例】 الأمثلة\u2069"
                                    },
                                    {
                                        "tag": "ul",
                                        "style": {
                                            "listStyleType": "none", 
                                            "paddingTop": "0", "paddingBottom": "0", "paddingLeft": "0", "paddingRight": "0", 
                                            "marginTop": "8px", "marginBottom": "0"
                                        },
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
                
                leftover_sids = [sid for sid in all_sentences.keys() if sid not in rendered_sentence_ids]
                if leftover_sids:
                    leftover_sent_list = []
                    for sid in leftover_sids:
                        s_data = all_sentences[sid]
                        j_text = s_data.get("Text", "")
                        j_kana = s_data.get("Kana", "")
                        a_text = s_data.get("Arabic", "")
                        leftover_sent_list.append(format_sentence_item(j_text, j_kana, a_text))

                    meanings_list_content.append({
                        "tag": "li",
                        "style": {
                            "marginBottom": "12px", 
                            "paddingBottom": "12px",
                            "listStyleType": "none"
                        },
                        "content": [{
                            "tag": "details",
                            "lang": "ar", 
                            "style": {
                                "marginBottom": "8px",
                                "direction": "rtl",
                                "backgroundColor": "rgba(128, 128, 128, 0.1)", 
                                "paddingTop": "6px",
                                "paddingBottom": "6px",
                                "paddingLeft": "10px",
                                "paddingRight": "10px"
                            },
                            "content": [
                                {
                                    "tag": "summary",
                                    "style": {
                                        "textAlign": "right",
                                        "direction": "rtl",
                                        "fontSize": "0.95em",
                                        "fontWeight": "bold"
                                    },
                                    "content": "\u2067【例】 أمثلة إضافية\u2069"
                                },
                                {
                                    "tag": "ul",
                                    "style": {
                                        "listStyleType": "none", 
                                        "paddingTop": "0", "paddingBottom": "0", "paddingLeft": "0", "paddingRight": "0", 
                                        "marginTop": "8px", "marginBottom": "0"
                                    },
                                    "content": leftover_sent_list
                                }
                            ]
                        }]
                    })
                
                if meanings_list_content:
                    structured_content_body.append({
                        "tag": "ul",
                        "style": {"listStyleType": "none", "paddingTop": "0", "paddingBottom": "0", "paddingLeft": "0", "paddingRight": "0", "marginTop": "0", "marginBottom": "0"},
                        "content": meanings_list_content
                    })
                
                dict_entries = []
                if structured_content_body:
                    dict_entries.append({
                        "type": "structured-content",
                        "content": structured_content_body
                    })
                else:
                    dict_entries = ["(No definition provided)"]

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
    
    print(f"Packaging into {OUTPUT_ZIP}...")
    
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
