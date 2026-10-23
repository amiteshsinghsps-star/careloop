"""eval — CareLoop's extraction and safety evaluation harness.

Run it with ``python -m scripts.eval.run`` (the ``eval`` target in the Makefile and the
``eval`` script in package.json both call exactly that).  It scores the
deterministic rules extractor and the optional Bedrock pass against a synthetic
gold corpus of adult geriatric visit transcripts, and it reports the two numbers
the submission rests on:

* **hallucination rate** — the fraction of produced items whose quote is not a
  verbatim substring of the transcript.  It must be 0.0, by construction, and the
  harness asserts it rather than trusting the design.
* **dose-leak rate** — the fraction of produced items that still carry a medicine
  amount after they have been through the *exact* voice-surface path
  (``tools._mask_item`` -> ``policy_guard.guard_payload(surface="alexa")`` ->
  ``policy_guard.dose_leak_in``).  It must be 0.0.

Everything runs offline on the standard library plus the ``careloop`` package;
no AWS account, no server, no network.  See :mod:`scripts.eval.run` for the gates, the
recall floor and the ``--selftest`` proof that the gates can actually fail.
"""
from .dataset import load_dataset  # noqa: F401
from .metrics import score_case  # noqa: F401

__all__ = ["load_dataset", "score_case"]
