package ai.holospeed.client;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** A ten-obsidian, two-by-three portal cast using one bucket and a temporary mold. */
final class CastPortalPlan {
    record Point(int x, int y, int z) {}
    final Point origin;
    final int rotation;
    static final int MATERIALS = 9;

    CastPortalPlan(Point origin, int rotation) {
        if (rotation < 0 || rotation > 3 || origin.y() < -63 || origin.y() > 313)
            throw new IllegalArgumentException("invalid portal construction bounds");
        this.origin = origin;
        this.rotation = rotation;
    }

    static PortalRoute.Point parseTarget(JsonObject target) {
        if (target == null || !target.keySet().equals(Set.of("x", "y", "z")))
            throw new IllegalArgumentException("natural lava target requires x,y,z only");
        return new PortalRoute.Point(ActionSpec.integer(target, "x", 0, -29_999_968, 29_999_967),
            ActionSpec.integer(target, "y", 0, -63, 313),
            ActionSpec.integer(target, "z", 0, -29_999_968, 29_999_967));
    }

    Point at(int x, int y, int z) {
        int dx = switch (rotation) { case 0 -> x; case 1 -> -z; case 2 -> -x; default -> z; };
        int dz = switch (rotation) { case 0 -> z; case 1 -> x; case 2 -> -z; default -> -x; };
        return new Point(origin.x() + dx, origin.y() + y, origin.z() + dz);
    }

    Point pad() { return at(2, 1, 1); }
    Point stand() { return at(2, 2, 1); }
    Point approach() { return at(3, 1, 1); }
    Point header() { return at(0, 3, 1); }
    Point water() { return at(0, 4, 1); }
    Point bridgeStand() { return at(2, 1, 4); }
    Point entry() { return at(0, 1, 1); }

    /** Build from the ground upward; two channels are opened before water placement. */
    List<Point> mold() {
        List<Point> points = new ArrayList<>();
        points.add(pad());
        for (int y = 1; y <= 3; y++) points.add(at(0, y, -1));
        for (int z = 0; z <= 3; z++) points.add(at(0, 3, z));
        points.add(at(0, 4, 3));
        return List.copyOf(points);
    }

    List<Point> initialRemovals() { return List.of(at(0, 3, 0), at(0, 3, 2)); }

    /** Bottom-up columns retain water above each new lava source until it solidifies. */
    List<Point> frame() {
        List<Point> points = new ArrayList<>();
        points.add(at(0, 0, 1)); points.add(at(0, 0, 2));
        for (int y = 1; y <= 3; y++) points.add(at(0, y, 0));
        for (int y = 1; y <= 3; y++) points.add(at(0, y, 3));
        points.add(at(0, 4, 2)); points.add(water());
        return List.copyOf(points);
    }

    List<Point> interior() {
        List<Point> points = new ArrayList<>();
        for (int y = 1; y <= 3; y++) for (int z = 1; z <= 2; z++) points.add(at(0, y, z));
        return List.copyOf(points);
    }

    List<Point> waterfallFeet() {
        return List.of(at(-1, 1, 1), at(1, 1, 1), at(0, 1, 0), at(0, 1, 2));
    }
}
