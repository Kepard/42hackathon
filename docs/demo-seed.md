# Fixed-seed demonstration

Use Java Edition **1.21.1**, seed **`-1950884254474434485`**, Survival,
Easy difficulty, default world generation, no cheats, and no bonus chest.
World name: `HoloSpeed Demo`.

This seed and version appear in verified competitive set-seed runs, including
[KenanKardes's 1:15.970 run](https://www.speedrun.com/mc/runs/y9w6v92z) and
[Nonsochiama's 1:17.847 run](https://www.speedrun.com/mc/runs/y6jgg76m).
The record route is useful future research; it is not a claim that HoloSpeed
can reproduce it, or that this is the easiest ordinary early-game route.

The initial agent objective remains acquired logs → crafting table → wooden
pickaxe → stone pickaxe and axe. Current state-aware decisions use player
coordinates, inventory, sampled blocks/entities, and optional screenshots;
normal interactions and Baritone execute the selected skills. This changes the
information available to the agent from the earlier visual-only trials.
Recreate the same settings for comparable attempts, and keep resolution, GUI
scale, render distance, difficulty, and gameplay mode consistent. Record the seed
explicitly rather than treating a declared configuration value as a world query.

Earlier control validation used seed `42`; keep those smoke-test results
separate from the selected demo seed. A successful bridge test verifies
controls and screenshots, not an autonomous gameplay milestone.
