"""Doubao chat API — pure HTTP, no browser needed.

Usage:
    python tools/doubao_chat_test.py "你好"
    python tools/doubao_chat_test.py "写一首关于春天的诗"
    echo "翻译成英文：你好世界" | python tools/doubao_chat_test.py
"""

import json
import sys
import uuid
import time

import httpx

CREDS_PATH = "credentials/www_doubao_com.json"
BASE_URL = "https://www.doubao.com"
BOT_ID = "7338286299411103781"


def load_cookie() -> str:
    creds = json.load(open(CREDS_PATH))
    cookie = creds.get("full_cookie_string", "")
    if not cookie:
        cookie = "; ".join(f"{k}={v}" for k, v in creds.get("cookies", {}).items())
    if not cookie:
        print("No cookies found. Run api_capture.py --profile doubao first.")
        sys.exit(1)
    return cookie


def chat(message: str, cookie: str) -> str:
    """Send a message and stream the reply to stdout."""
    params = {
        "aid": "497858",
        "device_id": "7619249794900690447",
        "device_platform": "web",
        "language": "zh",
        "pc_version": "3.10.4",
        "pkg_type": "release_version",
        "real_aid": "497858",
        "samantha_web": "1",
        "use-olympus-account": "1",
        "version_code": "20800",
        "web_id": "7619249808787850803",
        "web_tab_id": str(uuid.uuid4()),
    }

    headers = {
        "Content-Type": "application/json",
        "Cookie": cookie,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": f"{BASE_URL}/",
        "Accept": "application/json, text/plain, */*",
        "agw-js-conv": "str, str",
    }

    body = {
        "client_meta": {
            "local_conversation_id": f"local_{int(time.time()*1000)}",
            "conversation_id": "",
            "bot_id": BOT_ID,
            "last_section_id": "",
            "last_message_index": None,
        },
        "messages": [{
            "local_message_id": str(uuid.uuid4()),
            "content_block": [{
                "block_type": 10000,
                "content": {
                    "text_block": {"text": message},
                    "pc_event_block": "",
                },
                "block_id": str(uuid.uuid4()),
                "parent_id": "",
                "meta_info": [],
                "append_fields": [],
            }],
            "message_status": 0,
        }],
        "option": {
            "send_message_scene": "",
            "create_time_ms": int(time.time() * 1000),
            "need_deep_think": 0,
            "need_create_conversation": True,
            "tts_switch": False,
            "is_regen": False,
            "is_replace": False,
            "unique_key": str(uuid.uuid4()),
            "start_seq": 0,
        },
        "ext": {"use_deep_think": "0"},
    }

    full_text = ""
    with httpx.Client(timeout=120, verify=False) as client:
        with client.stream("POST", f"{BASE_URL}/chat/completion",
                           params=params, headers=headers, json=body) as resp:
            if resp.status_code != 200:
                print(f"Error: HTTP {resp.status_code}")
                return ""

            current_event = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue

                data_str = line[5:].strip()
                if not data_str or data_str == "{}":
                    continue

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # STREAM_MSG_NOTIFY — first token(s)
                if current_event == "STREAM_MSG_NOTIFY":
                    for block in data.get("content", {}).get("content_block", []):
                        text = block.get("content", {}).get("text_block", {}).get("text", "")
                        if text:
                            print(text, end="", flush=True)
                            full_text += text

                # STREAM_CHUNK — subsequent tokens (patch_object=1 is text content)
                elif current_event == "STREAM_CHUNK":
                    for patch in data.get("patch_op", []):
                        if patch.get("patch_object") != 1:
                            continue
                        for block in patch.get("patch_value", {}).get("content_block", []):
                            text = block.get("content", {}).get("text_block", {}).get("text", "")
                            if text:
                                print(text, end="", flush=True)
                                full_text += text

    print()  # final newline
    return full_text


def main():
    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    elif not sys.stdin.isatty():
        message = sys.stdin.read().strip()
    else:
        message = input("You: ")

    if not message:
        print("Usage: python tools/doubao_chat_test.py '你的问题'")
        sys.exit(1)

    cookie = load_cookie()
    chat(message, cookie)


if __name__ == "__main__":
    main()
