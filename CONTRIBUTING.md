# Contributing to Sickos

## Version policy

Use [Semantic Versioning](https://semver.org/) as a player-facing compatibility
policy. `pack.toml` is the version source; GitHub tags use `vMAJOR.MINOR.PATCH`.
Compatibility means existing worlds and builds remain usable without a reset,
manual data migration, or loss of registered blocks/items. It does not promise
that clients on different pack versions can connect: use the same release on
client and server even for patches.

| Change | Bump | Examples |
| --- | --- | --- |
| Restore intended behavior without adding features | Patch | Missing client dependency, crash fix, compatible mod bugfix, broken recipe correction |
| Add compatible player-facing functionality | Minor, reset patch to zero | New content mod such as Diesel Generators, new default server list, additive recipes or configuration features |
| Break the compatibility contract | Major after 1.0; see 0.x below | Remove a mod with placed blocks, require a world reset/migration, change loader or incompatible Minecraft version, deliberately invalidate established builds/progression |
| Documentation, listing copy, or CI maintenance with no pack-content change | No pack bump | CONTRIBUTING edits, Modrinth description sync, workflow repair |

Judge dependency updates by their effect on Sickos, not by the dependency's own
version number. Routine compatible tuning may be a patch; deliberate new gameplay
is minor, and invalidating existing builds/progression is breaking. New terrain
in unexplored chunks can be minor when old chunks remain valid; document seams,
exploration requirements, and any required migration. Use the highest-impact
category when batching changes.

### Before 1.0

Sickos is still in initial development. Our explicit `0.y.z` convention is:

- Compatible fixes: `0.2.10` -> `0.2.11`.
- New features: `0.2.10` -> `0.3.0`.
- Breaking changes: also advance minor during `0.x`, with **BREAKING** release
  notes and concrete migration/reset instructions. Do not hide them in patches.
- `1.0.0` declares the first stable compatibility baseline; it is not an automatic
  consequence of adding another mod. From then on, breaking changes advance major.

Early releases through `0.2.10` used patch numbers for features, including Default
Options and Diesel Generators. Preserve those published releases and tags. Apply
this policy to future releases; do not republish or renumber existing artifacts.

## Release process

1. Classify the change and explain the selected bump in the commit or PR. For
   breaking changes, include the migration plan and backup/rollback implications.
2. Update `pack.toml` once per release, pin compatible mods, and verify client/server
   side metadata for every dependency.
3. Run `make refresh`, stage the intended pack changes, then `make check` and
   `make build`. Inspect the exported `.mrpack` for the required files and sides.
4. Push the reviewed change to `main`. CI creates the GitHub release, publishes to
   Modrinth, and triggers hosted deployment. Do not create releases manually.
   (For a Dynamic Atmosphere bump specifically, steps 1-3 can be prepared by
   `scripts/bump-dynamic-atmosphere.py` -- see `docs/da-auto-bump.md`. If that
   commit carries a `DA-Auto-Bump` trailer, a follow-up workflow also edits the
   release body -- see `docs/release-notes-plumbing.md`.)
5. Verify release and deployment results, including restart and health. Distinguish
   a successful server start from an actual client launch/join test in reporting.

The current workflow reads the version from `pack.toml`; it does not infer the
semantic category from commit messages. Contributors and agents select the bump.
Pack-content changes need a fresh version; documentation-only changes do not.
Keep repository instructions and documentation excluded by `.packwizignore`.
