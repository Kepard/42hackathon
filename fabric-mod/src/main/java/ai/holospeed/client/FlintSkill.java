package ai.holospeed.client;

import net.minecraft.block.Blocks;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.ItemEntity;
import net.minecraft.item.Item;
import net.minecraft.item.Items;
import net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket;
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

import java.util.Comparator;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Gravel's random drops need their own inventory target and explicit flint pickup. */
final class FlintSkill {
    private static final ExecutorService PREPARER = Executors.newSingleThreadExecutor(task -> {
        Thread thread = new Thread(task, "holospeed-flint-preparation");
        thread.setDaemon(true);
        return thread;
    });
    private final MinecraftClient client;
    private final DeferredPreparation<Object> preparation = new DeferredPreparation<>();
    private SkillController.BaritoneAccess navigation;
    private Object lookup, originWorld, originPlayer, originConnection;
    private String status = "idle", phase = "idle", reason, pendingResult, pendingReason;
    private int target, maxTicks, elapsed, phaseAt, inventoryCount, originalSlot = -1, pickupBefore;
    private long deadline;
    private boolean cancellationPending;
    private ItemEntity pickup;
    private Placement placement;

    FlintSkill(MinecraftClient client) { this.client = client; }
    boolean active() { return status.equals("running") || cancellationPending; }
    String status() { return status; }
    String phase() { return phase; }
    String reason() { return reason; }
    int inventoryCount() { return inventoryCount; }

    void start(int totalFlint, int ticks) throws ReflectiveOperationException {
        if (active()) throw new IllegalStateException("flint skill already owns controls");
        if (totalFlint < 1 || totalFlint > 64 || ticks < 20 || ticks > 1800) throw new IllegalArgumentException("invalid flint target or deadline");
        if (!client.isOnThread() || !ready()) throw new IllegalArgumentException("focused, unpaused survival world required for flint mining");
        if (client.currentScreen != null || client.player.currentScreenHandler != client.player.playerScreenHandler
            || !client.player.currentScreenHandler.getCursorStack().isEmpty()) throw new IllegalArgumentException("close screens and empty cursor before flint mining");
        originWorld = client.world; originPlayer = client.player; originConnection = client.getNetworkHandler();
        target = totalFlint; maxTicks = ticks; elapsed = 0;
        deadline = System.nanoTime() + (ticks * 50L + 2000) * 1_000_000;
        originalSlot = client.player.getInventory().selectedSlot;
        status = "running"; reason = null; cancellationPending = false; pickup = null; placement = null; lookup = null;
        inventoryCount = count(Items.FLINT);
        try {
            if (inventoryCount >= target) { finish("completed", "flint inventory target already satisfied"); return; }
            navigation = new SkillController.BaritoneAccess();
            if (!navigation.cancel()) throw new IllegalStateException("could not obtain exclusive flint mining controls");
            setPhase("preparing_gravel");
            preparation.start(PREPARER, () -> {
                try { return SkillController.BaritoneAccess.prepareLookup(new String[]{"minecraft:gravel"}); }
                catch (ReflectiveOperationException error) { throw new CompletionException(error); }
            });
        } catch (ReflectiveOperationException | RuntimeException error) {
            finish("failed", message(error));
            throw error;
        }
    }

    void tick() {
        if (!active()) return;
        elapsed++;
        if (cancellationPending) { finish(pendingResult, pendingReason); return; }
        try {
            if (!ready() || client.world != originWorld || client.player != originPlayer || client.getNetworkHandler() != originConnection) {
                finish("cancelled", "world, player, connection, focus or pause changed"); return;
            }
            if (elapsed >= maxTicks || System.nanoTime() >= deadline) { finish("failed", "flint mining deadline reached; random drops are not guaranteed"); return; }
            if (client.currentScreen != null || client.player.currentScreenHandler != client.player.playerScreenHandler
                || !client.player.currentScreenHandler.getCursorStack().isEmpty()) throw new IllegalStateException("screen or cursor changed during flint mining");
            inventoryCount = count(Items.FLINT);
            if (inventoryCount >= target) { finish("completed", "actual flint inventory target verified"); return; }
            if (phase.equals("preparing_gravel")) {
                lookup = preparation.poll().orElse(null);
                if (lookup != null) mineGravel();
                return;
            }
            if (!phase.equals("collecting_flint")) {
                ItemEntity flint = nearbyDrop(Items.FLINT);
                if (flint != null) { collect(flint, Items.FLINT); return; }
            }
            if (phase.equals("collecting_flint") || phase.equals("collecting_gravel")) { tickPickup(); return; }
            if (phase.equals("equip_gravel") || phase.equals("place_gravel")) { tickPlacement(); return; }
            if (!phase.equals("mining_gravel")) throw new IllegalStateException("unknown flint skill phase");
            if (elapsed - phaseAt > 20 && !navigation.active()) {
                ItemEntity gravel = nearbyDrop(Items.GRAVEL);
                if (gravel != null) collect(gravel, Items.GRAVEL);
                else if (count(Items.GRAVEL) > 0) prepareRecycle();
                else throw new IllegalStateException("no reachable gravel or reusable owned gravel remains");
            }
        } catch (ReflectiveOperationException | RuntimeException error) { finish("failed", message(error)); }
    }

