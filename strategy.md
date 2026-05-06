# 운영 전략 (plan의 실행판)

원본 plan: [`/home/seory0/.claude/plans/https-judgementday-aim-intelligence-com-expressive-church.md`](/home/seory0/.claude/plans/https-judgementday-aim-intelligence-com-expressive-church.md)

## 4단계 일정

| Phase | Day | 날짜 | 핵심 산출물 |
|---|---|---|---|
| 1. Recon & Wiring | 1–4 | 4/30–5/3 | 8 brief, bounty matrix, 도구 동작 확인 |
| 2. Pattern Library | 5–10 | 5/4–5/9 | attack_taxonomy.md 9 family × 3 instantiation |
| 3. Sweep | 11–25 | 5/10–5/24 | 일 30–60 제출, breach 누적 100+ |
| 4. High-Bounty 정예화 | 26–32 | 5/25–5/31 | high-multiplier 5조합 집중, writeup 정비 |

## 일일 루틴 (Phase 3)

1. 전날 `attempts.jsonl` → 결과 sync (riser/declined 분류)
2. 오늘의 2개 시나리오 회전 슬롯 확인 ([scenarios](scenarios/) 우선순위 표)
3. 모델 6종 × family 3–5개 sweep (일 30–60 제출)
4. `dedupe.py`로 사전 검사 (cosine ≥ 0.85면 변형 강제)
5. `notes/daily_log.md`에 무엇을 했는지, 새 family·관찰 기록
6. `leaderboard_log.md`에 순위·breach·점수 기록

## 의사결정 규칙

- **family 폐기 기준**: 같은 family를 시나리오 3개 × 모델 4개 시도해서 breach 0이면 폐기
- **family 확장 기준**: family 1개가 모델 2개 이상에서 통하면 변형 5–10개 자동 생성 후 sweep
- **새 family 도입 금지**: 마감 24시간 전부터는 검증된 family 변형만 제출
- **rate limit 대응**: 429 시 exponential backoff (60s → 5min → 30min). 일일 제출 상한 잠정 200

## 우선순위 회전 (Phase 3)

| 라운드 | Day | 시나리오 슬롯 |
|---|---|---|
| 1 | 5/10–5/12 | track2_0 (SOC), track2_1 (Aircraft) |
| 2 | 5/13–5/15 | track1_1 (Robot), track1_2 (Sports) |
| 3 | 5/16–5/18 | track1_3 (Dam), track2_2 (Epidemic) |
| 4 | 5/19–5/21 | track2_3 (Deepfake), track1_0 (Triage) |
| 5 | 5/22–5/24 | 직전 4라운드 weakest cell 보강 |

2026-04-30 18:22 KST bounty matrix 기준으로 갱신. 100x cells는 track2_0 SOC (Gemini audio, Claude text)와 track2_1 Aircraft (Gemini audio, Claude document).

## High-Bounty 집중 후보

| 우선 | Cell | 이유 |
|---:|---|---|
| 1 | track2_0 SOC / Gemini-3.1-pro audio / 100x | Slack huddle + fake CMDB/tool-result spoof 조합 |
| 2 | track2_0 SOC / Claude-Opus-4.6 text / 100x | text DM은 제작비 낮고 반복 sweep 가능 |
| 3 | track2_1 Aircraft / Gemini-3.1-pro audio / 100x | maintenance chief + BPAT sync/outage narrative |
| 4 | track2_1 Aircraft / Claude-Opus-4.6 document / 100x | SIL PDF + fake BPAT verification artifact |
| 5 | track1_1 Robot / Gemini-3.1-pro audio / 57x | official maintenance exception을 음성으로 보강 |
