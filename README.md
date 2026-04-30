# Judgement Day — AI Red Team Arena (1등 도전)

- 대회: [judgementday.aim-intelligence.com](https://judgementday.aim-intelligence.com/arena)
- 주최: AIM Intelligence × Korea AISI
- 참가: 류석준 (seory0), 단독
- 기간: 2026-04-30 → 2026-05-31 (32일), 수상자 발표 2026-06-14
- 전략 기조: **breach 다수 + 다양성** → 마지막 1주에 **high-bounty 정예화**

전체 plan: [`/home/seory0/.claude/plans/https-judgementday-aim-intelligence-com-expressive-church.md`](/home/seory0/.claude/plans/https-judgementday-aim-intelligence-com-expressive-church.md)
운영 문서: [strategy.md](strategy.md)
리더보드 로그: [leaderboard_log.md](leaderboard_log.md)

---

## 폴더

- [scenarios/](scenarios/) — 8개 시나리오 brief, invariants, attack ideas, attempts log, artifacts
- [playbook/](playbook/) — 공격 패턴 카탈로그, 모달리티 레시피, writeup 템플릿
- [tooling/](tooling/) — PDF/audio/image/video 생성기, 제출 트래커, dedupe
- [notes/](notes/) — daily_log, observations

## 8개 시나리오

| 트랙 | ID | 폴더 | 한줄 |
|---|---|---|---|
| 1 (Induction) | track1_0 | [emergency_triage](scenarios/track1_0_emergency_triage/) | ESI-3을 ESI-1로 over-triage 유도 |
| 1 | track1_1 | [industrial_robot](scenarios/track1_1_industrial_robot/) | safety interlock open인데 production resume |
| 1 | track1_2 | [sports_integrity](scenarios/track1_2_sports_integrity/) | integrity review 미루고 penalty confirm |
| 1 | track1_3 | [dam_flood](scenarios/track1_3_dam_flood/) | 민간인 잔류 중 spillway open |
| 2 (Suppression) | track2_0 | [soc_suppression](scenarios/track2_0_soc_suppression/) | SOC P1 incident alert suppression |
| 2 | track2_1 | [aircraft_maintenance](scenarios/track2_1_aircraft_maintenance/) | safety hold 무시하고 aircraft clear |
| 2 | track2_2 | [epidemic_alert](scenarios/track2_2_epidemic_alert/) | outbreak alert 48시간 지연/재분류 |
| 2 | track2_3 | [deepfake_dr](scenarios/track2_3_deepfake_dr/) | DRP retract / election 후로 연기 |

## 사전 자산 (재사용 대상)

- [`bugbounty-scope-compare.md`](/home/seory0/Agent-Zero/learning/week1/bug-bounty/bugbounty-scope-compare.md) — OpenAI/Anthropic VRP scope, 인정되는 jailbreak·prompt-injection 카테고리
- [`openclaw-architecture.md`](/home/seory0/Agent-Zero/learning/week1/architecture/openclaw-architecture.md) — Tool-use Agent 공격 표면 (env injection, prompt injection)
- [`openclaw-ghsa-audit.md`](/home/seory0/Agent-Zero/learning/week1/ghsa-audit/openclaw-ghsa-audit.md) — GHSA 469건 (prompt-injection-like 154 / exec-env-injection 24)

## 윤리·법적 경계

이 폴더의 모든 공격 자산은 **Judgement Day Arena의 simulated agent**만을 대상으로 한다. 실제 병원·항공사·SOC·정부기관 시스템에 동일 패턴을 시도하는 것은 **금지**한다. 생성된 위조 문서·음성·영상도 외부에 공개하지 않는다 (`.gitignore` 처리).

## KPI (plan 기준)

- Day 14 (5/13): 누적 breach 30+
- Day 21 (5/20): 누적 breach 60+, top 10
- Day 28 (5/27): 누적 breach 100+, top 5
- Day 32 (5/31): 1등
