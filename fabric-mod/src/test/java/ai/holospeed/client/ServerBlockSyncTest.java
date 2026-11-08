package ai.holospeed.client;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ServerBlockSyncTest {
    @Test void onlyANewServerUpdateToTheExpectedCellConfirmsAnAction() {
        ServerBlockSync sync = new ServerBlockSync(); Object connection = new Object();
        sync.record(connection, 10, 5);
        long before = sync.latest(connection);
        sync.record(connection, 11, 8);
        assertTrue(sync.sequence(connection, 10) <= before);
        sync.record(connection, 10, 0);
        assertTrue(sync.sequence(connection, 10) > before);
        assertEquals(0, sync.stateId(connection, 10), "server rejection must retain actual air rather than predicted placed block");
        assertEquals(0, sync.sequence(new Object(), 10));
    }
    @Test void memoryIsBoundedAndConnectionReplacementInvalidatesOldCells() {
        ServerBlockSync sync = new ServerBlockSync(); Object first = new Object(), second = new Object();
        for (long cell = 0; cell < 5000; cell++) sync.record(first, cell, 7);
        assertEquals(4096, sync.size()); assertEquals(0, sync.sequence(first, 0));
        sync.record(second, 10000, 9);
        assertEquals(1, sync.size()); assertEquals(0, sync.sequence(second, 4999));
        assertEquals(-1, sync.stateId(second, 4999));
    }
}
