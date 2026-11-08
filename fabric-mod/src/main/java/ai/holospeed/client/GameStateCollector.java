package ai.holospeed.client;

import ai.holospeed.client.mixin.HandledScreenAccessor;
import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.gui.screen.ingame.HandledScreen;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.ItemEntity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.registry.tag.BlockTags;
import net.minecraft.screen.ScreenHandler;
import net.minecraft.screen.slot.Slot;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.EntityHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.util.function.BooleanBiFunction;
import net.minecraft.util.shape.VoxelShapes;

import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;

/** Bounded, read-only client state. Never queries server worlds or loads chunks. */
final class GameStateCollector {
    private final GameStateCache cache = new GameStateCache();

    void invalidate() { cache.invalidate(); }

    JsonObject reply(MinecraftClient client, long tick, String requestId) {
        if (!client.isOnThread()) throw new IllegalStateException("state must be sampled on client thread");
        return cache.reply(client.world, client.player, requestId, System.nanoTime(),
            System.currentTimeMillis(), () -> sample(client, tick));
    }

    private static JsonObject sample(MinecraftClient client, long tick) {
        JsonObject state = new JsonObject();
        state.addProperty("tick", tick);
        boolean ready = client.player != null && client.world != null;
        state.addProperty("world_ready", ready);
        state.addProperty("screen_open", client.currentScreen != null);
        state.add("screen", screen(client.currentScreen));
        state.add("player", JsonNull.INSTANCE);
        state.add("dimension", JsonNull.INSTANCE);
        state.add("local_bounds", JsonNull.INSTANCE);
        state.add("inventory", new JsonArray());
        state.add("local_blocks", new JsonArray());
        state.add("nearby_blocks", new JsonArray());
        state.add("nearby_entities", new JsonArray());
        JsonObject miss = new JsonObject();
        miss.addProperty("type", "miss");
        state.add("crosshair", miss);
        if (!ready) return state;

        ClientPlayerEntity player = client.player;
        ClientWorld world = client.world;
        state.addProperty("dimension", world.getRegistryKey().getValue().toString());
        JsonObject person = new JsonObject();
        person.add("position", position(player.getPos()));
        person.add("eye_position", position(player.getEyePos()));
        person.addProperty("eye_height", player.getEyeHeight(player.getPose()));
        person.addProperty("yaw", player.getYaw());
        person.addProperty("pitch", player.getPitch());
        person.add("velocity", position(player.getVelocity()));
        person.addProperty("on_ground", player.isOnGround());
        person.addProperty("health", player.getHealth());
        person.addProperty("hunger", player.getHungerManager().getFoodLevel());
        person.addProperty("selected_slot", player.getInventory().selectedSlot);
        person.addProperty("block_interaction_range", player.getBlockInteractionRange());
        state.add("player", person);
        JsonArray inventory = new JsonArray();
        for (int slot = 0; slot < Math.min(41, player.getInventory().size()); slot++) {
            JsonObject item = stack(player.getInventory().getStack(slot));
            item.addProperty("slot", slot);
            inventory.add(item);
        }
        state.add("inventory", inventory);
        state.add("crosshair", crosshair(client.crosshairTarget, player, world));
        collectBlocks(state, world, player.getBlockPos());
        collectEntities(state, world, player);
        return state;
    }

    private static com.google.gson.JsonElement screen(Screen current) {
        if (current == null) return JsonNull.INSTANCE;
        JsonObject screen = new JsonObject();
        String type = current.getClass().getSimpleName();
        if (type.isBlank()) type = current.getClass().getName();
        screen.addProperty("type", boundedText(type, 160));
        screen.addProperty("title", boundedText(current.getTitle().getString(), 256));
        screen.addProperty("width", current.width);
        screen.addProperty("height", current.height);
        screen.add("sync_id", JsonNull.INSTANCE);
        screen.add("cursor", JsonNull.INSTANCE);
        JsonArray slots = new JsonArray();
        screen.add("slots", slots);
        if (current instanceof HandledScreen<?> handled) {
            ScreenHandler handler = handled.getScreenHandler();
            screen.addProperty("sync_id", handler.syncId);
            screen.addProperty("origin_x", ((HandledScreenAccessor) handled).holospeed$getOriginX());
            screen.addProperty("origin_y", ((HandledScreenAccessor) handled).holospeed$getOriginY());
            screen.add("cursor", stack(handler.getCursorStack()));
            for (int index = 0; index < Math.min(256, handler.slots.size()); index++) {
                Slot slot = handler.slots.get(index);
                JsonObject item = stack(slot.getStack());
                item.addProperty("slot", slot.id);
                item.addProperty("x", slot.x);
                item.addProperty("y", slot.y);
                slots.add(item);
            }
            screen.addProperty("slots_truncated", handler.slots.size() > 256);
        }
        return screen;
    }

