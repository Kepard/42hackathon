package ai.holospeed.client;

import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

/** Conservative, bounded downstream check for the construction site's four waterfalls. */
final class CastWaterSafety {
    enum Cell { SOLID, OPEN, LAVA, UNKNOWN }

    private record Flow(CastPortalPlan.Point position, int remaining) {}
    private static final int HORIZONTAL_FLOW = 7;
    private static final int MAX_VISITED = 4096;
    private static final int MAX_DISTANCE = 32;
    private static final int MAX_DROP = 16;
    private static final int[][] SIDES = {{1, 0, 0}, {-1, 0, 0}, {0, 0, 1}, {0, 0, -1}, {0, -1, 0}, {0, 1, 0}};

    private CastWaterSafety() {}

    /**
     * OPEN includes air and existing water. SOLID must mean a cell that blocks water.
     * Unknown cells, adjacent lava, and an exhausted search bound all fail closed.
     * Distance is horizontal Manhattan distance from at least one starting baseline.
     */
    static boolean safe(List<CastPortalPlan.Point> starts, Function<CastPortalPlan.Point, Cell> sample) {
        if (starts == null || starts.isEmpty() || starts.size() > 4 || sample == null
            || starts.stream().anyMatch(java.util.Objects::isNull)) return false;
        List<CastPortalPlan.Point> baselines = List.copyOf(starts);
        ArrayDeque<Flow> pending = new ArrayDeque<>();
        baselines.forEach(position -> pending.addLast(new Flow(position, HORIZONTAL_FLOW)));
        Map<CastPortalPlan.Point, Integer> visited = new HashMap<>();
        Map<CastPortalPlan.Point, Cell> sampled = new HashMap<>();
        while (!pending.isEmpty()) {
            Flow flow = pending.removeFirst();
            CastPortalPlan.Point position = flow.position();
            if (visited.getOrDefault(position, -1) >= flow.remaining()) continue;
            if (!withinBounds(position, baselines)) return false;
            visited.put(position, flow.remaining());
            if (visited.size() > MAX_VISITED) return false;
            Cell here = read(position, sample, sampled);
            if (here == Cell.UNKNOWN || here == Cell.LAVA) return false;
            if (here == Cell.SOLID) continue;

            CastPortalPlan.Point[] neighbors = new CastPortalPlan.Point[SIDES.length];
            for (int side = 0; side < SIDES.length; side++) {
                neighbors[side] = offset(position, SIDES[side]);
                if (neighbors[side] == null) return false;
                Cell adjacent = read(neighbors[side], sample, sampled);
                if (adjacent == Cell.UNKNOWN || adjacent == Cell.LAVA) return false;
            }
            CastPortalPlan.Point below = neighbors[4];
            if (read(below, sample, sampled) == Cell.OPEN) {
                // A falling column gets a fresh spreading distance on its next floor.
                pending.addLast(new Flow(below, HORIZONTAL_FLOW));
            } else if (flow.remaining() > 0) {
                for (int side = 0; side < 4; side++) {
                    if (read(neighbors[side], sample, sampled) == Cell.OPEN)
                        pending.addLast(new Flow(neighbors[side], flow.remaining() - 1));
                }
            }
        }
        return true;
    }

    private static Cell read(CastPortalPlan.Point point, Function<CastPortalPlan.Point, Cell> sample,
                             Map<CastPortalPlan.Point, Cell> cache) {
        return cache.computeIfAbsent(point, position -> {
            Cell cell = sample.apply(position);
            return cell == null ? Cell.UNKNOWN : cell;
        });
    }

    private static boolean withinBounds(CastPortalPlan.Point point, List<CastPortalPlan.Point> starts) {
        for (CastPortalPlan.Point start : starts) {
            long horizontal = Math.abs((long) point.x() - start.x()) + Math.abs((long) point.z() - start.z());
            long drop = (long) start.y() - point.y();
            if (horizontal <= MAX_DISTANCE && drop >= 0 && drop <= MAX_DROP) return true;
        }
        return false;
    }

    private static CastPortalPlan.Point offset(CastPortalPlan.Point point, int[] direction) {
        try {
            return new CastPortalPlan.Point(Math.addExact(point.x(), direction[0]),
                Math.addExact(point.y(), direction[1]), Math.addExact(point.z(), direction[2]));
        } catch (ArithmeticException overflow) {
            return null;
        }
    }
}
