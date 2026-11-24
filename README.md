# Black-Friday-Sucher

Privater Deal-Feed: Der **Motor** (`motor/motor.py`) holt alle 15 Minuten neue Deals aus den mydealz-RSS-Feeds und bewertet sie mit einer Ampel. Die **Website** (`docs/`) zeigt sie an.

## Ampel
- **Grün:** heiß (von der Community hochgewählt) und laut Deal-Post Bestpreis/Tiefstpreis
- **Gelb:** heiß, aber ohne Beleg aus dem Preisverlauf, oder Bestpreis laut Post, aber noch nicht heiß
- **Rot (ausgeblendet):** nicht heiß und kein Beleg, z. B. nur gegen UVP gerechnet

## Dateien
- `docs/deals.json` – schreibt der Motor
- `docs/claude-deals.json` – schreibt der tägliche Claude-Check
- `docs/profiles.json` – Stichworte und Budget pro Person (Filter + Handy-Alarm)

## Handy-Alarm
Repo-Secret `NTFY_TOPIC` setzen (Settings → Secrets and variables → Actions) und den gleichen Kanal in der ntfy-App abonnieren.
