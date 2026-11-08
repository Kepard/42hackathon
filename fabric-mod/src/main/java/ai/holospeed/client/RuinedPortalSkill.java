package ai.holospeed.client;

import net.minecraft.block.Blocks;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ingame.GenericContainerScreen;
import net.minecraft.item.Item;
import net.minecraft.item.Items;
import net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket;
import net.minecraft.screen.GenericContainerScreenHandler;
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

/** Normal chest, placement, ignition, movement and portal dwell for one verified route. */
final class RuinedPortalSkill {
    private final MinecraftClient client;
    private SkillController.BaritoneAccess navigation;
    private Object originWorld, originPlayer, originConnection;
    private String status = "idle", phase = "idle", reason;
    private String pendingResult, pendingReason;
    private int elapsed, phaseAt, maxTicks, originalSlot = -1, lootSlot, lastTransferAt = -20;
    private int enterAttempts, nudgeTicks;
    private long deadline, transitionAt;
    private GenericContainerScreenHandler chestHandler;
    private boolean forwardHeld, jumpHeld;
    private Item equipping;
    private final java.util.Set<String> verifiedSteps = new java.util.LinkedHashSet<>();
    private long chestSyncAfter;
    private int lootBeforeObsidian, lootBeforeCharge, lootBeforeSteel;

    RuinedPortalSkill(MinecraftClient client) { this.client = client; }
    boolean active() { return status.equals("running"); }
    String status() { return status; }
    String phase() { return phase; }
    String reason() { return reason; }
    java.util.List<String> verifiedSteps() {
        if (client.getNetworkHandler() != originConnection) return java.util.List.of();
        if (client.world != originWorld && !permitsWorldTransition()
            && !(status.equals("completed") && client.world != null && client.world.getRegistryKey().equals(World.NETHER))) return java.util.List.of();
        return java.util.List.copyOf(verifiedSteps);
    }
    boolean permitsWorldTransition() {
        return active() && SetPhases.ENTRY.contains(phase)
            && client.getNetworkHandler() == originConnection;
    }

    void start(int ticks) throws ReflectiveOperationException {
        if (!ready()) throw new IllegalArgumentException("focused survival world required for portal route");
        if (!client.world.getRegistryKey().equals(World.OVERWORLD)) throw new IllegalArgumentException("portal route starts in the Overworld");
        if (client.currentScreen != null || !client.player.currentScreenHandler.getCursorStack().isEmpty()) throw new IllegalArgumentException("close screens and empty cursor before portal route");
        if (client.player.getPos().squaredDistanceTo(Vec3d.ofCenter(pos(PortalRoute.LANDMARK))) > 128 * 128) throw new IllegalArgumentException("verified ruined portal landmark is more than128 blocks away");
        maxTicks = ticks; elapsed = 0; deadline = System.nanoTime() + (ticks * 50L + 2000) * 1_000_000;
        originWorld = client.world; originPlayer = client.player; originConnection = client.getNetworkHandler();
        originalSlot = client.player.getInventory().selectedSlot;
        status = "running"; reason = null; enterAttempts = 0; nudgeTicks = 0; transitionAt = 0;
        verifiedSteps.clear();
        validateFrame(false);
        markAcquisitions();
        navigation = new SkillController.BaritoneAccess();
        if (!navigation.cancel()) throw new IllegalStateException("could not obtain portal navigation controls");
        if (suppliesReady()) approachFrame();
        else { setPhase("approach_chest"); navigation.navigateNear(pos(PortalRoute.CHEST), 2); }
    }

