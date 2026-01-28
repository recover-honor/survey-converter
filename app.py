"""
설문지 웹업용 변환 서비스 - FastAPI 백엔드
Loop 문항 확장 및 누락 방지 강화 버전
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

## 핵심 원칙: 완전성
- 모든 문항을 빠짐없이 포함해야 합니다
- 문항 하나라도 누락되면 안 됩니다
- 각 Part/Section의 모든 문항을 순서대로 변환합니다

## Loop 문항 처리 (매우 중요!)
설문지에서 "Loop", "반복", "카테고리별 제시" 등의 지시가 있으면:

1. Loop 문항은 **실제로 확장**해서 각각 별도 행으로 출력합니다
2. Loop 대상이 명시되어 있으면 (예: "SQ15 응답 카테고리 중 최대 5개") 해당 카테고리별로 문항을 반복 생성합니다
3. Loop 횟수가 명시되지 않으면 기본 3회로 가정합니다

### Loop 확장 예시:
원본: "A1~A7은 SQ15 응답 카테고리 중 최대 5개 Loop"
SQ15 보기: 과자/스낵류, 쿠키/비스켓류, 빵/베이커리류, 케이크류, 푸딩류...

출력 (각각 별도 행):
- A1_1. [과자/스낵류]가 '디저트'에 얼마나 가깝다고 생각하시나요?
- A1_2. [쿠키/비스켓류]가 '디저트'에 얼마나 가깝다고 생각하시나요?
- A1_3. [빵/베이커리류]가 '디저트'에 얼마나 가깝다고 생각하시나요?
- A1_4. [케이크류]가 '디저트'에 얼마나 가깝다고 생각하시나요?
- A1_5. [푸딩류]가 '디저트'에 얼마나 가깝다고 생각하시나요?
- A2_1. [과자/스낵류]를 최근 1개월 이내에 총 몇 번 취식하셨나요?
- A2_2. [쿠키/비스켓류]를 최근 1개월 이내에 총 몇 번 취식하셨나요?
... (A7_5까지 총 35개 문항)

### 문항번호 규칙 (Loop 적용 시):
- 기본 형태: A1_1, A1_2, A1_3... 또는 A1-1, A1-2, A1-3...
- Loop 대상 카테고리명을 문항 텍스트에 [괄호]로 삽입

## 출력 형식
반드시 유효한 JSON 형식으로만 응답하세요. 다른 설명 없이 JSON만 출력합니다.
```json
{
  "questions": [
    {
      "문항번호": "SQ1 또는 A1 또는 A1_1 형식",
      "콘솔번호": "Q1, Q2... 순차 증가",
      "프로그래밍_로직": "보기 Rotation, Loop 등 (없으면 null)",
      "콘솔_로직": "분기/종료 조건 (없으면 null)",
      "응답가이드": "응답자 안내문, Loop 대상 카테고리 등 (없으면 null)",
      "검수_로직": "데이터 검수 조건 (없으면 null)",
      "질문유형": "객관식 또는 주관식",
      "보기유형": "단일선택/복수선택/순위선택/척도형/텍스트",
      "문항": "문항번호. 질문 텍스트 (Loop시 [카테고리명] 포함)",
      "보기": ["보기1", "보기2"]
    }
  ]
}
```

## 문항 유형 판단
- SA, 단일선택, 하나만 선택 → 단일선택
- MA, 복수선택, 모두 선택 → 복수선택
- 순위, 순서대로 N개 → 순위선택
- 척도, 점수, 동의 정도 → 척도형
- OE, 주관식, 직접 입력 → 텍스트

## 변환 규칙
1. 스크리너: SQ1, SQ2... / 본설문: A1, B1, C1... (원본 체계 유지)
2. 보기에서 번호 제거: "1) 네" → "네"
3. 설문 종료 조건(CLOSE) → 콘솔_로직에 기재
4. [PRO: ...] 지시사항 → 프로그래밍_로직 또는 응답가이드로 변환
5. 취소선 처리된 문항 → 제외
6. 반드시 유효한 JSON만 출력
7. 문자열 안에 큰따옴표가 있으면 이스케이프 처리

## 주의사항
- 텍스트가 길어 일부만 전달될 수 있습니다. 전달된 범위 내 모든 문항을 빠짐없이 변환하세요.
- Part A, Part B, Part C 등 섹션별로 문항 번호가 리셋될 수 있습니다.
- 같은 번호가 여러 번 나오면 섹션 prefix로 구분하세요 (A1, B1, C1...)"""