    private boolean ready() {
        return client.player != null && client.world != null && client.interactionManager != null && client.getNetworkHandler() != null
            && client.player.isAlive() && client.isWindowFocused() && !client.isPaused()
            && client.interactionManager.getCurrentGameMode() == GameMode.SURVIVAL;
    }
    private int count(Item item) { return client.player == null ? 0 : client.player.getInventory().count(item); }
    private void setPhase(String next) { phase = next; phaseAt = elapsed; }

    private void mineGravel() throws ReflectiveOperationException {
        if (!navigation.cancel()) throw new IllegalStateException("could not release previous flint navigation");
        navigation.reserveToolMaterials();
        // Baritone samples one gravel loot result, so its quota might count gravel.
        // Disable that quota; this executor counts only actual flint in inventory.
        navigation.minePrepared(0, lookup);
        pickup = null; placement = null;
        setPhase("mining_gravel");
    }

    private ItemEntity nearbyDrop(Item item) {
        return client.world.getEntitiesByClass(ItemEntity.class, client.player.getBoundingBox().expand(32),
                entity -> entity.isAlive() && entity.getStack().isOf(item) && safePickup(entity.getBlockPos()))
            .stream().min(Comparator.comparingDouble(entity -> entity.squaredDistanceTo(client.player))).orElse(null);
    }
    private boolean safePickup(BlockPos position) {
        if (!client.world.isChunkLoaded(position.getX() >> 4, position.getZ() >> 4)) return false;
        var state = client.world.getBlockState(position);
        var floor = client.world.getBlockState(position.down());
        return state.getFluidState().isEmpty() && !state.isOf(Blocks.FIRE) && !state.isOf(Blocks.SOUL_FIRE)
            && !floor.isOf(Blocks.MAGMA_BLOCK) && !floor.isOf(Blocks.CAMPFIRE) && !floor.isOf(Blocks.SOUL_CAMPFIRE)
            && floor.getFluidState().isEmpty();
    }
    private void collect(ItemEntity entity, Item item) throws ReflectiveOperationException {
        if (!navigation.cancel()) throw new IllegalStateException("could not release gravel mining for item pickup");
        navigation.reserveToolMaterials();
        pickup = entity; pickupBefore = count(item);
        navigation.navigateExact(entity.getBlockPos());
        setPhase(item == Items.FLINT ? "collecting_flint" : "collecting_gravel");
    }
    private void tickPickup() throws ReflectiveOperationException {
        Item expected = phase.equals("collecting_flint") ? Items.FLINT : Items.GRAVEL;
        if (count(expected) > pickupBefore) { mineGravel(); return; }
        if (pickup == null || !pickup.isAlive() || !pickup.getStack().isOf(expected)) {
            if (elapsed - phaseAt < 20) return;
            mineGravel(); return;
        }
        if (elapsed - phaseAt >= 160) throw new IllegalStateException("dropped item was not acquired within the pickup deadline");
        if (elapsed - phaseAt > 5 && !navigation.navigationActive()) navigation.navigateExact(pickup.getBlockPos());
    }

