package ai.holospeed.client;

import net.minecraft.block.Blocks;
import net.minecraft.block.entity.AbstractFurnaceBlockEntity;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ingame.FurnaceScreen;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket;
import net.minecraft.registry.Registries;
import net.minecraft.screen.FurnaceScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.GameMode;
import net.minecraft.world.RaycastContext;

import java.util.HashSet;
import java.util.Set;

/** A bounded iron-smelting skill. All changes use ordinary player interactions. */
public final class FurnaceController {
    private static final int INPUT = 0, FUEL = 1, OUTPUT = 2;
    private static final Set<String> IRON_INPUTS = Set.of("minecraft:raw_iron", "minecraft:iron_ore", "minecraft:deepslate_iron_ore");
    private static final Set<String> PLANK_FUELS = new HashSet<>();
    private static final Set<String> LOG_FUELS = new HashSet<>();
    static {
        for (String wood : new String[]{"oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry"}) {
            PLANK_FUELS.add("minecraft:" + wood + "_planks");
            LOG_FUELS.add("minecraft:" + wood + "_log");
            LOG_FUELS.add("minecraft:stripped_" + wood + "_log");
        }
        PLANK_FUELS.add("minecraft:bamboo_planks");
    }

    record SmeltPlan(int missingIngots, int inputToLoad, boolean needsFuel, boolean complete) {}

    static void validateBounds(int targetCount, int maxTicks) {
        if (targetCount < 1 || targetCount > 64) throw new IllegalArgumentException("iron target must be between 1 and 64 total ingots");
        if (maxTicks < 20 || maxTicks > 1800) throw new IllegalArgumentException("smelting deadline must be between 20 and 1800 ticks");
    }

    /** Counts already in the furnace prevent loading or burning extra resources. */
    static SmeltPlan plan(int target, int inventory, int input, int output, int fuel, boolean burning) {
        validateBounds(target, 20);
        for (int count : new int[]{inventory, input, output, fuel}) {
            if (count < 0 || count > 4096) throw new IllegalArgumentException("invalid smelting inventory count");
        }
        int missing = Math.max(0, target - inventory);
        int toLoad = Math.max(0, missing - input - output);
        boolean needsHeat = missing > output && (input > 0 || toLoad > 0);
        return new SmeltPlan(missing, toLoad, needsHeat && !burning && fuel == 0, missing == 0);
    }

    static boolean ironInput(String registryId) { return registryId != null && IRON_INPUTS.contains(registryId); }

    /** Local slot predictions cannot prove a completed smelt without a newer server sync. */
    static boolean confirmedInventory(long requestedAfter, long observedSequence, int target, int inventory) {
        validateBounds(target, 20);
        return requestedAfter >= 0 && observedSequence > requestedAfter && inventory >= target;
    }

    /** Deliberately excludes wooden tools, sticks, workstations, and nonflammable Nether wood. */
    static int fuelRank(String registryId) {
        if ("minecraft:coal".equals(registryId)) return 0;
        if ("minecraft:charcoal".equals(registryId)) return 1;
        if (PLANK_FUELS.contains(registryId)) return 2;
        if (LOG_FUELS.contains(registryId)) return 3;
        return Integer.MAX_VALUE;
    }

    private final MinecraftClient client;
    private final WorkstationPlacement placementHelper;
    private String status = "idle", phase = "idle", reason;
    private int target, maxTicks, elapsed, phaseAt, inventoryCount;
    private int lastClickAt, lastProgressAt, previousTotal, stableCount, stableSince;
    private float previousCook;
    private long deadline;
    private Object world, player, connection;
    private FurnaceScreenHandler handler;
    private FurnaceScreenHandler ownedHandler;
    private boolean confirmingInventory;
    private long furnaceSyncAfter;
    private int originalSelectedSlot = -1;
    private Hand furnaceHand = Hand.MAIN_HAND;
    private BlockPos placedFurnace;
    private Object furnaceWorld;
    private BlockPos ownedFurnace;
    private int transferSource = -1, transferBefore;
    private Item transferItem;

    public FurnaceController(MinecraftClient client) {
        this.client = client; this.placementHelper = new WorkstationPlacement(client);
    }

    public boolean active() { return status.equals("running"); }
    public String status() { return status; }
    public String phase() { return phase; }
    public String reason() { return reason; }
    public int inventoryCount() { return inventoryCount; }

