# BUILDLOG — AI usage log

Honest record of where AI helped, where it was wrong, and what I changed.

| Date | Area | How AI helped | What was wrong / what I changed |
|------|------|---------------|---------------------------------|
| 2026-09-15 | Repo setup | Suggested PowerShell 5.1 BOM-free file helper, gitignore, env layout | Local Postgres already on 5432, so Docker Postgres mapped to 5433. First commit message mentions `.env.example` but the file was missed; added in the next commit instead of rewriting history. |
| 2026-09-15 | Design | Drafted data model, API contracts, pipeline order | Added dev IP override after realising localhost/Docker IPs cannot be geolocated. |