package ai.holospeed.client;

import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonArray;
import net.minecraft.block.Blocks;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.ingame.CraftingScreen;
import net.minecraft.client.gui.screen.ingame.InventoryScreen;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket;
import net.minecraft.recipe.RecipeEntry;
import net.minecraft.recipe.RecipeMatcher;
import net.minecraft.registry.Registries;
import net.minecraft.screen.AbstractRecipeScreenHandler;
import net.minecraft.screen.CraftingScreenHandler;
import net.minecraft.screen.slot.SlotActionType;
import net.minecraft.util.Hand;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.GameMode;
import net.minecraft.world.RaycastContext;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Bounded, tick-driven skills using ordinary survival interactions only. */
public final class SkillController {
    private static final ExecutorService MINING_PREPARER = Executors.newSingleThreadExecutor(task -> {
        Thread thread = new Thread(task, "holospeed-mining-preparation");
        thread.setDaemon(true);
        return thread;
    });
    private static final String[] WOODS = {"oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "mangrove", "cherry"};
    private static final Set<String> MINE_ITEMS = new HashSet<>();
    private static final Set<String> CRAFT_ITEMS = new HashSet<>();
    private static final Set<String> TABLE_RECIPES = Set.of("minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:stone_axe", "minecraft:furnace", "minecraft:bucket");
    private static final Set<String> TOOL_MATERIALS = Set.of("minecraft:cobblestone", "minecraft:cobbled_deepslate", "minecraft:blackstone");
    private static final Set<String> REQUEST_FIELDS = Set.of("type", "id", "skill", "item", "target_count", "max_ticks", "target");
    static {
        for (String wood : WOODS) {
            MINE_ITEMS.add("minecraft:" + wood + "_log");
            CRAFT_ITEMS.add("minecraft:" + wood + "_planks");
        }
        MINE_ITEMS.addAll(Set.of("minecraft:stone", "minecraft:cobblestone", "minecraft:deepslate", "minecraft:cobbled_deepslate", "minecraft:raw_iron", "minecraft:coal", "minecraft:flint", "minecraft:dirt"));
        CRAFT_ITEMS.addAll(Set.of("minecraft:stick", "minecraft:crafting_table", "minecraft:wooden_pickaxe", "minecraft:stone_pickaxe", "minecraft:stone_axe", "minecraft:furnace", "minecraft:bucket", "minecraft:flint_and_steel"));
    }

    record SkillRequest(String kind, String item, int targetCount, int maxTicks, PortalRoute.Point target) {}

    static boolean reserveForCrafting(String registryId) { return registryId != null && TOOL_MATERIALS.contains(registryId); }

    static SkillRequest parseRequest(JsonObject request) {
        if (request == null || !REQUEST_FIELDS.containsAll(request.keySet())) throw new IllegalArgumentException("unsupported skill request field");
        String kind = string(request, "skill");
        String item = string(request, "item");
        if (!item.contains(":")) item = "minecraft:" + item;
        if (!Set.of("mine", "craft", "smelt", "cast_portal").contains(kind)) throw new IllegalArgumentException("unsupported skill");
        PortalRoute.Point target = null;
        if (kind.equals("cast_portal")) {
            if (!item.equals("minecraft:nether_portal")) throw new IllegalArgumentException("unsupported portal target");
            if (!request.has("target") || !request.get("target").isJsonObject()) throw new IllegalArgumentException("portal landmark target must be an object");
            target = CastPortalPlan.parseTarget(request.getAsJsonObject("target"));
        } else if (kind.equals("smelt")) {
            if (request.has("target") || !item.equals("minecraft:iron_ingot")) throw new IllegalArgumentException("only iron ingot smelting is supported");
        } else {
            if (request.has("target")) throw new IllegalArgumentException("only portal skills accept a landmark target");
            if (!(kind.equals("mine") ? MINE_ITEMS : CRAFT_ITEMS).contains(item)) throw new IllegalArgumentException("unsupported skill target");
        }
        int count = integer(request, "target_count", 1, 1, 64);
        int ticks = integer(request, "max_ticks", 1200, 20, 1800);
        if (kind.equals("cast_portal") && count != 1) throw new IllegalArgumentException("portal target_count must be1");
        return new SkillRequest(kind, item, count, ticks, target);
    }

    private static String string(JsonObject request, String field) {
        if (!request.has(field) || !request.get(field).isJsonPrimitive() || !request.getAsJsonPrimitive(field).isString()) throw new IllegalArgumentException("missing or invalid " + field);
        String value = request.get(field).getAsString();
        if (value.isEmpty() || value.length() > 128) throw new IllegalArgumentException("invalid " + field);
        return value;
    }

    private static int integer(JsonObject request, String field, int fallback, int minimum, int maximum) {
        if (!request.has(field)) return fallback;
        try {
            if (!request.get(field).isJsonPrimitive() || !request.getAsJsonPrimitive(field).isNumber()) throw new ArithmeticException();
            int value = request.get(field).getAsBigDecimal().intValueExact();
            if (value < minimum || value > maximum) throw new ArithmeticException();
            return value;
        } catch (RuntimeException error) { throw new IllegalArgumentException("invalid " + field); }
    }

    /** Position change is only a liveness signal; inventory is the completion condition. */
    static final class ProgressWatch {
        private int count;
        private int changedAt;
        private double x, y, z;
        ProgressWatch(int initialCount, double x, double y, double z) { this.count = initialCount; this.x = x; this.y = y; this.z = z; }
        void observe(int elapsed, int inventoryCount, double nextX, double nextY, double nextZ) {
            double distance = (nextX - x) * (nextX - x) + (nextY - y) * (nextY - y) + (nextZ - z) * (nextZ - z);
            if (inventoryCount > count || distance >= 0.0625) {
                changedAt = elapsed;
                count = Math.max(count, inventoryCount);
                x = nextX; y = nextY; z = nextZ;
            }
        }
        boolean stalled(int elapsed) { return elapsed - changedAt >= 400; }
    }

    private final MinecraftClient client;
    private final boolean baritoneAvailable;
    private final DeferredPreparation<Object> miningPreparation = new DeferredPreparation<>();
    private final TableMemory tableMemory = new TableMemory();
    private SkillRequest request;
    private String skillId;
    private String status = "idle";
    private String phase = "idle";
    private String reason;
    private Object startWorld;
    private Object startPlayer;
    private int elapsed;
    private int phaseAt;
    private int inventoryCount;
    private int cycleCount;
    private int stableCount;
    private int stableSince;
    private long wallDeadline;
    private BaritoneAccess baritone;
    private CastPortalSkill portal;
    private final FurnaceController furnace;
    private final FlintSkill flint;
    private final WorkstationPlacement tablePlacementHelper;
    private boolean cancellationPending;
    private ProgressWatch progress;
    private AbstractRecipeScreenHandler<?, ?> craftHandler;
    private boolean openedScreen;
    private int originalSelectedSlot = -1;
    private Hand tableHand = Hand.MAIN_HAND;
    private BlockPos placedTable;
    private BlockPos returningTable;
    private long clientTicks;
    private long nextTableDiscoveryTick;
    private Object discoveryWorld;
    private BlockPos lastTableDiscoveryPosition;

    public SkillController(MinecraftClient client) {
        this.client = client;
        this.baritoneAvailable = BaritoneAccess.available();
        this.furnace = new FurnaceController(client);
        this.flint = new FlintSkill(client);
        this.tablePlacementHelper = new WorkstationPlacement(client);
    }

    public boolean active() { return status.equals("running") || cancellationPending || portal != null && portal.active() || furnace.active() || flint.active(); }
    public boolean permitsWorldTransition() { return portal != null && portal.permitsWorldTransition(); }

    public JsonObject start(JsonObject message) {
        String id = message != null && message.has("id") && message.get("id").isJsonPrimitive() ? message.get("id").getAsString() : "";
        if (active()) return ack(id, "rejected", "another skill owns the controls");
        try {
            SkillRequest proposed = parseRequest(message);
            if (!client.isOnThread()) throw new IllegalArgumentException("skills must start on the client thread");
            if (!ready()) throw new IllegalArgumentException("a focused, unpaused survival world is required");
            if (proposed.kind().equals("mine") && client.currentScreen != null) throw new IllegalArgumentException("close the current screen before mining");
            if (proposed.kind().equals("craft") && client.currentScreen != null && !(client.currentScreen instanceof InventoryScreen) && !(client.currentScreen instanceof CraftingScreen)) throw new IllegalArgumentException("close the unrelated screen before crafting");
            if (!client.player.currentScreenHandler.getCursorStack().isEmpty()) throw new IllegalArgumentException("empty the cursor stack before starting a skill");
            request = proposed;
            skillId = UUID.randomUUID().toString();
            status = "running"; reason = null; elapsed = 0; phaseAt = 0;
            startWorld = client.world; startPlayer = client.player;
            craftHandler = null; openedScreen = false; cancellationPending = false;
            originalSelectedSlot = -1; placedTable = null; returningTable = null;
            portal = null;
            inventoryCount = countInventory();
            wallDeadline = System.nanoTime() + (long) ((request.maxTicks() / 20.0 + 2) * 1_000_000_000L);
            if (inventoryCount >= request.targetCount()) {
                finish("completed", "inventory target already satisfied");
                return ack(id, "completed", reason);
            }
            if (request.kind().equals("cast_portal")) {
                if (!baritoneAvailable) throw new IllegalArgumentException("Baritone Fabric API1.11.3 is required for portal navigation");
                portal = new CastPortalSkill(client);
                portal.start(request.target(), request.maxTicks());
                phase = portal.phase();
            } else if (request.kind().equals("smelt")) {
                furnace.start(request.targetCount(), request.maxTicks()); phase = furnace.phase();
                if (!furnace.active()) { status = furnace.status(); reason = furnace.reason(); }
            } else if (request.kind().equals("mine") && request.item().equals("minecraft:flint")) {
                if (!baritoneAvailable) throw new IllegalArgumentException("Baritone Fabric API1.11.3 is required for gravel mining");
                flint.start(request.targetCount(), request.maxTicks()); phase = flint.phase();
            } else if (request.kind().equals("mine")) {
                if (!baritoneAvailable) throw new IllegalArgumentException("Baritone Fabric API 1.11.3 is not installed");
                beginMining();
            }
            else beginCrafting();
            return ack(id, status.equals("running") ? "accepted" : "rejected", reason);
        } catch (ReflectiveOperationException | RuntimeException error) {
            if (status.equals("running") || baritone != null) finish("failed", message(error));
            return ack(id, "rejected", message(error));
        }
    }

    private JsonObject ack(String id, String result, String detail) {
        JsonObject reply = new JsonObject();
        reply.addProperty("type", "skill_ack"); reply.addProperty("id", id);
        reply.addProperty("status", result);
        if (skillId == null) reply.add("skill_id", JsonNull.INSTANCE); else reply.addProperty("skill_id", skillId);
        if (detail == null) reply.add("reason", JsonNull.INSTANCE); else reply.addProperty("reason", bounded(detail));
        reply.add("skill", snapshot());
        return reply;
    }

    private boolean ready() {
        return client.player != null && client.world != null && client.interactionManager != null
            && client.player.isAlive() && client.isWindowFocused() && !client.isPaused()
            && client.interactionManager.getCurrentGameMode() == GameMode.SURVIVAL;
    }

    private int countInventory() {
        if (client.player == null || request == null) return 0;
        if (request.kind().equals("cast_portal")) return 0;
        String item = outputItem(request.item());
        return client.player.getInventory().count(Registries.ITEM.get(Identifier.of(item)));
    }

    private static String outputItem(String item) {
        return item.equals("minecraft:stone") ? "minecraft:cobblestone"
            : item.equals("minecraft:deepslate") ? "minecraft:cobbled_deepslate" : item;
    }

    private void beginMining() throws ReflectiveOperationException {
        String target = request.item();
        String[] blocks = target.equals("minecraft:cobblestone") ? new String[]{"minecraft:stone", "minecraft:cobblestone"}
            : target.equals("minecraft:cobbled_deepslate") ? new String[]{"minecraft:deepslate", "minecraft:cobbled_deepslate"}
            : target.equals("minecraft:raw_iron") ? new String[]{"minecraft:iron_ore", "minecraft:deepslate_iron_ore"}
            : target.equals("minecraft:coal") ? new String[]{"minecraft:coal_ore", "minecraft:deepslate_coal_ore"}
            : target.equals("minecraft:dirt") ? new String[]{"minecraft:dirt", "minecraft:grass_block"}
            : new String[]{target};
        setPhase("preparing_mining");
        // Baritone 1.11.3's lookup constructor loads drop registries through a
        // future completed on MinecraftClient. Calling mineByName on this
        // thread would join that future and deadlock rendering/input handling.
        // The worker only prepares an immutable lookup; it never starts mining.
        miningPreparation.start(MINING_PREPARER, () -> {
            try { return BaritoneAccess.prepareLookup(blocks); }
            catch (ReflectiveOperationException error) { throw new CompletionException(error); }
        });
    }

    private void launchMining(Object lookup) throws ReflectiveOperationException {
        String target = request.item();
        originalSelectedSlot = client.player.getInventory().selectedSlot;
        if (target.contains("stone") || target.contains("deepslate") || target.equals("minecraft:raw_iron") || target.equals("minecraft:coal")) {
            var requiredBlock = target.equals("minecraft:raw_iron") ? Blocks.IRON_ORE.getDefaultState() : Blocks.STONE.getDefaultState();
            int toolSlot = -1;
            for (int slot = 0; slot < 36; slot++) {
                if (client.player.getInventory().getStack(slot).isSuitableFor(requiredBlock)) { toolSlot = slot; break; }
            }
            if (toolSlot < 0 && client.player.getOffHandStack().isSuitableFor(requiredBlock)) toolSlot = 40;
            if (toolSlot < 0) throw new IllegalArgumentException("a suitable pickaxe is required to collect stone drops");
            selectInventoryItem(toolSlot);
        }
        baritone = new BaritoneAccess();
        if (!baritone.cancel()) throw new IllegalStateException("could not obtain exclusive Baritone control");
        Vec3d position = client.player.getPos();
        progress = new ProgressWatch(inventoryCount, position.x, position.y, position.z);
        setPhase("mining");
        baritone.minePrepared(request.targetCount(), lookup);
    }

    private void beginCrafting() throws ReflectiveOperationException {
        setPhase("await_screen");
        if (TABLE_RECIPES.contains(request.item())) {
            if (client.player.currentScreenHandler instanceof CraftingScreenHandler) {
                reachableTable(); // Remember a directly observed table when one is visible.
                return;
            }
            if (client.currentScreen instanceof InventoryScreen) {
                if (client.player.currentScreenHandler instanceof AbstractRecipeScreenHandler<?, ?> handler) requireEmptyGrid(handler);
                client.player.closeHandledScreen();
            }
            if (client.currentScreen != null) throw new IllegalArgumentException("close the unrelated screen before placing or opening a table");
            BlockHitResult hit = reachableTable();
            if (hit != null) openTable(hit);
            else if (client.player.getInventory().count(Items.CRAFTING_TABLE) > 0) prepareTablePlacement();
            else beginTableReturn();
        } else if (client.currentScreen == null) {
            client.setScreen(new InventoryScreen(client.player));
            openedScreen = true;
        }
    }

    private void openTable(BlockHitResult hit) {
        if (client.player.isSneaking()) throw new IllegalStateException("release sneak before opening the crafting table");
        rememberTable(hit.getBlockPos());
        if (!client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hit).isAccepted()) throw new IllegalArgumentException("crafting table interaction was rejected");
        openedScreen = true;
        setPhase("await_screen");
    }

