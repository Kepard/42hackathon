package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import java.util.List;
import java.util.HashMap;
import java.util.Map;
import java.util.function.Function;
import static ai.holospeed.client.CastEscapeRoute.Cell.*;
import static org.junit.jupiter.api.Assertions.*;

class CastEscapeRouteTest {
    private static final CastPortalPlan.Point START = point(0, 1, 0);

    @Test void findsShortestDryExitWithAMarginAndExcludesStart() {
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> terrain = position -> {
            if (position.y() <= 0) return SOLID;
            return position.y() == 1 && Math.abs(position.x()) + Math.abs(position.z()) <= 3 ? WATER : OPEN;
        };
        List<CastPortalPlan.Point> route = CastEscapeRoute.find(START, terrain);
        assertEquals(5, route.size());
        assertFalse(route.contains(START));
        assertTrue(CastEscapeRoute.dryEnough(route.getLast(), terrain));
        assertFalse(CastEscapeRoute.dryEnough(point(4, 1, 0), terrain));
        assertThrows(UnsupportedOperationException.class, () -> route.add(START));
    }

    @Test void exactRouteCrossesShallowWaterBackToAnElevatedPad() {
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> terrain = position -> {
            if (position.y() <= 0 || position.equals(point(3, 1, 0))) return SOLID;
            return position.y() == 1 ? WATER : OPEN;
        };
        CastPortalPlan.Point goal = point(3, 2, 0);
        List<CastPortalPlan.Point> route = CastEscapeRoute.findTo(START, goal, terrain);
        assertEquals(3, route.size());
        assertEquals(goal, route.getLast());
        assertEquals(1, route.get(1).y());
    }

    @Test void recordedRunoffFailureHasAnOrdinaryTwoStepDryExit() {
        // Observed complete local terrain from run20261108T115915-30150c0a.
        Map<CastPortalPlan.Point, CastEscapeRoute.Cell> cells = new HashMap<>();
        cells.put(point(-6, 64, -18), SOLID); cells.put(point(-6, 65, -18), WATER);
        cells.put(point(-6, 66, -18), OPEN);
        cells.put(point(-6, 64, -19), SOLID); cells.put(point(-6, 65, -19), OPEN);
        cells.put(point(-6, 66, -19), OPEN); cells.put(point(-6, 67, -19), OPEN);
        cells.put(point(-6, 65, -20), SOLID); cells.put(point(-6, 66, -20), OPEN);
        cells.put(point(-6, 67, -20), OPEN);
        cells.put(point(-7, 66, -20), OPEN); cells.put(point(-5, 66, -20), OPEN);
        cells.put(point(-6, 66, -21), OPEN);
        assertEquals(List.of(point(-6, 65, -19), point(-6, 66, -20)),
            CastEscapeRoute.find(point(-6, 65, -18), cell -> cells.getOrDefault(cell, BLOCKED)));
    }

    @Test void rejectsDeepWaterVoidAndUnloadedOrHazardCells() {
        assertTrue(CastEscapeRoute.find(START, position -> position.y() <= 1 ? WATER : OPEN).isEmpty());
        assertTrue(CastEscapeRoute.find(START, position -> OPEN).isEmpty());
        assertTrue(CastEscapeRoute.find(START, position -> position.equals(START) ? BLOCKED : OPEN).isEmpty());
        assertFalse(CastEscapeRoute.standable(START, position -> position.y() <= 0 ? SOLID : WATER));
        assertFalse(CastEscapeRoute.dryEnough(START, position -> {
            if (position.equals(point(1, 1, 0))) return BLOCKED;
            return position.y() <= 0 ? SOLID : OPEN;
        }));
    }

    @Test void routesAroundBlockedCellsInsteadOfWalkingThroughThem() {
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> terrain = position -> {
            if (position.x() == 1 && position.z() == 0 && position.y() >= 1) return BLOCKED;
            return position.y() <= 0 ? SOLID : OPEN;
        };
        List<CastPortalPlan.Point> route = CastEscapeRoute.findTo(START, point(2, 1, 0), terrain);
        assertEquals(4, route.size());
        assertTrue(route.stream().noneMatch(position -> position.x() == 1 && position.z() == 0));
    }

    @Test void ascentRequiresTakeoffHeadroom() {
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> clear = stairs(true, false);
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> lowCeiling = stairs(true, true);
        assertEquals(List.of(point(1, 2, 0)), CastEscapeRoute.findTo(START, point(1, 2, 0), clear));
        assertTrue(CastEscapeRoute.findTo(START, point(1, 2, 0), lowCeiling).isEmpty());
    }

    @Test void descentRequiresClearanceBeforeTheBodyDrops() {
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> clear = stairs(false, false);
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> overhang = stairs(false, true);
        assertEquals(List.of(point(1, 0, 0)), CastEscapeRoute.findTo(START, point(1, 0, 0), clear));
        assertTrue(CastEscapeRoute.standable(point(1, 0, 0), overhang));
        assertTrue(CastEscapeRoute.findTo(START, point(1, 0, 0), overhang).isEmpty());
    }

    @Test void radiusAndHeightBoundsRejectDistantGoalsAndUnboundedWater() {
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> flat = position -> position.y() <= 0 ? SOLID : OPEN;
        assertEquals(12, CastEscapeRoute.findTo(START, point(12, 1, 0), flat).size());
        assertTrue(CastEscapeRoute.findTo(START, point(13, 1, 0), flat).isEmpty());
        assertTrue(CastEscapeRoute.findTo(START, point(0, 4, 0), flat).isEmpty());
        assertTrue(CastEscapeRoute.find(START, position -> position.y() <= 0 ? SOLID : position.y() == 1 ? WATER : OPEN).isEmpty());
    }

    @Test void alreadyDryAndMalformedInputsNeedNoRoute() {
        Function<CastPortalPlan.Point, CastEscapeRoute.Cell> flat = position -> position.y() <= 0 ? SOLID : OPEN;
        assertTrue(CastEscapeRoute.find(START, flat).isEmpty());
        assertTrue(CastEscapeRoute.findTo(START, START, flat).isEmpty());
        assertTrue(CastEscapeRoute.find(null, flat).isEmpty());
        assertTrue(CastEscapeRoute.find(START, null).isEmpty());
        assertFalse(CastEscapeRoute.standable(START, position -> null));
        assertFalse(CastEscapeRoute.dryEnough(point(Integer.MAX_VALUE, 1, 0), flat));
        assertTrue(CastEscapeRoute.findTo(point(Integer.MAX_VALUE, 1, 0), point(Integer.MIN_VALUE, 1, 0), flat).isEmpty());
    }

    private static Function<CastPortalPlan.Point, CastEscapeRoute.Cell> stairs(boolean up, boolean ceiling) {
        return position -> {
            if (position.z() != 0 || position.x() < 0 || position.x() > 1) return BLOCKED;
            if (ceiling && position.equals(up ? point(0, 3, 0) : point(1, 2, 0))) return SOLID;
            int floor = position.x() == 0 ? 0 : up ? 1 : -1;
            return position.y() <= floor ? SOLID : OPEN;
        };
    }

    private static CastPortalPlan.Point point(int x, int y, int z) {
        return new CastPortalPlan.Point(x, y, z);
    }
}
