package ai.holospeed.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import java.util.HashSet;
import static org.junit.jupiter.api.Assertions.*;

class PortalRouteTest {
    private static JsonObject json(String value) { return JsonParser.parseString(value).getAsJsonObject(); }

    @Test void verifiedRouteRetainsTheActualThreeByThreeInteriorAndOptionalCorners() {
        assertEquals(9, PortalRoute.interior().size());
        assertEquals(12, PortalRoute.requiredFrame().size());
        assertEquals(9, new HashSet<>(PortalRoute.interior()).size());
        assertEquals(12, new HashSet<>(PortalRoute.requiredFrame()).size());
        assertTrue(PortalRoute.requiredFrame().contains(PortalRoute.GAP));
        assertFalse(PortalRoute.requiredFrame().contains(new PortalRoute.Point(50, 72, 48)), "crying optional corner must not be mined");
        for (var point : PortalRoute.interior()) {
            assertEquals(50, point.x());
            assertTrue(point.y() >= 69 && point.y() <= 71);
            assertTrue(point.z() >= 49 && point.z() <= 51);
            assertFalse(PortalRoute.requiredFrame().contains(point));
        }
    }

    @Test void supportAndIgnitionFacesResolveToTheIntendedEmptyCells() {
        assertEquals(PortalRoute.GAP, new PortalRoute.Point(PortalRoute.SUPPORT.x(), PortalRoute.SUPPORT.y() - 1, PortalRoute.SUPPORT.z()));
        assertEquals(PortalRoute.ENTRY, new PortalRoute.Point(PortalRoute.BASE.x(), PortalRoute.BASE.y() + 1, PortalRoute.BASE.z()));
        assertTrue(PortalRoute.interior().contains(PortalRoute.ENTRY));
    }

    @Test void onlyTheVerifiedLandmarkAndStrictCoordinatesAreAccepted() {
        assertEquals(PortalRoute.LANDMARK, PortalRoute.parseTarget(json("{\"x\":48,\"y\":68,\"z\":48}")));
        for (String target : new String[]{"{}", "{\"x\":48,\"y\":68}", "{\"x\":49,\"y\":68,\"z\":48}",
            "{\"x\":\"48\",\"y\":68,\"z\":48}", "{\"x\":48,\"y\":68.5,\"z\":48}", "{\"x\":48,\"y\":68,\"z\":48,\"command\":\"x\"}"}) {
            assertThrows(IllegalArgumentException.class, () -> PortalRoute.parseTarget(json(target)));
        }
    }

    @Test void portalRequestCannotBeConfusedWithAnInventoryCountOrOtherSkillTarget() {
        JsonObject request = json("{\"skill\":\"portal\",\"item\":\"minecraft:nether_portal\",\"target_count\":1,\"max_ticks\":1800,\"target\":{\"x\":48,\"y\":68,\"z\":48}}");
        var parsed = SkillController.parseRequest(request);
        assertEquals("portal", parsed.kind());
        assertEquals(PortalRoute.LANDMARK, parsed.target());
        request.addProperty("target_count", 2);
        assertThrows(IllegalArgumentException.class, () -> SkillController.parseRequest(request));
        request.addProperty("target_count", 1);
        request.addProperty("skill", "mine");
        request.addProperty("item", "minecraft:stone");
        assertThrows(IllegalArgumentException.class, () -> SkillController.parseRequest(request));
        request.addProperty("skill", "portal");
        request.addProperty("item", "minecraft:nether_portal");
        request.add("target", JsonParser.parseString("[]"));
        assertThrows(IllegalArgumentException.class, () -> SkillController.parseRequest(request));
    }
}