    private void rememberTable(BlockPos position) {
        if (client.world != null && client.world.isChunkLoaded(position.getX() >> 4, position.getZ() >> 4)
            && client.world.getBlockState(position).isOf(Blocks.CRAFTING_TABLE)) {
            tableMemory.remember(client.world, new TableMemory.Position(position.getX(), position.getY(), position.getZ()));
        }
    }

    /** An unloaded position is still a memory, never evidence that a table vanished. */
    private BlockPos knownTable() {
        TableMemory.Position position = tableMemory.position(client.world);
        if (position == null || client.world == null) return null;
        BlockPos block = new BlockPos(position.x(), position.y(), position.z());
        if (client.world.isChunkLoaded(block.getX() >> 4, block.getZ() >> 4)
            && !client.world.getBlockState(block).isOf(Blocks.CRAFTING_TABLE)) {
            tableMemory.forget(client.world);
            return null;
        }
        return block;
    }

    /** Bounded loaded-world discovery also recovers a nearby table after a client restart. */
    private void discoverKnownTable() {
        if (discoveryWorld != client.world) {
            discoveryWorld = client.world;
            nextTableDiscoveryTick = 0;
            lastTableDiscoveryPosition = null;
        }
        if (knownTable() != null || client.world == null || client.player == null) return;
        var inventory = client.player.getInventory();
        if (inventory.count(Items.WOODEN_PICKAXE) == 0 && inventory.count(Items.STONE_PICKAXE) == 0) return;
        BlockPos center = client.player.getBlockPos();
        boolean moved = lastTableDiscoveryPosition != null
            && squaredDistance(center, lastTableDiscoveryPosition) >= 64;
        if (lastTableDiscoveryPosition != null && clientTicks < nextTableDiscoveryTick && !moved) return;
        lastTableDiscoveryPosition = center.toImmutable();
        nextTableDiscoveryTick = clientTicks + 200;
        BlockPos nearest = null;
        double nearestDistance = Double.POSITIVE_INFINITY;
        // 17 * 49 * 17 = 14,161 cells at most. Never load chunks to look for a table.
        for (BlockPos position : BlockPos.iterate(center.add(-8, -24, -8), center.add(8, 24, 8))) {
            if (!client.world.isChunkLoaded(position.getX() >> 4, position.getZ() >> 4)
                || !client.world.getBlockState(position).isOf(Blocks.CRAFTING_TABLE)) continue;
            double distance = squaredDistance(center, position);
            if (distance < nearestDistance) { nearestDistance = distance; nearest = position.toImmutable(); }
        }
        if (nearest != null) rememberTable(nearest);
    }