    void tick() {
        if (!active()) return;
        elapsed++;
        try {
            if (phase.equals("cancellation_pending")) { finish(pendingResult, pendingReason); return; }
            if (elapsed >= maxTicks || System.nanoTime() >= deadline) { finish("failed", "portal route deadline reached"); return; }
            if (client.getNetworkHandler() != originConnection) { finish("cancelled", "connection changed during portal route"); return; }
            if (permitsWorldTransition() && (client.world != originWorld || client.player == null)) {
                releaseInputs();
                if (navigation != null && !navigation.cancel()) throw new IllegalStateException("could not release portal navigation");
                if (client.world != null && client.player != null && client.world.getRegistryKey().equals(World.NETHER)) {
                    finish("completed", "Nether dimension verified"); return;
                }
                if (transitionAt == 0) transitionAt = System.nanoTime();
                if (System.nanoTime() - transitionAt > 5_000_000_000L) throw new IllegalStateException("Nether loading exceeded5 seconds");
                if (client.world != null && client.world != originWorld) throw new IllegalStateException("unexpected destination dimension");
                setPhaseOnce("changing_dimension");
                return;
            }
            if (!ready() || client.world != originWorld || client.player != originPlayer) { finish("cancelled", "world, player, focus or pause changed"); return; }
            switch (phase) {
                case "approach_chest" -> approachChest();
                case "await_chest" -> awaitChest();
                case "loot_chest" -> lootChest();
                case "verify_loot" -> verifyLoot();
                case "approach_frame" -> approachFrameTick();
                case "equip_obsidian" -> placeObsidian();
                case "verify_repair" -> verifyRepair();
                case "approach_ignition" -> approachIgnition();
                case "equip_ignition" -> ignite();
                case "verify_ignition" -> verifyIgnition();
                case "enter_portal", "in_portal" -> enterPortal();
                default -> throw new IllegalStateException("unknown portal phase");
            }
        } catch (ReflectiveOperationException | RuntimeException error) {
            finish("failed", error instanceof IllegalArgumentException || error instanceof IllegalStateException
                ? error.getMessage() : "portal API failure: " + error.getClass().getSimpleName());
        }
    }

    private boolean ready() {
        return client.player != null && client.world != null && client.interactionManager != null
            && client.player.isAlive() && client.isWindowFocused() && !client.isPaused()
            && client.interactionManager.getCurrentGameMode() == GameMode.SURVIVAL;
    }

