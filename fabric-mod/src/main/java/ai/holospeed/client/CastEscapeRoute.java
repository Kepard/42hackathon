package ai.holospeed.client;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.function.Predicate;

/** Small, loaded-terrain walking routes for runoff that Baritone treats as impassable. */
final class CastEscapeRoute {
    enum Cell { SOLID, OPEN, WATER, BLOCKED }

    private static final int MAX_RADIUS = 12;
    private static final int MAX_HEIGHT_CHANGE = 2;
    private static final int MAX_VISITED = 2048;
    private static final int[][] SIDES = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};

    private CastEscapeRoute() {}

    /** Immutable cardinal waypoints excluding start, including the first safe dry goal. */
    static List<CastPortalPlan.Point> find(CastPortalPlan.Point start, Function<CastPortalPlan.Point, Cell> sample) {
        if (start == null || sample == null) return List.of();
        Function<CastPortalPlan.Point, Cell> cached = cached(sample);
        return search(start, cached, point -> dryEnough(point, cached));
    }

    /**
     * Immutable cardinal waypoints excluding start, including goal. An empty list means
     * no route or an already-reached valid goal. Exact goals may be in shallow water.
     */
    static List<CastPortalPlan.Point> findTo(CastPortalPlan.Point start, CastPortalPlan.Point goal,
                                          Function<CastPortalPlan.Point, Cell> sample) {
        if (start == null || goal == null || sample == null || !withinBounds(start, goal)) return List.of();
        Function<CastPortalPlan.Point, Cell> cached = cached(sample);
        if (!standable(goal, cached)) return List.of();
        return search(start, cached, goal::equals);
    }

    /** A full floor, shallow open/water feet, and an entirely clear head are required. */
    static boolean standable(CastPortalPlan.Point point, Function<CastPortalPlan.Point, Cell> sample) {
        if (point == null || sample == null) return false;
        Cell feet = read(point, sample);
        return (feet == Cell.OPEN || feet == Cell.WATER)
            && read(offset(point, 0, -1, 0), sample) == Cell.SOLID
            && read(offset(point, 0, 1, 0), sample) == Cell.OPEN;
    }

    /** Require a dry center and a known, water-free cardinal margin before handing off. */
    static boolean dryEnough(CastPortalPlan.Point point, Function<CastPortalPlan.Point, Cell> sample) {
        if (!standable(point, sample) || read(point, sample) != Cell.OPEN) return false;
        for (int[] side : SIDES) {
            Cell neighbor = read(offset(point, side[0], 0, side[1]), sample);
            if (neighbor != Cell.OPEN && neighbor != Cell.SOLID) return false;
        }
        return true;
    }

    private static List<CastPortalPlan.Point> search(CastPortalPlan.Point start,
                                                    Function<CastPortalPlan.Point, Cell> sample,
                                                    Predicate<CastPortalPlan.Point> goal) {
        if (!standable(start, sample)) return List.of();
        ArrayDeque<CastPortalPlan.Point> pending = new ArrayDeque<>();
        Map<CastPortalPlan.Point, CastPortalPlan.Point> previous = new HashMap<>();
        pending.addLast(start);
        previous.put(start, null);
        while (!pending.isEmpty()) {
            CastPortalPlan.Point current = pending.removeFirst();
            if (goal.test(current)) return reconstruct(current, previous);
            for (int[] side : SIDES) for (int dy : new int[]{0, 1, -1}) {
                CastPortalPlan.Point next = offset(current, side[0], dy, side[1]);
                if (next == null || previous.containsKey(next) || !withinBounds(start, next)
                    || !standable(next, sample)) continue;
                // Jumping needs headroom above the takeoff cell. Stepping down needs
                // clearance at the old head height until the player's body descends.
                if (dy > 0 && read(offset(current, 0, 2, 0), sample) != Cell.OPEN) continue;
                if (dy < 0 && read(offset(next, 0, 2, 0), sample) != Cell.OPEN) continue;
                if (previous.size() >= MAX_VISITED) return List.of();
                previous.put(next, current);
                pending.addLast(next);
            }
        }
        return List.of();
    }

    private static List<CastPortalPlan.Point> reconstruct(CastPortalPlan.Point goal,
                                                         Map<CastPortalPlan.Point, CastPortalPlan.Point> previous) {
        List<CastPortalPlan.Point> result = new ArrayList<>();
        for (CastPortalPlan.Point cursor = goal; previous.get(cursor) != null; cursor = previous.get(cursor))
            result.add(cursor);
        Collections.reverse(result);
        return List.copyOf(result);
    }

    private static boolean withinBounds(CastPortalPlan.Point start, CastPortalPlan.Point point) {
        return Math.abs((long) point.x() - start.x()) + Math.abs((long) point.z() - start.z()) <= MAX_RADIUS
            && Math.abs((long) point.y() - start.y()) <= MAX_HEIGHT_CHANGE;
    }

    private static Function<CastPortalPlan.Point, Cell> cached(Function<CastPortalPlan.Point, Cell> sample) {
        Map<CastPortalPlan.Point, Cell> cells = new HashMap<>();
        return point -> cells.computeIfAbsent(point, position -> read(position, sample));
    }

    private static Cell read(CastPortalPlan.Point point, Function<CastPortalPlan.Point, Cell> sample) {
        if (point == null) return Cell.BLOCKED;
        Cell cell = sample.apply(point);
        return cell == null ? Cell.BLOCKED : cell;
    }

    private static CastPortalPlan.Point offset(CastPortalPlan.Point point, int x, int y, int z) {
        try {
            return new CastPortalPlan.Point(Math.addExact(point.x(), x), Math.addExact(point.y(), y), Math.addExact(point.z(), z));
        } catch (ArithmeticException overflow) {
            return null;
        }
    }
}
