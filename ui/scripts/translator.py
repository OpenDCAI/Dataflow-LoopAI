import getpass
import json
import os
import re
import subprocess
from typing import Dict, Iterable, List

import requests

DATA_PATH = "./src/js/i18n.js"
SOURCE_PREFIX = "./src"
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"
LOCAL_PATTERN = r"local\([\"'`]([^\"'`]+)[\"'`]\)"
CHUNK_SIZE = 100


def load_i18n_map(raw_text: str) -> Dict[str, dict]:
    text = raw_text.replace("export default", "", 1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        node_script = r"""
const fs = require('fs');
const vm = require('vm');
const path = process.argv[1];
const src = fs.readFileSync(path, 'utf8').replace('export default', '').trim();
const obj = vm.runInNewContext('(' + src + ')', {});
process.stdout.write(JSON.stringify(obj));
"""
        proc = subprocess.run(
            ["node", "-e", node_script, DATA_PATH],
            check=True,
            capture_output=True,
            text=False,
        )
        return json.loads(proc.stdout.decode("utf-8"))


def ensure_entry_shape(key: str, value) -> dict:
    if isinstance(value, dict):
        return {
            "en": value.get("en", key),
            "zh": value.get("zh", ""),
        }
    if isinstance(value, list):
        return {
            "en": value[0] if len(value) > 0 else key,
            "zh": value[1] if len(value) > 1 else "",
        }
    if isinstance(value, str):
        return {"en": key, "zh": value}
    return {"en": key, "zh": ""}


def iter_source_files(prefix: str) -> Iterable[str]:
    for root, _, files in os.walk(prefix):
        for file in files:
            file_path = os.path.join(root, file)
            if file_path.endswith((".js", ".vue", ".html")):
                yield file_path


def read_text(file_path: str) -> str:
    try:
        with open(file_path, encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(file_path, encoding="gbk", errors="ignore") as f:
            return f.read()


def collect_missing_keys(i18n_map: Dict[str, dict]) -> List[str]:
    keys = set()
    for file_path in iter_source_files(SOURCE_PREFIX):
        content = read_text(file_path)
        for key in re.findall(LOCAL_PATTERN, content):
            if key not in i18n_map:
                keys.add(key)
    return sorted(keys)


def chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def extract_json_object(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def translate_chunk(keys: List[str], api_key: str) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": max(256, len(keys) * 24),
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": 'Return json only. Translate each English UI string to Simplified Chinese. Output format: {"English":"Chinese"}. Keep keys unchanged. Use "" if unsure.',
            },
            {
                "role": "user",
                "content": json.dumps(keys, ensure_ascii=False, separators=(",", ":")),
            },
        ],
    }
    response = requests.post(
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)
    if not isinstance(parsed, dict):
        raise ValueError("response is not a json object")
    return {str(key): str(value) if value is not None else "" for key, value in parsed.items()}


def request_api_key() -> str:
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    try:
        return getpass.getpass("DeepSeek API Key: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        ori_i18n = f.read()

    raw_map = load_i18n_map(ori_i18n)
    result = {
        key: ensure_entry_shape(key, value)
        for key, value in raw_map.items()
    }

    missing_keys = collect_missing_keys(result)
    if not missing_keys:
        print("No missing i18n keys.")
        return

    api_key = request_api_key()

    for keys in chunked(missing_keys, CHUNK_SIZE):
        translations = {}
        if api_key:
            try:
                translations = translate_chunk(keys, api_key)
            except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as err:
                print(f"[warn] translate failed, fallback to english only: {err}")

        for key in keys:
            zh = translations.get(key, "").strip()
            result[key] = {
                "en": key,
                "zh": zh,
            }
            print(key, zh or "[en-only]")

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json_content = json.dumps(result, indent=4, ensure_ascii=False)
        f.write("export default " + json_content)


if __name__ == "__main__":
    main()
