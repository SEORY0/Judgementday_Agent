# Video 생성기

- `clip.py`: 정적 HUD 이미지 → 짧은 mp4 (ffmpeg)
- `drone_overlay.py` (TBD): drone-style HUD overlay 합성

핵심:
- 짧은 clip (10–30s)이 검수도 빠르고 제출도 쉬움
- ffmpeg로 mux: `ffmpeg -framerate 30 -i frame_%04d.png -i narration.mp3 -c:v libx264 -shortest out.mp4`

예시:
```
python -m tooling.video_gen.clip --preset dam_drone --duration 12
python -m tooling.video_gen.clip --preset epidemic_briefing --duration 12
```
