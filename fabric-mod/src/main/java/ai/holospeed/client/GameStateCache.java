package ai.holospeed.client;

import com.google.gson.JsonObject;
import java.util.function.Supplier;

/** Client-thread cache; requests never overwrite observation/action frame identities. */
final class GameStateCache {
    static final long SAMPLE_INTERVAL_NANOS = 200_000_000L;
    private Object worldIdentity;
    private Object playerIdentity;
    private JsonObject snapshot;
    private long sampledNanos;

    void invalidate() { snapshot = null; }

    static String requestId(JsonObject request) {
        var id = request.get("id");
        if (id == null || !id.isJsonPrimitive() || !id.getAsJsonPrimitive().isString()) {
            throw new IllegalArgumentException("state request id must be a string");
        }
        return id.getAsString();
    }

    JsonObject reply(Object world, Object player, String requestId, long nanos,
                     long wallMillis, Supplier<JsonObject> sample) {
        if (requestId == null || requestId.isBlank() || requestId.length() > 128) {
            throw new IllegalArgumentException("state request id must contain 1..128 characters");
        }
        if (snapshot == null || world != worldIdentity || player != playerIdentity
                || nanos - sampledNanos >= SAMPLE_INTERVAL_NANOS) {
            snapshot = sample.get().deepCopy();
            snapshot.addProperty("sampled_at_ms", wallMillis);
            sampledNanos = nanos;
            worldIdentity = world;
            playerIdentity = player;
        }
        JsonObject response = snapshot.deepCopy();
        response.addProperty("type", "game_state");
        response.addProperty("id", requestId);
        response.addProperty("cache_age_ms", Math.max(0, (nanos - sampledNanos) / 1_000_000));
        return response;
    }
}
