package ai.holospeed.client;

import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import java.util.HashSet;
import static org.junit.jupiter.api.Assertions.*;

class CastPortalPlanTest {
    @Test void frameUsesTenNewBlocksAndHasNoOptionalCorners() {
        for (int rotation = 0; rotation < 4; rotation++) {
            CastPortalPlan plan = new CastPortalPlan(new CastPortalPlan.Point(10, 64, 20), rotation);
            assertEquals(10, new HashSet<>(plan.frame()).size());
            assertEquals(6, new HashSet<>(plan.interior()).size());
            assertTrue(plan.frame().stream().noneMatch(plan.interior()::contains));
            assertFalse(plan.frame().contains(plan.at(0, 0, 0)));
            assertFalse(plan.frame().contains(plan.at(0, 4, 3)));
            assertEquals(plan.water(), plan.frame().getLast());
        }
    }

    @Test void moldIsSmallConnectedAndItsTemporaryBridgesAreRemoved() {
        CastPortalPlan plan = new CastPortalPlan(new CastPortalPlan.Point(0, 64, 0), 0);
        assertEquals(CastPortalPlan.MATERIALS, new HashSet<>(plan.mold()).size());
        assertEquals(plan.pad(), plan.mold().getFirst());
        assertTrue(plan.mold().containsAll(plan.initialRemovals()));
        assertEquals(plan.at(0, 4, 1), plan.water());
        assertTrue(plan.interior().contains(plan.header()));
        assertEquals(java.util.Set.of(plan.header(), plan.at(0, 3, 2)),
            plan.interior().stream().filter(plan.mold()::contains).collect(java.util.stream.Collectors.toSet()));
        assertTrue(plan.initialRemovals().contains(plan.at(0, 3, 2)));
    }

    @Test void lavaSiteIsGenericButStrictlyBounded() {
        var target = JsonParser.parseString("{\"x\":4,\"y\":63,\"z\":2}").getAsJsonObject();
        assertEquals(new PortalRoute.Point(4, 63, 2), CastPortalPlan.parseTarget(target));
        target.addProperty("y", 314);
        assertThrows(IllegalArgumentException.class, () -> CastPortalPlan.parseTarget(target));
        target.addProperty("y", 63.5);
        assertThrows(IllegalArgumentException.class, () -> CastPortalPlan.parseTarget(target));
        target.addProperty("y", "63");
        assertThrows(IllegalArgumentException.class, () -> CastPortalPlan.parseTarget(target));
    }
}
