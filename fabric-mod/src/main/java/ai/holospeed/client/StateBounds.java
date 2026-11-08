package ai.holospeed.client;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** Deterministic, bounded scan geometry shared by state capture and its tests. */
public final class StateBounds {
    public static final int NEARBY_RADIUS = 8;
    public static final int MAX_NEARBY_BLOCKS = 512;
    public static final int LOCAL_RADIUS = 4;
    public static final int MAX_NEARBY_ENTITIES = 64;

    public record Offset(int x, int y, int z) {
        public int squaredDistance() {
            return x * x + y * y + z * z;
        }
    }

    private static final Comparator<Offset> ORDER = Comparator
            .comparingInt(Offset::squaredDistance)
            .thenComparingInt(Offset::y)
            .thenComparingInt(Offset::x)
            .thenComparingInt(Offset::z);
    private static final List<Offset> NEARBY_OFFSETS = offsets(NEARBY_RADIUS, true);
    private static final List<Offset> LOCAL_OFFSETS = offsets(LOCAL_RADIUS, false);

    private StateBounds() {}

    /** All 2,109 positions inside the radius-eight sphere, including the origin. */
    public static List<Offset> nearbyOffsets() {
        return NEARBY_OFFSETS;
    }

    /** All 729 positions in the radius-four cube, including the origin. */
    public static List<Offset> localOffsets() {
        return LOCAL_OFFSETS;
    }

    private static List<Offset> offsets(int radius, boolean spherical) {
        List<Offset> values = new ArrayList<>();
        int radiusSquared = radius * radius;
        for (int x = -radius; x <= radius; x++) {
            for (int y = -radius; y <= radius; y++) {
                for (int z = -radius; z <= radius; z++) {
                    Offset offset = new Offset(x, y, z);
                    if (!spherical || offset.squaredDistance() <= radiusSquared) {
                        values.add(offset);
                    }
                }
            }
        }
        values.sort(ORDER);
        return List.copyOf(values);
    }
}
