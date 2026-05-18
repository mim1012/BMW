import os
from dotenv import load_dotenv

load_dotenv()

# 로그인 정보
EMAIL = os.getenv("BMW_EMAIL", "")
PASSWORD = os.getenv("BMW_PASSWORD", "")

# BMW 샵 URL
BASE_URL = "https://shop.bmw.co.kr"
LOGIN_URL = "https://shop.bmw.co.kr/redirect_gcdm"

# 구매 가능 모델 목록
MODELS = {
    "1": {
        "name": "BMW M240i",
        "url": f"{BASE_URL}/online/oom/OMG4223020",
    },
    "2": {
        "name": "BMW 뉴 M440i 쿠페 프로",
        "url": f"{BASE_URL}/online/oom/OOM24090002",
    },
    "3": {
        "name": "BMW Online Exclusive (한정 에디션)",
        "url": f"{BASE_URL}/online/oem/model",
    },
}

# 브라우저 설정
HEADLESS = False          # 수동 CAPTCHA 처리를 위해 headed 모드 필수
SLOW_MO = 300            # ms, 동작 간 딜레이 (안정적인 자동화를 위해)
TIMEOUT = 30_000         # ms, 기본 타임아웃
