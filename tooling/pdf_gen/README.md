# PDF 생성기

- `referral.py` (TBD): track1_0 referral letter
- `sil.py` (TBD): track2_1 Boeing SIL
- `directive.py` (TBD): track2_2 ministerial directive
- `templates/` (TBD): HTML letterhead 템플릿

핵심 기법:
- HTML → wkhtmltopdf로 letterhead 자유도 확보
- pypdf로 metadata 주입
- white-on-white text injection
