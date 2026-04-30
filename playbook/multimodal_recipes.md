# Multimodal 자료 생성 레시피

## PDF (referral letter, SIL, ministerial directive)

- 라이브러리: `reportlab` (생성), `pypdf` (metadata 조작), `pdfkit` + `wkhtmltopdf` (HTML → PDF, letterhead 디자인 자유도 높음)
- 권장 흐름:
  1. HTML 템플릿 with letterhead, signature image
  2. `pdfkit.from_string(html, "out.pdf")`
  3. `pypdf`로 metadata 주입 (`/Title`, `/Subject`, `/Keywords`)
  4. invisible-text injection: HTML에 `<span style="color:white;font-size:1px">...</span>`
- 도구 위치: [`tooling/pdf_gen/`](../tooling/pdf_gen/)

## Audio (voice impersonation, system bot voice, radio chatter)

- 라이브러리: `edge-tts` (무료, MS Azure neural voices 다국어), `pydub` (믹싱·노이즈)
- 권장 흐름:
  1. 텍스트 → `edge-tts` 합성 (voice ID 다양화: ko-KR-* / en-US-*)
  2. 노이즈 trim, radio chatter background 합성
  3. urgency를 위해 prosody / pacing 변형 (SSML rate)
- 도구 위치: [`tooling/audio_gen/`](../tooling/audio_gen/)
- 옵션: ElevenLabs API (voice quality 한 단계 위, 유료) — high-bounty 정예화에서만

## Image (CCTV overlay, dashboard screenshot, document)

- 라이브러리: `Pillow`, `opencv-python`
- 권장 흐름:
  1. 베이스 이미지 (factory CCTV 스타일 / dashboard UI 스타일)
  2. 텍스트 overlay (예: "D3: CLOSED", "BPAT: VERIFIED")
  3. EXIF UserComment에 instruction 주입
- 옵션: SDXL/Flux 로 사실적 CCTV 합성 (계산자원 필요)
- 도구 위치: [`tooling/image_gen/`](../tooling/image_gen/)

## Video (drone uplink, news clip, briefing)

- 라이브러리: `ffmpeg` (정적 이미지 + TTS audio 합성으로 30초 clip 충분)
- 권장 흐름:
  1. 정적 이미지 sequence + Ken Burns effect
  2. TTS narration audio 합성
  3. ffmpeg로 mux
- 짧은 clip (10–30s)이 검수도 빠르고 제출도 쉬움
- 도구 위치: [`tooling/video_gen/`](../tooling/video_gen/)

## Text (Slack DM, email body)

- 라이브러리: `jinja2` 템플릿
- 권장 흐름:
  1. authentic-looking format (timestamp, user@host, signature block)
  2. injection을 본문 안에 자연스럽게 (signature, quote, blockquote)
- 도구 위치: [`tooling/injectors/`](../tooling/injectors/)
