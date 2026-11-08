package ai.holospeed.client;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** Verified route geometry for the explicitly selected Minecraft 1.21.1 seed. */
final class PortalRoute {
    record Point(int x, int y, int z) {}
    static final Point LANDMARK = new Point(48, 68, 48);
    static final Point CHEST = new Point(49, 68, 52);
    static final Point GAP = new Point(50, 72, 50);
    static final Point SUPPORT = new Point(50, 73, 50);
    static final Point BASE = new Point(50, 68, 50);
    static final Point ENTRY = new Point(50, 69, 50);

    static Point parseTarget(JsonObject target) {
        if (target == null || !target.keySet().equals(Set.of("x", "y", "z"))) throw new IllegalArgumentException("portal target requires x,y,z only");
        Point point = new Point(ActionSpec.integer(target, "x", 0, -30_000_000, 29_999_999),
            ActionSpec.integer(target, "y", 0, -2048, 2047),
            ActionSpec.integer(target, "z", 0, -30_000_000, 29_999_999));
        if (!point.equals(LANDMARK)) throw new IllegalArgumentException("only the verified ruined portal landmark (48,68,48) is supported");
        return point;
    }

    static List<Point> requiredFrame() {
        List<Point> points = new ArrayList<>();
        for (int z = 49; z <= 51; z++) { points.add(new Point(50, 68, z)); points.add(new Point(50, 72, z)); }
        for (int y = 69; y <= 71; y++) { points.add(new Point(50, y, 48)); points.add(new Point(50, y, 52)); }
        return List.copyOf(points);
    }

    static List<Point> interior() {
        List<Point> points = new ArrayList<>();
        for (int y = 69; y <= 71; y++) for (int z = 49; z <= 51; z++) points.add(new Point(50, y, z));
        return List.copyOf(points);
    }
}
