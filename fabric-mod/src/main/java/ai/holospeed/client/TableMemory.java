package ai.holospeed.client;

/** Last verified table position, scoped to one client-world identity. */
final class TableMemory {
    record Position(int x, int y, int z) {
        Position {
            for (int coordinate : new int[]{x, y, z}) {
                if (coordinate < -30_000_000 || coordinate > 29_999_999) {
                    throw new IllegalArgumentException("table coordinate outside supported world bounds");
                }
            }
        }
    }

    private Object worldIdentity;
    private Position table;

    void remember(Object world, Position position) {
        if (world == null || position == null) throw new IllegalArgumentException("verified world and table position required");
        worldIdentity = world;
        table = position;
    }

    Position position(Object world) {
        if (world == null || world != worldIdentity) {
            worldIdentity = world;
            table = null;
        }
        return table;
    }

    void forget(Object world) {
        worldIdentity = world;
        table = null;
    }
}
