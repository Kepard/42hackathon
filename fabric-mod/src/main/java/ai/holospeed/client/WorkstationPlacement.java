package ai.holospeed.client;

import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.client.MinecraftClient;
import net.minecraft.registry.tag.BlockTags;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.util.shape.VoxelShapes;
import net.minecraft.world.RaycastContext;

/** Reach-checked workstation placement with bounded normal vegetation clearing. */
final class WorkstationPlacement {
    static final int MAX_CLEARED = 8;
    record Candidate(BlockHitResult hit, BlockPos position, BlockPos clear) {}
    private final MinecraftClient client;
    private BlockPos clearing;
    private BlockHitResult clearHit;
    private Object connection;
    private long after;
    private int started, cleared;

    WorkstationPlacement(MinecraftClient client) { this.client = client; }
    void reset() { cancel(); cleared = 0; }
    boolean pending() { return clearing != null; }

    static boolean clearable(boolean leaves, boolean replaceable, boolean emptyCollision,
                             boolean emptyFluid, boolean blockEntity, float hardness) {
        return !blockEntity && emptyFluid && Float.isFinite(hardness) && hardness >= 0 && hardness <= .2f
            && (leaves || replaceable && emptyCollision);
    }
    static boolean mayClear(int removed, boolean placementSupport, boolean occupiedSupport) {
        return removed >= 0 && removed < MAX_CLEARED && !placementSupport && !occupiedSupport;
    }
    static boolean preferSupport(boolean leaves, double distance, boolean previousLeaves, double previousDistance) {
        return leaves != previousLeaves ? !leaves : distance < previousDistance;
    }
    private boolean clearable(BlockPos cell) {
        if (!loaded(cell)) return false;
        BlockState state = client.world.getBlockState(cell);
        return !state.isAir() && clearable(state.isIn(BlockTags.LEAVES), state.isReplaceable(),
            state.getCollisionShape(client.world, cell).isEmpty(), state.getFluidState().isEmpty(),
            state.hasBlockEntity(), state.getHardness(client.world, cell));
    }
    private boolean loaded(BlockPos cell) { return client.world.isChunkLoaded(cell.getX() >> 4, cell.getZ() >> 4); }
    private boolean hazardous(BlockPos cell) {
        if (!loaded(cell)) return true;
        var state = client.world.getBlockState(cell);
        return !state.getFluidState().isEmpty() || state.isOf(Blocks.FIRE) || state.isOf(Blocks.SOUL_FIRE)
            || state.isOf(Blocks.MAGMA_BLOCK) || state.isOf(Blocks.CAMPFIRE) || state.isOf(Blocks.SOUL_CAMPFIRE)
            || state.isOf(Blocks.CACTUS) || state.isOf(Blocks.SWEET_BERRY_BUSH) || state.isOf(Blocks.POWDER_SNOW);
    }
    private boolean surroundingsSafe(BlockPos cell) {
        if (hazardous(cell)) return false;
        for (Direction side : Direction.values()) if (hazardous(cell.offset(side))) return false;
        return true;
    }
    private boolean entityFree(BlockPos cell) {
        return client.world.doesNotIntersectEntities(null, VoxelShapes.cuboid(new Box(cell)));
    }
    private BlockHitResult ray(Vec3d target) {
        return client.world.raycast(new RaycastContext(client.player.getEyePos(), target,
            RaycastContext.ShapeType.OUTLINE, RaycastContext.FluidHandling.NONE, client.player));
    }
    Candidate find() {
        BlockPos center = client.player.getBlockPos();
        Vec3d eye = client.player.getEyePos();
        double reachSquared = Math.pow(client.player.getBlockInteractionRange(), 2);
        double nearest = Double.POSITIVE_INFINITY;
        boolean chosenLeaves = true;
        Candidate best = null;
        for (BlockPos candidate : BlockPos.iterate(center.add(-3, -2, -3), center.add(3, 1, 3))) {
            BlockPos target = candidate.toImmutable(), floor = target.down();
            if (!loaded(target) || !loaded(floor)) continue;
            var base = client.world.getBlockState(floor);
            if ((!client.world.getBlockState(target).isAir() && !clearable(target))
                || !base.isFullCube(client.world, floor) || !base.isSideSolidFullSquare(client.world, floor, Direction.UP)
                || base.hasBlockEntity() || base.isOf(Blocks.CRAFTING_TABLE) || !surroundingsSafe(target) || !entityFree(target)) continue;
            Vec3d top = new Vec3d(floor.getX() + .5, floor.getY() + .999, floor.getZ() + .5);
            double distance = eye.squaredDistanceTo(top);
            boolean leaves = base.isIn(BlockTags.LEAVES);
            if (distance > reachSquared || best != null && !preferSupport(leaves, distance, chosenLeaves, nearest)) continue;
            BlockHitResult hit = ray(top);
            if (hit.getType() != HitResult.Type.BLOCK) continue;
            BlockPos obstruction = hit.getBlockPos().toImmutable();
            boolean direct = obstruction.equals(floor) && hit.getSide() == Direction.UP && client.world.getBlockState(target).isAir();
            if (!direct && (!clearable(obstruction) || !surroundingsSafe(obstruction)
                || !mayClear(cleared, obstruction.equals(floor), !entityFree(obstruction.up())))) continue;
            nearest = distance;
            chosenLeaves = leaves;
            best = new Candidate(hit, target, direct ? null : obstruction);
        }
        return best;
    }

