# 플링캐스트 댓글 → 텔레그램 알림 봇

플링캐스트 작품 페이지에 **새 댓글이나 답글**이 달리면 텔레그램으로 알려 주는 봇입니다.

## 폴더 구성

```
comment-bot/
├─ comment_bot.py     ← 메인 코드
├─ config.json        ← 감시할 URL 목록, 확인 간격
├─ .env.example       ← 텔레그램 토큰 예시 (복사해서 .env 로 사용)
├─ requirements.txt   ← 필요한 라이브러리
├─ .gitignore
├─ .env               ← (직접 만듦) 내 토큰/Chat ID — 남에게 공유 금지!
├─ state.json         ← (자동 생성) 이미 알림 보낸 댓글 ID
└─ bot.log            ← (자동 생성) 실행 기록
```

---

## 0. 크롤링 방식: 왜 Playwright/Selenium 이 아닌가?

사이트를 분석해 보니 플링캐스트는 댓글을 공개 **GraphQL API**
(`https://api.plingcast.co.kr/graphql`, `showSynopsisComments` 쿼리)로 불러옵니다.
화면에 보이는 댓글도 결국 이 API 에서 받아온 데이터입니다.

| 방식 | 장점 | 단점 |
|---|---|---|
| **API 직접 호출 (채택)** | 빠름(요청 1~2회), 메모리 적음, 크롬 설치 불필요, 댓글 ID·작성시간이 정확히 나옴 | 사이트가 API 를 바꾸면 수정 필요 |
| Playwright / Selenium | 화면 그대로 읽음 | 크롬을 매번 띄워서 무겁고 느림, HTML 구조가 바뀌면 바로 깨짐 |

그래서 자바스크립트 동적 로딩 문제를 **브라우저 없이** 해결합니다.
답글(대댓글)도 같은 API 로 가져옵니다.

---

## 1. 개발 환경 세팅 (VS Code)

1. **Python 설치**: <https://www.python.org/downloads/> 에서 3.10 이상 설치.
   설치 첫 화면에서 **"Add python.exe to PATH" 체크** 필수.
2. **VS Code 설치**: <https://code.visualstudio.com/>
3. **확장 프로그램 설치** (왼쪽 네모 4개 아이콘 → 검색):
   - `Python` (Microsoft) — 필수
   - `Pylance` (Microsoft) — 자동완성, 보통 Python 과 같이 설치됨
   - `Korean Language Pack` — 메뉴 한국어로 (선택)
