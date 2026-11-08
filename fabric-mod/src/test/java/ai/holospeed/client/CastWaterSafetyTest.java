package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Function;
import static ai.holospeed.client.CastWaterSafety.Cell.*;
import static org.junit.jupiter.api.Assertions.*;

class CastWaterSafetyTest {
    private static final CastPortalPlan.Point START = point(0, 1, 0);

    @Test void flatGroundStopsAfterSevenCellsAndDoesNotScanUnreachableUnknownTerrain() {
        AtomicInteger samples = new AtomicInteger();
        assertTrue(CastWaterSafety.safe(List.of(START), position -> {
            samples.incrementAndGet();
            if (Math.abs(position.x()) + Math.abs(position.z()) > 8)
                fail("sampled outside the reachable flow and its immediate neighbors");
            return position.y() <= 0 ? SOLID : OPEN;
        }));
        assertTrue(samples.get() < 600, "flat flow should remain a small bounded search");
    }

    @Test void adjacentLavaAtTheLastFlowingCellIsUnsafeButDistantLavaIsNotReached() {
        assertFalse(CastWaterSafety.safe(List.of(START), lavaOnFlat(point(8, 1, 0))));
        assertTrue(CastWaterSafety.safe(List.of(START), lavaOnFlat(point(9, 1, 0))));
        assertFalse(CastWaterSafety.safe(List.of(START), lavaOnFlat(START)));
    }

    @Test void downhillFlowResetsItsBudgetAndFindsLavaBeyondTheOldNineBlockCutoff() {
        assertFalse(CastWaterSafety.safe(List.of(START), position -> {
            if (position.equals(point(12, 0, 0))) return LAVA;
            int floor = position.x() >= 4 ? -1 : 0;
            return position.y() <= floor ? SOLID : OPEN;
        }));
    }

    @Test void solidWallShieldsLavaAndPreventsFloodTraversal() {
        assertTrue(CastWaterSafety.safe(List.of(START), position -> {
            if (position.x() == 1 || position.y() <= 0) return SOLID;
            if (position.equals(point(2, 1, 0))) return LAVA;
            return OPEN;
        }));
    }

    @Test void unknownOrNullAdjacentCellsCannotBeClaimedSafe() {
        assertFalse(CastWaterSafety.safe(List.of(START), position ->
            position.equals(point(1, 1, 0)) ? UNKNOWN : position.y() <= 0 ? SOLID : OPEN));
        assertFalse(CastWaterSafety.safe(List.of(START), position ->
            position.equals(point(0, 2, 0)) ? null : position.y() <= 0 ? SOLID : OPEN));
    }

    @Test void verticalAndHorizontalSearchLimitsFailClosed() {
        assertFalse(CastWaterSafety.safe(List.of(START), position -> OPEN));
        assertFalse(CastWaterSafety.safe(List.of(START), position -> {
            // A one-wide stair descent refreshes flow repeatedly past the32-block limit.
            if (position.z() != 0) return SOLID;
            int floor = -Math.max(0, position.x()) / 4;
            return position.y() <= floor ? SOLID : OPEN;
        }));
    }

    @Test void overlappingWaterfallsDeduplicateSamplingAndRetainTheStrongestFlowBudget() {
        AtomicInteger samples = new AtomicInteger();
        java.util.Set<CastPortalPlan.Point> queried = new java.util.HashSet<>();
        assertTrue(CastWaterSafety.safe(List.of(START, point(4, 1, 0), START), position -> {
            samples.incrementAndGet();
            assertTrue(queried.add(position), "each world cell should be sampled once");
            return position.y() <= 0 ? SOLID : OPEN;
        }));
        assertTrue(samples.get() < 1000);
        assertFalse(CastWaterSafety.safe(List.of(START, point(4, 1, 0)), lavaOnFlat(point(12, 1, 0))));
    }

    @Test void branchingDownhillTerrainCannotExceedTheBoundedSamplingBudget() {
        AtomicInteger samples = new AtomicInteger();
        assertFalse(CastWaterSafety.safe(List.of(point(0, 16, 0)), position -> {
            samples.incrementAndGet();
            // Perforated shelves create many downward routes and overlapping flow fronts.
            if (Math.floorMod(position.y(), 3) == 0
                && Math.floorMod(position.x() + position.z() + position.y() / 3, 3) != 0) return SOLID;
            return OPEN;
        }));
        assertTrue(samples.get() <= 4096 * 7, "world samples are bounded by cells and their six neighbors");
    }

    @Test void malformedStartsAndCoordinateOverflowFailClosed() {
        assertFalse(CastWaterSafety.safe(List.of(), position -> OPEN));
        assertFalse(CastWaterSafety.safe(null, position -> OPEN));
        assertFalse(CastWaterSafety.safe(List.of(START), null));
        assertFalse(CastWaterSafety.safe(List.of(point(Integer.MAX_VALUE, 1, 0)), position -> OPEN));
    }

    private static Function<CastPortalPlan.Point, CastWaterSafety.Cell> lavaOnFlat(CastPortalPlan.Point lava) {
        return position -> position.equals(lava) ? LAVA : position.y() <= 0 ? SOLID : OPEN;
    }

    private static CastPortalPlan.Point point(int x, int y, int z) {
        return new CastPortalPlan.Point(x, y, z);
    }
}
