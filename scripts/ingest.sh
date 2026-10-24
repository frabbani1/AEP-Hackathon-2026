#!/usr/bin/env bash
# Upload the original files, untouched, to the raw-sensitive bucket and prove the upload matches local.
#
#   export RAW_BUCKET=aep-hack-raw-<account-id>      (plus AWS credentials and AWS_REGION=us-east-2)
#   scripts/ingest.sh                                 # upload + verify
#   DRYRUN=1 scripts/ingest.sh                        # preview only
#   INGEST_DATE=2026-10-24 scripts/ingest.sh          # pin the date folder (default: today, UTC)
#
# Layout: s3://$RAW_BUCKET/raw/<dataset>/<date>/...
#   Data1, Data2         shapefile sets (customer, ARCOS damage, devices, lines, facility layers)
#   matched_upstream     premise -> upline topology (TXT)
#   labor                Install_Labor_HackOHIO.xlsx
#   drawings             structure_drawings.pdf
set -euo pipefail

: "${RAW_BUCKET:?set RAW_BUCKET}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATE="${INGEST_DATE:-$(date -u +%Y-%m-%d)}"
DRY=(); [[ "${DRYRUN:-0}" == "1" ]] && DRY=(--dryrun)

# 1. Refuse to upload Git LFS pointer stubs instead of real tables.
stubs=0
while IFS= read -r -d '' f; do
  if [[ $(wc -c <"$f") -lt 1024 ]] && head -c 64 "$f" | grep -q "git-lfs"; then echo "LFS pointer: $f" >&2; stubs=1; fi
done < <(find "$ROOT/data" -name '*.dbf' -print0)
[[ $stubs == 0 ]] || { echo "run 'git lfs pull' first" >&2; exit 1; }

echo "account: $(aws sts get-caller-identity --query Account --output text)  bucket: $RAW_BUCKET  date: $DATE"

# dataset name | local dir | include pattern ("" = whole dir)
DATASETS=(
  "Data1|$ROOT/data/Data1|"
  "Data2|$ROOT/data/Data2|"
  "matched_upstream|$ROOT/data/matched_upstream|"
  "labor|$ROOT/data|Install_Labor_HackOHIO.xlsx"
  "drawings|$ROOT/docs|structure_drawings.pdf"
)

fail=0
for entry in "${DATASETS[@]}"; do
  IFS='|' read -r name dir include <<<"$entry"
  dest="s3://$RAW_BUCKET/raw/$name/$DATE/"
  filt=(--exclude ".DS_Store")
  [[ -n "$include" ]] && filt=(--exclude "*" --include "$include")
  if [[ ${#DRY[@]} -gt 0 ]]; then
    echo "$name -> $dest"; aws s3 sync "$dir" "$dest" "${filt[@]}" --dryrun | sed 's/^/  /'; continue
  fi
  aws s3 sync "$dir" "$dest" "${filt[@]}" --only-show-errors

  # 2. Verify: same file count and total bytes as local.
  if [[ -n "$include" ]]; then
    l_n=1; l_b=$(stat -f %z "$dir/$include")
  else
    l_n=$(find "$dir" -type f ! -name .DS_Store | wc -l | tr -d ' ')
    l_b=$(find "$dir" -type f ! -name .DS_Store -exec stat -f %z {} + | awk '{s+=$1} END{print s+0}')
  fi
  read -r r_n r_b < <(aws s3api list-objects-v2 --bucket "$RAW_BUCKET" --prefix "raw/$name/$DATE/" \
      --query '[length(Contents), sum(Contents[].Size)]' --output text)
  status=OK; [[ "$l_n" == "$r_n" && "$l_b" == "$r_b" ]] || { status=MISMATCH; fail=1; }
  printf "%-17s local %4s files %12s bytes | s3 %4s files %12s bytes  %s\n" "$name" "$l_n" "$l_b" "$r_n" "$r_b" "$status"
done

[[ $fail == 0 ]] || { echo "ingest verification FAILED" >&2; exit 1; }
[[ ${#DRY[@]} -gt 0 ]] && echo "DRY RUN: nothing uploaded" || echo "ingest verified: s3://$RAW_BUCKET/raw/*/$DATE/"