CONTINUATION_PROMPT = """이전에 분석한 설문지의 다음 부분입니다. 이어서 분석해주세요.

## 중요
- 이전 파트의 마지막 콘솔번호는 Q{last_q_num}이었습니다.
- 이번 파트는 Q{next_q_num}부터 시작해주세요.
- 문항번호는 원본의 번호를 그대로 사용하세요 (A1, B1, C1 등).
- Loop 문항은 반드시 확장해서 각각 별도 행으로 출력하세요.
- 모든 문항을 빠짐없이 포함하세요.

동일한 JSON 형식으로 응답해주세요."""


def parse_json_response(response_text: str) -> dict:
    """Claude 응답에서 JSON 추출 및 파싱"""
    
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
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    
    brace_match = re.search(r'\{[\s\S]*\}', json_str)
    if brace_match:
        try:
            return json.loads(brace_match.group())
        except json.JSONDecodeError:
            pass
    
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


def split_survey_into_chunks(text: str, chunk_size: int = 20000) -> list:
    """
    설문지 텍스트를 섹션 경계에서 분할
    청크 크기를 줄여서 더 세밀하게 처리
    """
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    current_pos = 0
    section_pattern = re.compile(r'\n\s*(PART\s+[A-Z]|Section|[0-9]+\.\s+[가-힣]|\[Q\d+\]|Q\d+\.|SQ\d+\.?)', re.IGNORECASE)
    
    while current_pos < len(text):
        end_pos = min(current_pos + chunk_size, len(text))
        
        if end_pos >= len(text):
            chunks.append(text[current_pos:])
            break
        
        search_start = max(current_pos + chunk_size - 5000, current_pos)
        search_text = text[search_start:end_pos]
        matches = list(section_pattern.finditer(search_text))
        
        if matches:
            split_point = search_start + matches[-1].start()
        else:
            newline_pos = text.rfind('\n', current_pos, end_pos)
            split_point = newline_pos if newline_pos > current_pos else end_pos
        
        chunks.append(text[current_pos:split_point])
        current_pos = split_point
    
    return chunks


def process_survey_chunks(text: str) -> dict:
    """긴 설문지를 청크로 나눠서 처리하고 결과 병합"""
    chunks = split_survey_into_chunks(text)
    all_questions = []
    last_q_num = 0
    
    print(f"Total chunks to process: {len(chunks)}")
    
    for i, chunk in enumerate(chunks):
        print(f"Processing chunk {i+1}/{len(chunks)} (size: {len(chunk)})")
        
        if i == 0:
            messages = [
                {
                    "role": "user",
                    "content": f"다음 설문지를 분석하여 웹업용 JSON 구조로 변환해주세요. Loop 문항은 반드시 확장해서 각각 별도 행으로 출력하세요:\n\n{chunk}"
                }
            ]
        else:
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
        
        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=messages
            )
            
            response_text = message.content[0].text
            chunk_result = parse_json_response(response_text)
            
            if chunk_result is None:
                print(f"  -> Chunk {i+1} returned None, skipping")
                continue
            
            chunk_questions = chunk_result.get("questions") or []
            
            if not chunk_questions:
                print(f"  -> No questions found in chunk {i+1}")
                continue
            
            for q in chunk_questions:
                if q is None:
                    continue
                last_q_num += 1
                q["콘솔번호"] = f"Q{last_q_num}"
            
            all_questions.extend([q for q in chunk_questions if q is not None])
            print(f"  -> Found {len(chunk_questions)} questions (total: {len(all_questions)})")
            
        except ValueError as e:
            print(f"  -> Error parsing chunk {i+1}: {e}")
            continue
        except Exception as e:
            print(f"  -> Unexpected error in chunk {i+1}: {e}")
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
        raise HTTPException(status_code=400, detail="지원하지 않는 파일 형식입니다.")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        text = extract_text_from_file(tmp_path, file_ext)
        
        if not text.strip():
            raise HTTPException(status_code=400, detail="파일에서 텍스트를 추출할 수 없습니다.")
        
        print(f"Total text length: {len(text)} characters")
        
        survey_structure = process_survey_chunks(text)
        
        if not survey_structure.get("questions"):
            raise HTTPException(status_code=500, detail="설문지에서 문항을 찾을 수 없습니다.")
        
        print(f"Total questions extracted: {len(survey_structure['questions'])}")
        
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
        result = process_survey_chunks(text)
        
        return {
            "questions": result.get("questions") or [],
            "total_questions": len(result.get("questions") or []),
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
