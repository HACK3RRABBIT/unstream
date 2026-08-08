# Ship it as an app people run themselves

Unstream is open source under MIT, and `docker compose up` is a supported way to use it rather than a development convenience. The hosted instance at unstream.amiralibg.xyz continues, but it is one deployment of the project and not the project.

## Why

YouTube treats a datacenter address and a home address as different things. From a VPS it answers `LOGIN_REQUIRED` at the playability check — before a proof-of-origin token is asked for and before a JS challenge exists to solve — so the two defences the API image carries cannot reach the point where they would help. Signing in with cookies moves yt-dlp onto the web clients where those defences do matter, which is why all three are needed together on the server and why none of them are needed on a laptop.

That asymmetry is not a bug to fix in this codebase. It is the reason the same code works instantly on a home connection and fails on a rented one, and it makes "run it yourself" the configuration where the project is at its best: no bot checks, no shared account carrying everyone's downloads, no operator standing between users and the files.

The legal shape agrees. A public downloader concentrates every request onto one operator; the same software, distributed, puts each person in charge of what they download. That is the position yt-dlp, spotDL and MeTube occupy, and it is a well-understood one.

## The hosted instance

Keeping unstream.amiralibg.xyz working for strangers needs egress from a non-datacenter address — a residential or ISP proxy, which costs money. That is planned, not ruled out. It changes nothing about the code: `compose.dokploy.yml` already keeps the per-caller limits hardcoded on, and those limits stop being decorative the moment the public instance can actually serve people.

This supersedes an earlier reading in which paying for egress was ruled out on principle. The keyless constraint ([ADR 0004's sibling, stated in the README](../../README.md)) is about *metadata providers* — no account, no API key, nothing that can be revoked or start charging per call. Renting an IP address does not touch that.

## Rejected

**A split architecture**, with the public frontend on the VPS and a download worker on a home connection. It gets a residential IP for free and is strictly worse: every stranger's download traces to one home ISP account, the upload link becomes the bottleneck, and the liability lands on a person rather than a host.

**A SoundCloud-only public demo.** SoundCloud is unaffected by all of this, so a public instance could have offered full search with downloads restricted to it. Honest, free and permanently working — but a downloader that mostly cannot download is a poor advertisement, and a proxy solves it properly.

## Consequences

- `docker-compose.yml` is the self-hosting file and must work on a fresh clone with no `.env`, no external network and no file mounts. `compose.dokploy.yml` is the deployment. Changes to one are not automatically right for the other.
- Defaults differ deliberately by audience: the code's defaults are public-safe (downloads expire, disk is capped, limits are tight) and the self-hosting compose file overrides them toward "this is my machine". Anything new with a limit attached needs a decision on both sides.
- The bot-check apparatus — deno, the challenge solver, the PO token provider — stays in the image even though most self-hosters will never need it. It costs nothing idle, and the day YouTube starts asking a home address for a token is not a day to spend reading documentation.
- Documentation is now addressed to someone installing this, not to the person who wrote it.
