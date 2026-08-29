#!/bin/sh
# Resume the WorldPop national raster. The previous command used -o without
# -C -, so every retry restarted from byte 0; that is why 125 MB was lost.
URL="https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/BSGM/IND/ind_ppp_2020_constrained.tif"
OUT="data/ind_national.tif"
TOTAL=531062384
prev=-1
for i in $(seq 1 15); do
  sz=$(stat -c %s "$OUT" 2>/dev/null || echo 0)
  echo "[attempt $i] local=$sz / $TOTAL ($((sz*100/TOTAL))%)"
  if [ "$sz" -ge "$TOTAL" ]; then echo "COMPLETE at $sz bytes"; exit 0; fi
  if [ "$sz" -eq "$prev" ]; then
    echo "STALLED: no progress at byte offset $sz across two attempts. Stopping."
    exit 2
  fi
  prev=$sz
  curl -C - -fL -o "$OUT" \
    --retry 5 --retry-delay 10 --speed-time 60 --speed-limit 1000 \
    "$URL" || echo "  curl exited $? — retrying with resume"
done
sz=$(stat -c %s "$OUT" 2>/dev/null || echo 0)
[ "$sz" -ge "$TOTAL" ] && echo "COMPLETE at $sz bytes" || echo "INCOMPLETE at $sz after 15 attempts"