    private static JsonObject crosshair(HitResult target, ClientPlayerEntity player, ClientWorld world) {
        JsonObject hit = new JsonObject();
        hit.addProperty("type", target == null ? "miss" : target.getType().name().toLowerCase(java.util.Locale.ROOT));
        if (target == null) return hit;
        hit.add("position", position(target.getPos()));
        hit.addProperty("distance", player.getEyePos().distanceTo(target.getPos()));
        if (target instanceof BlockHitResult block && target.getType() == HitResult.Type.BLOCK) {
            BlockPos pos = block.getBlockPos();
            hit.add("block_pos", position(pos));
            if (world.isChunkLoaded(pos.getX() >> 4, pos.getZ() >> 4)) {
                hit.addProperty("block_id", Registries.BLOCK.getId(world.getBlockState(pos).getBlock()).toString());
            } else {
                hit.addProperty("type", "miss");
                hit.remove("block_pos");
            }
        } else if (target instanceof EntityHitResult entity) {
            hit.addProperty("entity_id", entity.getEntity().getId());
        }
        return hit;
    }

    private static void collectBlocks(JsonObject state, ClientWorld world, BlockPos center) {
        Map<Long, JsonObject> sampled = new HashMap<>();
        Map<Long, Boolean> loadedChunks = new HashMap<>();
        JsonArray local = new JsonArray();
        boolean complete = true;
        for (StateBounds.Offset offset : StateBounds.localOffsets()) {
            BlockPos pos = center.add(offset.x(), offset.y(), offset.z());
            if (!loaded(world, pos, loadedChunks)) { complete = false; continue; }
            JsonObject block = block(world, pos, world.getBlockState(pos));
            sampled.put(pos.asLong(), block);
            local.add(block);
        }
        JsonObject bounds = new JsonObject();
        bounds.add("min", position(center.add(-StateBounds.LOCAL_RADIUS, -StateBounds.LOCAL_RADIUS, -StateBounds.LOCAL_RADIUS)));
        bounds.add("max", position(center.add(StateBounds.LOCAL_RADIUS, StateBounds.LOCAL_RADIUS, StateBounds.LOCAL_RADIUS)));
        bounds.addProperty("complete", complete);
        state.add("local_bounds", bounds);
        state.add("local_blocks", local);

        JsonArray preferred = new JsonArray();
        JsonArray ordinary = new JsonArray();
        int matches = 0;
        for (StateBounds.Offset offset : StateBounds.nearbyOffsets()) {
            BlockPos pos = center.add(offset.x(), offset.y(), offset.z());
            if (!loaded(world, pos, loadedChunks)) continue;
            BlockState blockState = world.getBlockState(pos);
            if (!interesting(blockState)) continue;
            matches++;
            JsonArray destination = priority(blockState) ? preferred : ordinary;
            if (destination.size() == StateBounds.MAX_NEARBY_BLOCKS) continue;
            JsonObject cached = sampled.get(pos.asLong());
            destination.add(cached == null ? block(world, pos, blockState) : cached);
        }
        JsonArray nearby = new JsonArray();
        for (var block : preferred) { if (nearby.size() < StateBounds.MAX_NEARBY_BLOCKS) nearby.add(block); }
        for (var block : ordinary) { if (nearby.size() < StateBounds.MAX_NEARBY_BLOCKS) nearby.add(block); }
        state.add("nearby_blocks", nearby);
        state.addProperty("nearby_blocks_truncated", matches > StateBounds.MAX_NEARBY_BLOCKS);
        state.addProperty("nearby_radius", StateBounds.NEARBY_RADIUS);
    }

    private static boolean loaded(ClientWorld world, BlockPos pos, Map<Long, Boolean> loadedChunks) {
        int chunkX = pos.getX() >> 4;
        int chunkZ = pos.getZ() >> 4;
        long key = (long) chunkX << 32 | chunkZ & 0xffffffffL;
        return loadedChunks.computeIfAbsent(key, ignored -> world.isChunkLoaded(chunkX, chunkZ));
    }

