package ai.holospeed.client;

import java.util.LinkedHashMap;
import java.util.Map;

/** Sequence markers for applied server inventory packets; contains no item data. */
public final class ServerInventorySync {
    public static final ServerInventorySync INSTANCE = new ServerInventorySync();
    private final Map<Integer, Long> handlers = new LinkedHashMap<>();
    private Object connection;
    private long sequence;

    public void record(Object connection, int syncId) {
        if (connection != this.connection) { handlers.clear(); this.connection = connection; }
        handlers.put(syncId, ++sequence);
        if (handlers.size() > 256) handlers.remove(handlers.keySet().iterator().next());
    }

    public long sequence(Object connection, int syncId) { return connection == this.connection ? handlers.getOrDefault(syncId, 0L) : 0; }
    public long latest(Object connection) { return connection == this.connection ? sequence : 0; }
}