    private static double squaredDistance(BlockPos first, BlockPos second) {
        double x = (double) first.getX() - second.getX();
        double y = (double) first.getY() - second.getY();
        double z = (double) first.getZ() - second.getZ();
        return x * x + y * y + z * z;
    }

    private void beginTableReturn() throws ReflectiveOperationException {
        discoverKnownTable();
        returningTable = knownTable();
        if (returningTable == null) throw new IllegalArgumentException("craft or collect a crafting table before crafting this tool; no placed table is remembered");
        if (!baritoneAvailable) throw new IllegalArgumentException("Baritone Fabric API 1.11.3 is required to return to the remembered table");
        baritone = new BaritoneAccess();
        if (!baritone.cancel()) throw new IllegalStateException("could not obtain exclusive Baritone control");
        baritone.reserveToolMaterials();
        Vec3d position = client.player.getPos();
        progress = new ProgressWatch(0, position.x, position.y, position.z);
        setPhase("returning_table");
        baritone.navigateNear(returningTable, 2);
    }

    private boolean tickTableReturn() throws ReflectiveOperationException {
        if (!phase.equals("returning_table")) return false;
        if (client.currentScreen != null || client.player.currentScreenHandler != client.player.playerScreenHandler
            || !client.player.currentScreenHandler.getCursorStack().isEmpty()) {
            throw new IllegalStateException("screen or cursor changed while returning to the table");
        }
        BlockPos remembered = knownTable();
        if (remembered == null) throw new IllegalStateException("remembered crafting table is no longer present");
        if (!remembered.equals(returningTable)) throw new IllegalStateException("remembered crafting table changed during navigation; replan the return");
        BlockHitResult hit = reachableTable();
        if (hit != null) {
            releaseBaritone();
            if (cancellationPending) throw new IllegalStateException("could not release navigation controls before crafting");
            openTable(hit);
            return true;
        }
        int navigationTicks = elapsed - phaseAt;
        Vec3d position = client.player.getPos();
        progress.observe(navigationTicks, 0, position.x, position.y, position.z);
        if (progress.stalled(navigationTicks)) throw new IllegalStateException("return to table made no position progress for 400 ticks");
        if (navigationTicks > 20 && !baritone.navigationActive()) throw new IllegalStateException("Baritone stopped before a crafting table was reachable");
        return true;
    }

