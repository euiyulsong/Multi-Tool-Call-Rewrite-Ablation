# Multi-tool Query Rewriting 실험 결과

## 실험 목적과 조건

선택된 tool idx 리스트를 받은 뒤 tool별 standalone query를 만드는 세 방식을 비교했다. 모델은 `gpt-6-luna`, reasoning effort는 `none`, 기본 concurrency는 4다.

| Variant | 리라이팅 호출 | 제공되는 tool 정보 |
|---|---|---|
| `joint` | 전체 tool을 한 번에 처리 | 선택된 전체 schema/idx |
| `per_tool_local` | tool별 병렬 호출 | 현재 target schema/idx |
| `per_tool_with_list` | tool별 병렬 호출 | 현재 target + 선택된 전체 schema/idx |

모든 variant는 같은 대화와 같은 router 결과를 받는다. 데이터는 공개 Hermes Function Calling의 multi-tool 샘플이다. `single`은 원본 요청, `multi_derived`는 요청을 이전 대화에 두고 마지막 질문에서 재참조하도록 만든 파생 조건이다. 실제 다중 사용자 질문, 조건 변경, 충돌 해결을 포함한 native multi-turn 평가는 아니다.

`oracle`은 정답 idx로 리라이팅을 비교하고, `predicted`는 LLM router가 생성한 idx를 재사용해 전체 파이프라인을 비교한다. 제공된 첫 배열은 oracle, 두 번째 배열은 predicted로 해석했다.

## 주요 지표

- **Tool argument exact**: 선택된 tool별 query만 보고 공통 decoder가 복원한 arguments가 gold와 strict 일치하는 비율. tool 기준 micro 평균이다.
- **All-gold exact**: tool idx set과 모든 gold tool의 arguments가 동시에 일치한 샘플 비율.
- **Valid**: rewrite 출력 JSON, idx coverage, 비어 있지 않은 한 줄 query 형식의 유효성. 의미적 정확성과 별개다.
- **Latency**: rewrite 단계의 병렬 critical-path 추정 시간. router/decoder 시간을 포함하지 않는다.
- **Tokens**: 한 샘플의 rewrite 호출 전체에 걸친 입력/출력 토큰 합계.

## Oracle 결과: 각 조건 100개, 완료

### 품질

| 조건 | Variant | n | Router exact | Valid | Tool argument exact | All-gold exact |
|---|---|---:|---:|---:|---:|---:|
| Single | joint | 100 | 100% | 97% | 70.40% | 57% |
| Single | per_tool_local | 100 | 100% | 100% | 71.48% | 57% |
| Single | per_tool_with_list | 100 | 100% | 99% | 72.56% | 58% |
| Multi derived | joint | 100 | 100% | 99% | 70.04% | 53% |
| Multi derived | per_tool_local | 100 | 100% | 100% | 71.12% | 56% |
| Multi derived | per_tool_with_list | 100 | 100% | 99% | 70.76% | 54% |

### 지연과 토큰

| 조건 | Variant | 평균 지연(s) | p95(s) | 평균 입력 토큰 | 평균 출력 토큰 |
|---|---|---:|---:|---:|---:|
| Single | joint | 3.328 | 11.510 | 919.02 | 168.31 |
| Single | per_tool_local | 2.765 | 8.500 | 1,588.47 | 185.85 |
| Single | per_tool_with_list | 3.566 | 5.222 | 2,639.80 | 140.74 |
| Multi derived | joint | 3.511 | 6.382 | 878.40 | 176.87 |
| Multi derived | per_tool_local | 2.541 | 5.231 | 1,747.05 | 156.92 |
| Multi derived | per_tool_with_list | 2.401 | 5.083 | 2,758.79 | 147.09 |

### 해석

**joint는 입력 토큰 효율이 가장 좋았다.** local 대비 single 입력 토큰은 약 42.1%, multi derived는 약 49.7% 적었다. with_list 대비는 각각 약 65.2%, 68.2% 적었다. 출력 토큰까지 포함한 실제 비용은 모델 가격과 caching에 따라 달라진다.

**per_tool_local은 관측된 품질·형식 안정성·평균 지연의 균형이 좋았다.** single all-gold exact는 joint와 같은 57%, multi derived는 3%p 높은 56%였다. 두 조건에서 valid는 100%였다. 평균 지연은 joint 대비 single 약 16.9%, multi derived 약 27.6% 낮았다.

**전체 선택 목록을 추가한 효과는 일관되지 않았다.** with_list는 single에서 local 대비 all-gold exact +1%p, tool argument exact +1.08%p였지만, multi derived에서는 각각 −2%p, −0.36%p였다. 입력 토큰은 local 대비 single 약 66.2%, multi derived 약 57.9% 늘었다. 이번 결과에서는 추가 목록의 품질 이득이 비용 증가를 뚜렷하게 설명하지 못했다.

single all-gold exact는 57–58%, multi derived는 53–56%였다. 같은 source에서 만들어진 paired 조건이므로 이 차이를 실제 멀티턴 난도 증가의 일반적인 효과로 해석할 수 없다. 1–3%p 차이의 통계적 유의성도 현재 집계만으로 판단할 수 없다.

## Predicted 결과: 진행 중인 중간 집계

