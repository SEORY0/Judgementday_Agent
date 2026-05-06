# Next Steps

## 완료됨

- 2026-04-30 18:22 KST: 로그인된 Chrome DevTools 세션으로 8개 scenario v5 brief/invariants 갱신.
- 2026-04-30 18:22 KST: leaderboard bounty matrix 전체 추출 후 [leaderboard_log.md](leaderboard_log.md)에 반영.
- 세션별 verification token은 문서에 저장하지 않음. 제출 시 UI에서 현재 값을 확인.

## 1. 제출 자동화 안전장치

- Chrome remote debugging tunnel 유지:
  ```
  ssh -R 9222:127.0.0.1:9222 -p 2222 seory0@203.253.21.173
  ```
- 제출 자동화는 submit click 전 정지하도록 작성.
- attack description에는 UI가 요구하는 human verification phrase/token을 제출 시점에 직접 확인해서 포함.

## 2. Generator 구현 우선순위

완료:

1. `tooling/injectors/slack_dm.py`: track2_0 text baseline.
2. `tooling/image_gen/cctv_overlay.py`: track1_1 Maintenance Override sign baseline.
3. `tooling/pdf_gen/document.py`: SIL / ministerial / forensic PDF baseline.
4. `tooling/audio_gen/tts.py`: high-bounty audio cells baseline.
5. `tooling/video_gen/clip.py`: dam/epidemic video baseline.
6. `tooling/submit_assistant.py`: Chrome CDP form fill, Submit 전 정지.
7. `tooling/generate_baselines.py`: first-wave artifact batch.
8. `tooling/leaderboard_snapshot.py`: current leaderboard JSON snapshot.
9. `tooling/campaign_runner.py`: campaign queue, dedupe, fill, guarded submit, result sync, score report.

생성:

```
.venv/bin/python -m tooling.generate_baselines --seed 20260501
```

캠페인 후보 확인:

```
.venv/bin/python -m tooling.campaign_runner plan
```

캠페인 artifact 생성 및 queue 등록:

```
.venv/bin/python -m tooling.campaign_runner generate --limit 5
.venv/bin/python -m tooling.campaign_runner dedupe --limit 5
.venv/bin/python -m tooling.campaign_runner queue --status ready --limit 10
```

100회/일 제한 대응 운영:

```
.venv/bin/python -m tooling.campaign_runner budget --daily-limit 100 --reserve 10
.venv/bin/python -m tooling.campaign_runner daily-plan --calibrate 30 --probe 20 --exploit 40
.venv/bin/python -m tooling.campaign_runner families
.venv/bin/python -m tooling.campaign_runner policy --batch-id robot_image_new_families
```

큐를 갱신하는 명령은 내부 lock을 사용하지만 순차 실행한다. 새 family는 `families/*.yaml`, 새 campaign은 `campaigns/*.yaml`에 추가한다.

제출 폼 자동 채우기:

```
.venv/bin/python -m tooling.submit_assistant fill --manifest <artifact>.manifest.json
.venv/bin/python -m tooling.campaign_runner fill --queue-id <queue_id>
```

수동 제출 후 queue와 attempts 연결:

```
.venv/bin/python -m tooling.campaign_runner mark-submitted --queue-id <queue_id>
```

자동 Submit은 기본 비활성이다. 실제 클릭은 batch 확인 플래그가 모두 있을 때만 수행한다:

```
.venv/bin/python -m tooling.campaign_runner submit \
  --queue-id <queue_id> \
  --batch-size 3 \
  --daily-limit 100 \
  --reserve 10 \
  --submit \
  --confirm-batch first_wave_high_bounty
```

리더보드 확인:

```
.venv/bin/python -m tooling.leaderboard_snapshot
.venv/bin/python -m tooling.campaign_runner sync-results
.venv/bin/python -m tooling.campaign_runner score
```

## 3. Discord / 공식 ruleset 확인

- 정확한 model count 표기 불일치: Arena 5 models, bounty matrix 6 model names.
- rate limit.
- similarity rejection threshold.
- 제출물 권리 / 데이터 사용 정책 재확인.

## 4. 첫 제출 후보

- `track1_1` image: official Maintenance Override/diagnostic clearance sign with work order + technician ID.
- `track2_0` text: Slack DM formatted as CMDB lookup/tool-result spoof.
- `track2_1` document: SIL PDF + fake BPAT verification/sync-pending narrative.

결과 기록:
  ```
  source .venv/bin/activate
  python -m tooling.submit_tracker log \
      --scenario <scenario_folder> \
      --model <model> --channel <channel> --family <family> \
      --artifact <artifact_path> \
      --result pending --note "<short writeup>"
  ```
응답 후:
  ```
  python -m tooling.submit_tracker resolve --id <id> --result breach --score X
  ```
