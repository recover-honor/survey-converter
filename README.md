# 📋 설문지 웹업용 변환기

Claude AI를 활용하여 다양한 형식의 설문지를 **TheSurvey.ai 웹업로드용 Excel**로 자동 변환하는 웹 서비스입니다.

![Preview](https://via.placeholder.com/800x400/667eea/ffffff?text=Survey+Converter)

## ✨ 기능

- **다양한 입력 형식 지원**: `.docx`, `.xlsx`, `.txt`, `.pdf`
- **AI 기반 분석**: Claude API로 설문지 구조 자동 파악
- **자동 변환**: 문항번호, 보기유형, 분기 로직 등 자동 매핑
- **웹업용 Excel 출력**: TheSurvey.ai 업로드 규격에 맞는 Excel 생성

## 🚀 빠른 시작

### 1. 로컬 실행

```bash
# 저장소 클론
git clone https://github.com/your-username/survey-converter.git
cd survey-converter

# 가상환경 생성 및 활성화
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
export ANTHROPIC_API_KEY="your-api-key-here"

# 서버 실행
python app.py
```

브라우저에서 http://localhost:8000 접속

### 2. Docker로 실행

```bash
docker build -t survey-converter .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY="your-api-key" survey-converter
```

## ☁️ 클라우드 배포

### Render.com (추천, 무료 티어 있음)

1. [Render.com](https://render.com) 계정 생성
2. New > Web Service > GitHub 저장소 연결
3. 설정:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Environment Variables에 `ANTHROPIC_API_KEY` 추가
5. Deploy!

### Railway.app

```bash
# Railway CLI 설치 후
railway login
railway init
railway add
railway variables set ANTHROPIC_API_KEY="your-api-key"
railway up
```

### AWS (EC2 + Docker)

```bash
# EC2 인스턴스에서
sudo yum install docker -y
sudo service docker start

# Docker 이미지 빌드 및 실행
docker build -t survey-converter .
docker run -d -p 80:8000 \
  -e ANTHROPIC_API_KEY="your-api-key" \
  survey-converter
```

## 📁 프로젝트 구조

```
survey-converter/
├── app.py              # FastAPI 메인 애플리케이션
├── converter.py        # 텍스트 추출 및 Excel 생성 모듈
├── templates/
│   └── index.html      # 프론트엔드 UI
├── requirements.txt    # Python 의존성
├── Dockerfile          # Docker 설정
└── README.md
```

## 🔧 API 엔드포인트

### `POST /convert`
설문지 파일을 웹업용 Excel로 변환

```bash
curl -X POST -F "file=@survey.docx" http://localhost:8000/convert -o output.xlsx
```

### `POST /analyze`
설문지 구조만 분석하여 JSON 반환 (미리보기용)

```bash
curl -X POST -F "file=@survey.docx" http://localhost:8000/analyze
```

## 💰 비용 안내

- **Claude API**: 입력 토큰당 $3/MTok, 출력 토큰당 $15/MTok (Sonnet 기준)
- **예상 비용**: 설문지 1개당 약 $0.01~0.05 (문항 수에 따라 다름)

## 🛠️ 커스터마이징

### 시스템 프롬프트 수정

`app.py`의 `SYSTEM_PROMPT`를 수정하여 변환 규칙 커스터마이징 가능:

```python
SYSTEM_PROMPT = """
당신의 커스텀 프롬프트...
"""
```

### 출력 형식 변경

`converter.py`의 `generate_excel_from_structure()` 함수에서 Excel 형식 변경 가능

## 📝 라이선스

MIT License

## 🤝 기여

이슈와 PR 환영합니다!