4. **폴더 열기**: 이 파일들이 든 폴더(예: `C:\comment-bot`)를 `파일 → 폴더 열기`.
5. **터미널 열기**: 메뉴 `터미널 → 새 터미널` (단축키 `` Ctrl + ` ``).
6. **가상환경 만들기** (이 프로젝트 전용 파이썬 공간):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   - 줄 맨 앞에 `(.venv)` 가 보이면 성공.
   - **"이 시스템에서 스크립트를 실행할 수 없으므로…"** 오류가 나면 아래를 한 번 실행 후 다시 시도:
     ```powershell
     Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
     ```
   - Mac/Linux: `source .venv/bin/activate`
7. VS Code 오른쪽 아래(또는 `Ctrl+Shift+P` → `Python: Select Interpreter`)에서 `.venv` 를 선택.

## 2. 라이브러리 설치

```powershell
pip install -r requirements.txt
```

`requirements.txt` 내용:

```
requests>=2.31        # 웹 요청 (플링캐스트 API, 텔레그램 API)
python-dotenv>=1.0    # .env 파일에서 토큰 읽기
```

## 3. 텔레그램 봇 토큰 & Chat ID 발급

**봇 토큰**
1. 텔레그램에서 **@BotFather** 검색 → 대화 시작.
2. `/newbot` 입력 → 봇 이름 입력 → 봇 아이디 입력 (반드시 `bot` 으로 끝나야 함, 예: `my_pling_alert_bot`).
3. `123456789:ABC...` 형태의 **토큰**을 받습니다. (비밀번호처럼 관리!)

**Chat ID**
1. 방금 만든 봇을 텔레그램에서 검색해 들어가서 **`/start` 를 누르거나 아무 말이나 보냅니다.** (안 하면 봇이 나에게 메시지를 못 보냄)
2. 브라우저 주소창에 아래를 입력 (`<토큰>` 부분을 내 토큰으로 바꿈):
   ```
   https://api.telegram.org/bot<토큰>/getUpdates
   ```
3. 결과에서 `"chat":{"id":123456789, ...` 의 숫자가 **Chat ID** 입니다.
   - 결과가 `"result":[]` 로 비어 있으면 봇에게 메시지를 한 번 더 보내고 새로고침.
   - 다른 방법: **@userinfobot** 에게 아무 말이나 보내면 내 ID 를 알려줍니다.
   - 그룹방으로 받고 싶다면 봇을 그룹에 초대 → 그룹에서 말하기 → getUpdates 의 음수 ID(`-100...`) 사용.

## 4. 설정 파일

### `.env` (토큰 보관)

`.env.example` 을 복사해 이름을 `.env` 로 바꾸고 값을 채웁니다.

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz
TELEGRAM_CHAT_ID=123456789
```

> 파일 이름은 정확히 `.env` (앞에 점, 확장자 없음). `.env.txt` 가 되지 않도록 주의.

### `config.json` (감시 대상)

```json
{
  "notify_existing_on_first_run": false,
  "include_replies": true,
  "max_pages": 10,
  "targets": [
    {
      "name": "청람대학교",
      "url": "https://www.plingcast.com/kr/content/%EC%B2%AD%EB%9E%8C%EB%8C%80%ED%95%99%EA%B5%90_8816/comment/",
      "enabled": true
    }
  ]
}
```

| 항목 | 설명 |
|---|---|
| `notify_existing_on_first_run` | `false`: 처음 켤 때 이미 있던 댓글은 알림 안 보냄. `true`: 기존 댓글도 전부 보냄 |
| `include_replies` | 답글(대댓글)도 알림 받을지 |
| `max_pages` | 댓글 몇 페이지(1페이지=50개)까지 볼지 |
| `ignore_authors` | 알림을 받지 않을 닉네임 목록. 예: `["닉네임1", "닉네임2"]`. 제외된 댓글도 '본 것'으로 저장되어 나중에 다시 알림이 오지 않음. 비워 두려면 `[]` |
| `targets` | 감시할 작품 목록. `enabled: false` 면 잠시 끔 |

**URL 추가하기**: `targets` 에 `{ ... }` 를 쉼표로 이어 붙이면 됩니다.

```json
  "targets": [
    { "name": "청람대학교", "url": "https://www.plingcast.com/kr/content/%EC%B2%AD%EB%9E%8C%EB%8C%80%ED%95%99%EA%B5%90_8816/comment/", "enabled": true },
    { "name": "다른 작품", "url": "https://www.plingcast.com/kr/content/작품이름_1234/comment/", "enabled": true }
  ]
```

URL 끝의 `_숫자/comment/` 에서 작품 번호를 자동으로 꺼내 쓰므로, 작품 댓글 페이지 주소를 그대로 복사해 넣으면 됩니다.

## 5. 실행 & 테스트

가상환경이 켜진 터미널(`(.venv)` 표시)에서 순서대로:

```powershell
# ① 텔레그램 없이 댓글이 잘 읽히는지 확인 (아무것도 저장/전송 안 함)
python comment_bot.py --preview

# ② 텔레그램 연결 테스트 — 폰으로 "연결 성공" 메시지와 버튼이 와야 함
python comment_bot.py --test

# ③ 실제 확인 1회 실행 (URL 목록을 한 번 확인하고 바로 종료)
python comment_bot.py
```

봇은 실행할 때마다 **딱 1회만** 확인하고 종료합니다. 5분마다 반복 실행하는 일은 GitHub Actions 가 맡습니다(9번 참고).

처음 ③을 실행하면 로그에 `첫 실행: 기존 댓글/답글 N개를 '이미 본 것'으로 저장` 이 나오고 알림은 오지 않습니다.
그 뒤에 새 댓글이 달린 상태에서 다시 실행하면 이렇게 옵니다:

```
💬 새 댓글 · 청람대학교
👤 작성자  🕒 2026-10-31 11:09
━━━━━━━━━━━━
댓글 내용...
[ 💬 댓글 보러 가기 ]   ← 누르면 해당 URL 로 이동
```

**실제 알림이 오는지 바로 확인하는 방법** (새 댓글을 기다리지 않고):
1. `state.json` 파일을 다른 이름으로 잠시 바꿔 둡니다 (예: `state_backup.json`).
2. `config.json` 에서 `"notify_existing_on_first_run": true` 로 바꾸고 `python comment_bot.py` 실행 → 기존 댓글이 전부 알림으로 옵니다.
3. 확인 후 `false` 로 되돌리고, 새로 생긴 `state.json` 을 지운 뒤 백업 파일 이름을 다시 `state.json` 으로 바꿉니다.

## 6. 중복 방지 원리

- 모든 댓글/답글에는 고유 ID(예: `204832`)가 있습니다.
- 알림을 보낸 ID 는 `state.json` 에 저장되고, 다음 확인 때 저장된 ID 는 건너뜁니다.
- 하나 보낼 때마다 즉시 저장하므로, 중간에 컴퓨터가 꺼져도 같은 알림이 두 번 가지 않습니다.
- 답글은 각 댓글의 "답글 수"가 늘었을 때만 추가로 조회해서 요청 수를 아낍니다.
- 처음부터 다시 받고 싶으면 `state.json` 을 지우면 됩니다.

## 7. 자주 겪는 문제

| 증상 | 해결 |
|---|---|
| `'python'은(는) ... 인식되지 않습니다` | Python 설치 시 PATH 체크 안 함 → 재설치하거나 `py` 로 실행 |
| `ModuleNotFoundError: No module named 'dotenv'` | 가상환경이 안 켜졌거나 설치 안 함 → `.\.venv\Scripts\Activate.ps1` 후 `pip install -r requirements.txt` |
| `텔레그램 전송 실패 (401)` | 토큰이 틀림 |
| `텔레그램 전송 실패 (400) chat not found` | Chat ID 가 틀렸거나 봇에게 `/start` 를 안 보냄 |
| 알림이 안 옴 | 첫 실행은 원래 알림 없음(정상). `bot.log` 확인 |
| `GraphQL 오류` / 계속 실패 | 사이트 API 가 바뀌었을 수 있음 → `--preview` 로 확인 |

## 8. 주기적으로 실행하려면

`comment_bot.py` 는 1회 확인 후 종료하므로, 누군가 주기적으로 실행해 줘야 합니다.
- **추천: 아래 9번 GitHub Actions** — PC 가 꺼져 있어도 5분마다 자동 실행.
- 내 PC 에서 하고 싶다면: Windows `작업 스케줄러` 에서 5분마다 `pythonw.exe comment_bot.py` 를 실행하도록 등록.
  (GitHub Actions 와 동시에 쓰면 알림이 두 번 오니 하나만 사용)

## 9. GitHub Actions 로 5분마다 자동 실행 (PC 꺼져도 동작)

`.github/workflows/monitor.yml` 이 5분마다 아래를 실행합니다.
1. `python comment_bot.py` → 새 댓글 1회 확인 후 종료
2. 바뀐 `state.json` 을 저장소에 다시 커밋 & 푸시 → 다음 실행 때 중복 알림 없음

### 9-1. 저장소 공개 여부 먼저 정하기 (중요)

| | 공개(Public) 저장소 | 비공개(Private) 저장소 |
|---|---|---|
| Actions 사용 시간 | **무료·무제한** | 월 2,000분 무료. 5분 간격이면 월 약 8,600분 → **초과(유료)** |
| 남에게 보이는 것 | 코드, `config.json`(감시 URL·제외 닉네임), `state.json`(댓글 번호) | 없음 |
| 권장 설정 | cron `*/5` 그대로 | cron 을 `'*/30 * * * *'`(30분) 정도로 늘리기 |

텔레그램 토큰은 어느 쪽이든 **Secrets** 에 넣으므로 공개 저장소여도 노출되지 않습니다.

### 9-2. GitHub 에 올리기

1. <https://github.com/new> 에서 새 저장소 생성 (README 추가 체크는 하지 않기).
2. VS Code 터미널(`pling_bot` 폴더)에서:
   ```powershell
   git init
   git add .
   git status
   ```
   → 목록에 **`.env` 가 없는지 꼭 확인** (있으면 절대 커밋하지 말 것). `state.json` 은 있어야 정상.
   ```powershell
   git commit -m "comment bot"
   git branch -M main
   git remote add origin https://github.com/<내아이디>/<저장소이름>.git
   git push -u origin main
   ```

### 9-3. 텔레그램 토큰을 Secrets 에 등록

저장소 페이지 → **Settings → Secrets and variables → Actions → New repository secret** 에서 2개 추가:

| Name | Secret |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `.env` 에 넣었던 봇 토큰 |
| `TELEGRAM_CHAT_ID` | `.env` 에 넣었던 Chat ID |

### 9-4. 동작 확인

1. 저장소 → **Actions** 탭 → 왼쪽 `Plingcast Comment Monitor` → **Run workflow** 버튼으로 바로 1회 실행.
2. 초록 체크 ✅ 가 뜨면 성공. 클릭하면 `댓글 1회 확인` 단계에서 로그를 볼 수 있습니다.
3. 새 댓글이 있었다면 텔레그램 알림이 오고, 저장소에 `chore: update state.json` 커밋이 생깁니다.

### 9-5. 주의할 점

- **내 PC 에서 돌던 봇은 꺼 주세요.** 둘 다 켜져 있으면 같은 댓글 알림이 두 번 옵니다.
- GitHub 의 예약 실행은 정확히 5분마다가 아니라, 혼잡할 때 **10~20분씩 늦어지거나 가끔 건너뛸 수 있습니다.** (`state.json` 덕분에 늦어도 놓친 댓글은 다음 실행에서 모두 옵니다.)
- 공개 저장소는 **60일 동안 활동이 없으면 예약 실행이 자동으로 꺼질 수 있습니다.** GitHub 이 미리 메일로 알려 주니, 그럴 땐 Actions 탭에서 다시 켜면(Enable workflow) 됩니다.
- `config.json` 을 수정하려면 GitHub 웹에서 직접 고치거나, PC 에서 고친 뒤 **먼저 `git pull`** 하고 커밋·푸시하세요. (봇이 계속 `state.json` 을 커밋하므로 pull 없이 push 하면 거절됩니다.)
- 확인 중 오류가 나면 해당 실행이 빨간색 ❌ 으로 표시되고 GitHub 이 메일을 보냅니다. 일시적인 네트워크 문제라면 다음 실행에서 자동으로 정상화됩니다.