    private void approachChest() throws ReflectiveOperationException {
        requireClosed();
        BlockPos chest = pos(PortalRoute.CHEST);
        if (loaded(chest) && !client.world.getBlockState(chest).isOf(Blocks.CHEST)) throw new IllegalStateException("verified route chest is absent");
        BlockHitResult hit = hit(chest, null);
        if (hit != null) {
            stopNavigation();
            chestSyncAfter = ServerInventorySync.INSTANCE.latest(originConnection);
            lootBeforeObsidian = count(Items.OBSIDIAN); lootBeforeCharge = count(Items.FIRE_CHARGE); lootBeforeSteel = count(Items.FLINT_AND_STEEL);
            if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hit).isAccepted()) throw new IllegalStateException("chest interaction rejected");
            setPhase("await_chest");
        } else ensureNavigation("chest");
    }

    private void awaitChest() {
        if (client.currentScreen instanceof GenericContainerScreen
            && client.player.currentScreenHandler instanceof GenericContainerScreenHandler handler && handler.getRows() == 3
            && ServerInventorySync.INSTANCE.sequence(originConnection, handler.syncId) > chestSyncAfter) {
            chestHandler = handler; lootSlot = 0; lastTransferAt = elapsed;
            setPhase("loot_chest");
        } else if (elapsed - phaseAt >= 40) throw new IllegalStateException("chest screen did not open");
    }

    private void lootChest() throws ReflectiveOperationException {
        if (client.player.currentScreenHandler != chestHandler || !(client.currentScreen instanceof GenericContainerScreen)
            || !chestHandler.getCursorStack().isEmpty()) throw new IllegalStateException("chest or cursor changed during loot");
        if (elapsed - lastTransferAt < 10) return;
        if (suppliesReady()) {
            beginLootVerification();
            return;
        }
        while (lootSlot < 27) {
            int slot = lootSlot++;
            var stack = chestHandler.getSlot(slot).getStack();
            if (stack.isOf(Items.OBSIDIAN) || stack.isOf(Items.FIRE_CHARGE) || stack.isOf(Items.FLINT_AND_STEEL)) {
                client.interactionManager.clickSlot(chestHandler.syncId, slot, 0, SlotActionType.QUICK_MOVE, client.player);
                lastTransferAt = elapsed;
                return;
            }
        }
        beginLootVerification();
    }

    private void beginLootVerification() {
        client.player.closeHandledScreen();
        BlockHitResult hit = hit(pos(PortalRoute.CHEST), null);
        if (hit == null) throw new IllegalStateException("chest is no longer reachable for inventory confirmation");
        chestSyncAfter = ServerInventorySync.INSTANCE.latest(originConnection);
        if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hit).isAccepted()) throw new IllegalStateException("could not reopen chest for server inventory confirmation");
        setPhase("verify_loot");
    }

    private void verifyLoot() throws ReflectiveOperationException {
        if (!(client.currentScreen instanceof GenericContainerScreen)
            || !(client.player.currentScreenHandler instanceof GenericContainerScreenHandler handler)
            || ServerInventorySync.INSTANCE.sequence(originConnection, handler.syncId) <= chestSyncAfter) {
            if (elapsed - phaseAt >= 40) throw new IllegalStateException("server chest inventory confirmation did not arrive");
            return;
        }
        if (!handler.getCursorStack().isEmpty()) throw new IllegalStateException("cursor changed while confirming loot");
        if (count(Items.OBSIDIAN) > lootBeforeObsidian || count(Items.FIRE_CHARGE) > lootBeforeCharge
            || count(Items.FLINT_AND_STEEL) > lootBeforeSteel) verifiedSteps.add("chest_looted");
        markAcquisitions();
        client.player.closeHandledScreen();
        if (!suppliesReady()) throw new IllegalStateException("server-confirmed inventory lacks obsidian or an ignition item required for this route");
        approachFrame();
    }

    private void markAcquisitions() {
        if (count(Items.OBSIDIAN) > 0) verifiedSteps.add("obsidian_acquired");
        if (ignitionItem() != null) verifiedSteps.add("ignition_acquired");
    }

    private boolean suppliesReady() {
        boolean repaired = loaded(pos(PortalRoute.GAP)) && client.world.getBlockState(pos(PortalRoute.GAP)).isOf(Blocks.OBSIDIAN);
        return (repaired || count(Items.OBSIDIAN) > 0) && (portalPresent() || ignitionItem() != null);
    }

    private void approachFrame() throws ReflectiveOperationException {
        requireClosed();
        if (portalPresent()) { beginEntry(); return; }
        validateFrame(false);
        if (client.world.getBlockState(pos(PortalRoute.GAP)).isOf(Blocks.OBSIDIAN)) { beginIgnition(); return; }
        if (count(Items.OBSIDIAN) == 0) throw new IllegalStateException("owned obsidian is missing");
        setPhase("approach_frame");
        navigation.navigateNear(pos(PortalRoute.GAP), 3);
    }

    private void approachFrameTick() throws ReflectiveOperationException {
        requireClosed(); validateFrame(false);
        BlockHitResult support = hit(pos(PortalRoute.SUPPORT), Direction.DOWN);
        if (support != null) {
            stopNavigation(); equip(Items.OBSIDIAN); setPhase("equip_obsidian");
        } else ensureNavigation("portal frame repair position");
    }

    private void placeObsidian() {
        requireClosed();
        if (elapsed - phaseAt < 5) return;
        verifyEquipped(); validateFrame(false);
        BlockPos gap = pos(PortalRoute.GAP);
        if (!client.world.getBlockState(gap).isAir()) throw new IllegalStateException("portal repair cell is no longer empty");
        if (client.player.getBoundingBox().intersects(new Box(gap)) || !client.world.getOtherEntities(client.player, new Box(gap)).isEmpty()) throw new IllegalStateException("portal repair cell is occupied");
        BlockHitResult support = hit(pos(PortalRoute.SUPPORT), Direction.DOWN);
        if (support == null) throw new IllegalStateException("portal repair support moved outside reach");
        if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, support).isAccepted()) throw new IllegalStateException("obsidian placement rejected");
        setPhase("verify_repair");
    }

    private void verifyRepair() throws ReflectiveOperationException {
        requireClosed();
        if (elapsed - phaseAt < 10) return;
        if (client.world.getBlockState(pos(PortalRoute.GAP)).isOf(Blocks.OBSIDIAN)) { validateFrame(true); beginIgnition(); }
        else if (elapsed - phaseAt >= 40) throw new IllegalStateException("obsidian repair was not confirmed");
    }

    private void beginIgnition() throws ReflectiveOperationException {
        if (portalPresent()) { beginEntry(); return; }
        validateFrame(true);
        if (ignitionItem() == null) throw new IllegalStateException("owned fire charge or flint and steel required");
        setPhase("approach_ignition");
        navigation.navigateNear(pos(PortalRoute.BASE).up(), 2);
    }

    private void approachIgnition() throws ReflectiveOperationException {
        requireClosed(); validateFrame(true);
        BlockHitResult base = hit(pos(PortalRoute.BASE), Direction.UP);
        if (base != null) { stopNavigation(); equip(ignitionItem()); setPhase("equip_ignition"); }
        else ensureNavigation("portal ignition position");
    }

    private void ignite() {
        requireClosed();
        if (elapsed - phaseAt < 5) return;
        verifyEquipped(); validateFrame(true);
        BlockHitResult base = hit(pos(PortalRoute.BASE), Direction.UP);
        if (base == null) throw new IllegalStateException("portal ignition face moved outside reach");
        if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, base).isAccepted()) throw new IllegalStateException("portal ignition interaction rejected");
        setPhase("verify_ignition");
    }

    private void verifyIgnition() throws ReflectiveOperationException {
        requireClosed();
        if (elapsed - phaseAt < 5) return;
        if (portalPresent()) beginEntry();
        else if (elapsed - phaseAt >= 60) throw new IllegalStateException("Nether portal blocks were not confirmed after ignition");
    }

    private void beginEntry() throws ReflectiveOperationException {
        if (!portalPresent()) throw new IllegalStateException("no verified Nether portal to enter");
        stopNavigation(); restoreSlot();
        navigation.navigateExact(pos(PortalRoute.ENTRY));
        setPhase("enter_portal"); enterAttempts++; nudgeTicks = 0;
    }

    private void enterPortal() throws ReflectiveOperationException {
        requireClosed();
        if (!portalPresent()) throw new IllegalStateException("portal vanished before entry");
        if (touchingPortal()) {
            releaseInputs(); stopNavigation();
            setPhaseOnce("in_portal");
            if (elapsed - phaseAt > 240) throw new IllegalStateException("standing in the portal did not change dimension");
            return;
        }
        if (phase.equals("in_portal")) {
            if (enterAttempts >= 3) throw new IllegalStateException("player repeatedly moved out of the portal");
            beginEntry(); return;
        }
        Vec3d center = Vec3d.ofBottomCenter(pos(PortalRoute.ENTRY));
        double dx = center.x - client.player.getX(), dz = center.z - client.player.getZ();
        if (dx * dx + dz * dz < 1.6 && Math.abs(client.player.getY() - center.y) < 1.2) {
            stopNavigation();
            if (++nudgeTicks > 12) throw new IllegalStateException("could not step into the verified portal plane");
            client.player.setYaw((float) Math.toDegrees(Math.atan2(-dx, dz)));
            client.options.forwardKey.setPressed(true); forwardHeld = true;
            if (client.player.isOnGround() && center.y - client.player.getY() > 0.6) { client.options.jumpKey.setPressed(true); jumpHeld = true; }
        } else { releaseInputs(); ensureNavigation("portal interior"); }
    }

    private boolean touchingPortal() {
        for (PortalRoute.Point point : PortalRoute.interior()) {
            BlockPos pos = pos(point);
            var state = client.world.getBlockState(pos);
            if (state.isOf(Blocks.NETHER_PORTAL) && state.getOutlineShape(client.world, pos).getBoundingBoxes().stream()
                .anyMatch(box -> client.player.getBoundingBox().intersects(box.offset(pos)))) return true;
        }
        return false;
    }

    private void validateFrame(boolean complete) {
        for (PortalRoute.Point point : PortalRoute.requiredFrame()) {
            BlockPos pos = pos(point);
            if (!loaded(pos)) throw new IllegalStateException("portal frame chunk is not loaded");
            var block = client.world.getBlockState(pos);
            if (!complete && point.equals(PortalRoute.GAP) && block.isAir()) continue;
            if (!block.isOf(Blocks.OBSIDIAN)) throw new IllegalStateException("observed portal frame differs from the verified route");
        }
        for (PortalRoute.Point point : PortalRoute.interior()) {
            var block = client.world.getBlockState(pos(point));
            if (!block.isAir() && !block.isOf(Blocks.NETHER_PORTAL) && !block.isOf(Blocks.FIRE)) throw new IllegalStateException("portal interior is obstructed");
        }
        if (!complete && !client.world.getBlockState(pos(PortalRoute.GAP)).isOf(Blocks.OBSIDIAN)
            && !client.world.getBlockState(pos(PortalRoute.SUPPORT)).isOf(Blocks.GOLD_BLOCK)) throw new IllegalStateException("verified gold placement support is missing");
        verifiedSteps.add("portal_located");
        if (client.world.getBlockState(pos(PortalRoute.GAP)).isOf(Blocks.OBSIDIAN)) verifiedSteps.add("portal_repaired");
    }

    private boolean portalPresent() {
        if (client.world == null) return false;
        for (PortalRoute.Point point : PortalRoute.interior()) {
            BlockPos pos = pos(point);
            if (!loaded(pos) || !client.world.getBlockState(pos).isOf(Blocks.NETHER_PORTAL)) return false;
        }
        verifiedSteps.add("portal_lit");
        return true;
    }

    private BlockHitResult hit(BlockPos block, Direction face) {
        if (!loaded(block) || client.world.getBlockState(block).isAir()) return null;
        Vec3d eye = client.player.getEyePos();
        Vec3d target = Vec3d.ofCenter(block);
        if (face != null) target = target.add(face.getOffsetX() * 0.499, face.getOffsetY() * 0.499, face.getOffsetZ() * 0.499);
        if (eye.squaredDistanceTo(target) > Math.pow(client.player.getBlockInteractionRange(), 2)) return null;
        BlockHitResult hit = client.world.raycast(new RaycastContext(eye, target, RaycastContext.ShapeType.OUTLINE, RaycastContext.FluidHandling.NONE, client.player));
        return hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(block) && (face == null || hit.getSide() == face) ? hit : null;
    }

    private void equip(Item item) {
        if (item == null) throw new IllegalStateException("required owned item is missing");
        int source = -1;
        for (int slot = 0; slot < 36; slot++) if (client.player.getInventory().getStack(slot).isOf(item)) { source = slot; break; }
        if (source < 0 && client.player.getOffHandStack().isOf(item)) source = 40;
        if (source < 0) throw new IllegalStateException("required item is absent from inventory");
        int hotbar = source;
        if (source >= 9) {
            hotbar = client.player.getInventory().selectedSlot;
            for (int slot = 0; slot < 9; slot++) if (client.player.getInventory().getStack(slot).isEmpty()) { hotbar = slot; break; }
            client.interactionManager.clickSlot(client.player.playerScreenHandler.syncId, source == 40 ? 45 : source, hotbar, SlotActionType.SWAP, client.player);
        }
        client.player.getInventory().selectedSlot = hotbar;
        client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(hotbar));
        equipping = item;
    }

    private void verifyEquipped() {
        if (!client.player.getMainHandStack().isOf(equipping)) throw new IllegalStateException("required item did not reach selected hand");
    }
    private Item ignitionItem() { return count(Items.FLINT_AND_STEEL) > 0 ? Items.FLINT_AND_STEEL : count(Items.FIRE_CHARGE) > 0 ? Items.FIRE_CHARGE : null; }
    private int count(Item item) { return client.player == null ? 0 : client.player.getInventory().count(item); }
    private boolean loaded(BlockPos pos) { return client.world != null && client.world.isChunkLoaded(pos.getX() >> 4, pos.getZ() >> 4); }
    private static BlockPos pos(PortalRoute.Point point) { return new BlockPos(point.x(), point.y(), point.z()); }
    private void requireClosed() {
        if (client.currentScreen != null || client.player.currentScreenHandler != client.player.playerScreenHandler
            || !client.player.currentScreenHandler.getCursorStack().isEmpty()) throw new IllegalStateException("unexpected screen or cursor during portal route");
    }
    private void ensureNavigation(String target) throws ReflectiveOperationException {
        if (elapsed - phaseAt > 20 && !navigation.navigationActive()) throw new IllegalStateException("navigation stopped before reaching " + target);
    }
    private void stopNavigation() {
        if (navigation != null && !navigation.cancel()) throw new IllegalStateException("could not release portal navigation controls");
    }
    private void setPhase(String value) { phase = value; phaseAt = elapsed; }
    private void setPhaseOnce(String value) { if (!phase.equals(value)) setPhase(value); }
    private void releaseInputs() {
        if (forwardHeld) client.options.forwardKey.setPressed(false);
        if (jumpHeld) client.options.jumpKey.setPressed(false);
        forwardHeld = false; jumpHeld = false;
    }
    private void restoreSlot() {
        if (originalSlot >= 0 && client.player != null && client.getNetworkHandler() == originConnection) {
            client.player.getInventory().selectedSlot = originalSlot;
            try { client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(originalSlot)); }
            catch (RuntimeException ignored) { /* Local slot restoration remains valid after disconnect. */ }
        }
        originalSlot = -1;
    }
    void cancel(String detail) { if (active()) finish("cancelled", detail); else releaseInputs(); }
    private void finish(String result, String detail) {
        pendingResult = result; pendingReason = detail;
        releaseInputs();
        if (navigation != null && !navigation.cancel()) {
            reason = "portal cancellation pending; input ownership retained";
            phase = "cancellation_pending";
            return;
        }
        restoreSlot(); status = result; phase = result;
        reason = detail == null ? "" : detail.substring(0, Math.min(300, detail.length()));
    }
    private static final class SetPhases { static final java.util.Set<String> ENTRY = java.util.Set.of("enter_portal", "in_portal", "changing_dimension"); }
}