single은 variant별 n=32–33, multi derived는 n=32다. 완주 결과가 아니며, single에서는 마지막 샘플의 일부 variant만 완료된 상태로 보인다. 같은 샘플 집합으로 완료한 뒤 최종 비교해야 한다.

### 품질

| 조건 | Variant | n | Router exact | Valid | Tool argument exact | All-gold exact |
|---|---|---:|---:|---:|---:|---:|
| Single | joint | 33 | 75.76% | 100% | 73.81% | 42.42% |
| Single | per_tool_local | 33 | 75.76% | 100% | 77.38% | 45.45% |
| Single | per_tool_with_list | 32 | 75.00% | 100% | 75.31% | 43.75% |
| Multi derived | joint | 32 | 78.13% | 100% | 71.43% | 43.75% |
| Multi derived | per_tool_local | 32 | 78.13% | 100% | 71.43% | 40.63% |
| Multi derived | per_tool_with_list | 32 | 78.13% | 100% | 73.81% | 43.75% |

### 지연과 토큰

| 조건 | Variant | 평균 지연(s) | p95(s) | 평균 입력 토큰 | 평균 출력 토큰 |
|---|---|---:|---:|---:|---:|
| Single | joint | 2.592 | 8.596 | 746.70 | 128.91 |
| Single | per_tool_local | 2.986 | 7.948 | 1,423.36 | 141.33 |
| Single | per_tool_with_list | 2.239 | 4.247 | 2,287.53 | 117.94 |
| Multi derived | joint | 2.420 | 4.613 | 782.59 | 106.44 |
| Multi derived | per_tool_local | 2.348 | 5.126 | 1,591.50 | 144.22 |
| Multi derived | per_tool_with_list | 2.411 | 7.055 | 2,483.22 | 128.25 |

### 해석

router exact는 약 75–78%로, router idx set이 gold와 다른 샘플은 all-gold exact에서 실패한다. 다만 exact만으로 누락과 과잉 선택 중 무엇이 주요 원인인지 구분할 수 없다.

predicted의 tool argument exact가 일부 oracle 수치보다 높아도 전체 성능 향상을 뜻하지 않는다. 현재 평가 샘플 수가 다르고, 분모가 선택된 tool이므로 누락된 gold tool은 해당 tool별 지표에서 직접 실패로 계산되지 않는다. 누락은 all-gold exact에 반영된다.

single에서는 local의 all-gold exact가 가장 높고, multi derived에서는 joint/with_list가 높았다. 적은 중간 표본에서의 결과로 최종 순위를 정하기 어렵다. Oracle와의 차이를 router의 순수 손실로 계산하려면 동일 source_id 집합으로 재집계해야 한다.

## 평가의 한계

**Strict exact는 자유 텍스트의 표면 차이에도 실패한다.** 확인된 첫 샘플에서는 `notes`의 문장 끝 마침표 하나 때문에 tool 0이 실패했고, 나머지 tool 1·2는 정확했다. 전체 샘플은 False였다. 따라서 현재 exact 수치는 의미적으로 정확한 리라이팅까지 실패로 포함할 수 있다.

strict 지표를 유지하면서 자유 텍스트 필드에만 제한적인 공백·문장 끝 마침표 정규화를 적용한 보조 지표를 추가하는 것이 적절하다. ID·날짜·수치·enum·코드 등의 값은 strict 비교를 유지해야 한다. 정규화 점수도 완전한 semantic accuracy는 아니다. 저장된 decoder_details로 API 재호출 없이 재채점할 수 있지만, 현재 제공된 summary는 재채점 이전 수치다.

arguments 복원에는 별도의 LLM decoder를 사용하므로 이 지표는 **리라이팅 + decoder의 downstream proxy**다. 실패가 리라이팅의 정보 손실인지 decoder의 복원 오류인지 집계만으로 분리할 수 없다.

Latency는 호출별 semaphore 대기와 재시도를 포함한 시간의 max를 사용하는 추정값이다. provider load, concurrency, 재시도와 캐시의 영향을 받는다. 특히 작은 표본의 p95 차이는 반복 측정으로 확인해야 하며, 서비스 전체 latency나 SLA로 해석할 수 없다.

## 현재 선택과 후속 검증

| 우선순위 | 현재 후보 | 근거 |
|---|---|---|
| 입력 토큰 절감 | joint | Oracle 두 조건에서 가장 적은 입력 토큰 |
| 형식 안정성과 평균 지연 | per_tool_local | Oracle valid 100%, joint보다 낮은 평균 지연 |
| 전체 목록을 통한 task 분배 | per_tool_with_list | 품질 이득이 조건별로 달라 추가 검증 필요 |

현재 관측치에서는 **joint와 per_tool_local을 주요 후보로 유지**할 수 있다. with_list의 추가 토큰을 정당화하는 일관된 품질 개선은 확인되지 않았다.

후속 검증은 predicted 완주, 동일 source_id로 paired 비교, 자유 텍스트 정규화 재채점, 실패 샘플의 정보 손실/decoder 오류 분류 순서로 진행한다. 실제 multi-user 대화에서는 조건 수정·참조 해결·이전 tool 결과 의존성을 포함한 별도 평가가 필요하다.