    /** Only a furnace placed and observed by this executor is an eligible workstation. */
    public BlockPos knownFurnace() {
        if (client.world == null || furnaceWorld != client.world) {
            furnaceWorld = client.world; ownedFurnace = null; ownedHandler = null;
        }
        if (ownedFurnace != null && loaded(ownedFurnace) && !client.world.getBlockState(ownedFurnace).isOf(Blocks.FURNACE)) {
            ownedFurnace = null; ownedHandler = null;
        }
        return ownedFurnace;
    }

    /** Target is total ingots in the player's inventory, not a number to add. */
    public void start(int targetCount, int maximumTicks) {
        if (active()) throw new IllegalStateException("smelting already owns the controls");
        validateBounds(targetCount, maximumTicks);
        if (!client.isOnThread() || !ready()) throw new IllegalStateException("a focused, unpaused survival client is required");
        if (client.currentScreen != null && !(client.currentScreen instanceof FurnaceScreen)) throw new IllegalStateException("close the unrelated screen before smelting");
        if (client.currentScreen == null && client.player.currentScreenHandler != client.player.playerScreenHandler) throw new IllegalStateException("wait for the previous container to close before smelting");
        knownFurnace();
        if (client.currentScreen instanceof FurnaceScreen
            && (ownedFurnace == null || client.player.currentScreenHandler != ownedHandler)) {
            throw new IllegalStateException("only the agent's placed furnace may be used; unrelated contents were preserved");
        }
        requireEmptyCursor();
        target = targetCount; maxTicks = maximumTicks; elapsed = 0;
        world = client.world; player = client.player; connection = client.getNetworkHandler();
        deadline = System.nanoTime() + (long) (maximumTicks * 50_000_000L) + 2_000_000_000L;
        handler = null; placedFurnace = null; originalSelectedSlot = -1;
        confirmingInventory = false; furnaceSyncAfter = ServerInventorySync.INSTANCE.latest(connection);
        transferSource = -1; transferItem = null;
        lastClickAt = 0; lastProgressAt = 0; previousTotal = -1; previousCook = -1;
        stableCount = -1; stableSince = 0;
        inventoryCount = countIngots();
        status = "running"; reason = null; setPhase("await_furnace");
        try {
            if (inventoryCount >= target) { finish("completed", "inventory target already satisfied"); return; }
            if (client.currentScreen instanceof FurnaceScreen) {
                // Resume only through another server-issued inventory snapshot.
                client.player.closeHandledScreen();
                ownedHandler = null;
            }
            BlockHitResult hit = reachableFurnace();
            if (hit != null) openFurnace(hit);
            else preparePlacement();
        } catch (RuntimeException error) {
            finish("failed", error.getMessage());
            throw error;
        }
    }

    private boolean ready() {
        return client.player != null && client.world != null && client.interactionManager != null
            && client.getNetworkHandler() != null
            && client.player.isAlive() && client.isWindowFocused() && !client.isPaused()
            && client.interactionManager.getCurrentGameMode() == GameMode.SURVIVAL;
    }

    private int countIngots() { return client.player == null ? 0 : client.player.getInventory().count(Items.IRON_INGOT); }
    private static String itemId(ItemStack stack) { return Registries.ITEM.getId(stack.getItem()).toString(); }
    private static int count(ItemStack stack) { return stack.isEmpty() ? 0 : stack.getCount(); }
    private void requireEmptyCursor() {
        if (!client.player.currentScreenHandler.getCursorStack().isEmpty()) throw new IllegalStateException("cursor stack must be empty; no items were discarded");
    }
    private void setPhase(String next) { phase = next; phaseAt = elapsed; }

