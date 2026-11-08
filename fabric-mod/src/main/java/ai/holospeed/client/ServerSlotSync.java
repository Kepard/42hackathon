package ai.holospeed.client;

/** Server-origin hotbar evidence, separate from full-container synchronization. */
public final class ServerSlotSync {
    public static final ServerSlotSync INSTANCE = new ServerSlotSync();
    private record Update(long sequence, String item, int count) {}
    private final Update[] hotbar = new Update[9];
    private Object connection;
    private long sequence;

    public void record(Object connection, int syncId, int packetSlot, String item, int count) {
        int slot = syncId == -2 ? packetSlot : syncId == 0 ? packetSlot - 36 : -1;
        if (slot < 0 || slot >= hotbar.length) return;
        if (connection != this.connection) {
            java.util.Arrays.fill(hotbar, null);
            this.connection = connection;
        }
        hotbar[slot] = new Update(++sequence, item, count);
    }

    public long latest(Object connection) { return connection == this.connection ? sequence : 0; }

    public boolean matches(Object connection, int slot, long after, String item, int minimumCount) {
        if (connection != this.connection || slot < 0 || slot >= hotbar.length) return false;
        Update update = hotbar[slot];
        return update != null && update.sequence() > after && update.item().equals(item) && update.count() >= minimumCount;
    }
}
