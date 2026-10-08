"""
tests/test_routing.py — does every way of asking land on the right feature?

    python backend/tests/test_routing.py            # offline: no AI calls, a few seconds
    python backend/tests/test_routing.py --live     # through the real classifier (~15 min, spaced
                                                    # out to stay inside Groq's free per-minute limit)

Offline mode makes every classifier model "unavailable", which is what
happens on the free tier when questions come quickly — so it checks the
instant rules plus the keyword fallback that answers in that case. Live
mode checks the classifier's own judgement. VIT cases need VTOP connected
(backend/.env); with it off they'd all be "feature_off" by design.
"""

import os
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

from tests.routing_cases import CASES, HELDOUT   # noqa: E402
import core.router as router                      # noqa: E402


def main() -> int:
    live = "--live" in sys.argv
    from config import VTOP_ENABLED
    if not VTOP_ENABLED:
        print("VTOP isn't connected in backend/.env, so the VIT cases can't be checked.")
        return 1
    if not live:
        router.extract_general_intent_groq = lambda text: None

    failures, cases = [], CASES + HELDOUT
    for question, want in cases:
        started = time.time()
        category, payload = router.route(question)
        if category not in want:
            failures.append(f"  {question!r} -> {category} {payload or ''}   (want {' / '.join(sorted(want))})")
        if live and time.time() - started > 0.3:      # a model was called: leave room under the rate limit
            time.sleep(4)

    print("\n".join(failures) if failures else "")
    print(f"{'live' if live else 'offline'}: {len(cases) - len(failures)}/{len(cases)} routed correctly")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