    public void tick() {
        if (!active()) return;
        elapsed++;
        if (!ready() || world != client.world || player != client.player || connection != client.getNetworkHandler()) { cancel("world, player, connection, focus or pause changed"); return; }
        if (elapsed >= maxTicks || System.nanoTime() >= deadline) { finish("failed", "smelting deadline reached"); return; }
        inventoryCount = countIngots();
        try {
            requireEmptyCursor();
            if (tickPlacement()) return;
            if (!(client.currentScreen instanceof FurnaceScreen)
                || !(client.player.currentScreenHandler instanceof FurnaceScreenHandler current)) {
                if (phase.equals("await_furnace") && elapsed - phaseAt < 40) return;
                throw new IllegalStateException("expected furnace screen is unavailable");
            }
            if (handler != null && handler != current) throw new IllegalStateException("furnace screen changed during smelting");
            handler = current;
            if (phase.equals("await_furnace")) {
                long observed = ServerInventorySync.INSTANCE.sequence(connection, handler.syncId);
                if (observed <= furnaceSyncAfter) {
                    if (elapsed - phaseAt >= 40) throw new IllegalStateException("server furnace inventory did not synchronize");
                    return;
                }
                ownedHandler = handler;
                if (confirmingInventory) {
                    if (!confirmedInventory(furnaceSyncAfter, observed, target, inventoryCount)) {
                        throw new IllegalStateException("server-confirmed inventory lacks the requested iron ingots");
                    }
                    client.player.closeHandledScreen();
                    finish("completed", "iron inventory target verified by a fresh server inventory update");
                    return;
                }
            }
            validateContents();
            if (phase.equals("await_transfer")) {
                if (elapsed - phaseAt < 10) return;
                ItemStack source = handler.getSlot(transferSource).getStack();
                int after = source.isOf(transferItem) ? source.getCount() : 0;
                if (after != transferBefore - 1) throw new IllegalStateException("furnace item transfer was not synchronized");
                transferSource = -1; transferItem = null; setPhase("smelting");
            } else if (phase.equals("await_collection")) {
                if (elapsed - phaseAt < 10) return;
                if (inventoryCount <= transferBefore) throw new IllegalStateException("iron output did not reach inventory; check inventory space");
                setPhase("smelting");
            } else if (phase.equals("await_furnace")) {
                // The opening server inventory packet above is authoritative.
                setPhase("smelting");
            }
            int input = count(handler.getSlot(INPUT).getStack());
            int output = count(handler.getSlot(OUTPUT).getStack());
            int fuel = count(handler.getSlot(FUEL).getStack());
            SmeltPlan plan = plan(target, inventoryCount, input, output, fuel, handler.isBurning());
            if (plan.complete()) {
                if (stableCount != inventoryCount) { stableCount = inventoryCount; stableSince = elapsed; }
                setPhaseIfNeeded("verify_inventory");
                if (elapsed - lastClickAt >= 10 && elapsed - stableSince >= 3) {
                    client.player.closeHandledScreen();
                    handler = null; ownedHandler = null; confirmingInventory = true;
                    BlockHitResult hit = reachableFurnace();
                    if (hit == null) throw new IllegalStateException("owned furnace is unreachable for server inventory confirmation");
                    openFurnace(hit);
                }
                return;
            }
            stableCount = -1;
            if (output > 0) {
                transferBefore = inventoryCount;
                client.interactionManager.clickSlot(handler.syncId, OUTPUT, 0, SlotActionType.QUICK_MOVE, client.player);
                lastClickAt = elapsed; setPhase("await_collection"); return;
            }
            if (plan.inputToLoad() > availableInput()) throw new IllegalStateException("not enough raw iron or iron ore for the requested inventory target");
            if (plan.needsFuel() && findFuel() < 0) throw new IllegalStateException("coal, charcoal, planks or logs are required as furnace fuel");
            // Load one fuel unit first so smelting overlaps the later input transfers.
            if (plan.needsFuel()) { transferOne(findFuel(), FUEL); return; }
            if (plan.inputToLoad() > 0) {
                int source = findInput(handler.getSlot(INPUT).getStack());
                if (source >= 0) { transferOne(source, INPUT); return; }
                // A different supported input must wait until the current stack is consumed.
                if (input == 0) throw new IllegalStateException("compatible iron input is unavailable in the main inventory");
            }
            float cook = handler.getCookProgress();
            int total = inventoryCount + input + output;
            if (total != previousTotal || Float.compare(cook, previousCook) != 0) {
                lastProgressAt = elapsed; previousTotal = total; previousCook = cook;
            }
            if (elapsed - lastProgressAt >= 400) throw new IllegalStateException("furnace made no cooking or inventory progress for 400 ticks");
            setPhaseIfNeeded("smelting");
        } catch (RuntimeException error) { finish("failed", error.getMessage()); }
    }

    private void setPhaseIfNeeded(String next) { if (!phase.equals(next)) setPhase(next); }

    private void validateContents() {
        ItemStack input = handler.getSlot(INPUT).getStack();
        ItemStack output = handler.getSlot(OUTPUT).getStack();
        ItemStack fuel = handler.getSlot(FUEL).getStack();
        if (!input.isEmpty() && !ironInput(itemId(input))) throw new IllegalStateException("furnace contains another recipe; existing contents were preserved");
        if (!output.isEmpty() && !output.isOf(Items.IRON_INGOT)) throw new IllegalStateException("furnace contains noniron output; existing contents were preserved");
        if (!fuel.isEmpty() && !AbstractFurnaceBlockEntity.canUseAsFuel(fuel) && !handler.isBurning()) {
            throw new IllegalStateException("furnace fuel slot contains an unusable item; it was preserved");
        }
    }

