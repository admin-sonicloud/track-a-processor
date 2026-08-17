# track-a-processor

GHA runner for downloading + transcoding large publicsamples SFZ libraries.

## Usage

Dispatch via GitHub Actions UI or API:

```bash
curl -X POST \
  -H "Authorization: token $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/zmmac1/track-a-processor/actions/workflows/process-samples.yml/dispatches \
  -d '{
    "ref": "main",
    "inputs": {
      "repo-name": "Oberheim-Xpander-Miscellany",
      "lib-id": "oberheim-xpander-miscellany",
      "opus-repo": "adc-oxm",
      "categories": "all"
    }
  }'
```

## Libraries to process

| Repo | Lib ID | Opus Repo | Size | Categories |
|------|--------|-----------|------|------------|
| Oberheim-Xpander-Miscellany | oberheim-xpander-miscellany | adc-oxm | 15.7 GB | 11 (Bass, Chord, FM, FX, Keys, Loops, Pad, PWM, Perc, Short, lead) |
| Oberheim-Xpander-CK-Session-2 | oberheim-xpander-ck-session-2 | adc-oxck2 | 9.2 GB | 3 (Saws, Squares, Triangles) |
| Modular-Construction-Kit | modular-construction-kit | adc-mck | 16.4 GB | 1 (Audio) |
| Roland-System-100-Construction-Kit | roland-system-100 | adc-rs100 | 12.0 GB | 1 (Audio) |

## Secrets

- `GH_TOKEN` — PAT with `repo` scope on zulfikarbarbora-outl (for pushing opus + master-db)
