"""Run assistant/eval_cases.py through the real model and score it, the way
backtest_forecast scores the forecast. Unit tests mock Gemini, so they prove
the plumbing; this proves real phrasing picks the right action.
See docs/ask-eval.md.
"""

import sys

import djclick as click
from django.conf import settings

from accounts.models import Organisation, User
from assistant import actions, gemini
from assistant.eval_cases import CASES
from assistant.views import _system_prompt
from stock.matching import Matcher
from stockroom.demo import DEMO_ORG


def _same_call(expected, name, args, matcher):
    if name != expected["action"]:
        return False
    for key, want in expected.items():
        if key == "action":
            continue
        got = args.get(key)
        if key == "item":
            found = matcher.match(str(got or "")).item
            ok = found is not None and found.name == want
        elif isinstance(want, str):
            ok = str(got or "").strip().lower() == want.lower()
        else:
            ok = got == want
        if not ok:
            return False
    return True


def _passes(case, reply, matcher):
    """Whether reply, gemini.generate's answer (a list of calls or text), is what the case wants."""
    if not isinstance(reply, list):
        return not case.calls and case.says in reply
    left = list(reply)
    for want in case.calls:
        hit = next((got for got in left if _same_call(want, *got, matcher)), None)
        if hit is None:
            return False
        left.remove(hit)
    return not left and not case.says


@click.command()
@click.option("--min", "minimum", default=90, show_default=True, help="Fail below this percent for any action.")
def command(minimum):
    """Score Ask StockRoom's choice of action against the seeded demo practice."""
    if not settings.AI_API_KEY:
        click.echo("AI_API_KEY isn't set, so there's no model to score. Nothing run.")
        return
    org = Organisation.objects.filter(name=DEMO_ORG).first()
    if org is None:
        raise click.ClickException(f'No "{DEMO_ORG}" practice. Run: python manage.py seed_demo')
    users = {role: User.objects.filter(organisation=org, role=role).first() for role in ("admin", "assistant")}
    prompts = {role: (_system_prompt(user), actions.declarations(user)) for role, user in users.items()}
    matcher = Matcher(org)

    totals = {}  # action, or "none" for a plain answer -> [passed, cases]
    for case in CASES:
        system, tools = prompts[case.role]
        try:
            reply = gemini.generate(system, [("user", case.message)], tools=tools)
        except gemini.GeminiError:
            reply = "(no answer from the model)"
            passed = False
        else:
            passed = _passes(case, reply, matcher)
        for action in {c["action"] for c in case.calls} or {"none"}:
            score = totals.setdefault(action, [0, 0])
            score[0] += passed
            score[1] += 1
        if not passed:
            wanted = list(case.calls) or "a plain answer" + (f" saying {case.says!r}" if case.says else "")
            click.echo(f"MISS ({case.role}) {case.message!r}\n  wanted: {wanted}\n  got:    {reply!r}")

    click.echo(f"\n{'Action':<20} {'Passed':>8} {'Score':>6}")
    failed = []
    for action, (passed, total) in sorted(totals.items()):
        percent = 100 * passed / total
        click.echo(f"{action:<20} {f'{passed}/{total}':>8} {percent:>5.0f}%")
        if percent < minimum:
            failed.append(action)
    if failed:
        click.echo(f"\nBelow {minimum}%: {', '.join(failed)}")
        sys.exit(1)
