# Secrets and private corpus

- **Never commit** corpus PDFs, corpus manifests, document contents, extraction
  output, API keys, tokens, or `.env`. The corpus and its manifest (which holds
  the digest salt) live **outside the repository**.
- Keep `.env`, `data/profiles/`, `uploads/`, `*.pdf` and `.streamlit/secrets.toml`
  out of Git; they are already in `.gitignore`. Do not remove those entries.
- Never print, log or paste document text, claimant names, claim numbers or
  amounts into reports, commits, PR bodies or issues. The corpus gate reports
  ids, counts and keyed digests only — follow that rule everywhere.
- Profiles store **structure only**; never claim data. The whitelist in
  `core/profiles.py` raises rather than dropping extra fields — never bypass it.
- If a secret is ever committed, stop and report it; do not simply delete the
  line and continue.