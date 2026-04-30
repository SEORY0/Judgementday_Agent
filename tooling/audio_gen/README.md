# Audio 생성기

- `tts.py` (TBD): edge-tts wrapper. voice catalog와 SSML rate/pitch 옵션
- `radio.py` (TBD): radio chatter background mix (pydub)
- `voices.yaml` (TBD): 시나리오별 권장 voice id

핵심:
- 한국어 voice (ko-KR-InJoonNeural, ko-KR-SunHiNeural 등)
- 영어 voice (en-US-GuyNeural, en-GB-RyanNeural, en-US-DavisNeural)
- emotional prosody는 SSML `<prosody rate="fast" pitch="+5%">` 활용