    void beginClear(Candidate candidate, int elapsed) {
        if (candidate.clear() == null || pending()) throw new IllegalStateException("invalid workstation clearance state");
        BlockPos cell = candidate.clear();
        if (!clearable(cell) || !surroundingsSafe(cell)
            || !mayClear(cleared, cell.equals(candidate.position().down()), !entityFree(cell.up())))
            throw new IllegalStateException("workstation vegetation clearance precondition changed");
        clearing = cell; clearHit = candidate.hit(); started = elapsed;
        connection = client.getNetworkHandler(); after = ServerBlockSync.INSTANCE.latest(connection);
        look(clearHit.getPos());
        client.interactionManager.attackBlock(clearing, clearHit.getSide());
    }
    boolean tickClear(int elapsed) {
        if (!pending()) return true;
        if (client.getNetworkHandler() != connection || !loaded(clearing)) throw new IllegalStateException("connection changed during workstation clearance");
        int stateId = ServerBlockSync.INSTANCE.stateId(connection, clearing.asLong());
        if (client.world.getBlockState(clearing).isAir() && stateId >= 0
            && Block.getStateFromRawId(stateId).isAir()
            && ServerBlockSync.INSTANCE.sequence(connection, clearing.asLong()) > after) {
            cleared++; cancel(); return true;
        }
        if (elapsed - started >= 60) throw new IllegalStateException("server did not confirm workstation vegetation removal");
        if (client.world.getBlockState(clearing).isAir()) return false;
        if (!clearable(clearing) || !surroundingsSafe(clearing) || !entityFree(clearing.up()))
            throw new IllegalStateException("workstation obstruction changed while clearing");
        Vec3d inside = clearHit.getPos().add(Vec3d.ofCenter(clearing).subtract(clearHit.getPos()).normalize().multiply(.001));
        BlockHitResult hit = ray(inside);
        if (client.player.getEyePos().squaredDistanceTo(hit.getPos()) > Math.pow(client.player.getBlockInteractionRange(), 2)
            || hit.getType() != HitResult.Type.BLOCK || !hit.getBlockPos().equals(clearing))
            throw new IllegalStateException("workstation vegetation is no longer visibly reachable");
        look(hit.getPos()); client.interactionManager.updateBlockBreakingProgress(clearing, hit.getSide());
        if (elapsed % 5 == 0) client.player.swingHand(Hand.MAIN_HAND);
        return false;
    }
    private void look(Vec3d target) {
        Vec3d delta = target.subtract(client.player.getEyePos());
        client.player.setYaw((float) Math.toDegrees(Math.atan2(-delta.x, delta.z)));
        client.player.setPitch((float) -Math.toDegrees(Math.atan2(delta.y, Math.hypot(delta.x, delta.z))));
    }
    void cancel() {
        if (clearing != null && client.interactionManager != null) client.interactionManager.cancelBlockBreaking();
        clearing = null; clearHit = null; connection = null;
    }
}
