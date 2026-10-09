# Multi-tool router → tool-specific query rewriting

모델: `gpt-6-luna`, reasoning effort `none`. Python 3.10+.

## 비교 설계

| Variant | 호출 수 | 입력 tool 정보 | 출력 |
|---|---:|---|---|
| joint | 1 | router가 선택한 전체 tool schema와 idx | 모든 idx의 query |
| per_tool_with_list | K, 병렬 | 현재 target schema + 선택된 전체 schema/idx | 현재 idx의 query |
| per_tool_local | K, 병렬 | 현재 target schema/idx만 | 현재 idx의 query |

세 조건 모두 동일한 전체 대화와 마지막 질문을 받습니다. local 조건에서 **다른 선택 tool 목록만 제거**합니다. Router는 query나 arguments를 생성하지 않고 `[0, 2]` 같은 idx 리스트만 출력합니다. idx는 샘플 내부 0-based 번호입니다. idx 리스트의 순서는 실행 순서를 뜻하지 않습니다.

joint 출력도 tool별 query입니다. 하나의 통합 query를 여러 tool에 복사하는 조건이 아닙니다. with_list는 idx만이 아니라 해당 schema도 제공하므로 협업 task 분해의 정보 효과를 봅니다. 실제 즉시 dispatch의 스트리밍 지연은 측정하지 않습니다.

## 데이터

공개 NousResearch/hermes-function-calling-v1 (dataset card: Apache-2.0)의 `func-calling-singleturn.json` 사용.
https://huggingface.co/datasets/NousResearch/hermes-function-calling-v1

Hugging Face 토큰·로그인 없이 urllib 공개 HTTP로 다운로드합니다. `huggingface_hub` 의존성을 제거했습니다. 원본 tool schema와 assistant의 `<tool_call>` JSON을 정답으로 사용합니다. 서로 다른 gold tool이 2개 이상인 샘플만 포함하고 동일 tool의 반복 호출은 유지합니다. 파싱 불가능한 샘플은 준비 단계에서 제외하고 수를 기록합니다. 다운로드 SHA와 원본 파일 hash, 샘플 hash를 기록합니다.

이 파일에는 필터 후 572개 multi-tool 원본 샘플이 확인됐습니다. 현재 질문 이후 assistant 정답은 모델 입력에서 제외합니다. `func-calling.json`도 확인했지만 후속 tool response가 있는 형태이며, 검사된 tool-call 샘플에 복수 user turn은 없었습니다. 따라서 멀티턴은 아래처럼 명시적인 파생 조건을 유지합니다.

- `single`: 원본 요청 그대로.
- `multi_derived`: 원본 요청 → assistant의 미실행 확인 → 마지막 user가 이전 요청 실행을 참조. 두 조건은 같은 source_id로 짝지어집니다.

**기본 멀티턴은 파생 reference-back 실험입니다. 실제 BFCL 멀티턴이나 수정·충돌·tool-result 의존성을 검증한 결과로 해석할 수 없습니다.** 원본 single query가 history에 그대로 존재하므로 난도가 낮습니다. 원본 멀티턴이 필요하면 아래 정규화 JSONL을 입력하세요. 이 패키지는 BFCL simulator/trajectory adapter를 포함하지 않습니다.

```json
{"id":"native:1:turn2","source_id":"native:1","split":"multi_native","messages":[{"role":"user","content":"Search Seoul weather and Busan hotels."},{"role":"assistant","content":"Which hotel date?"},{"role":"user","content":"2026-10-23. Keep the other request."}],"tools":[{"idx":0,"name":"weather","description":"Weather lookup","parameters":{"type":"object","properties":{"city":{"type":"string"}}}},{"idx":1,"name":"hotels","description":"Hotel lookup","parameters":{"type":"object","properties":{"city":{"type":"string"},"date":{"type":"string"}}}}],"gold_idx":[0,1],"gold_arguments":{"0":[{"city":"Seoul"}],"1":[{"city":"Busan","date":"2026-10-23"}]}}
```

native 입력에서 messages는 현재 마지막 질문까지만 포함하며, 현재 turn gold와 tool schema를 사람이 검증해야 합니다. 미래 turn/정답 trajectory를 history에 넣지 마세요. 실제 tool response를 포함하려면 messages에 role/content 객체로 기록하면 됩니다. 본 프로그램은 대화를 JSON 데이터로 전달합니다.

