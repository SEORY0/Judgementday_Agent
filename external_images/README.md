# External GPT Image Workflow

Use this when images are generated outside the local Pillow templates.

1. Pick a prompt from `prompts/gpt_multitrack_visual_prompts.md`.
2. Save generated images under `external_images/inbox/<family_id>/`.
   - Image tracks: one image per file.
   - Video tracks: put 3-5 related stills in the same family folder and build with `--group-by-dir`.
   - PDF/email tracks: one or more related images can be wrapped into a PDF packet.
3. Build a campaign from the inbox:

```bash
python -m tooling.campaign_runner build-external-campaign \
  --recursive \
  --infer-family-from-dir \
  --output campaigns/gpt_multitrack_external.yaml \
  --name gpt_multitrack_external
```

For video still folders:

```bash
python -m tooling.campaign_runner build-external-campaign \
  --recursive \
  --infer-family-from-dir \
  --group-by-dir \
  --output campaigns/gpt_multitrack_video.yaml \
  --name gpt_multitrack_video
```

Then run:

```bash
python -m tooling.campaign_runner generate --campaign campaigns/gpt_multitrack_external.yaml
python -m tooling.campaign_runner dedupe --batch-id gpt_multitrack_external --fail-on-warn
python -m tooling.campaign_runner submit --batch-id gpt_multitrack_external --limit 5
```

The last command is a dry run unless `--submit --confirm-batch gpt_multitrack_external` is added. `dedupe_warn` records are not selected by default.
