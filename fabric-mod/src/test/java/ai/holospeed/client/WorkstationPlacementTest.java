package ai.holospeed.client;

import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class WorkstationPlacementTest {
    @Test void recordedAcaciaObstructionCanBeClearedBeforeNearbyTablePlacement() {
        // Run20261108T114834-a16d6d05: this leaf blocks an otherwise valid
        // grass-floor face. Removing it is a single ordinary mining action.
        Vec3d eye = new Vec3d(-11.547047906945458, 66.62000000476837, -2.6075054323062625);
        Vec3d floorTop = new Vec3d(-11.5, 64.999, -3.5);
        Box target = new Box(-12, 65, -4, -11, 66, -3);
        Box player = new Box(-11.847047906945458, 65, -2.9075054323062625,
            -11.247047906945458, 66.8, -2.3075054323062625);
        assertTrue(target.raycast(eye, floorTop).isPresent());
        assertTrue(eye.squaredDistanceTo(floorTop) < 4.5 * 4.5);
        assertFalse(player.intersects(target));
        assertFalse(player.intersects(target.offset(0, 1, 0)));
        assertTrue(WorkstationPlacement.clearable(true, false, false, true, false, .2f));
        assertTrue(WorkstationPlacement.mayClear(0, false, false));
    }

    @Test void shortGrassCanBeClearedButLogsStoneContainersAndWaterCannot() {
        assertTrue(WorkstationPlacement.clearable(false, true, true, true, false, 0));
        assertFalse(WorkstationPlacement.clearable(false, false, false, true, false, 2));
        assertFalse(WorkstationPlacement.clearable(false, false, false, true, false, 1.5f));
        assertFalse(WorkstationPlacement.clearable(true, false, false, true, true, .2f));
        assertFalse(WorkstationPlacement.clearable(true, false, false, false, false, .2f));
    }

    @Test void LeafSupportsUnderAPlayerOrUnderThePlannedTableAreNeverCleared() {
        assertFalse(WorkstationPlacement.mayClear(0, true, false));
        assertFalse(WorkstationPlacement.mayClear(0, false, true));
        assertFalse(WorkstationPlacement.mayClear(0, true, true));
    }

    @Test void clearanceCannotExpandIntoAnUnboundedExcavation() {
        assertTrue(WorkstationPlacement.mayClear(7, false, false));
        assertFalse(WorkstationPlacement.mayClear(8, false, false));
        assertFalse(WorkstationPlacement.mayClear(-1, false, false));
        for (float hardness : new float[]{-.1f, .3f, Float.NaN, Float.POSITIVE_INFINITY})
            assertFalse(WorkstationPlacement.clearable(true, false, false, true, false, hardness));
    }
}