    private boolean playerSlot(Slot slot) { return slot.inventory == client.player.getInventory(); }

    private int availableInput() {
        int count = 0;
        for (int index = 3; index < handler.slots.size(); index++) {
            Slot slot = handler.getSlot(index);
            if (playerSlot(slot) && ironInput(itemId(slot.getStack()))) count += slot.getStack().getCount();
        }
        return count;
    }

    private int findInput(ItemStack existing) {
        int best = -1, rank = Integer.MAX_VALUE;
        for (int index = 3; index < handler.slots.size(); index++) {
            Slot slot = handler.getSlot(index); ItemStack stack = slot.getStack();
            if (!playerSlot(slot) || stack.isEmpty() || !ironInput(itemId(stack))) continue;
            if (!existing.isEmpty() && !ItemStack.areItemsAndComponentsEqual(stack, existing)) continue;
            int candidateRank = stack.isOf(Items.RAW_IRON) ? 0 : stack.isOf(Items.IRON_ORE) ? 1 : 2;
            if (candidateRank < rank) { rank = candidateRank; best = index; }
        }
        return best;
    }

    private int findFuel() {
        int best = -1, rank = Integer.MAX_VALUE;
        for (int index = 3; index < handler.slots.size(); index++) {
            Slot slot = handler.getSlot(index); ItemStack stack = slot.getStack();
            if (!playerSlot(slot) || stack.isEmpty() || !AbstractFurnaceBlockEntity.canUseAsFuel(stack)) continue;
            int candidateRank = fuelRank(itemId(stack));
            if (candidateRank < rank) { rank = candidateRank; best = index; }
        }
        return best;
    }

    /** Three normal clicks move exactly one item and return the rest to its original slot. */
    private void transferOne(int sourceIndex, int destinationIndex) {
        if (sourceIndex < 3 || destinationIndex < INPUT || destinationIndex > FUEL) throw new IllegalStateException("invalid furnace transfer");
        Slot source = handler.getSlot(sourceIndex), destination = handler.getSlot(destinationIndex);
        ItemStack stack = source.getStack(), existing = destination.getStack();
        if (!playerSlot(source) || stack.isEmpty() || !source.canTakeItems(client.player) || !destination.canInsert(stack)
            || (!existing.isEmpty() && !ItemStack.areItemsAndComponentsEqual(stack, existing))
            || existing.getCount() >= destination.getMaxItemCount(stack)) throw new IllegalStateException("furnace transfer precondition changed");
        requireEmptyCursor();
        transferSource = sourceIndex; transferBefore = stack.getCount(); transferItem = stack.getItem();
        ItemStack expected = stack.copy();
        client.interactionManager.clickSlot(handler.syncId, sourceIndex, 0, SlotActionType.PICKUP, client.player);
        try {
            ItemStack cursor = handler.getCursorStack();
            if (!ItemStack.areItemsAndComponentsEqual(cursor, expected) || cursor.getCount() != transferBefore) {
                throw new IllegalStateException("source pickup did not match the planned furnace transfer");
            }
            client.interactionManager.clickSlot(handler.syncId, destinationIndex, 1, SlotActionType.PICKUP, client.player);
        } finally {
            // The original slot always has room for the unchanged remainder. Never click outside.
            ItemStack cursor = handler.getCursorStack(), original = source.getStack();
            if (!cursor.isEmpty()) {
                if ((!original.isEmpty() && !ItemStack.areItemsAndComponentsEqual(cursor, original))
                    || original.getCount() + cursor.getCount() > source.getMaxItemCount(cursor)) {
                    throw new IllegalStateException("source slot changed; cursor stack retained for recovery");
                }
                client.interactionManager.clickSlot(handler.syncId, sourceIndex, 0, SlotActionType.PICKUP, client.player);
            }
        }
        requireEmptyCursor();
        lastClickAt = elapsed; setPhase("await_transfer");
    }

