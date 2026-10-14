# Ask StockRoom eval

Unit tests mock Gemini, so they prove the plumbing but not that real phrasing
picks the right action. `ask_eval` runs a fixed set of messages through the
real model and scores it, the way `backtest_forecast` scores the forecast.

```bash
python manage.py seed_demo      # the cases use the demo practice's items
python manage.py ask_eval [--min 90]
```

- It needs `AI_API_KEY`. Without one it says so and exits 0. It makes one real
  call per case, so it isn't run in CI.
- Each case goes through `assistant/gemini.py` with the same prompt and the
  same role-filtered functions the chat uses, as the demo's manager or
  assistant.
- A case passes when the model calls exactly the expected functions with the
  expected items and numbers, or, for "no action" cases, answers in plain
  text (an assistant asking about money must get the usual refusal).
- It prints each miss, then a score per action, and exits 1 if any action is
  under `--min` (default 90).

## The cases

`assistant/eval_cases.py`: message, role, the calls expected, and text the
answer must contain. Keep them like the practice talks: NZ phrasing ("chucked
out", "were out of"), typos, and numbers in words. Every action keeps some
"no action" neighbours too (questions, chit-chat, clinical questions, an
assistant asking about spending), so the model isn't rewarded for calling
anything that sounds close.

## The rule

**Each action needs 90% or better here before its issue closes.** An issue
that adds an action adds its own cases to `eval_cases.py`, runs this, and puts
the score in the issue. Changing the chat's prompt or an action's declaration
means running it again.
