# Security and privacy

## Where your data goes

Everything runs on your own machine. The single exception is the plan-reading step: the
PDFs you choose are sent to Anthropic's API using **your** key, and the schedules come
back. Nothing is sent to Homeplay, and there is no server, account or telemetry in this
tool.

If a client's drawings may not leave your machine, do not use the plan-reading step —
write the schedule CSVs by hand (`docs/INSTRUCTIONS.md` documents every column) and use
the review and build steps, which are entirely offline.

## Your API key

- Stored in the app's own config folder on your machine, and nowhere else.
- Never written to the repository, the generated project, or any log.
- If you think it has leaked, revoke it at
  [console.anthropic.com](https://console.anthropic.com) — that is instant and free.

## Please don't commit client work

A `.hw` file contains a client's entire design; schedule CSVs contain their room names
and fittings. `.gitignore` is set up to keep both out, but it is worth knowing why. When
reporting a problem, send a redacted crop and the `EXTRACTION-REPORT.txt`, not the job.

## Reporting a vulnerability

Email **hello@homeplay.tv** rather than opening a public issue. This is a small tool
with no server and no user accounts, so the realistic surface is narrow — but if you
find a way it could leak a key or a client's data, please say so directly.
