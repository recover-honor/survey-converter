"""
설문지 웹업용 변환 서비스 - FastAPI 백엔드
긴 설문지를 청크로 분할 처리하여 완전한 변환 지원
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import anthropic
import json
import os
import re
import tempfile
from pathlib import Path
from converter import extract_text_from_file, generate_excel_from_structure

app = FastAPI(title="설문지 웹업용 변환기")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """당신은 설문지 분석 전문가입니다. 주어진 설문지 텍스트를 분석하여 TheSurvey.ai 웹업로드용 구조로 변환합니다.

## 출력 형식
반드시 유효한 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 출력합니다.
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
      "보기": ["보기1", "보기2"]
    }
  ]
}
```

## 중요 규칙
1. 스크리너: SQ1, SQ2... / 본설문: A1, A2... (또는 원본 번호 유지)
2. 보기에서 번호 제거: "1) 네" → "네"
3. 설문 종료 조건 → 콘솔_로직에 기재
4. 취소선 처리된 문항 → 제외
5. 반드시 유효한 JSON만 출력
6. 문자열 안에 큰따옴표가 있으면 이스케이프 처리"""


CONTINUATION_PROMPT = """이전에 분석한 설문지의 다음 부분입니다. 이어서 분석해주세요.

## 중요
- 이전 파트의 마지막 콘솔번호는 Q{last_q_num}이었습니다.
- 이번 파트는 Q{next_q_num}부터 시작해주세요.
- 문항번호는 원본의 번호를 그대로 사용하세요.

동일한 JSON 형식으로 응답해주세요."""


def parse_json_response(response_text: str) -> dict:
    """Claude 응답에서 JSON 추출 및 파싱"""
    
    # 1. ```json ... ``` 블록 추출
    json_match = re.search(r'```json\s*([\s\S]*?)\s*```', response_text)
    if json_match:
        json_str = json_match.group(1)
    elif '```' in response_text:
        parts = response_text.split('```')
        if len(parts) >= 2:
            json_str = parts[1]
        else:
            json_str = response_text
    else:
        json_str = response_text
    
    json_str = json_str.strip()
    
    # 2. 첫 번째 시도: 직접 파싱
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    
    # 3. { } 블록만 추출
    brace_match = re.search(r'\{[\s\S]*\}', json_str)
    if brace_match:
        try:
            return json.loads(brace_match.group())
        except json.JSONDecodeError:
            pass
    
    # 4. 잘린 JSON 복구 시도
    json_str = json_str.rstrip(',')
    
    open_braces = json_str.count('{') - json_str.count('}')
    open_brackets = json_str.count('[') - json_str.count(']')
    
    if open_brackets > 0:
        json_str += ']' * open_brackets
    if open_braces > 0:
        json_str += '}' * open_braces
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 파싱 실패: {str(e)[:100]}")


def split_survey_into_chunks(text: str, chunk_size: int = 25000) -> list:
    """
    설문지 텍스트를 섹션 경계에서 분할
    문항 번호 패턴을 기준으로 자연스럽게 분할
    """
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    current_pos = 0
    
    # 섹션 구분 패턴 (문항 시작 패턴)
    section_pattern = re.compile(r'\n\s*(\d+\.|\[Q\d+\]|Q\d+\.|SQ\d+\.?|Part\s+[A-Z0-9])', re.IGNORECASE)
    
    while current_pos < len(text):
        end_pos = min(current_pos + chunk_size, len(text))
        
        if end_pos >= len(text):
            chunks.append(text[current_pos:])
            break
        
        # 청크 끝 부분에서 섹션 경계 찾기
        search_start = max(current_pos + chunk_size - 3000, current_pos)
        search_text = text[search_start:end_pos]
        
        # 마지막 섹션 시작점 찾기
        matches = list(section_pattern.finditer(search_text))
        
        if matches:
            # 마지막에서 두 번째 매치 위치에서 자르기 (완전한 섹션 포함)
            if len(matches) >= 2:
                split_point = search_start + matches[-1].start()
            else:
                split_point = search_start + matches[-1].start()
        else:
            # 섹션 경계를 못 찾으면 줄바꿈에서 자르기
            newline_pos = text.rfind('\n', current_pos, end_pos)
            split_point = newline_pos if newline_pos > current_pos else end_pos
        
        chunks.append(text[current_pos:split_point])
        current_pos = split_point
    
    return chunks


def process_survey_chunks(text: str) -> dict:
    """
    긴 설문지를 청크로 나눠서 처리하고 결과 병합
    """
    chunks = split_survey_into_chunks(text)
    all_questions = []
    last_q_num = 0
    
    for i, chunk in enumerate(chunks):
        print(f"Processing chunk {i+1}/{len(chunks)} (size: {len(chunk)})")
        
        if i == 0:
            # 첫 번째 청크
            messages = [
                {
                    "role": "user",
                    "content": f"다음 설문지를 분석하여 웹업용 JSON 구조로 변환해주세요:\n\n{chunk}"
                }
            ]
        else:
            # 후속 청크 - 콘솔번호 이어서
            continuation = CONTINUATION_PROMPT.format(
                last_q_num=last_q_num,
                next_q_num=last_q_num + 1
            )
            messages = [
                {
                    "role": "user",
                    "content": f"{continuation}\n\n{chunk}"
                }
            ]
        
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=messages
        )
        
        response_text = message.content[0].text
        
        try:
            chunk_result = parse_json_response(response_text)
            chunk_questions = chunk_result.get("questions", [])
            
            # 콘솔번호 재조정 (연속성 유지)
            for q in chunk_questions:
                last_q_num += 1
                q["콘솔번호"] = f"Q{last_q_num}"
            
            all_questions.extend(chunk_questions)
            print(f"  -> Found {len(chunk_questions)} questions (total: {len(all_questions)})")
            
        except ValueError as e:
            print(f"  -> Error parsing chunk {i+1}: {e}")
            continue
    
    return {"questions": all_questions}


@app.get("/", response_class=HTMLResponse)
async def read_root():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/convert")
async def convert_survey(file: UploadFile = File(...)):
    allowed_extensions = {".docx", ".xlsx", ".txt", ".pdf"}
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 파일 형식입니다.")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        text = extract_text_from_file(tmp_path, file_ext)
        
        if not text.strip():
            raise HTTPException(status_code=400, detail="파일에서 텍스트를 추출할 수 없습니다.")
        
        print(f"Total text length: {len(text)} characters")
        
        # 청크 분할 처리
        survey_structure = process_survey_chunks(text)
        
        if not survey_structure.get("questions"):
            raise HTTPException(status_code=500, detail="설문지에서 문항을 찾을 수 없습니다.")
        
        output_filename = Path(file.filename).stem + "_웹업용.xlsx"
        output_path = tempfile.mktemp(suffix=".xlsx")
        generate_excel_from_structure(survey_structure, output_path)
        
        return FileResponse(
            output_path,
            filename=output_filename,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"변환 중 오류: {str(e)[:200]}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/analyze")
async def analyze_survey(file: UploadFile = File(...)):
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
        
        # 청크 분할 처리
        result = process_survey_chunks(text)
        
        return {
            "questions": result.get("questions", []),
            "total_questions": len(result.get("questions", [])),
            "text_length": len(text),
            "chunks_processed": len(split_survey_into_chunks(text))
        }
        
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"분석 중 오류: {str(e)[:200]}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
