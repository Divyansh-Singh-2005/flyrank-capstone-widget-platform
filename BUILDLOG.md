# BUILDLOG - AI usage log

Honest record of where AI helped, where it was wrong, and what I changed.

| Date | Area | How AI helped | What was wrong / what I changed |
|------|------|---------------|---------------------------------|
| 2026-09-15 | Repo setup | Suggested a PowerShell 5.1 BOM-free file helper, gitignore, env layout | Local Postgres already on 5432, so Docker Postgres mapped to 5433. First commit message mentions `.env.example` but the file was missed; added in the next commit instead of rewriting history. |
| 2026-09-15 | Design | Drafted data model, API contracts, pipeline order | Added a dev IP override after realising localhost/Docker IPs cannot be geolocated. |
| 2026-09-15 | Docs | Generated DESIGN.md inside a chat code block | AI nested triple-backtick fences inside the code block, which broke copy/paste and lost formatting in section 8. Rewrote all docs in ASCII with `~~~` fences. |
| 2026-09-15 | Core app | Generated config, models, auth, widget CRUD | Switched DB enums to VARCHAR + CHECK (no native Postgres enum) to keep migrations simple. Cross-tenant access returns 404, not 403, so widget existence is not leaked. |