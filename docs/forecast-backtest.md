# Forecast backtest

`python manage.py backtest_forecast [--org "Practice name"]` replays each
practice's history through `stock.forecast` and prints how accurate it would
have been. It's the gate for any change to the forecast (issue #41's Croston
and Holt-Winters included): **a new model ships only if it beats the moving
average here on real pilot data, and doesn't get worse on the seeded data.**

## Method (`stock/backtest.py`)

- **Cutoffs:** every week, stepping back from the practice's newest event.
  At each cutoff the forecast sees only what was known then, including only
  the orders that were open then.
- **Prediction:** the forecast's weekly usage × 2, meaning use over the next 2 weeks.
- **Actual:** the use over those 2 weeks, measured in hindsight from the
  counts either side of them, the same way the forecast measures consumption.
  A cutoff with no count 2+ weeks after it can't be measured, so it's skipped.
- **MAE / bias:** per item, in the item's own unit per 2 weeks. Bias is the
  mean of predicted − actual, so a negative bias means it under-predicted.
  The overall figure is MAE as a share of actual use (WAPE), so items with
  different units add up.
- **Stock-outs:** a count of 0 or "used the last one" when there was stock
  before. A stock-out is **missed** if, the supplier's lead time before it
  happened, the forecast still said OK, which is too late to order.

## Baseline: moving average on seeded data

Recorded 13 Sep 2026 on `seed_demo` data, which gives the same numbers after
any reseed:

```
Overall: 559 forecasts, off by 21% of actual use (bias -17%). 1 stock-out, 0 missed.
```

The per-item table is in the command's output. The biggest misses in units
are the high-use items: prophy paste cups (MAE 50.5 on 163.5 used),
autoclave pouches (17.9 on 107.8) and digital sensor barriers (13.5 on 81.7).

**Why it under-predicts.** Bias by how much history the forecast had:

| History at the cutoff | 0 weeks | 1 | 2 | 3–7 | 10–12 |
|---|---|---|---|---|---|
| Bias | −53% | −27% | −21% | −9% to −14% | −6% to −7% |

1. New items. With little history, the forecast leans on taps, and taps only
   catch some use (`seed_demo` taps about a third of it, and real staff tap
   some of the time). Every item starts at once in the seed, as it will in a
   pilot, so these forecasts are a big share of the total.
2. The part-week since the last count. It has only taps, which is about −7%
   once history is long. How far a cutoff is from a count moves the overall
   bias between −18% and −13%.

These are the obvious targets for a better model. Seeded data is synthetic,
with ±15% noise around a fixed rate, so check them against pilot data first.

## Pilot data

No pilot has run yet (issue #39). Once one has, run the command against
production and record its table here as the real baseline:

```
railway ssh python manage.py backtest_forecast --org "Pilot practice name"
```
