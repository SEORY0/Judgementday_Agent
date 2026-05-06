# Text payload 빌더

- `slack_dm.py`: Slack DM 형식 (timestamp, user@host, fake CMDB/tool result)
- `email.py` (TBD): email body + signature + invisible quote
- `templates/` (TBD): jinja2 템플릿

핵심:
- 본문은 자연스럽게, injection은 signature/quote/blockquote에 숨김
- 격식·언어(한/영) 변형 축

예시:
```
python -m tooling.injectors.slack_dm --variant json_tool --seed 20260501
```
