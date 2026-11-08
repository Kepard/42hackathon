package ai.holospeed.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;

class GameStateCacheTest {
    private static JsonObject sample(int tick) {
        JsonObject state = new JsonObject();
        state.addProperty("tick", tick);
        JsonObject player = new JsonObject();
        player.addProperty("health", 20);
        state.add("player", player);
        return state;
    }

    @Test void requestsWithinTwoHundredMillisecondsShareOneSampleButKeepDistinctIds() {
        GameStateCache cache = new GameStateCache();
        AtomicInteger samples = new AtomicInteger();
        Object world = new Object();
        Object player = new Object();
        JsonObject first = cache.reply(world, player, "one", 1_000_000_000L, 1000, () -> sample(samples.incrementAndGet()));
        JsonObject second = cache.reply(world, player, "two", 1_199_999_999L, 1199, () -> sample(samples.incrementAndGet()));
        assertEquals(1, samples.get());
        assertEquals("one", first.get("id").getAsString());
        assertEquals("two", second.get("id").getAsString());
        assertEquals("game_state", second.get("type").getAsString());
        assertEquals(1000, second.get("sampled_at_ms").getAsLong());
        assertEquals(199, second.get("cache_age_ms").getAsLong());
        JsonObject third = cache.reply(world, player, "three", 1_200_000_000L, 1200, () -> sample(samples.incrementAndGet()));
        assertEquals(2, samples.get());
        assertEquals(1200, third.get("sampled_at_ms").getAsLong());
        assertEquals(0, third.get("cache_age_ms").getAsLong());
    }

    @Test void returnedStateCannotMutateCachedSnapshotOrItsOriginalSample() {
        GameStateCache cache = new GameStateCache();
        Object world = new Object();
        JsonObject original = sample(42);
        JsonObject first = cache.reply(world, null, "one", 0, 1000, () -> original);
        first.getAsJsonObject("player").addProperty("health", 0);
        original.addProperty("tick", 999);
        JsonObject second = cache.reply(world, null, "two", 1, 1000, () -> fail("must use cache"));
        assertEquals(20, second.getAsJsonObject("player").get("health").getAsInt());
        assertEquals(42, second.get("tick").getAsInt());
        assertFalse(original.has("type"));
    }

    @Test void worldUnloadAndRespawnInvalidateCachedIdentityImmediately() {
        GameStateCache cache = new GameStateCache();
        AtomicInteger samples = new AtomicInteger();
        Object world = new Object();
        Object player = new Object();
        cache.reply(world, player, "one", 0, 1000, () -> sample(samples.incrementAndGet()));
        cache.reply(world, new Object(), "respawn", 1, 1000, () -> sample(samples.incrementAndGet()));
        cache.reply(null, null, "unload", 2, 1000, () -> sample(samples.incrementAndGet()));
        assertEquals(3, samples.get());
    }

    @Test void invalidCorrelationIdsCannotTriggerASample() {
        GameStateCache cache = new GameStateCache();
        for (String id : new String[]{null, "", "  ", "a".repeat(129)}) {
            assertThrows(IllegalArgumentException.class,
                () -> cache.reply(null, null, id, 0, 1000, () -> fail("invalid request sampled game")));
        }
    }

    @Test void correlationIdsCannotBeCoercedFromOtherJsonTypes() {
        for (String value : new String[]{"{}", "{\"id\":null}", "{\"id\":true}",
                "{\"id\":4}", "{\"id\":[]}", "{\"id\":{}}"}) {
            JsonObject request = JsonParser.parseString(value).getAsJsonObject();
            assertThrows(IllegalArgumentException.class, () -> GameStateCache.requestId(request));
        }
        JsonObject valid = JsonParser.parseString("{\"id\":\"state-001\"}").getAsJsonObject();
        assertEquals("state-001", GameStateCache.requestId(valid));
    }

    @Test void operatorResumeCannotReuseAPreviousPausedScreenSnapshot() {
        Object world = new Object();
        GameStateCache cache = new GameStateCache();
        JsonObject paused = sample(10);
        paused.addProperty("screen_open", true);
        cache.reply(world, null, "paused", 0, 1000, () -> paused);
        cache.invalidate();
        JsonObject resumed = sample(11);
        resumed.addProperty("screen_open", false);
        JsonObject reply = cache.reply(world, null, "resumed", 1_000_000, 1001, () -> resumed);
        assertFalse(reply.get("screen_open").getAsBoolean());
        assertEquals(11, reply.get("tick").getAsInt());
        assertEquals(1001, reply.get("sampled_at_ms").getAsLong());
    }
}
