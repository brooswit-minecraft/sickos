import unittest
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"


class TestModAutoBumpPollCheckoutPinning(unittest.TestCase):
    """MINECRAFT-362 review, seventeenth round, P1/P2: without an explicit
    `ref: main`, actions/checkout defaults to the triggering event's own
    ref/sha. For `workflow_dispatch` that is WHATEVER ref the dispatch
    call named -- an arbitrary feature branch, not necessarily main --
    and this workflow then pushes whatever it generates on top of that
    checkout to main, fast-forwarding that branch's own unrelated
    ancestors along with the intended bump commit. The same missing
    `ref` also means a queued or re-run job keeps the ORIGINAL event's
    sha even after a later run in the same concurrency group has already
    pushed, so it would recompute against a stale base. Pinning
    `ref: main` fixes both: this test guards against either regressing
    silently (a YAML change is not caught by any Python-level test
    otherwise, since nothing here exercises the real checkout action)."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = (WORKFLOWS_DIR / "mod-auto-bump-poll.yml").read_text()

    def test_checkout_step_pins_ref_to_main(self):
        checkout_block = self.workflow.split("- uses: actions/checkout@v4", 1)[1].split("- name:", 1)[0]
        self.assertIn("ref: main", checkout_block)


class TestDaAutoBumpCheckoutPinning(unittest.TestCase):
    """Same staleness fix as TestModAutoBumpPollCheckoutPinning above,
    for da-auto-bump.yml's checkout step (MINECRAFT-362 review,
    seventeenth round, P2). repository_dispatch always targets the
    default branch, so this file has no ref-injection exposure the way
    mod-auto-bump-poll.yml's workflow_dispatch trigger does -- only the
    stale-rerun-checkout defect applies here."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = (WORKFLOWS_DIR / "da-auto-bump.yml").read_text()

    def test_checkout_step_pins_ref_to_main(self):
        checkout_block = self.workflow.split("- uses: actions/checkout@v4", 1)[1].split("- name:", 1)[0]
        self.assertIn("ref: main", checkout_block)


if __name__ == "__main__":
    unittest.main()
