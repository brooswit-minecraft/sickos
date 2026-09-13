# Sickos agent instructions

Read CONTRIBUTING.md before changing pack content or selecting a release version.
The user explicitly wants meaningful semantic versions, not automatic patch bumps.
During 0.x: fixes bump patch; features and breaking changes bump minor, with breaking
changes explicitly labeled and migration documented. After 1.0, breaking changes
bump major. Documentation/listing/CI-only edits need no pack release.

Preserve published versions. Use pack.toml and the existing CI release/deployment
chain. Check client/server dependency sides and report only verification actually
performed; server readiness alone does not prove the client can launch or join.

Only add mods hosted on Modrinth with native NeoForge builds for Minecraft 1.21.1.
Do not add CurseForge-only jars or Fabric compatibility bridges.
