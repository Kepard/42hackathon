package ai.holospeed.client;

import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import net.minecraft.block.Blocks;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.item.Item;
import net.minecraft.item.Items;
import net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket;
import net.minecraft.registry.tag.FluidTags;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.GameMode;
import net.minecraft.world.RaycastContext;
import net.minecraft.world.World;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.Map;
import java.util.HashMap;

/** Builds every frame block from natural lava using normal, server-checked bucket interactions. */
final class CastPortalSkill {
    private static final Set<String> MATERIAL_IDS = Set.of("minecraft:dirt", "minecraft:cobblestone");
    private static final Set<String> ENTRY_PHASES = Set.of("enter_portal", "in_portal", "changing_dimension");
    private final MinecraftClient client;
    private SkillController.BaritoneAccess navigation;
    private Object originWorld, originPlayer, connection;
    private String status = "idle", phase = "idle", reason, pendingResult, pendingReason;
    private long deadline, transitionAt, updateAfter, inventoryAfter;
    private int elapsed, phaseAt, maxTicks, originalSlot = -1, moldIndex, frameIndex, removalIndex, enterAttempts, entryNudges;
    private int jumpStarted = -1, stepStarted, bucketCountBefore;
    private boolean jumpHeld, forwardHeld, breaking;
    private CastPortalPlan plan;
    private BlockPos selectedSource, pendingBlock, navigationGoal;
    private String navigationMode = "idle";
    private int navigationStarted, navigationAttempts, settledTicks, localStarted, localProgressAt, localIndex;
    private double localBestDistance;
    private boolean localReachesGoal;
    private List<CastPortalPlan.Point> localRoute = List.of();
    private final Set<BlockPos> rejectedFluidStands = new HashSet<>();
    private Item pendingMaterial;
    private final List<BlockPos> lava = new ArrayList<>(), water = new ArrayList<>();
    private final Set<BlockPos> collectedLava = new HashSet<>(), placedMold = new HashSet<>(), castBlocks = new HashSet<>();
    private final Set<String> verified = new LinkedHashSet<>();

    CastPortalSkill(MinecraftClient client) { this.client = client; }
    boolean active() { return status.equals("running"); }
    String status() { return status; }
    String phase() { return phase; }
    String reason() { return reason; }
    boolean permitsWorldTransition() { return active() && ENTRY_PHASES.contains(phase) && client.getNetworkHandler() == connection; }
    List<String> verifiedSteps() {
        if (client.getNetworkHandler() != connection) return List.of();
        if (client.world != originWorld && !permitsWorldTransition()
            && !(status.equals("completed") && client.world != null && client.world.getRegistryKey().equals(World.NETHER))) return List.of();
        return List.copyOf(verified);
    }

    void start(PortalRoute.Point target, int ticks) throws ReflectiveOperationException {
        if (!ready() || !client.world.getRegistryKey().equals(World.OVERWORLD)) throw new IllegalStateException("focused Overworld survival client required");
        requireClosed();
        if (count(Items.BUCKET) + count(Items.WATER_BUCKET) == 0 || count(Items.FLINT_AND_STEEL) == 0
            || count(Items.STONE_PICKAXE) == 0 || materials() < CastPortalPlan.MATERIALS)
            throw new IllegalStateException("casting requires a bucket or water bucket, flint and steel, stone pickaxe, and nine dirt/cobblestone");
        BlockPos landmark = new BlockPos(target.x(), target.y(), target.z());
        if (client.player.getPos().squaredDistanceTo(Vec3d.ofCenter(landmark)) > 128 * 128)
            throw new IllegalStateException("natural lava landmark is more than128 blocks away");
        originWorld = client.world; originPlayer = client.player; connection = client.getNetworkHandler();
        originalSlot = client.player.getInventory().selectedSlot;
        maxTicks = ticks; deadline = System.nanoTime() + (ticks * 50L + 2000) * 1_000_000;
        elapsed = 0; transitionAt = 0; moldIndex = frameIndex = removalIndex = enterAttempts = 0;
        plan = null; navigationGoal = selectedSource = null; navigationMode = "idle"; localRoute = List.of();
        rejectedFluidStands.clear();
        lava.clear(); water.clear(); collectedLava.clear(); placedMold.clear(); castBlocks.clear(); verified.clear();
        discoverSources(landmark);
        if (lava.size() < 10) throw new IllegalStateException("ten exposed natural lava sources were not observed near the landmark");
        if (water.isEmpty() && count(Items.WATER_BUCKET) == 0) throw new IllegalStateException("no exposed natural water source observed within24 blocks");
        plan = findPlan(landmark);
        if (plan == null) throw new IllegalStateException("no clear dry five-by-six construction site outside the lava flood radius");
        status = "running"; reason = null;
        navigation = new SkillController.BaritoneAccess();
        if (!navigation.cancel()) throw new IllegalStateException("could not obtain casting controls");
        verified.add("lava_located");
        if (count(Items.WATER_BUCKET) > 0) { verified.add("water_collected"); beginSite(); }
        else beginFluid(false);
    }

    private boolean ready() {
        return client.player != null && client.world != null && client.interactionManager != null
            && client.getNetworkHandler() != null && client.player.isAlive() && client.isWindowFocused()
            && !client.isPaused() && client.interactionManager.getCurrentGameMode() == GameMode.SURVIVAL;
    }