    private void prepareTablePlacement() {
        tablePlacementHelper.reset();
        if (tablePlacement() == null) throw new IllegalArgumentException("no reachable safe table placement space; move to a clear solid floor");
        var inventory = client.player.getInventory();
        int tableSlot = -1;
        for (int slot = 0; slot < 36; slot++) if (inventory.getStack(slot).isOf(Items.CRAFTING_TABLE)) { tableSlot = slot; break; }
        tableHand = Hand.MAIN_HAND;
        if (tableSlot < 0) {
            if (!client.player.getOffHandStack().isOf(Items.CRAFTING_TABLE)) throw new IllegalArgumentException("craft or collect a crafting table before crafting this tool");
            tableHand = Hand.OFF_HAND;
        } else {
            selectInventoryItem(tableSlot);
        }
        setPhase("await_table_item");
    }

    private void selectInventoryItem(int inventorySlot) {
        var inventory = client.player.getInventory();
        if (originalSelectedSlot < 0) originalSelectedSlot = inventory.selectedSlot;
        int hotbar = inventorySlot;
        if (inventorySlot >= 9) {
            hotbar = originalSelectedSlot;
            for (int slot = 0; slot < 9; slot++) if (inventory.getStack(slot).isEmpty()) { hotbar = slot; break; }
            // Player-handler slots 9..35 are main inventory; slot 45 is offhand.
            // Use the same SWAP packet as the normal inventory number-key action.
            int screenSlot = inventorySlot == 40 ? 45 : inventorySlot;
            client.interactionManager.clickSlot(client.player.playerScreenHandler.syncId, screenSlot, hotbar, SlotActionType.SWAP, client.player);
        }
        inventory.selectedSlot = hotbar;
        client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(hotbar));
    }

    private WorkstationPlacement.Candidate tablePlacement() { return tablePlacementHelper.find(); }

    private boolean tickTablePlacement() {
        if ((phase.equals("await_table_item") || phase.equals("clearing_table_space") || phase.equals("await_table_placement"))
            && (client.currentScreen != null || client.player.currentScreenHandler != client.player.playerScreenHandler
                || !client.player.currentScreenHandler.getCursorStack().isEmpty())) {
            throw new IllegalStateException("screen or cursor changed during table placement");
        }
        if (phase.equals("await_table_item") || phase.equals("clearing_table_space")) {
            if (phase.equals("await_table_item") && elapsed - phaseAt < 5) return true;
            if (tablePlacementHelper.pending() && !tablePlacementHelper.tickClear(elapsed)) return true;
            if (!client.player.getStackInHand(tableHand).isOf(Items.CRAFTING_TABLE)) {
                if (elapsed - phaseAt < 40) return true;
                throw new IllegalStateException("crafting table did not reach the selected hand");
            }
            WorkstationPlacement.Candidate placement = tablePlacement();
            if (placement == null) throw new IllegalStateException("safe table placement space changed");
            if (placement.clear() != null) {
                tablePlacementHelper.beginClear(placement, elapsed);
                setPhase("clearing_table_space");
                return true;
            }
            if (!client.interactionManager.interactBlock(client.player, tableHand, placement.hit()).isAccepted()) throw new IllegalStateException("table placement interaction was rejected");
            placedTable = placement.position();
            setPhase("await_table_placement");
            return true;
        }
        if (phase.equals("await_table_placement")) {
            if (elapsed - phaseAt < 5) return true;
            if (!client.world.getBlockState(placedTable).isOf(Blocks.CRAFTING_TABLE)) {
                if (elapsed - phaseAt < 40) return true;
                throw new IllegalStateException("placed crafting table was not confirmed in the client world");
            }
            rememberTable(placedTable);
            restoreSelectedSlot();
            BlockHitResult hit = reachableTable();
            if (hit == null) throw new IllegalStateException("placed crafting table is no longer reachable");
            openTable(hit);
            return true;
        }
        return false;
    }

    private BlockHitResult reachableTable() {
        BlockPos center = client.player.getBlockPos();
        Vec3d eye = client.player.getEyePos();
        double nearest = client.player.getBlockInteractionRange() * client.player.getBlockInteractionRange();
        BlockHitResult selected = null;
        for (BlockPos position : BlockPos.iterate(center.add(-4, -4, -4), center.add(4, 4, 4))) {
            if (!client.world.isChunkLoaded(position.getX() >> 4, position.getZ() >> 4) || !client.world.getBlockState(position).isOf(Blocks.CRAFTING_TABLE)) continue;
            Vec3d target = Vec3d.ofCenter(position);
            if (eye.squaredDistanceTo(target) > nearest) continue;
            BlockHitResult hit = client.world.raycast(new RaycastContext(eye, target, RaycastContext.ShapeType.OUTLINE, RaycastContext.FluidHandling.NONE, client.player));
            if (hit.getType() == HitResult.Type.BLOCK && hit.getBlockPos().equals(position)) {
                nearest = eye.squaredDistanceTo(target);
                selected = hit;
            }
        }
        if (selected != null) rememberTable(selected.getBlockPos());
        return selected;
    }

    public void tick() {
        clientTicks++;
        if (portal != null && (portal.active() || status.equals("running") && request.kind().equals("cast_portal"))) {
            elapsed++;
            portal.tick();
            phase = portal.phase();
            if (!portal.active()) { status = portal.status(); reason = portal.reason(); }
            return;
        }
        if (furnace.active() || status.equals("running") && request != null && request.kind().equals("smelt")) {
            elapsed++; furnace.tick(); phase = furnace.phase(); inventoryCount = furnace.inventoryCount();
            if (!furnace.active()) { status = furnace.status(); reason = furnace.reason(); }
            return;
        }
        if (flint.active() || status.equals("running") && request != null && request.kind().equals("mine") && request.item().equals("minecraft:flint")) {
            elapsed++; flint.tick(); phase = flint.phase(); inventoryCount = flint.inventoryCount();
            if (!flint.active()) { status = flint.status(); reason = flint.reason(); }
            return;
        }
        if (cancellationPending) { releaseBaritone(); return; }
        discoverKnownTable();
        if (!status.equals("running")) return;
        elapsed++;
        if (!ready() || client.world != startWorld || client.player != startPlayer) { cancel("world, player, focus or pause changed"); return; }
        if (elapsed >= request.maxTicks() || System.nanoTime() >= wallDeadline) { finish("failed", "skill deadline reached"); return; }
        inventoryCount = countInventory();
        try {
            if (request.kind().equals("mine")) {
                if (client.currentScreen != null) { cancel("screen opened during mining"); return; }
                if (inventoryCount >= request.targetCount()) { finish("completed", "inventory target satisfied"); return; }
                if (phase.equals("preparing_mining")) {
                    Object lookup = miningPreparation.poll().orElse(null);
                    if (lookup != null) launchMining(lookup);
                    return;
                }
                Vec3d position = client.player.getPos();
                int miningTicks = elapsed - phaseAt;
                progress.observe(miningTicks, inventoryCount, position.x, position.y, position.z);
                if (progress.stalled(miningTicks)) { finish("failed", "mining made no position or inventory progress for 400 ticks"); return; }
                if (miningTicks > 20 && !baritone.active()) finish("failed", "Baritone stopped before the inventory target was met");
            } else tickCraft();
        } catch (ReflectiveOperationException | RuntimeException error) { finish("failed", message(error)); }
    }

    private void tickCraft() throws ReflectiveOperationException {
        if (tickTableReturn()) return;
        if (tickTablePlacement()) return;
        if (!(client.player.currentScreenHandler instanceof AbstractRecipeScreenHandler<?, ?> handler)
            || TABLE_RECIPES.contains(request.item()) && !(handler instanceof CraftingScreenHandler)) {
            if (phase.equals("await_screen") && elapsed - phaseAt < 40) return;
            throw new IllegalStateException("expected crafting screen is unavailable");
        }
        if (!handler.getCursorStack().isEmpty()) throw new IllegalStateException("cursor stack changed during crafting");
        if (craftHandler != null && craftHandler != handler) throw new IllegalStateException("crafting screen changed during the skill");
        if (phase.equals("await_screen")) {
            craftHandler = handler;
            requireEmptyGrid(handler);
            setPhase("await_recipe");
            return;
        }
        if (phase.equals("await_recipe")) {
            if (client.getNetworkHandler() == null) throw new IllegalStateException("network handler unavailable");
            RecipeEntry<?> recipe = client.getNetworkHandler().getRecipeManager().get(Identifier.of(request.item())).orElse(null);
            if (recipe == null || !client.player.getRecipeBook().contains(recipe)) {
                if (elapsed - phaseAt < 40) return;
                throw new IllegalStateException("recipe is not unlocked or synchronized");
            }
            if (!recipe.value().fits(handler.getCraftingWidth(), handler.getCraftingHeight())) throw new IllegalStateException("recipe does not fit the current crafting grid");
            Item expected = Registries.ITEM.get(Identifier.of(request.item()));
            if (!recipe.value().getResult(client.world.getRegistryManager()).isOf(expected)) throw new IllegalStateException("recipe output does not match requested item");
            requireEmptyGrid(handler);
            RecipeMatcher matcher = new RecipeMatcher();
            client.player.getInventory().populateRecipeFinder(matcher);
            handler.populateRecipeFinder(matcher);
            if (!matcher.match(recipe.value(), null)) throw new IllegalStateException("required crafting ingredients are missing");
            cycleCount = inventoryCount;
            client.interactionManager.clickRecipe(handler.syncId, recipe, false);
            setPhase("await_result");
            return;
        }
        if (phase.equals("await_result")) {
            ItemStack result = handler.getSlot(handler.getCraftingResultSlotIndex()).getStack();
            if (!result.isEmpty() && result.isOf(Registries.ITEM.get(Identifier.of(request.item())))) {
                client.interactionManager.clickSlot(handler.syncId, handler.getCraftingResultSlotIndex(), 0, SlotActionType.QUICK_MOVE, client.player);
                stableCount = -1;
                stableSince = elapsed;
                setPhase("await_inventory");
            } else if (elapsed - phaseAt >= 40) throw new IllegalStateException("recipe fill produced no matching result");
            return;
        }
        if (phase.equals("await_inventory")) {
            if (inventoryCount != stableCount) { stableCount = inventoryCount; stableSince = elapsed; }
            // clickSlot predicts locally. Wait for normal client/server inventory
            // synchronization before treating the client inventory as evidence.
            if (elapsed - phaseAt >= 10 && elapsed - stableSince >= 3 && inventoryCount > cycleCount) {
                if (inventoryCount >= request.targetCount()) {
                    if (openedScreen && gridEmpty(handler) && handler.getCursorStack().isEmpty()) client.player.closeHandledScreen();
                    finish("completed", "inventory target satisfied after crafting synchronization");
                } else { requireEmptyGrid(handler); setPhase("await_recipe"); }
            } else if (elapsed - phaseAt >= 40) throw new IllegalStateException("craft result did not reach inventory; check inventory space");
        }
    }

    private static boolean gridEmpty(AbstractRecipeScreenHandler<?, ?> handler) {
        for (int slot = 1; slot < handler.getCraftingSlotCount(); slot++) if (!handler.getSlot(slot).getStack().isEmpty()) return false;
        return true;
    }

    private static void requireEmptyGrid(AbstractRecipeScreenHandler<?, ?> handler) {
        if (!gridEmpty(handler)) throw new IllegalStateException("crafting grid must be empty before recipe fill");
    }

    private void setPhase(String value) { phase = value; phaseAt = elapsed; }

    public void cancel(String detail) {
        if (active()) finish("cancelled", detail);
    }

    private void finish(String result, String detail) {
        tablePlacementHelper.cancel();
        miningPreparation.cancel();
        if (portal != null) portal.cancel(detail);
        furnace.cancel(detail); flint.cancel(detail);
        status = result;
        reason = bounded(detail);
        phase = result;
        releaseBaritone();
        restoreSelectedSlot();
    }

    private void restoreSelectedSlot() {
        if (originalSelectedSlot < 0) return;
        int slot = originalSelectedSlot;
        originalSelectedSlot = -1;
        if (client.player == startPlayer && client.world == startWorld && client.getNetworkHandler() != null) {
            client.player.getInventory().selectedSlot = slot;
            try { client.getNetworkHandler().sendPacket(new UpdateSelectedSlotC2SPacket(slot)); }
            catch (RuntimeException error) { reason = "selected slot restored locally; server synchronization was unavailable"; }
        }
    }

    private void releaseBaritone() {
        if (baritone == null) { cancellationPending = false; return; }
        cancellationPending = !baritone.cancel();
        if (!cancellationPending) baritone = null;
        else { phase = "cancellation_pending"; reason = "Baritone cancellation failed; control ownership retained pending retry"; }
    }

    public JsonObject snapshot() {
        discoverKnownTable();
        JsonObject result = new JsonObject();
        result.addProperty("baritone_available", baritoneAvailable);
        result.addProperty("active", active());
        if (skillId == null) result.add("skill_id", JsonNull.INSTANCE); else result.addProperty("skill_id", skillId);
        if (request == null) { result.add("kind", JsonNull.INSTANCE); result.add("item", JsonNull.INSTANCE); }
        else { result.addProperty("kind", request.kind()); result.addProperty("item", request.item()); }
        result.addProperty("target_count", request == null ? 0 : request.targetCount());
        result.addProperty("inventory_count", inventoryCount);
        result.addProperty("status", status);
        result.addProperty("phase", phase);
        if (reason == null) result.add("reason", JsonNull.INSTANCE); else result.addProperty("reason", bounded(reason));
        result.addProperty("elapsed_ticks", Math.min(1800, elapsed));
        result.addProperty("max_ticks", request == null ? 0 : request.maxTicks());
        JsonArray capabilities = new JsonArray();
        MINE_ITEMS.stream().sorted().forEach(item -> capabilities.add("mine:" + item));
        CRAFT_ITEMS.stream().sorted().forEach(item -> capabilities.add("craft:" + item));
        capabilities.add("smelt:minecraft:iron_ingot");
        if (baritoneAvailable) capabilities.add("cast_portal:minecraft:nether_portal");
        result.add("capabilities", capabilities);
        JsonArray verified = new JsonArray();
        if (portal != null) portal.verifiedSteps().forEach(verified::add);
        result.add("verified_steps", verified);
        BlockPos knownFurnace = furnace.knownFurnace();
        if (knownFurnace == null) result.add("known_furnace", JsonNull.INSTANCE);
        else {
            JsonObject position = new JsonObject();
            position.addProperty("x", knownFurnace.getX()); position.addProperty("y", knownFurnace.getY()); position.addProperty("z", knownFurnace.getZ());
            result.add("known_furnace", position);
        }
        BlockPos table = knownTable();
        if (table == null) result.add("known_table", JsonNull.INSTANCE);
        else {
            JsonObject position = new JsonObject();
            position.addProperty("x", table.getX());
            position.addProperty("y", table.getY());
            position.addProperty("z", table.getZ());
            result.add("known_table", position);
        }
        return result;
    }

    private static String bounded(String value) { return value == null ? "" : value.substring(0, Math.min(300, value.length())); }
    private static String message(Exception error) {
        return error instanceof IllegalArgumentException || error instanceof IllegalStateException
            ? bounded(error.getMessage()) : "skill API failure: " + error.getClass().getSimpleName();
    }

    /** Reflection is confined to the public API interfaces, never obfuscated implementations. */
    static final class BaritoneAccess {
        private final Object mine, pathing, input, customGoal;
        private final Class<?> mineApi, pathingApi, inputApi, customGoalApi;
        private Object throwawaySetting;
        private Object originalThrowawayItems;
        private Field throwawayValue;

        static boolean available() {
            try { Class.forName("baritone.api.BaritoneAPI", false, SkillController.class.getClassLoader()); return true; }
            catch (ClassNotFoundException | LinkageError error) { return false; }
        }

        BaritoneAccess() throws ReflectiveOperationException {
            Class<?> api = Class.forName("baritone.api.BaritoneAPI");
            Method providerGetter = api.getMethod("getProvider");
            Object provider = providerGetter.invoke(null);
            Method primaryGetter = providerGetter.getReturnType().getMethod("getPrimaryBaritone");
            Object primary = primaryGetter.invoke(provider);
            Class<?> primaryApi = primaryGetter.getReturnType();
            Method mineGetter = primaryApi.getMethod("getMineProcess");
            mine = mineGetter.invoke(primary); mineApi = mineGetter.getReturnType();
            Method pathGetter = primaryApi.getMethod("getPathingBehavior");
            pathing = pathGetter.invoke(primary); pathingApi = pathGetter.getReturnType();
            Method inputGetter = primaryApi.getMethod("getInputOverrideHandler");
            input = inputGetter.invoke(primary); inputApi = inputGetter.getReturnType();
            Method goalGetter = primaryApi.getMethod("getCustomGoalProcess");
            customGoal = goalGetter.invoke(primary); customGoalApi = goalGetter.getReturnType();
        }

        static Object prepareLookup(String[] blocks) throws ReflectiveOperationException {
            Class<?> lookupApi = Class.forName("baritone.api.utils.BlockOptionalMetaLookup");
            return lookupApi.getConstructor(String[].class).newInstance((Object) blocks.clone());
        }
        void minePrepared(int targetCount, Object lookup) throws ReflectiveOperationException {
            Class<?> lookupApi = Class.forName("baritone.api.utils.BlockOptionalMetaLookup");
            mineApi.getMethod("mine", int.class, lookupApi).invoke(mine, targetCount, lookup);
        }
        boolean active() throws ReflectiveOperationException { return (boolean) mineApi.getMethod("isActive").invoke(mine); }
        void navigateNear(BlockPos position, int radius) throws ReflectiveOperationException {
            Class<?> goalApi = Class.forName("baritone.api.pathing.goals.Goal");
            Object goal = Class.forName("baritone.api.pathing.goals.GoalNear")
                .getConstructor(BlockPos.class, int.class).newInstance(position, radius);
            customGoalApi.getMethod("setGoalAndPath", goalApi).invoke(customGoal, goal);
        }
        boolean navigationActive() throws ReflectiveOperationException { return (boolean) customGoalApi.getMethod("isActive").invoke(customGoal); }
        void navigateExact(BlockPos position) throws ReflectiveOperationException {
            Class<?> goalApi = Class.forName("baritone.api.pathing.goals.Goal");
            Object goal = Class.forName("baritone.api.pathing.goals.GoalBlock")
                .getConstructor(BlockPos.class).newInstance(position);
            customGoalApi.getMethod("setGoalAndPath", goalApi).invoke(customGoal, goal);
        }
        void reserveToolMaterials() throws ReflectiveOperationException {
            reserveItems(TOOL_MATERIALS);
        }
        void reserveItems(Set<String> reserved) throws ReflectiveOperationException {
            Class<?> api = Class.forName("baritone.api.BaritoneAPI");
            Method getter = api.getMethod("getSettings");
            Object settings = getter.invoke(null);
            Object setting = getter.getReturnType().getField("acceptableThrowawayItems").get(settings);
            Field value = Class.forName("baritone.api.Settings$Setting").getField("value");
            Object original = value.get(setting);
            if (!(original instanceof List<?> items)) throw new IllegalStateException("Baritone throwaway items setting has an unsupported type");
            List<Item> filtered = new ArrayList<>();
            for (Object entry : items) {
                if (!(entry instanceof Item item)) throw new IllegalStateException("Baritone throwaway items contains an unsupported value");
                if (!reserved.contains(Registries.ITEM.getId(item).toString())) filtered.add(item);
            }
            // Keep the exact previous value for restoration. Never mutate its list.
            throwawaySetting = setting;
            originalThrowawayItems = original;
            throwawayValue = value;
            value.set(setting, filtered);
        }
        private void restoreToolMaterials() throws ReflectiveOperationException {
            if (throwawaySetting == null) return;
            throwawayValue.set(throwawaySetting, originalThrowawayItems);
            throwawaySetting = null;
            originalThrowawayItems = null;
            throwawayValue = null;
        }
        boolean cancel() {
            boolean success = true;
            for (String method : new String[]{"cancelEverything", "forceCancel"}) {
                try { pathingApi.getMethod(method).invoke(pathing); }
                catch (ReflectiveOperationException | RuntimeException error) { success = false; }
            }
            try { inputApi.getMethod("clearAllKeys").invoke(input); }
            catch (ReflectiveOperationException | RuntimeException error) { success = false; }
            try { restoreToolMaterials(); }
            catch (ReflectiveOperationException | RuntimeException error) { success = false; }
            return success;
        }
    }
}
