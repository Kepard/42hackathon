package ai.holospeed.client;

import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class StateBoundsTest {
    @Test
    void nearbyScanContainsExactlyTheRadiusEightSphere() {
        List<StateBounds.Offset> offsets = StateBounds.nearbyOffsets();
        assertEquals(2109, offsets.size());
        assertEquals(offsets.size(), new HashSet<>(offsets).size());
        assertTrue(offsets.stream().allMatch(offset -> offset.squaredDistance() <= 64));
        assertTrue(offsets.contains(new StateBounds.Offset(8, 0, 0)));
        assertTrue(offsets.contains(new StateBounds.Offset(-8, 0, 0)));
        assertTrue(offsets.contains(new StateBounds.Offset(0, 8, 0)));
        assertTrue(offsets.contains(new StateBounds.Offset(0, -8, 0)));
        assertTrue(offsets.contains(new StateBounds.Offset(0, 0, 8)));
        assertTrue(offsets.contains(new StateBounds.Offset(0, 0, -8)));
        assertFalse(offsets.contains(new StateBounds.Offset(8, 1, 0)));
        assertFalse(offsets.contains(new StateBounds.Offset(8, 8, 8)));
    }

    @Test
    void localScanContainsEveryPositionInTheNineByNineByNineCube() {
        List<StateBounds.Offset> offsets = StateBounds.localOffsets();
        assertEquals(729, offsets.size());
        assertEquals(offsets.size(), new HashSet<>(offsets).size());
        assertTrue(offsets.stream().allMatch(offset ->
                Math.abs(offset.x()) <= 4 && Math.abs(offset.y()) <= 4 && Math.abs(offset.z()) <= 4));
        for (int x : new int[]{-4, 4}) {
            for (int y : new int[]{-4, 4}) {
                for (int z : new int[]{-4, 4}) {
                    assertTrue(offsets.contains(new StateBounds.Offset(x, y, z)),
                            "Cube corner must not be dropped by a spherical filter");
                }
            }
        }
        assertFalse(offsets.contains(new StateBounds.Offset(5, 0, 0)));
    }

    @Test
    void bothScansStartAtOriginAndHaveStableCoordinateTieBreaks() {
        List<StateBounds.Offset> expectedFirstSeven = List.of(
                new StateBounds.Offset(0, 0, 0),
                new StateBounds.Offset(0, -1, 0),
                new StateBounds.Offset(-1, 0, 0),
                new StateBounds.Offset(0, 0, -1),
                new StateBounds.Offset(0, 0, 1),
                new StateBounds.Offset(1, 0, 0),
                new StateBounds.Offset(0, 1, 0)
        );
        assertEquals(expectedFirstSeven, StateBounds.nearbyOffsets().subList(0, 7));
        assertEquals(expectedFirstSeven, StateBounds.localOffsets().subList(0, 7));
        assertOrdered(StateBounds.nearbyOffsets());
        assertOrdered(StateBounds.localOffsets());
    }

    @Test
    void callersCannotCorruptSharedScanGeometry() {
        for (List<StateBounds.Offset> offsets : List.of(
                StateBounds.nearbyOffsets(), StateBounds.localOffsets())) {
            assertThrows(UnsupportedOperationException.class,
                    () -> offsets.add(new StateBounds.Offset(99, 99, 99)));
            assertThrows(UnsupportedOperationException.class, () -> offsets.remove(0));
            assertThrows(UnsupportedOperationException.class,
                    () -> offsets.set(0, new StateBounds.Offset(1, 1, 1)));
            assertThrows(UnsupportedOperationException.class, () -> offsets.subList(0, 2).clear());
        }
        assertEquals(new StateBounds.Offset(0, 0, 0), StateBounds.nearbyOffsets().getFirst());
        assertEquals(new StateBounds.Offset(0, 0, 0), StateBounds.localOffsets().getFirst());
    }

    @Test
    void squaredDistanceIsSignIndependentAndMatchesKnownPositions() {
        assertEquals(0, new StateBounds.Offset(0, 0, 0).squaredDistance());
        assertEquals(25, new StateBounds.Offset(3, 0, 4).squaredDistance());
        assertEquals(25, new StateBounds.Offset(-3, 0, -4).squaredDistance());
        assertEquals(48, new StateBounds.Offset(-4, 4, -4).squaredDistance());
    }

    private static void assertOrdered(List<StateBounds.Offset> offsets) {
        for (int index = 1; index < offsets.size(); index++) {
            StateBounds.Offset before = offsets.get(index - 1);
            StateBounds.Offset after = offsets.get(index);
            int distance = Integer.compare(before.squaredDistance(), after.squaredDistance());
            int y = Integer.compare(before.y(), after.y());
            int x = Integer.compare(before.x(), after.x());
            int z = Integer.compare(before.z(), after.z());
            assertTrue(distance < 0 || distance == 0 && (y < 0 || y == 0 && (x < 0 || x == 0 && z < 0)),
                    "Unexpected order at " + index + ": " + before + " then " + after);
        }
    }
}