    private void discoverSources(BlockPos landmark) {
        // Bounded loaded client cells only: 49*17*49 = 40,817. No chunk requests.
        for (BlockPos position : BlockPos.iterate(landmark.add(-24, -8, -24), landmark.add(24, 8, 24))) {
            if (!loaded(position) || !client.world.getBlockState(position.up()).isAir()) continue;
            var fluid = client.world.getFluidState(position);
            if (!fluid.isStill()) continue;
            if (fluid.isIn(FluidTags.LAVA) && Math.abs(position.getX() - landmark.getX()) <= 12
                && Math.abs(position.getZ() - landmark.getZ()) <= 12) lava.add(position.toImmutable());
            else if (fluid.isIn(FluidTags.WATER)) water.add(position.toImmutable());
        }
        Comparator<BlockPos> nearest = Comparator.comparingDouble(p -> client.player.getPos().squaredDistanceTo(Vec3d.ofCenter(p)));
        lava.sort(nearest); water.sort(nearest);
        if (lava.size() > 256) lava.subList(256, lava.size()).clear();
        if (water.size() > 256) water.subList(256, water.size()).clear();
    }

    private CastPortalPlan findPlan(BlockPos landmark) {
        Map<CastPortalPlan.Point, CastWaterSafety.Cell> terrain = new HashMap<>();
        List<BlockPos> candidates = new ArrayList<>();
        for (int y = Math.max(-63, landmark.getY() - 1); y <= Math.min(313, landmark.getY() + 4); y++)
            for (int x = -20; x <= 20; x++) for (int z = -20; z <= 20; z++)
                candidates.add(new BlockPos(landmark.getX() + x, y, landmark.getZ() + z));
        candidates.sort(Comparator.comparingDouble(p -> client.player.getPos().squaredDistanceTo(Vec3d.ofCenter(p))));
        int checked = 0, flowChecks = 0;
        for (BlockPos origin : candidates) {
            if (++checked > 10086) break;
            for (int rotation = 0; rotation < 4; rotation++) {
                CastPortalPlan candidate = new CastPortalPlan(new CastPortalPlan.Point(origin.getX(), origin.getY(), origin.getZ()), rotation);
                if (validSite(candidate)) {
                    if (++flowChecks > 64) return null;
                    if (safeWaterFlow(candidate, terrain)) return candidate;
                }
            }
        }
        return null;
    }

    private boolean validSite(CastPortalPlan candidate) {
        for (CastPortalPlan.Point foot : candidate.waterfallFeet()) for (BlockPos source : lava)
            if (Math.abs(foot.x() - source.getX()) + Math.abs(foot.z() - source.getZ()) < 9) return false;
        for (int x = -1; x <= 3; x++) for (int z = -1; z <= 4; z++) {
            BlockPos floor = pos(candidate.at(x, 0, z));
            if (!loaded(floor) || !client.world.getBlockState(floor).isFullCube(client.world, floor)
                || !client.world.getFluidState(floor).isEmpty() || client.world.getBlockState(floor).isOf(Blocks.OBSIDIAN)
                || client.world.getBlockState(floor).isOf(Blocks.CRYING_OBSIDIAN)) return false;
            for (int y = 1; y <= 6; y++) {
                BlockPos cell = floor.up(y);
                var state = client.world.getBlockState(cell);
                if (!state.isAir() && !softPlant(cell, state)) return false;
            }
        }
        for (CastPortalPlan.Point point : candidate.frame()) {
            var state = client.world.getBlockState(pos(point));
            if (state.isOf(Blocks.OBSIDIAN) || state.isOf(Blocks.NETHER_PORTAL)) return false;
            if (point.y() == candidate.origin.y() && (state.getHardness(client.world, pos(point)) < 0
                || state.getHardness(client.world, pos(point)) > 3)) return false;
        }
        return true;
    }

    private boolean safeWaterFlow(CastPortalPlan candidate, Map<CastPortalPlan.Point, CastWaterSafety.Cell> terrain) {
        Set<CastPortalPlan.Point> mold = new HashSet<>(candidate.mold());
        mold.removeAll(candidate.initialRemovals());
        List<CastPortalPlan.Point> frame = candidate.frame();
        // Each partially built column can redirect water. Include the temporary
        // excavated base holes and every prefix, not just the initial/final frame.
        for (int prefix = 0; prefix < frame.size(); prefix++) {
            final int completed = prefix;
            Set<CastPortalPlan.Point> solid = new HashSet<>(mold);
            if (prefix >= 7) solid.remove(candidate.at(0, 3, 3));
            solid.addAll(frame.subList(0, prefix));
            solid.remove(candidate.water());
            if (!CastWaterSafety.safe(List.of(candidate.water()), point -> {
                if (point.equals(candidate.water())) return CastWaterSafety.Cell.OPEN;
                if (completed < 2 && point.equals(frame.get(completed))) return CastWaterSafety.Cell.OPEN;
                if (solid.contains(point)) return CastWaterSafety.Cell.SOLID;
                return terrain.computeIfAbsent(point, location -> {
                    if (terrain.size() >= 131072) return CastWaterSafety.Cell.UNKNOWN;
                    BlockPos cell = pos(location);
                    if (!loaded(cell)) return CastWaterSafety.Cell.UNKNOWN;
                    var state = client.world.getBlockState(cell);
                    if (state.getFluidState().isIn(FluidTags.LAVA)) return CastWaterSafety.Cell.LAVA;
                    if (state.isAir() || state.isOf(Blocks.WATER) || softPlant(cell, state))
                        return CastWaterSafety.Cell.OPEN;
                    if (state.isFullCube(client.world, cell) && state.getFluidState().isEmpty()) return CastWaterSafety.Cell.SOLID;
                    return CastWaterSafety.Cell.UNKNOWN;
                });
            })) return false;
        }
        return true;
    }

