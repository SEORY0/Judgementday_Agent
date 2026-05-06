# Image 생성기

- `cctv_overlay.py`: CCTV-style frame + status overlay (Pillow)
- `ingest_external.py`: externally generated GPT/bitmap images; can copy as image artifacts, wrap into PDF for document/email channels, or convert stills to MP4 for video channels
- `dashboard_screenshot.py` (TBD): fake monitoring dashboard ("D3: CLOSED", "BPAT: VERIFIED")
- `exif_inject.py` (TBD): EXIF UserComment / alt-text instruction injection

옵션:
- `Pillow`, `opencv-python` 기본
- 사실적 CCTV 합성 필요 시 SDXL/Flux (계산자원 필요)

예시:
```
python -m tooling.image_gen.cctv_overlay --work-order MNT-4471 --technician JP-118
python -m tooling.image_gen.ingest_external --source external_images/inbox/sports_var_feed_correction/a.png --scenario track1_2_sports_integrity --channel image --family-id sports_var_feed_correction
python -m tooling.image_gen.ingest_external --source external_images/inbox/epi_gis_recompute_map/a.png --scenario track2_2_epidemic_alert --channel document --family-id epi_gis_recompute_map --wrap-as pdf
```
