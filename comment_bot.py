"""
플링캐스트(plingcast.com) 댓글 알림 봇
- config.json 에 적힌 URL들의 새 댓글/답글을 1회 확인하고
- 새로 달린 것만 텔레그램으로 보낸 뒤 바로 종료합니다.
- 주기적인 반복 실행은 GitHub Actions(.github/workflows/monitor.yml)가 5분마다 담당합니다.

실행 방법:
    python comment_bot.py            # 1회 확인 후 종료
    python comment_bot.py --once     # 위와 동일 (예전 명령 호환용)
    python comment_bot.py --test     # 텔레그램 연결 테스트 메시지 전송
    python comment_bot.py --preview  # 텔레그램 전송 없이 현재 댓글 목록만 출력
"""

import argparse
import html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# 기본 설정
# ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"  # 이미 알림 보낸 댓글 ID 저장 파일 (자동 생성)

GRAPHQL_URL = "https://api.plingcast.co.kr/graphql"
KST = timezone(timedelta(hours=9))
HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.plingcast.com",
    "Referer": "https://www.plingcast.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CommentNotifyBot/1.0",
}

# 사이트가 실제로 사용하는 GraphQL 쿼리 (필요한 필드만 요청)
COMMENTS_QUERY = """
query ShowSynopsisComments($synopsisId: Int!, $rootCommentId: Int, $depth: Int, $skip: Int, $take: Int) {
  showSynopsisComments(synopsisId: $synopsisId, rootCommentId: $rootCommentId,
                       depth: $depth, skip: $skip, take: $take) {
    total
    pinnedComments { ...F }
    comments { ...F }
  }
}
fragment F on SynopsisComment {
  id content createdAt replyCount rootCommentId isPinned
  user { name }
  synopsis { title }
}
"""

# Windows 콘솔에서 한글/이모지가 깨지거나 에러 나는 것 방지
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("comment_bot")


# ──────────────────────────────────────────────
# 설정 / 상태 파일
# ──────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("notify_existing_on_first_run", False)
    cfg.setdefault("include_replies", True)
    cfg.setdefault("max_pages", 10)
    cfg.setdefault("ignore_authors", [])
    return cfg


def load_state():
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("state.json 을 읽지 못해 새로 시작합니다.")
    return {}


def save_state(state):
    # 임시 파일에 먼저 쓰고 교체 → 저장 도중 꺼져도 파일이 깨지지 않음
    tmp = STATE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def extract_synopsis_id(url):
    """URL 의 '..._8816/comment/' 부분에서 작품 번호(8816)를 꺼냅니다."""
    m = re.search(r"_(\d+)/comment", url) or re.search(r"/content/[^/]*?(\d+)/?", url)
    if not m:
        raise ValueError(f"URL 에서 작품 번호를 찾을 수 없습니다: {url}")
    return int(m.group(1))


