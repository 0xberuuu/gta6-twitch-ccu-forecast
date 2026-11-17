# GTA VI launch-day Twitch viewers, hour by hour

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/0xberuuu/gta6-twitch-ccu-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/0xberuuu/gta6-twitch-ccu-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Entry for the [make.fun bounty gta6-twitch-ccu-forecast-01](https://make.fun/bounty/gta6-twitch-ccu-forecast-01):
forecast how many people watch Grand Theft Auto VI live on Twitch in each
hour of its release day (2026-11-19 UTC) after the first, from everything
recorded up to the end of hour 1, as five quantiles an hour. Scored by mean
pinball loss on the log scale.

## How it works

`main.py` (Python 3.13, standard library only) reads every `*.in` case in
`$MAKEFUN_INPUT_DIR` (else `/input/`) and writes one forecast per case to
`$MAKEFUN_OUTPUT_DIR` (else `/output/`).

1. **Peer selection.** Each comparable launch is reduced to its release-day
   curve. GTA VI unlocks at midnight local time, rolling country by country,
   so its hour 1 already holds a large audience. The peers kept are the
   launches whose hour-1 print already reached more than 15% of their
   release-day peak — the signature of a rolling-midnight unlock. Launches
   that unlock all at once later in the UTC day (a tiny hour-1 print, then a
   vertical ramp) are a different regime and are left out.
2. **Scale-conditioned shape.** For each hour 2–24, the log ratio of the
   hour's viewers to the hour-1 print is regressed on the log hour-1 print
   across the peers. Bigger launches have flatter days, and the regression
   carries that (correlation ≈ −0.8). The target's log hour-1 print is
   winsorised to the peers' observed range, so a launch bigger than any peer
   gets the flattest observed shape rather than an extrapolation.
3. **Calibrated spread.** Each hour's quantiles are the fitted log ratio plus
   normal quantiles times that hour's residual spread (inflated 10%, floored
   at 0.20 log units), centred on the case's own hour-1 print.
4. **Fallbacks.** If the peer pool is too small the model widens to every
   usable comparable, then to pooled median ratios, then to a persistence
   forecast; any unexpected error also lands on persistence, so every case
   always yields a valid forecast.

Everything is computed from the case file at run time; nothing is hard-coded
from any particular launch, and the program is deterministic (two runs on
the same case write identical bytes) and fast (well under the 8-second cap).

## Local validation

Leave-one-out over the 26 rolling-unlock comparables in the kit (each held
out in turn, forecast from the remaining comparables, scored against its own
recorded release day):

- **Model: mean pinball loss 0.20, typical miss ≈ 38%, 90% coverage ≈ 0.92**
- House persistence baseline on the same pseudo-cases: loss 0.28

The four practice cases all produce valid, deterministic forecasts.

## Run locally

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

## License

MIT — see [LICENSE](LICENSE).