    private static boolean interesting(BlockState state) {
        return !state.isAir() && (state.isIn(BlockTags.LOGS) || state.isIn(BlockTags.LEAVES)
            || state.isIn(BlockTags.BASE_STONE_OVERWORLD) || state.isIn(BlockTags.BASE_STONE_NETHER)
            || !state.getFluidState().isEmpty() || state.isOf(Blocks.GRAVEL) || state.isOf(Blocks.DIRT)
            || state.isOf(Blocks.GRASS_BLOCK) || priority(state));
    }

    private static boolean priority(BlockState state) {
        return state.isOf(Blocks.CRAFTING_TABLE) || state.isOf(Blocks.FURNACE) || state.isOf(Blocks.BLAST_FURNACE)
            || state.isOf(Blocks.CHEST) || state.isOf(Blocks.TRAPPED_CHEST) || state.isOf(Blocks.OBSIDIAN)
            || state.isOf(Blocks.CRYING_OBSIDIAN) || state.isOf(Blocks.NETHER_PORTAL) || state.isOf(Blocks.GOLD_BLOCK)
            || state.isOf(Blocks.IRON_ORE) || state.isOf(Blocks.DEEPSLATE_IRON_ORE)
            || state.isOf(Blocks.COAL_ORE) || state.isOf(Blocks.DEEPSLATE_COAL_ORE);
    }

    private static JsonObject block(ClientWorld world, BlockPos pos, BlockState blockState) {
        JsonObject block = new JsonObject();
        block.add("position", position(pos));
        block.addProperty("id", Registries.BLOCK.getId(blockState.getBlock()).toString());
        var collision = blockState.getCollisionShape(world, pos);
        block.addProperty("solid", !collision.isEmpty());
        block.addProperty("full_cube", !VoxelShapes.matchesAnywhere(collision, VoxelShapes.fullCube(), BooleanBiFunction.NOT_SAME));
        block.addProperty("fluid", Registries.FLUID.getId(blockState.getFluidState().getFluid()).toString());
        block.addProperty("hardness", blockState.getHardness(world, pos));
        return block;
    }

    private static void collectEntities(JsonObject state, ClientWorld world, ClientPlayerEntity player) {
        // The client entity lookup only contains currently tracked entities, and does not load chunks.
        var entities = world.getOtherEntities(player, player.getBoundingBox().expand(StateBounds.NEARBY_RADIUS),
            entity -> entity.squaredDistanceTo(player) <= StateBounds.NEARBY_RADIUS * StateBounds.NEARBY_RADIUS);
        entities.sort(Comparator.comparingDouble((Entity entity) -> entity.squaredDistanceTo(player)).thenComparingInt(Entity::getId));
        JsonArray result = new JsonArray();
        for (int index = 0; index < Math.min(StateBounds.MAX_NEARBY_ENTITIES, entities.size()); index++) {
            Entity entity = entities.get(index);
            JsonObject entry = new JsonObject();
            entry.addProperty("id", entity.getId());
            entry.addProperty("type", Registries.ENTITY_TYPE.getId(entity.getType()).toString());
            entry.add("position", position(entity.getPos()));
            if (entity instanceof LivingEntity living) entry.addProperty("health", living.getHealth());
            if (entity instanceof ItemEntity item) entry.add("stack", stack(item.getStack()));
            result.add(entry);
        }
        state.add("nearby_entities", result);
        state.addProperty("nearby_entities_truncated", entities.size() > StateBounds.MAX_NEARBY_ENTITIES);
    }

    private static JsonObject stack(ItemStack stack) {
        JsonObject result = new JsonObject();
        result.addProperty("item", stack.isEmpty() ? "minecraft:air" : Registries.ITEM.getId(stack.getItem()).toString());
        result.addProperty("count", stack.isEmpty() ? 0 : stack.getCount());
        return result;
    }

    private static JsonObject position(Vec3d value) {
        JsonObject result = new JsonObject();
        result.addProperty("x", value.x); result.addProperty("y", value.y); result.addProperty("z", value.z);
        return result;
    }

    private static JsonObject position(BlockPos value) {
        JsonObject result = new JsonObject();
        result.addProperty("x", value.getX()); result.addProperty("y", value.getY()); result.addProperty("z", value.getZ());
        return result;
    }

    private static String boundedText(String text, int maxLength) {
        return text.length() <= maxLength ? text : text.substring(0, maxLength);
    }
}