    void tick() {
        if (!active()) return;
        elapsed++;
        try {
            if (phase.equals("cancellation_pending")) { finish(pendingResult, pendingReason); return; }
            if (elapsed >= maxTicks || System.nanoTime() >= deadline) throw new IllegalStateException("bucket casting deadline reached");
            if (client.getNetworkHandler() != connection) { finish("cancelled", "connection changed during casting"); return; }
            if (permitsWorldTransition() && (client.world != originWorld || client.player == null)) {
                releaseInputs(); stopNavigation();
                if (client.world != null && client.player != null && client.world.getRegistryKey().equals(World.NETHER)) {
                    finish("completed", "newly cast portal reached the Nether"); return;
                }
                if (transitionAt == 0) transitionAt = System.nanoTime();
                if (System.nanoTime() - transitionAt > 5_000_000_000L) throw new IllegalStateException("Nether loading exceeded5 seconds");
                if (client.world != null && client.world != originWorld) throw new IllegalStateException("unexpected portal destination");
                setPhaseOnce("changing_dimension"); return;
            }
            if (!ready() || client.world != originWorld || client.player != originPlayer) { finish("cancelled", "world, player, focus or pause changed"); return; }
            requireClosed();
            if (jumpHeld && elapsed - jumpStarted >= 10) releaseJump();
            if (tickNavigation()) return;
            switch (phase) {
                case "seek_water" -> seekFluid(false);
                case "seek_lava" -> seekFluid(true);
                case "confirm_water" -> confirmFluid(false);
                case "confirm_lava" -> confirmFluid(true);
                case "approach_site" -> approachSite();
                case "build_mold" -> buildMold();
                case "confirm_mold" -> confirmMold();
                case "stand_for_mold", "return_to_pad" -> awaitPad();
                case "stand_for_bridge" -> awaitBridgeStand();
                case "pour_water" -> pourWater();
                case "confirm_source" -> confirmSource();
                case "open_channels" -> openChannels();
                case "cast_frame" -> castFrame();
                case "confirm_obsidian" -> confirmObsidian();
                case "clear_header" -> clearHeader();
                case "drain_frame" -> drainFrame();
                case "ignite_frame" -> igniteFrame();
                case "confirm_portal" -> confirmPortal();
                case "enter_portal", "in_portal" -> enterPortal();
                default -> throw new IllegalStateException("unknown bucket casting phase");
            }
        } catch (ReflectiveOperationException | RuntimeException error) {
            finish("failed", error instanceof IllegalArgumentException || error instanceof IllegalStateException
                ? error.getMessage() : "casting API failure: " + error.getClass().getSimpleName());
        }
    }

    private void beginFluid(boolean lavaSource) throws ReflectiveOperationException {
        rejectedFluidStands.clear();
        chooseFluid(lavaSource);
    }

    private void chooseFluid(boolean lavaSource) throws ReflectiveOperationException {
        releaseInputs();
        List<BlockPos> sources = lavaSource ? lava : water;
        sources.sort(Comparator.comparingDouble(p -> client.player.getPos().squaredDistanceTo(Vec3d.ofCenter(p))));
        for (BlockPos source : sources) {
            if (!sourceStill(source, lavaSource) || lavaSource && collectedLava.contains(source)) continue;
            BlockPos stand = fluidStand(source);
            if (stand == null) continue;
            selectedSource = source; setPhase(lavaSource ? "seek_lava" : "seek_water");
            goTo(stand); return;
        }
        throw new IllegalStateException("no reachable exposed " + (lavaSource ? "natural lava" : "water") + " source remains");
    }

    private BlockPos fluidStand(BlockPos source) {
        Vec3d target = Vec3d.ofCenter(source).add(0, 0.35, 0);
        List<BlockPos> positions = new ArrayList<>();
        for (int x = -3; x <= 3; x++) for (int z = -3; z <= 3; z++) for (int y = 0; y <= 3; y++) {
            BlockPos feet = source.add(x, y, z), floor = feet.down();
            if (rejectedFluidStands.contains(feet) || !loaded(feet) || !client.world.getBlockState(floor).isFullCube(client.world, floor)
                || !client.world.getBlockState(feet).isAir() || !client.world.getBlockState(feet.up()).isAir()) continue;
            Vec3d eye = Vec3d.ofBottomCenter(feet).add(0, client.player.getStandingEyeHeight(), 0);
            if (eye.squaredDistanceTo(target) > Math.pow(client.player.getBlockInteractionRange() - 0.15, 2)) continue;
            BlockHitResult hit = ray(eye, target, RaycastContext.FluidHandling.SOURCE_ONLY);
            if (hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(source)) positions.add(feet);
        }
        return positions.stream().min(Comparator.comparingDouble(p -> client.player.getPos().squaredDistanceTo(Vec3d.ofBottomCenter(p)))).orElse(null);
    }

    private void seekFluid(boolean lavaSource) throws ReflectiveOperationException {
        if (!sourceStill(selectedSource, lavaSource)) { beginFluid(lavaSource); return; }
        BlockHitResult hit = fluidHit(selectedSource);
        if (hit == null) { requireNavigation("natural fluid source"); return; }
        stopNavigation(); equip(Items.BUCKET); look(hit.getPos());
        bucketCountBefore = count(lavaSource ? Items.LAVA_BUCKET : Items.WATER_BUCKET);
        inventoryAfter = ServerSlotSync.INSTANCE.latest(connection);
        updateAfter = ServerBlockSync.INSTANCE.latest(connection);
        if (!client.interactionManager.interactItem(client.player, Hand.MAIN_HAND).isAccepted()) throw new IllegalStateException("bucket pickup rejected");
        setPhase(lavaSource ? "confirm_lava" : "confirm_water");
    }