    private void openFurnace(BlockHitResult hit) {
        if (client.player.isSneaking()) throw new IllegalStateException("release sneak before opening the furnace");
        furnaceSyncAfter = ServerInventorySync.INSTANCE.latest(connection);
        if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hit).isAccepted()) throw new IllegalStateException("furnace interaction was rejected");
        setPhase("await_furnace");
    }

    private void preparePlacement() {
        placementHelper.reset();
        if (placement() == null) throw new IllegalStateException("no reachable safe furnace placement space");
        var inventory = client.player.getInventory();
        int slot = -1;
        for (int index = 0; index < 36; index++) if (inventory.getStack(index).isOf(Items.FURNACE)) { slot = index; break; }
        furnaceHand = Hand.MAIN_HAND;
        if (slot < 0) {
            if (!client.player.getOffHandStack().isOf(Items.FURNACE)) throw new IllegalStateException("a carried or previously self-placed furnace is required");
            furnaceHand = Hand.OFF_HAND;
        } else {
            originalSelectedSlot = inventory.selectedSlot;
            int hotbar = slot;
            if (slot >= 9) {
                hotbar = originalSelectedSlot;
                for (int index = 0; index < 9; index++) if (inventory.getStack(index).isEmpty()) { hotbar = index; break; }
                client.interactionManager.clickSlot(client.player.playerScreenHandler.syncId, slot, hotbar, SlotActionType.SWAP, client.player);
            }
            inventory.selectedSlot = hotbar;
            client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(hotbar));
        }
        setPhase("await_furnace_item");
    }

    private WorkstationPlacement.Candidate placement() { return placementHelper.find(); }

    private boolean loaded(BlockPos position) { return client.world.isChunkLoaded(position.getX() >> 4, position.getZ() >> 4); }
    private BlockHitResult raycast(Vec3d eye, Vec3d target) {
        return client.world.raycast(new RaycastContext(eye, target, RaycastContext.ShapeType.OUTLINE, RaycastContext.FluidHandling.NONE, client.player));
    }
    private BlockHitResult reachableFurnace() {
        BlockPos position = knownFurnace();
        if (position == null || !loaded(position)) return null;
        Vec3d eye = client.player.getEyePos(), target = Vec3d.ofCenter(position);
        if (eye.squaredDistanceTo(target) > Math.pow(client.player.getBlockInteractionRange(), 2)) return null;
        BlockHitResult hit = raycast(eye, target);
        return hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(position) ? hit : null;
    }

    private boolean tickPlacement() {
        if (!phase.equals("await_furnace_item") && !phase.equals("clearing_furnace_space") && !phase.equals("await_furnace_placement")) return false;
        if (client.currentScreen != null || client.player.currentScreenHandler != client.player.playerScreenHandler) throw new IllegalStateException("screen changed during furnace placement");
        if (!phase.equals("clearing_furnace_space") && elapsed - phaseAt < 5) return true;
        if (phase.equals("await_furnace_item") || phase.equals("clearing_furnace_space")) {
            if (placementHelper.pending() && !placementHelper.tickClear(elapsed)) return true;
            if (!client.player.getStackInHand(furnaceHand).isOf(Items.FURNACE)) {
                if (elapsed - phaseAt < 40) return true;
                throw new IllegalStateException("furnace did not reach the selected hand");
            }
            WorkstationPlacement.Candidate target = placement();
            if (target == null) throw new IllegalStateException("safe furnace placement space changed");
            if (target.clear() != null) {
                placementHelper.beginClear(target, elapsed);
                setPhase("clearing_furnace_space");
                return true;
            }
            if (!client.interactionManager.interactBlock(client.player, furnaceHand, target.hit()).isAccepted()) throw new IllegalStateException("furnace placement was rejected");
            placedFurnace = target.position(); setPhase("await_furnace_placement"); return true;
        }
        if (!loaded(placedFurnace) || !client.world.getBlockState(placedFurnace).isOf(Blocks.FURNACE)) {
            if (elapsed - phaseAt < 40) return true;
            throw new IllegalStateException("placed furnace was not confirmed in the client world");
        }
        furnaceWorld = client.world; ownedFurnace = placedFurnace.toImmutable();
        restoreSelectedSlot();
        BlockHitResult hit = reachableFurnace();
        if (hit == null) throw new IllegalStateException("placed furnace is no longer reachable");
        openFurnace(hit); return true;
    }

    public void cancel(String detail) { if (active()) finish("cancelled", detail); }

    private void finish(String result, String detail) {
        placementHelper.cancel();
        status = result; phase = result;
        reason = detail == null ? result : detail.substring(0, Math.min(300, detail.length()));
        restoreSelectedSlot();
        // Cancellation never closes a cursor-bearing screen or removes furnace contents.
    }

    private void restoreSelectedSlot() {
        if (originalSelectedSlot < 0) return;
        int selected = originalSelectedSlot; originalSelectedSlot = -1;
        if (client.player == player && client.world == world && client.getNetworkHandler() != null) {
            client.player.getInventory().selectedSlot = selected;
            try { client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(selected)); }
            catch (RuntimeException ignored) { /* Local selection restored; a disconnected server cannot acknowledge. */ }
        }
    }
}
