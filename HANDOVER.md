# HANDOVER.md

Överlämning mellan agenter och sessioner. Läs innan du börjar, uppdatera innan du slutar.

## Aktuellt läge

*Senast inventerat 2026-11-04 av Claude (Cowork).*

Projekt: **A secure runtime for autonomous AI workers.**

- Huvudbranch: `main`
- Senaste commit på `main`: 2026-11-01 `1675245` Add privacy guard for commits and CI (#2)
- Öppna PR:er: 0
- Andra brancher på remote: 2

## Pågående arbete

| Agent/vem | Branch | Status | Beskrivning |
|-----------|--------|--------|-------------|
| Cursor | `cursor/privacy-guard-6b61` | branch utan PR, senaste commit 2026-11-01 | Add privacy guard for commits and CI |
| Cursor | `feat/durable-sqlite-replay` | branch utan PR, senaste commit 2026-10-16 | style: fix pre-existing ruff findings |

## Nästa steg

- Gå igenom brancher utan PR: öppna PR, merga eller radera (fråga användaren innan radering).
- Fyll på "Bygga och testa" i AGENTS.md om något saknas eller är fel.

## Kända problem

- Inga dokumenterade ännu.

## Senaste commits

- 2026-11-01 `1675245` Add privacy guard for commits and CI (#2)
- 2026-10-16 `994b1ec` Merge pull request #1 from jojo-swe/feat/durable-sqlite-replay
- 2026-10-16 `8833941` style: fix pre-existing ruff findings
- 2026-10-15 `c77be5a` docs: document durable recovery guarantees and complete 0.2 store roadmap
- 2026-10-15 `b4a51ef` feat(runtime): recover pending deliveries and budgets from event history
- 2026-10-15 `f4568a4` feat(events): add atomic append-only SQLite event store
- 2026-07-18 `34c4ef5` Record capability security decision
- 2026-07-18 `f3409eb` Record event-sourcing decision
- 2026-07-18 `ee7de58` Document system architecture
- 2026-07-18 `bcafb92` Add core invariant tests

## Logg

| Datum | Agent | Vad som gjordes |
|-------|-------|-----------------|
| 2026-11-04 | Claude (Cowork) | Lade till AGENTS.md, HANDOVER.md och CLAUDE.md; inventerade PR:er, brancher och commits. |
