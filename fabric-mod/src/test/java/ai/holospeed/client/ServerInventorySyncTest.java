package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ServerInventorySyncTest {
    @Test void OnlyTheRequestedHandlerAndCurrentConnectionCanConfirmInventory() {
        ServerInventorySync sync = new ServerInventorySync();
        Object connection = new Object();
        assertEquals(0, sync.sequence(connection, 1));
        sync.record(connection, 1);
        long first = sync.sequence(connection, 1);
        sync.record(connection, 2);
        assertEquals(first, sync.sequence(connection, 1));
        assertTrue(sync.sequence(connection, 2) > first);
        Object nextConnection = new Object();
        sync.record(nextConnection, 1);
        assertEquals(0, sync.sequence(connection, 1));
        assertEquals(0, sync.sequence(nextConnection, 2));
    }

    @Test void ReopenedContainersRequireAFreshServerPacketEvenWhenAnIdIsReused() {
        ServerInventorySync sync = new ServerInventorySync();
        Object connection = new Object();
        sync.record(connection, 4);
        long beforeReopen = sync.latest(connection);
        assertFalse(sync.sequence(connection, 4) > beforeReopen);
        sync.record(connection, 4);
        assertTrue(sync.sequence(connection, 4) > beforeReopen);
    }
}
