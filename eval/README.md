# Eval benchmark

Photos with known-truth nutrition, used by `scripts/run_eval.py` to score
the pipeline: kcal error (MAPE), food-naming hit rate, DB match rate.

## Layout

- `photos/` — the benchmark images (jpg/png)
- `truth.csv` — the answer key, one row per photo
- `reports/` — timestamped JSON results, one per run (git-ignore if noisy)

## truth.csv columns

| column     | meaning                                              |
| ---------- | ---------------------------------------------------- |
| filename   | file inside `photos/`, e.g. `lasagna_frozen.jpg`     |
| true_kcal  | the real calories of what's in the photo             |
| true_name  | what the dish really is, plain words ("beef lasagna")|
| notes      | free text: where the truth came from                 |

## How to collect honest truth

- **Packaged meals**: cook, plate, photograph; the box states kcal.
- **Fast food / chains**: published nutrition data; photograph as served.
- **Home meals**: weigh ingredients, sum kcal from labels; more work,
  most valuable — closest to real user meals.

Conventions: photograph BEFORE eating, include a fork/hand when natural
(that's what real users do), typical phone angle, no studio staging.
10-15 photos = usable signal; 30+ = trustworthy. Mix cuisines and
containers (plates AND bowls — bowls are where portion error hides).

## Running

    python scripts/run_eval.py            # all rows
    python scripts/run_eval.py --limit 5  # first 5 only

Cost: one real model call per photo (~$0.001, a few seconds). The scan
cache is bypassed so every run measures the model, not yesterday's answer.