    private void confirmFluid(boolean lavaSource) throws ReflectiveOperationException {
        Item expected = lavaSource ? Items.LAVA_BUCKET : Items.WATER_BUCKET;
        int selected = client.player.getInventory().selectedSlot;
        boolean confirmedBucket = ServerSlotSync.INSTANCE.matches(connection, selected, inventoryAfter,
            net.minecraft.registry.Registries.ITEM.getId(expected).toString(), 1);
        boolean consumedSource = updated(selectedSource) && !sourceStill(selectedSource, true) && !serverSourceStill(selectedSource, true);
        if (count(expected) > bucketCountBefore && confirmedBucket && (!lavaSource || consumedSource)) {
            if (lavaSource) { collectedLava.add(selectedSource); setPhase("return_to_pad"); goTo(pos(plan.stand())); }
            else { verified.add("water_collected"); beginSite(); }
        } else checkPhaseDeadline(60, "server did not confirm bucket pickup");
    }

    private void beginSite() throws ReflectiveOperationException { setPhase("approach_site"); goTo(pos(plan.approach())); }
    private void approachSite() {
        if (placementHit(pos(plan.pad())) == null) { requireNavigation("construction site"); return; }
        stopNavigation(); moldIndex = 0; stepStarted = elapsed; setPhase("build_mold");
    }