## 실행

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY='your-key'
python3 benchmark.py prepare --n 100 --seed 42
# 리라이팅 자체 비교: 같은 정확한 gold idx를 주고 비교
python3 benchmark.py run --route oracle --out results/oracle --concurrency 4
# 실제 router idx를 한 번 생성해 세 variant가 공통 재사용
python3 benchmark.py run --route predicted --out results/predicted --concurrency 4
# 반복 안정성
python3 benchmark.py run --route oracle --out results/oracle --concurrency 4 --repeats 3
# native JSONL
python3 benchmark.py run --data data/native.jsonl --out results/native
```

먼저 `--n 5`와 별도 --data/--out으로 smoke run 권장. 기본 n=100은 source 100개 × 두 turn 조건 × 3 variants = 600 rewrite 평가 단위입니다. K개 local rewrite 호출과 각 query decoder 호출을 추가합니다. prepare는 모델 API를 호출하지 않습니다.

Responses API를 사용합니다. `OPENAI_BASE_URL`을 설정하면 SDK의 호환 endpoint를 사용할 수 있습니다. 모델 이름을 자동으로 바꾸지 않습니다.

## 평가와 해석

1. `router_exact`: gold idx set exact match. oracle에서는 1.
2. `rewrite_valid_rate`: idx 누락/중복/추가, 빈 query, 줄바꿈, JSON 오류 확인. 실패를 None query로 처리하지 않습니다.
3. `selected_tool_argument_exact`: tool schema + 해당 query만 받은 공통 decoder가 복원한 호출 arguments와 gold를 strict multiset 비교. 잘못 선택된 tool은 실패.
4. `all_gold_arguments_exact`: router set과 모든 gold tool의 arguments가 동시에 정답. 가장 중요한 end-to-end 지표.
5. rewrite 단계 입력/출력 토큰 합계와 병렬 critical-path 지연 평균/p95. decoder/router 토큰은 rewrite 토큰에 포함하지 않습니다. 상세 응답 usage와 오류를 보존합니다.

리라이팅 문장 자체는 gold가 없으므로 **argument exact는 downstream proxy**입니다. decoder 오류와 표현 차이에 민감하며 의미적 동등성을 완전히 평가하지 못합니다. strict JSON 비교는 타입/값 차이도 실패합니다. dependency가 실행 결과를 필요로 하면 UNRESOLVED를 사용하므로 원래 concrete gold와는 실패할 수 있습니다. 이 경우 실제 execution/replanning 평가를 별도로 구성해야 합니다.

개별 호출은 최대 3번 시도. 재시도 비용을 토큰에 포함하며 실패는 분모에서 제거하지 않습니다. 성공한 API 응답은 hash cache, 샘플 결과는 JSONL로 즉시 저장합니다. 설정/데이터가 바뀌면 새 out을 사용해야 합니다. resumes는 원래 저장된 호출 시간을 사용합니다. 각 호출 시간은 semaphore 대기와 재시도 시간을 포함하며, per-tool max로 critical-path를 추정합니다. provider load·cache·공유 concurrency의 영향을 받으므로 엄밀한 서비스 SLA 수치는 아닙니다. variant 순서를 샘플마다 섞어 순서 영향을 줄입니다.

`summary.json`은 split/variant별 집계, `results.jsonl`은 query/decoder/원시 오류. 반복 집계는 동일 source의 반복 관측이므로 독립 표본 수 증가로 해석하지 마세요. 품질 차이는 source_id 단위 paired 비교로 확인하고 실패 query를 수동 검토하세요. 통계 검정/CI 자동 산출은 포함하지 않습니다.

예상 가설(측정 결과 아님): joint는 토큰 효율, local은 격리/즉시 dispatch, with_list는 중복·task 분배 조정에 장점이 있을 수 있습니다. 품질 결과가 비슷하면 토큰과 병렬 지연을 보고 선택합니다.

## 검증 상태

토큰 없는 실제 공개 다운로드, 100개 원본 → 200개 평가 행 생성, 문법 및 API-free mock 흐름을 검증했습니다. 유료 모델 benchmark 결과는 포함하지 않습니다.

압축에 `data/samples.jsonl` 200행을 미리 포함했습니다. 다운로드 없이 `run`부터 실행할 수 있습니다. 다시 샘플링할 때만 `prepare`를 실행하세요. 다운로드 실패 시 `--raw-file`로 원본 JSON을 지정할 수 있습니다.