# ──────────────────────────────────────────────
# 플링캐스트 API
# ──────────────────────────────────────────────
def graphql(variables):
    resp = requests.post(
        GRAPHQL_URL,
        headers=HEADERS,
        json={"query": COMMENTS_QUERY, "variables": variables},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL 오류: {data['errors']}")
    return data["data"]["showSynopsisComments"]


def fetch_top_comments(synopsis_id, max_pages, page_size=50):
    """최상위 댓글 전체(고정 댓글 포함)를 가져옵니다."""
    result = {}
    for page in range(max_pages):
        data = graphql({"synopsisId": synopsis_id, "skip": page * page_size, "take": page_size})
        for c in (data.get("pinnedComments") or []) + (data.get("comments") or []):
            result[c["id"]] = c
        if len(data.get("comments") or []) < page_size:
            break
    return list(result.values())


def fetch_replies(synopsis_id, root_id):
    data = graphql({"synopsisId": synopsis_id, "rootCommentId": int(root_id), "depth": 1, "take": 100})
    return data.get("comments") or []


# ──────────────────────────────────────────────
# 텔레그램
# ──────────────────────────────────────────────
def send_telegram(token, chat_id, text, button_url=None, button_text="💬 댓글 보러 가기"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if button_url:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": button_text, "url": button_url}]]}

    for attempt in range(3):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=20)
        if r.status_code == 429:  # 너무 자주 보냄 → 텔레그램이 알려주는 시간만큼 대기
            wait = r.json().get("parameters", {}).get("retry_after", 5)
            log.warning("텔레그램 전송 제한, %s초 대기", wait)
            time.sleep(wait + 1)
            continue
        if not r.ok:
            raise RuntimeError(f"텔레그램 전송 실패 ({r.status_code}): {r.text}")
        return
    raise RuntimeError("텔레그램 전송 재시도 횟수 초과")


def format_time(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, KST).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "-"


def build_message(comment, name, is_reply):
    title = (comment.get("synopsis") or {}).get("title") or name
    author = (comment.get("user") or {}).get("name") or "익명"
    content = comment.get("content") or ""
    if len(content) > 3000:  # 텔레그램 메시지 최대 4096자
        content = content[:3000] + "…(생략)"
    kind = "↪️ 새 답글" if is_reply else "💬 새 댓글"
    return (
        f"<b>{kind}</b> · {html.escape(title)}\n"
        f"👤 {html.escape(author)}  🕒 {format_time(comment.get('createdAt'))}\n"
        f"━━━━━━━━━━━━\n"
        f"{html.escape(content)}"
    )


# ──────────────────────────────────────────────
# 핵심 로직: 한 URL 확인
# ──────────────────────────────────────────────
def check_target(target, cfg, state, token, chat_id, preview=False):
    url = target["url"]
    name = target.get("name") or url
    synopsis_id = extract_synopsis_id(url)
    key = str(synopsis_id)

    first_run = key not in state
    st = state.setdefault(key, {"seen_ids": [], "reply_counts": {}})
    seen = set(st["seen_ids"])
    reply_counts = st["reply_counts"]
    # 알림을 받지 않을 작성자 (앞뒤 공백 무시, 대소문자는 구분)
    ignore_authors = {a.strip() for a in cfg["ignore_authors"] if a and a.strip()}

    top = fetch_top_comments(synopsis_id, cfg["max_pages"])
    new_items = []  # (comment, is_reply)

    for c in top:
        if c["id"] not in seen:
            new_items.append((c, False))
        # 답글 수가 늘었을 때만 답글 목록을 추가로 조회 (요청 수 절약)
        rc = c.get("replyCount") or 0
        if cfg["include_replies"] and rc > reply_counts.get(c["id"], 0):
            for r in fetch_replies(synopsis_id, c["id"]):
                if r["id"] not in seen:
                    new_items.append((r, True))
        reply_counts[c["id"]] = rc

    if preview:
        for c, is_reply in sorted(new_items, key=lambda x: int(x[0]["createdAt"])):
            print("-" * 60)
            print(build_message(c, name, is_reply))
        print(f"\n[{name}] 미확인 댓글/답글 {len(new_items)}개 (preview 모드 - 저장/전송 안 함)")
        return

    if first_run and not cfg["notify_existing_on_first_run"]:
        log.info("[%s] 첫 실행: 기존 댓글/답글 %d개를 '이미 본 것'으로 저장 (알림 X)", name, len(new_items))
    else:
        # 오래된 것부터 순서대로 전송
        for c, is_reply in sorted(new_items, key=lambda x: int(x[0]["createdAt"])):
            author = ((c.get("user") or {}).get("name") or "").strip()
            if author in ignore_authors:
                # 전송만 건너뛰고 아래에서 '이미 본 것'으로 저장 → 다음에도 알림 안 감
                log.info("[%s] 제외 작성자(%s) → 알림 생략: %s", name, author, c["id"])
            else:
                send_telegram(token, chat_id, build_message(c, name, is_reply), button_url=url)
                log.info("[%s] 알림 전송: %s (%s)", name, c["id"], "답글" if is_reply else "댓글")
                time.sleep(1)
            seen.add(c["id"])
            st["seen_ids"] = sorted(seen, key=int)
            save_state(state)  # 하나 보낼 때마다 저장 → 중간에 꺼져도 중복 알림 없음
        if not new_items:
            log.info("[%s] 새 댓글 없음", name)

    seen.update(c["id"] for c, _ in new_items)
    st["seen_ids"] = sorted(seen, key=int)
    save_state(state)


# ──────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="플링캐스트 댓글 텔레그램 알림 봇")
    # 이제 항상 1회만 실행하므로 --once 는 효과가 없지만, 기존 명령이 깨지지 않도록 받아 둠
    parser.add_argument("--once", action="store_true", help="(호환용) 항상 1회만 확인하고 종료")
    parser.add_argument("--test", action="store_true", help="텔레그램 테스트 메시지 전송")
    parser.add_argument("--preview", action="store_true", help="전송 없이 댓글 목록만 출력")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    cfg = load_config()
    targets = [t for t in cfg["targets"] if t.get("enabled", True)]

    if args.preview:
        for t in targets:
            check_target(t, cfg, {}, token, chat_id, preview=True)
        return

    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN 과 TELEGRAM_CHAT_ID 가 없습니다. "
                  "(내 PC: .env 파일 / GitHub Actions: 저장소 Secrets 에 입력)")
        sys.exit(1)

    if args.test:
        send_telegram(token, chat_id, "✅ <b>댓글 알림 봇 연결 성공!</b>\n버튼을 누르면 첫 번째 감시 URL로 이동합니다.",
                      button_url=targets[0]["url"] if targets else None)
        log.info("테스트 메시지를 보냈습니다. 텔레그램을 확인하세요.")
        return

    # URL 목록을 딱 1회만 순회하고 종료 (반복은 GitHub Actions 가 담당)
    state = load_state()
    log.info("1회 확인 시작: %d개 URL", len(targets))
    failed = 0
    for t in targets:
        try:
            check_target(t, cfg, state, token, chat_id)
        except Exception as e:  # 네트워크 오류 등이 나도 다른 URL 은 계속 확인
            log.error("[%s] 확인 중 오류: %s", t.get("name") or t["url"], e)
            failed += 1
    log.info("1회 확인 완료 (실패 %d개)", failed)
    sys.exit(1 if failed else 0)  # 실패가 있으면 Actions 에 빨간불로 표시


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("사용자가 봇을 중지했습니다.")
