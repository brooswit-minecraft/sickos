# Illagers-only radar

The root category uses `ALL_BUT` with exactly four registry IDs:
`minecraft:pillager`, `minecraft:vindicator`, `minecraft:evoker`, and
`minecraft:illusioner`. The root exclusion prevents fallback into another
subcategory. Items, players, ravagers, vexes, and other entities are excluded.
Tracked-player minimap and in-world overlays are disabled separately.

The existing Xaero Minimap 26.6.0 native NeoForge 1.21.1 jar includes XaeroLib
1.7.3. Installing it on both sides enables server profiles; World Map remains
client-only. `default_enforced_profile` selects the profile ID `default`.
The explicit large-value JSON beside each profile uses the on-disk path and
pointer syntax verified in these installed jars. The client profile does not
bypass enforcement for users with profile-edit permission.

Client defaults and the explicit client profile supply the same filter when
playing without server enforcement. The server profile supplies the root filter
and tracked-player settings after the server component is deployed and restarted.
This is a cooperative Xaero configuration feature, not protection against
modified clients.

## Verification and rollout boundary

Read-only server inventory run 38460043132 found no Xaero mods or configuration.
The live 0.54.0 server therefore cannot enforce this profile yet. This draft
does not deploy or restart it. In-game rendering and server enforcement still
need acceptance testing after an authorized rollout.

For the separate local 0.54.0 test, only the default radar JSON root exclusion
and two tracked-player flags were edited, with original files backed up. Xaero
keeps its category tree in memory: external file edits are not proof of an active
filter. Relaunch the client when convenient, then check a scene containing an
allowed illager plus an item and another entity. If the running client rewrites
its old settings on exit, reapply the saved filter after closing it and before
launching. No client relaunch was performed by this task.
