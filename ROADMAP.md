# Roadmap

## Next
- [ ] Resume format and readability review (ATS and human readers) with ATS-safe export templates.
- [ ] Skills-gap dashboard across saved listings; application tracker.
- [ ] "Delete all my data" button in the app (the backend method exists).
- [ ] Improve redaction recall: non-US locations, schools and employers without keyword hints, common false misses.
- [ ] Tune the meaning-match threshold on real listings (it was calibrated on 9 pairs).
- [ ] macOS and Linux builds and testing (only Windows has been tested).

## Possible later
- A "copy the redacted prompt" fallback for people without Claude Code (paste into any chatbot, paste the reply back).
- An MCP server over the local database (considered; headless Claude was chosen instead, see docs/LOG.md).
- Email-alert scanning (opt-in), optional local-LLM adapter.
- scikit-learn (role clustering, learned scoring), PyTorch (fine-tuning), a web framework (other-device use).
