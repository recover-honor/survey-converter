"""
설문지 웹업용 변환 서비스 - FastAPI 백엔드
Claude API를 활용하여 다양한 형식의 설문지를 TheSurvey.ai 웹업로드용 Excel로 변환
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import anthropic
import json
import os
import tempfile
from pathlib import Path
from converter import extract_text_from_file, generate_excel_from_structure

app = FastAPI(title="설문지 웹업용 변환기")

# CORS 설정 (프론트엔드 연동용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Claude API 클라이언트
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# 시스템 프롬프트: 설문지 분석 및 변환 지시
SYSTEM_PROMPT = """당신은 설문지 분석 전문가입니다. 주어진 설문지 텍스트를 분석하여 TheSurvey.ai 웹업로드용 구조로 변환합니다.

## 출력 형식
반드시 아래 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 출력합니다.

```json
{
  "questions": [
    {
      "문항번호": "SQ1 또는 A1 형식",
      "콘솔번호": "Q1, Q2... 순차 증가",
      "프로그래밍_로직": "보기 Rotation 등 (없으면 null)",
      "콘솔_로직": "분기/종료 조건 (없으면 null)",
      "응답가이드": "응답자 안내문 (없으면 null)",
      "검수_로직": "데이터 검수 조건 (없으면 null)",
      "질문유형": "객관식 또는 주관식",
      "보기유형": "단일선택/복수선택/순위선택/척도형/텍스트",
      "문항": "문항번호. 질문 텍스트",
      "보기": ["보기1", "보기2", ...]
    }
  ]
}
```

## 변환 규칙
1. 스크리너 문항: SQ1, SQ2... / 본설문: A1, A2...
2. 보기에서 번호 제거: "1) 네" → "네"
3. 설문 종료 조건 → 콘솔_로직에 기재
4. 분기 조건 → 응답가이드/검수_로직에 기재
5. Rotation/랜덤 지시 → 프로그래밍_로직에 기재
6. 취소선 처리된 문항 → 제외
7. 척도형: 5점/7점/11점 척도는 "척도형"으로 분류

JSON 외의 텍스트는 절대 출력하지 마세요."""


@app.get("/", response_class=HTMLResponse)
async def read_root():
    """메인 페이지"""
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/convert")
async def convert_survey(file: UploadFile = File(...)):
    """
    설문지 파일을 웹업용 Excel로 변환
    지원 형식: .docx, .xlsx, .txt
    """
    # 파일 확장자 확인
    allowed_extensions = {".docx", ".xlsx", ".txt", ".pdf"}
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400, 
            detail=f"지원하지 않는 파일 형식입니다. 지원: {', '.join(allowed_extensions)}"
        )
    
    # 임시 파일로 저장
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        # 1. 파일에서 텍스트 추출
        text = extract_text_from_file(tmp_path, file_ext)
        
        if not text.strip():
            raise HTTPException(status_code=400, detail="파일에서 텍스트를 추출할 수 없습니다.")
        
        # 2. Claude API로 설문지 구조 분석
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"다음 설문지를 분석하여 웹업용 JSON 구조로 변환해주세요:\n\n{text}"
                }
            ]
        )
        
        # 3. JSON 파싱
        response_text = message.content[0].text
        
        # JSON 블록 추출 (```json ... ``` 형식 처리)
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0]
        else:
            json_str = response_text
        
        survey_structure = json.loads(json_str.strip())
        
        # 4. Excel 파일 생성
        output_filename = Path(file.filename).stem + "_웹업용.xlsx"
        output_path = tempfile.mktemp(suffix=".xlsx")
        generate_excel_from_structure(survey_structure, output_path)
        
        # 5. 파일 반환
        return FileResponse(
            output_path,
            filename=output_filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Claude 응답 파싱 실패: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"변환 중 오류 발생: {str(e)}")
    finally:
        # 임시 파일 정리
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/analyze")
async def analyze_survey(file: UploadFile = File(...)):
    """
    설문지 구조만 분석하여 JSON으로 반환 (미리보기용)
    """
    allowed_extensions = {".docx", ".xlsx", ".txt", ".pdf"}
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail="지원하지 않는 파일 형식입니다.")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        text = extract_text_from_file(tmp_path, file_ext)
        
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"다음 설문지를 분석하여 웹업용 JSON 구조로 변환해주세요:\n\n{text}"
                }
            ]
        )
        
        response_text = message.content[0].text
        
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0]
        else:
            json_str = response_text
            
        return json.loads(json_str.strip())
        
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