    private record Placement(BlockHitResult hit, BlockPos position) {}
    private Placement recyclePlacement() {
        Vec3d eye = client.player.getEyePos();
        BlockPos center = client.player.getBlockPos();
        double nearest = Math.pow(client.player.getBlockInteractionRange(), 2);
        Placement result = null;
        for (BlockPos raw : BlockPos.iterate(center.add(-2, -1, -2), center.add(2, 0, 2))) {
            BlockPos position = raw.toImmutable(), floor = position.down();
            if (!client.world.isChunkLoaded(position.getX() >> 4, position.getZ() >> 4)) continue;
            var support = client.world.getBlockState(floor);
            if (!client.world.getBlockState(position).isAir() || !support.isFullCube(client.world, floor)
                || !support.isSideSolidFullSquare(client.world, floor, Direction.UP) || support.hasBlockEntity()
                || support.isOf(Blocks.CRAFTING_TABLE) || !safePickup(position)) continue;
            if (client.player.getBoundingBox().intersects(new Box(position))
                || !client.world.getOtherEntities(client.player, new Box(position)).isEmpty()) continue;
            Vec3d top = new Vec3d(floor.getX() + .5, floor.getY() + .999, floor.getZ() + .5);
            if (eye.squaredDistanceTo(top) > nearest) continue;
            BlockHitResult hit = client.world.raycast(new RaycastContext(eye, top, RaycastContext.ShapeType.OUTLINE, RaycastContext.FluidHandling.NONE, client.player));
            if (hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(floor) && hit.getSide() == Direction.UP) {
                nearest = eye.squaredDistanceTo(top); result = new Placement(hit, position);
            }
        }
        return result;
    }
    private void prepareRecycle() throws ReflectiveOperationException {
        if (!navigation.cancel()) throw new IllegalStateException("could not release gravel mining before recycling");
        placement = recyclePlacement();
        if (placement == null) throw new IllegalStateException("no reachable safe space to place owned gravel");
        var inventory = client.player.getInventory();
        int source = -1;
        for (int slot = 0; slot < 36; slot++) if (inventory.getStack(slot).isOf(Items.GRAVEL)) { source = slot; break; }
        if (source < 0 && client.player.getOffHandStack().isOf(Items.GRAVEL)) source = 40;
        if (source < 0) throw new IllegalStateException("no owned gravel is available for recycling");
        int hotbar = source < 9 ? source : inventory.selectedSlot;
        if (source >= 9) client.interactionManager.clickSlot(client.player.playerScreenHandler.syncId,
            source == 40 ? 45 : source, hotbar, SlotActionType.SWAP, client.player);
        inventory.selectedSlot = hotbar;
        client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(hotbar));
        setPhase("equip_gravel");
    }
    private void tickPlacement() throws ReflectiveOperationException {
        if (elapsed - phaseAt < 5) return;
        if (phase.equals("equip_gravel")) {
            if (!client.player.getMainHandStack().isOf(Items.GRAVEL)) {
                if (elapsed - phaseAt < 40) return;
                throw new IllegalStateException("owned gravel did not reach the selected hand");
            }
            placement = recyclePlacement();
            if (placement == null) throw new IllegalStateException("safe gravel placement changed");
            if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, placement.hit()).isAccepted()) throw new IllegalStateException("owned gravel placement rejected");
            setPhase("place_gravel");
            return;
        }
        if (client.world.getBlockState(placement.position()).isOf(Blocks.GRAVEL)) { mineGravel(); return; }
        if (elapsed - phaseAt >= 40) throw new IllegalStateException("placed gravel was not confirmed in the world");
    }

    void cancel(String detail) { if (active()) finish("cancelled", detail); }
    private void finish(String result, String detail) {
        preparation.cancel();
        pendingResult = result; pendingReason = detail;
        cancellationPending = navigation != null && !navigation.cancel();
        if (cancellationPending) { status = "running"; phase = "cancellation_pending"; reason = "retaining ownership until gravel controls release"; return; }
        navigation = null; status = result; phase = result;
        reason = detail == null ? result : detail.substring(0, Math.min(300, detail.length()));
        if (originalSlot >= 0 && client.player == originPlayer && client.world == originWorld && client.getNetworkHandler() == originConnection) {
            client.player.getInventory().selectedSlot = originalSlot;
            try { client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(originalSlot)); }
            catch (RuntimeException ignored) { /* A disconnected server cannot acknowledge local selection restoration. */ }
        }
        originalSlot = -1;
    }
    private static String message(Exception error) {
        return error instanceof IllegalArgumentException || error instanceof IllegalStateException ? error.getMessage()
            : "flint API failure: " + error.getClass().getSimpleName();
    }
}