    private void buildMold() {
        if (moldIndex >= plan.mold().size()) { removalIndex = 0; stepStarted = elapsed; setPhase("open_channels"); return; }
        BlockPos target = pos(plan.mold().get(moldIndex));
        var state = client.world.getBlockState(target);
        if (!state.isAir()) {
            if (!softPlant(target, state)) throw new IllegalStateException("construction mold cell changed");
            if (!breakCell(target, false)) return;
        } else if (breaking && !breakCell(target, false)) return;
        BlockHitResult hit = placementHit(target);
        if (hit == null) { seekReach("mold placement"); return; }
        if (!client.world.doesNotIntersectEntities(null, net.minecraft.util.shape.VoxelShapes.cuboid(new Box(target))))
            throw new IllegalStateException("mold placement would intersect an entity");
        pendingMaterial = count(Items.DIRT) > 0 ? Items.DIRT : Items.COBBLESTONE;
        equip(pendingMaterial); look(hit.getPos());
        pendingBlock = target; updateAfter = ServerBlockSync.INSTANCE.latest(connection);
        if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hit).isAccepted()) throw new IllegalStateException("mold placement rejected");
        setPhase("confirm_mold");
    }

    private void confirmMold() throws ReflectiveOperationException {
        var expected = pendingMaterial == Items.DIRT ? Blocks.DIRT : Blocks.COBBLESTONE;
        if (confirmed(pendingBlock, state -> state.isOf(expected))) {
            placedMold.add(pendingBlock); moldIndex++; stepStarted = elapsed; releaseJump();
            if (moldIndex == 1 || moldIndex == 8) { setPhase("stand_for_mold"); goTo(pos(plan.stand())); }
            else if (moldIndex == 6) { setPhase("stand_for_bridge"); goTo(pos(plan.bridgeStand())); }
            else setPhase("build_mold");
        } else checkPhaseDeadline(40, "server did not confirm the placed mold block");
    }

    private void awaitPad() {
        if (client.player.getPos().squaredDistanceTo(Vec3d.ofBottomCenter(pos(plan.stand()))) < 0.45 && client.player.isOnGround()) {
            stopNavigation(); stepStarted = elapsed;
            setPhase(phase.equals("stand_for_mold") ? "build_mold" : "cast_frame");
        } else requireNavigation("dry casting platform");
    }

    private void awaitBridgeStand() {
        if (client.player.getPos().squaredDistanceTo(Vec3d.ofBottomCenter(pos(plan.bridgeStand()))) < 0.45 && client.player.isOnGround()) {
            stopNavigation(); stepStarted = elapsed; setPhase("build_mold");
        } else requireNavigation("visible south-facing mold support");
    }

    private void pourWater() {
        BlockHitResult hit = faceHit(pos(plan.header()), Direction.UP);
        if (hit == null) { seekReach("water source placement"); return; }
        equip(Items.WATER_BUCKET); look(hit.getPos());
        updateAfter = ServerBlockSync.INSTANCE.latest(connection);
        if (!client.interactionManager.interactItem(client.player, Hand.MAIN_HAND).isAccepted()) throw new IllegalStateException("water bucket placement rejected");
        setPhase("confirm_source");
    }

    private void confirmSource() throws ReflectiveOperationException {
        if (updated(pos(plan.water())) && sourceStill(pos(plan.water()), false) && serverSourceStill(pos(plan.water()), false) && count(Items.BUCKET) > 0) {
            beginFluid(true);
        } else checkPhaseDeadline(60, "server did not confirm the casting water source");
    }

    private void openChannels() throws ReflectiveOperationException {
        if (removalIndex >= plan.initialRemovals().size()) { releaseBreaking(); stepStarted = elapsed; setPhase("pour_water"); return; }
        BlockPos cell = pos(plan.initialRemovals().get(removalIndex));
        if (breakCell(cell, true)) { removalIndex++; stepStarted = elapsed; }
    }

    private void castFrame() {
        BlockPos cell = pos(plan.frame().get(frameIndex));
        var current = client.world.getBlockState(cell);
        if (current.isOf(Blocks.OBSIDIAN)) throw new IllegalStateException("uncounted obsidian appeared in a new frame cell");
        if (!current.isAir() && !current.getFluidState().isIn(FluidTags.WATER)) {
            if (frameIndex < 2 || placedMold.contains(cell)) {
                if (!breakCell(cell, frameIndex >= 2)) return;
            } else throw new IllegalStateException("new portal frame cell is obstructed");
        }
        if (!touchesWater(cell)) { checkPhaseDeadline(100, "flowing water has not reached the next casting cell"); return; }
        BlockHitResult hit = placementHit(cell);
        if (hit == null) { seekReach("lava casting support"); return; }
        if (client.player.getBoundingBox().intersects(new Box(cell))) throw new IllegalStateException("player intersects lava casting cell");
        equip(Items.LAVA_BUCKET); look(hit.getPos()); pendingBlock = cell;
        updateAfter = ServerBlockSync.INSTANCE.latest(connection);
        if (!client.interactionManager.interactItem(client.player, Hand.MAIN_HAND).isAccepted()) throw new IllegalStateException("lava bucket placement rejected");
        setPhase("confirm_obsidian");
    }

    private void confirmObsidian() throws ReflectiveOperationException {
        if (confirmed(pendingBlock, state -> state.isOf(Blocks.OBSIDIAN)) && count(Items.BUCKET) > 0) {
            castBlocks.add(pendingBlock); frameIndex++; releaseJump();
            if (frameIndex == 10) {
                verifyOwnFrame(); verified.add("portal_built"); stepStarted = elapsed; setPhase("clear_header");
            } else beginFluid(true);
        } else checkPhaseDeadline(60, "lava did not become server-confirmed obsidian");
    }

    private boolean breakCell(BlockPos cell, boolean ownedMold) {
        var state = client.world.getBlockState(cell);
        if (state.isAir() || state.getFluidState().isIn(FluidTags.WATER)) {
            if (breaking && confirmed(cell, block -> block.isAir() || block.getFluidState().isIn(FluidTags.WATER))) { releaseBreaking(); return true; }
            if (!breaking) return true;
            checkStepDeadline(100, "server did not confirm block removal"); return false;
        }
        if (ownedMold && !placedMold.contains(cell)) throw new IllegalStateException("refusing to remove an unowned construction block");
        if (state.isOf(Blocks.OBSIDIAN) || state.getHardness(client.world, cell) < 0 || state.getHardness(client.world, cell) > 3)
            throw new IllegalStateException("construction requires an unsupported block excavation");
        BlockHitResult hit = blockHit(cell);
        if (hit == null) { seekReach("construction block removal"); return false; }
        equip(Items.STONE_PICKAXE); look(hit.getPos());
        if (!breaking) {
            updateAfter = ServerBlockSync.INSTANCE.latest(connection);
            client.interactionManager.attackBlock(cell, hit.getSide()); breaking = true;
        } else client.interactionManager.updateBlockBreakingProgress(cell, hit.getSide());
        if (elapsed % 5 == 0) client.player.swingHand(Hand.MAIN_HAND);
        checkStepDeadline(100, "construction block removal stalled");
        return false;
    }

    private void clearHeader() {
        verifyOwnFrame();
        if (breakCell(pos(plan.header()), true)) setPhase("drain_frame");
    }

    private void drainFrame() {
        verifyOwnFrame();
        boolean clear = plan.interior().stream().allMatch(point -> client.world.getBlockState(pos(point)).isAir());
        if (clear) { stepStarted = elapsed; setPhase("ignite_frame"); }
        else checkPhaseDeadline(140, "flowing water did not drain from the completed frame");
    }

    private void igniteFrame() {
        verifyOwnFrame();
        BlockHitResult hit = faceHit(pos(plan.frame().getFirst()), Direction.UP);
        if (hit == null) { seekReach("portal ignition"); return; }
        equip(Items.FLINT_AND_STEEL); look(hit.getPos()); updateAfter = ServerBlockSync.INSTANCE.latest(connection);
        if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hit).isAccepted()) throw new IllegalStateException("portal ignition rejected");
        setPhase("confirm_portal");
    }

    private void confirmPortal() throws ReflectiveOperationException {
        if (portalPresent() && plan.interior().stream().allMatch(point -> confirmed(pos(point), state -> state.isOf(Blocks.NETHER_PORTAL)))) {
            verified.add("portal_lit"); restoreSlot(); beginEntry();
        } else checkPhaseDeadline(60, "newly built frame did not become a Nether portal");
    }

    private void beginEntry() throws ReflectiveOperationException {
        releaseInputs(); setPhase("enter_portal"); enterAttempts++; entryNudges = 0; goTo(pos(plan.entry()));
    }

    private void enterPortal() throws ReflectiveOperationException {
        verifyOwnFrame();
        if (!portalPresent()) throw new IllegalStateException("new portal vanished before entry");
        boolean inside = plan.interior().stream().anyMatch(point -> {
            BlockPos cell = pos(point);
            return client.world.getBlockState(cell).getOutlineShape(client.world, cell).getBoundingBoxes().stream()
                .anyMatch(box -> client.player.getBoundingBox().intersects(box.offset(cell)));
        });
        if (inside) {
            releaseInputs(); stopNavigation(); setPhaseOnce("in_portal");
            checkPhaseDeadline(240, "standing in the new portal did not change dimension"); return;
        }
        if (phase.equals("in_portal")) {
            if (enterAttempts >= 3) throw new IllegalStateException("player repeatedly left the new portal");
            beginEntry(); return;
        }
        Vec3d center = Vec3d.ofBottomCenter(pos(plan.entry()));
        double dx = center.x - client.player.getX(), dz = center.z - client.player.getZ();
        if (dx * dx + dz * dz < 1.6 && Math.abs(center.y - client.player.getY()) < 1.2) {
            stopNavigation();
            if (++entryNudges > 12) throw new IllegalStateException("could not step into the new portal plane");
            client.player.setYaw((float) Math.toDegrees(Math.atan2(-dx, dz)));
            client.options.forwardKey.setPressed(true); forwardHeld = true;
        } else {
            if (forwardHeld) client.options.forwardKey.setPressed(false);
            forwardHeld = false;
            requireNavigation("new portal interior");
        }
    }

    private void verifyOwnFrame() {
        if (castBlocks.size() != 10 || collectedLava.size() != 10
            || !plan.frame().stream().allMatch(point -> castBlocks.contains(pos(point)) && client.world.getBlockState(pos(point)).isOf(Blocks.OBSIDIAN)))
            throw new IllegalStateException("ten individually cast obsidian blocks are not intact");
    }
    private boolean portalPresent() { return plan.interior().stream().allMatch(point -> client.world.getBlockState(pos(point)).isOf(Blocks.NETHER_PORTAL)); }
    private boolean touchesWater(BlockPos cell) {
        for (Direction side : Direction.values()) if (side != Direction.DOWN && client.world.getFluidState(cell.offset(side)).isIn(FluidTags.WATER)) return true;
        return false;
    }
    private boolean sourceStill(BlockPos cell, boolean lavaSource) {
        return loaded(cell) && client.world.getFluidState(cell).isStill()
            && client.world.getFluidState(cell).isIn(lavaSource ? FluidTags.LAVA : FluidTags.WATER);
    }
    private boolean updated(BlockPos cell) { return ServerBlockSync.INSTANCE.sequence(connection, cell.asLong()) > updateAfter; }
    private BlockState serverState(BlockPos cell) {
        int id = ServerBlockSync.INSTANCE.stateId(connection, cell.asLong());
        return id < 0 ? null : Block.getStateFromRawId(id);
    }
    private boolean serverSourceStill(BlockPos cell, boolean lavaSource) {
        BlockState state = serverState(cell);
        return state != null && state.getFluidState().isStill() && state.getFluidState().isIn(lavaSource ? FluidTags.LAVA : FluidTags.WATER);
    }
    private boolean confirmed(BlockPos cell, java.util.function.Predicate<BlockState> expected) {
        BlockState server = serverState(cell);
        return updated(cell) && server != null && expected.test(server) && expected.test(client.world.getBlockState(cell));
    }
    private boolean loaded(BlockPos cell) { return client.world != null && client.world.isChunkLoaded(cell.getX() >> 4, cell.getZ() >> 4); }
    private boolean softPlant(BlockPos cell, BlockState state) {
        return state.isReplaceable() && state.getCollisionShape(client.world, cell).isEmpty()
            && state.getFluidState().isEmpty() && state.getHardness(client.world, cell) >= 0
            && state.getHardness(client.world, cell) <= 0.2f;
    }
    private static BlockPos pos(CastPortalPlan.Point point) { return new BlockPos(point.x(), point.y(), point.z()); }
    private int count(Item item) { return client.player == null ? 0 : client.player.getInventory().count(item); }
    private int materials() { return count(Items.DIRT) + count(Items.COBBLESTONE); }

    private BlockHitResult ray(Vec3d eye, Vec3d target, RaycastContext.FluidHandling fluids) {
        return client.world.raycast(new RaycastContext(eye, target, RaycastContext.ShapeType.OUTLINE, fluids, client.player));
    }
    private BlockHitResult fluidHit(BlockPos cell) {
        Vec3d target = Vec3d.ofCenter(cell).add(0, 0.35, 0);
        if (client.player.getEyePos().squaredDistanceTo(target) > Math.pow(client.player.getBlockInteractionRange(), 2)) return null;
        BlockHitResult hit = ray(client.player.getEyePos(), target, RaycastContext.FluidHandling.SOURCE_ONLY);
        return hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(cell) ? hit : null;
    }
    private BlockHitResult blockHit(BlockPos cell) {
        for (Direction side : Direction.values()) {
            BlockHitResult hit = faceHit(cell, side); if (hit != null) return hit;
        }
        return null;
    }
    private BlockHitResult faceHit(BlockPos block, Direction face) {
        if (!loaded(block) || client.world.getBlockState(block).isAir()) return null;
        Vec3d target = Vec3d.ofCenter(block).add(face.getOffsetX() * 0.499, face.getOffsetY() * 0.499, face.getOffsetZ() * 0.499);
        if (client.player.getEyePos().squaredDistanceTo(target) > Math.pow(client.player.getBlockInteractionRange(), 2)) return null;
        BlockHitResult hit = ray(client.player.getEyePos(), target, RaycastContext.FluidHandling.NONE);
        return hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(block) && hit.getSide() == face ? hit : null;
    }
    private BlockHitResult placementHit(BlockPos target) {
        for (Direction side : Direction.values()) {
            BlockPos support = target.offset(side);
            if (!loaded(support) || !client.world.getBlockState(support).isFullCube(client.world, support)) continue;
            BlockHitResult hit = faceHit(support, side.getOpposite());
            if (hit != null) return hit;
        }
        return null;
    }
    private void look(Vec3d target) {
        Vec3d delta = target.subtract(client.player.getEyePos());
        client.player.setYaw((float) Math.toDegrees(Math.atan2(-delta.x, delta.z)));
        client.player.setPitch((float) -Math.toDegrees(Math.atan2(delta.y, Math.hypot(delta.x, delta.z))));
    }
    private void equip(Item item) {
        int source = -1;
        for (int slot = 0; slot < 36; slot++) if (client.player.getInventory().getStack(slot).isOf(item)) { source = slot; break; }
        if (source < 0 && client.player.getOffHandStack().isOf(item)) source = 40;
        if (source < 0) throw new IllegalStateException("required owned item missing: " + item);
        int hotbar = source;
        if (source >= 9) {
            hotbar = client.player.getInventory().selectedSlot;
            for (int slot = 0; slot < 9; slot++) if (client.player.getInventory().getStack(slot).isEmpty()) { hotbar = slot; break; }
            client.interactionManager.clickSlot(client.player.playerScreenHandler.syncId, source == 40 ? 45 : source, hotbar, SlotActionType.SWAP, client.player);
        }
        client.player.getInventory().selectedSlot = hotbar;
        client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(hotbar));
        if (!client.player.getMainHandStack().isOf(item)) throw new IllegalStateException("item did not reach the selected hand");
    }
    private void goTo(BlockPos goal) throws ReflectiveOperationException {
        stopNavigation(); releaseInputs(); navigationGoal = goal.toImmutable();
        navigationAttempts = settledTicks = 0; navigationStarted = elapsed; navigationMode = "settling";
    }

    /** Baritone rejects flowing-water cells. Only this bounded, validated local walk crosses runoff. */
    private boolean tickNavigation() throws ReflectiveOperationException {
        if (navigationMode.equals("idle")) return false;
        if (navigationMode.equals("local_walk")) { walkLocalRoute(); return true; }
        if (navigationMode.equals("settling")) {
            if (client.player.isOnGround() && Math.abs(client.player.getVelocity().y) < 0.08) settledTicks++;
            else settledTicks = 0;
            if (settledTicks >= 2) {
                if (feetInWater() && startLocalRoute()) return true;
                startBaritone();
            } else if (elapsed - navigationStarted > 40) throw new IllegalStateException("could not settle on solid ground before casting navigation");
            return true;
        }
        if (navigation.navigationActive() || elapsed - navigationStarted < 20) return false;
        // A goal can finish at a block edge, or water can invalidate the next path.
        if (atNavigationGoal()) { navigationMode = "idle"; return false; }
        if (startLocalRoute()) return true;
        if (navigationAttempts < 3) {
            stopNavigation(); releaseInputs(); navigationMode = "settling";
            navigationStarted = elapsed; settledTicks = 0; return true;
        }
        if ((phase.equals("seek_lava") || phase.equals("seek_water")) && rejectedFluidStands.size() < 3) {
            rejectedFluidStands.add(navigationGoal);
            chooseFluid(phase.equals("seek_lava")); return true;
        }
        throw new IllegalStateException("no bounded route to " + navigationGoal + " from " + client.player.getBlockPos());
    }

    private void startBaritone() throws ReflectiveOperationException {
        stopNavigation(); releaseInputs(); navigation.reserveItems(MATERIAL_IDS); navigation.navigateExact(navigationGoal);
        navigationAttempts++; navigationStarted = elapsed; navigationMode = "baritone";
    }

    private boolean atNavigationGoal() {
        return client.player.isOnGround() && client.player.getPos().squaredDistanceTo(Vec3d.ofBottomCenter(navigationGoal)) < 0.12;
    }

    private boolean feetInWater() {
        return client.world.getFluidState(client.player.getBlockPos()).isIn(FluidTags.WATER);
    }

    private boolean startLocalRoute() {
        if (navigationAttempts >= 4 || !client.player.isOnGround()) return false;
        CastPortalPlan.Point start = point(client.player.getBlockPos()), goal = point(navigationGoal);
        List<CastPortalPlan.Point> route = CastEscapeRoute.findTo(start, goal, this::navigationCell);
        boolean reachesGoal = !route.isEmpty();
        if (route.isEmpty() && start.equals(goal) && CastEscapeRoute.standable(goal, this::navigationCell) && !atNavigationGoal()) {
            route = List.of(goal); reachesGoal = true;
        }
        if (route.isEmpty() && feetInWater()) route = CastEscapeRoute.find(start, this::navigationCell);
        if (route.isEmpty()) return false;
        stopNavigation(); releaseInputs(); navigationAttempts++;
        localRoute = route; localIndex = 0; localStarted = localProgressAt = elapsed;
        localBestDistance = Double.POSITIVE_INFINITY; localReachesGoal = reachesGoal; navigationMode = "local_walk";
        return true;
    }

    private CastEscapeRoute.Cell navigationCell(CastPortalPlan.Point point) {
        BlockPos cell = pos(point);
        if (!loaded(cell)) return CastEscapeRoute.Cell.BLOCKED;
        var state = client.world.getBlockState(cell);
        if (state.isOf(Blocks.MAGMA_BLOCK) || state.isOf(Blocks.CAMPFIRE) || state.isOf(Blocks.SOUL_CAMPFIRE)
            || state.isOf(Blocks.CACTUS) || state.isOf(Blocks.SWEET_BERRY_BUSH) || state.isOf(Blocks.POWDER_SNOW)
            || state.isOf(Blocks.FIRE) || state.isOf(Blocks.SOUL_FIRE) || state.getFluidState().isIn(FluidTags.LAVA))
            return CastEscapeRoute.Cell.BLOCKED;
        if (state.isOf(Blocks.WATER) && state.getCollisionShape(client.world, cell).isEmpty()) return CastEscapeRoute.Cell.WATER;
        if (!state.getFluidState().isEmpty()) return CastEscapeRoute.Cell.BLOCKED;
        if (state.isAir() || softPlant(cell, state)) return CastEscapeRoute.Cell.OPEN;
        return state.isFullCube(client.world, cell) ? CastEscapeRoute.Cell.SOLID : CastEscapeRoute.Cell.BLOCKED;
    }

    private void walkLocalRoute() throws ReflectiveOperationException {
        if (elapsed - localStarted >= 240) throw new IllegalStateException("bounded walk through casting runoff timed out");
        CastPortalPlan.Point waypoint = localRoute.get(localIndex);
        if (!CastEscapeRoute.standable(waypoint, this::navigationCell))
            throw new IllegalStateException("casting runoff route changed or became unsafe");
        Vec3d target = Vec3d.ofBottomCenter(pos(waypoint));
        double distance = client.player.getPos().squaredDistanceTo(target);
        if (distance < 0.10 && client.player.isOnGround()) {
            releaseInputs(); localIndex++; localProgressAt = elapsed; localBestDistance = Double.POSITIVE_INFINITY;
            if (localIndex == localRoute.size()) {
                localRoute = List.of();
                if (localReachesGoal) navigationMode = "idle";
                else { navigationMode = "settling"; navigationStarted = elapsed; settledTicks = 0; }
            }
            return;
        }
        if (distance + 0.02 < localBestDistance) { localBestDistance = distance; localProgressAt = elapsed; }
        if (elapsed - localProgressAt > 50 || distance > 4.5)
            throw new IllegalStateException("normal walking made no progress through casting runoff");
        // Revalidate the cardinal transition and body clearance before every pressed tick.
        CastPortalPlan.Point current = point(client.player.getBlockPos());
        if (client.player.isOnGround() && !current.equals(waypoint)) {
            List<CastPortalPlan.Point> checked = CastEscapeRoute.findTo(current, waypoint, this::navigationCell);
            if (checked.isEmpty()) throw new IllegalStateException("casting runoff waypoint is no longer reachable on verified terrain");
            if (checked.size() > 1) {
                // A valid detour does not authorize walking straight through its obstacle.
                List<CastPortalPlan.Point> repaired = new ArrayList<>(checked);
                repaired.addAll(localRoute.subList(localIndex + 1, localRoute.size()));
                localRoute = List.copyOf(repaired); localIndex = 0;
                localBestDistance = Double.POSITIVE_INFINITY; releaseInputs(); return;
            }
        }
        Vec3d direction = target.subtract(client.player.getPos());
        client.player.setYaw((float) Math.toDegrees(Math.atan2(-direction.x, direction.z)));
        client.options.forwardKey.setPressed(true); forwardHeld = true;
        if (target.y > client.player.getY() + 0.2 && (client.player.isOnGround() || feetInWater()) && !jumpHeld) {
            client.options.jumpKey.setPressed(true); jumpHeld = true; jumpStarted = elapsed;
        } else if (target.y <= client.player.getY() + 0.2 && jumpHeld) releaseJump();
    }

    private static CastPortalPlan.Point point(BlockPos point) { return new CastPortalPlan.Point(point.getX(), point.getY(), point.getZ()); }
    private void requireNavigation(String goal) {
        try {
            if (navigationMode.equals("idle") && !navigation.navigationActive()) {
                if ((phase.equals("seek_lava") || phase.equals("seek_water")) && rejectedFluidStands.size() < 3) {
                    rejectedFluidStands.add(navigationGoal); chooseFluid(phase.equals("seek_lava")); return;
                }
                throw new IllegalStateException("navigation reached its checked stance but could not interact with " + goal);
            }
        } catch (ReflectiveOperationException error) { throw new IllegalStateException("navigation status unavailable"); }
    }
    private void seekReach(String action) {
        if (client.player.isOnGround() && !jumpHeld) { client.options.jumpKey.setPressed(true); jumpHeld = true; jumpStarted = elapsed; }
        checkStepDeadline(60, action + " has no visible face within normal reach");
    }
    private void checkStepDeadline(int ticks, String detail) { if (elapsed - stepStarted >= ticks) throw new IllegalStateException(detail); }
    private void checkPhaseDeadline(int ticks, String detail) { if (elapsed - phaseAt >= ticks) throw new IllegalStateException(detail); }
    private void setPhase(String value) { phase = value; phaseAt = elapsed; }
    private void setPhaseOnce(String value) { if (!phase.equals(value)) setPhase(value); }
    private void stopNavigation() {
        if (navigation != null && !navigation.cancel()) throw new IllegalStateException("could not release casting navigation controls");
        navigationMode = "idle";
    }
    private void requireClosed() {
        if (client.currentScreen != null || client.player.currentScreenHandler != client.player.playerScreenHandler
            || !client.player.currentScreenHandler.getCursorStack().isEmpty()) throw new IllegalStateException("unexpected screen or cursor during casting");
    }
    private void releaseBreaking() { if (breaking && client.interactionManager != null) client.interactionManager.cancelBlockBreaking(); breaking = false; }
    private void releaseJump() { if (jumpHeld) client.options.jumpKey.setPressed(false); jumpHeld = false; }
    private void releaseInputs() {
        releaseJump(); releaseBreaking();
        if (forwardHeld) client.options.forwardKey.setPressed(false);
        forwardHeld = false;
    }
    private void restoreSlot() {
        if (originalSlot >= 0 && client.player != null && client.getNetworkHandler() == connection) {
            client.player.getInventory().selectedSlot = originalSlot;
            try { client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(originalSlot)); }
            catch (RuntimeException ignored) { /* Cancellation must still release inputs on disconnect. */ }
        }
        originalSlot = -1;
    }
    void cancel(String detail) { if (active()) finish("cancelled", detail); else releaseInputs(); }
    private void finish(String result, String detail) {
        pendingResult = result; pendingReason = detail; releaseInputs();
        if (navigation != null && !navigation.cancel()) { phase = "cancellation_pending"; reason = "casting cancellation pending; control ownership retained"; return; }
        restoreSlot(); navigationMode = "idle"; status = result; phase = result;
        reason = detail == null ? "" : detail.substring(0, Math.min(300, detail.length()));
    }

    JsonObject diagnostics() {
        JsonObject result = new JsonObject();
        result.add("origin", plan == null ? JsonNull.INSTANCE : positionJson(pos(plan.origin)));
        if (plan == null) result.add("rotation", JsonNull.INSTANCE); else result.addProperty("rotation", plan.rotation);
        result.add("selected_source", positionJson(selectedSource));
        result.add("navigation_goal", positionJson(navigationGoal));
        result.addProperty("frame_index", frameIndex); result.addProperty("cast_blocks", castBlocks.size());
        result.addProperty("lava_sources_collected", collectedLava.size()); result.addProperty("navigation_mode", navigationMode);
        return result;
    }

    private static com.google.gson.JsonElement positionJson(BlockPos point) {
        if (point == null) return JsonNull.INSTANCE;
        JsonObject result = new JsonObject();
        result.addProperty("x", point.getX()); result.addProperty("y", point.getY()); result.addProperty("z", point.getZ());
        return result;
    }
}
