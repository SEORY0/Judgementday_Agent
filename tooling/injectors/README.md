# Text payload 빌더

- `slack_dm.py` (TBD): Slack DM 형식 (timestamp, user@host, signature)
- `email.py` (TBD): email body + signature + invisible quote
- `templates/` (TBD): jinja2 템플릿

핵심:
- 본문은 자연스럽게, injection은 signature/quote/blockquote에 숨김
- 격식·언어(한/영) 변형 축
