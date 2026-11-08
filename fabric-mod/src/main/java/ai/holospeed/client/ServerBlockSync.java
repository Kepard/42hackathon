package ai.holospeed.client;

import java.util.LinkedHashMap;
import java.util.Map;

/** Bounded server packet evidence; raw state IDs distinguish rejection from client prediction. */
public final class ServerBlockSync {
    public static final ServerBlockSync INSTANCE = new ServerBlockSync();
    private record Update(long sequence, int stateId) {}
    private final Map<Long, Update> blocks = new LinkedHashMap<>();
    private Object connection;
    private long sequence;
    public void record(Object connection, long position, int stateId) {
        if (this.connection != connection) { blocks.clear(); this.connection = connection; }
        blocks.remove(position);
        blocks.put(position, new Update(++sequence, stateId));
        if (blocks.size() > 4096) blocks.remove(blocks.keySet().iterator().next());
    }
    public long sequence(Object connection, long position) {
        Update update = connection == this.connection ? blocks.get(position) : null;
        return update == null ? 0 : update.sequence();
    }
    public int stateId(Object connection, long position) {
        Update update = connection == this.connection ? blocks.get(position) : null;
        return update == null ? -1 : update.stateId();
    }
    public long latest(Object connection) { return connection == this.connection ? sequence : 0; }
    int size() { return blocks.size(); }
}
